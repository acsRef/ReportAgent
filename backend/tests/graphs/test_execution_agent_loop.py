from __future__ import annotations

import json

import pytest

from app.agent.sql_graph import _diagnose, _evaluate, _route_after_diagnose

pytestmark = pytest.mark.graphs


def _eval_and_diagnose(sql_result_dict: dict | None, retry_counters: dict, validation_result: dict | None = None) -> dict:
    state: dict = {
        "sql_result": json.dumps(sql_result_dict) if sql_result_dict is not None else "",
        "validation_result": validation_result or {"valid": True},
        "retry_counters": dict(retry_counters),
        "trace_id": "test-trace",
    }
    eval_out = _evaluate(state)
    state.update(eval_out)
    diag_out = _diagnose(state)
    state.update(diag_out)
    return state


def test_syntax_error_triggers_retry_sql():
    state = _eval_and_diagnose({"error": 'syntax error at end', "error_kind": "syntax"}, {"sql_generation": 0, "plan": 0})
    assert state["execution_status"] == "SQL_SYNTAX_ERROR"
    assert state["diagnose_decision"]["action"] == "retry_sql"
    assert _route_after_diagnose(state) == "generate_sql"


def test_object_error_replan_when_sql_budget_exhausted():
    state = _eval_and_diagnose({"error": 'column "x" does not exist', "error_kind": "object"}, {"sql_generation": 2, "plan": 0})
    assert state["execution_status"] == "SCHEMA_ERROR"
    assert state["diagnose_decision"]["action"] == "replan"
    assert state["retry_counters"]["plan"] == 1
    assert _route_after_diagnose(state) == "plan"


def test_timeout_does_not_retry():
    state = _eval_and_diagnose({"error": "canceling statement due to statement timeout", "error_kind": "timeout"}, {"sql_generation": 0, "plan": 0})
    assert state["execution_status"] == "FAILED"
    assert state["diagnose_decision"]["action"] == "fail"
    assert state["diagnose_decision"]["recoverable"] is False
    assert _route_after_diagnose(state) == "__end__"


def test_budget_exhausted_goes_to_clarify():
    state = _eval_and_diagnose({"error": 'column "y" does not exist', "error_kind": "syntax"}, {"sql_generation": 2, "plan": 1})
    assert state["execution_status"] == "NEED_CLARIFICATION"
    assert state["diagnose_decision"]["action"] == "clarify"
    assert _route_after_diagnose(state) == "__end__"


def test_success_goes_to_build_output():
    state = _eval_and_diagnose({"columns": [], "rows": [{"a": 1}]}, {"sql_generation": 1, "plan": 0})
    assert state["execution_status"] == "SUCCESS"
    # F3: SUCCESS pass-through 写 action="end"，路由 build_output。
    assert state["diagnose_decision"]["action"] == "end"
    assert _route_after_diagnose(state) == "build_output"


def test_replan_does_not_reset_sql_generation_counter():
    """F1 回归钉：`_plan` 入口必须保留 `_diagnose` 写入的 retry_counters，
    不能把 sql_generation 清零；否则单次分析最坏 4 次 SQL retry，与
    plan §D3 MAX_SQL_REPAIR_RETRIES=2 契约冲突。
    """
    from app.agent.sql_graph import _plan

    state: dict = {
        "schema_context": None,
        "user_query": "test",
        "query_plan": None,
        "generated_sql": "SELECT 1",
        "validation_result": {},
        "sql_result": "",
        "execution_status": "",
        "retry_counters": {"plan": 1, "sql_generation": 2},
        "trace_id": "test-trace-no-reset",
        "diagnose_decision": {"action": "replan", "error_kind": "object", "reason": "object"},
    }
    out = _plan(state)
    counters = out["retry_counters"]
    assert counters["plan"] == 1, f"plan retried counter wiped: {counters}"
    assert counters["sql_generation"] == 2, f"sql_generation wiped by _plan: {counters}"


def test_validation_failure_retries_sql():
    state = _eval_and_diagnose(None, {"sql_generation": 0, "plan": 0}, validation_result={"valid": False, "error": "syntax error"})
    assert state["execution_status"] == "SQL_SYNTAX_ERROR"
    assert state["diagnose_decision"]["action"] == "retry_sql"


def test_connection_failure_no_retry():
    state = _eval_and_diagnose({"error": "connection refused", "error_kind": "connection"}, {"sql_generation": 0, "plan": 0})
    assert state["execution_status"] == "FAILED"
    assert state["diagnose_decision"]["error_kind"] == "connection"


def test_evaluate_prioritizes_validation_over_stale_sql_result():
    """R1: validate 失败时 evaluate 必须走 VALIDATION_FAILED 路径，
    即使 state.sql_result 还残留上一轮 execution data。

    修复前用 `if not raw:` 间接推断 validation failure；上一轮 timeout
    留下的 sql_result 会污染本轮 evaluate，导致 DiagnosePolicy 把
    timeout 错误地走 retry_sql。
    """
    state = {
        "sql_result": json.dumps({"error": "canceling statement due to statement timeout", "error_kind": "timeout"}),
        "validation_result": {"valid": False, "error": "syntax error at or near"},
        "execution_status": "",
        "trace_id": "test-trace-stale",
    }
    out = _evaluate(state)
    assert out["execution_status"] == "SQL_SYNTAX_ERROR"
    eval_r = out["evaluate_result"]
    assert eval_r["status"] == "VALIDATION_FAILED"
    # 关键：kind 必须是 syntax（来自 validation_result），不是 timeout（来自 stale raw）
    assert eval_r["kind"] == "syntax"
    # 后续 _diagnose 看到 validation_failed + kind=syntax，走 retry_sql
    state.update(out)
    diag = _diagnose(state)
    assert diag["diagnose_decision"]["action"] == "retry_sql"


def test_full_retry_lifecycle_respects_budget(monkeypatch):
    """R-budget: 完整 lifecycle `build_sql_graph().invoke()` 真跑，
    验证 SQL repair <= 2 + plan <= 1 + 最终 clarify。
    之前 _eval_and_diagnose 只测局部函数，没测真实 LangGraph wiring。
    """
    from app.agent import sql_graph as sql_graph_module
    from app.agent.sql_graph import build_sql_graph

    monkeypatch.setenv("MAX_SQL_REPAIR_RETRIES", "2")
    monkeypatch.setenv("MAX_PLAN_RETRIES", "1")

    def fake_call_llm(*args, **kwargs):
        # plan vs generate_sql 用 prompt 关键词区分
        prompt = ""
        if args:
            if isinstance(args[0], str):
                prompt = args[0]
            elif isinstance(args[0], list) and args[0]:
                prompt = str(args[0][0].get("content", "")) if isinstance(args[0][0], dict) else ""
        if "SQL规划器" in prompt:
            return json.dumps({
                "target_metric": "x",
                "dimensions": ["a"],
                "filters": [],
                "aggregation": "sum",
                "time_range": None,
                "clarify_decision": {
                    "action": "run_direct",
                    "missing_dimensions": [],
                    "predicted_table": "fact_sales",
                    "confidence": 0.9,
                    "reasoning": "ok",
                },
            })
        # generate_sql: 永远 invalid SQL，让 validate 一直失败
        return "INVALID SQL FOR TESTING"

    # patch sql_graph 模块的本地引用（模块级 import 已绑定，否则 patch 源模块无效）
    monkeypatch.setattr(sql_graph_module, "call_llm", fake_call_llm)

    def fake_validate_sql(sql):
        return json.dumps({"valid": False, "error": "syntax error"})

    monkeypatch.setattr(sql_graph_module, "validate_sql", fake_validate_sql)

    # extract_sql 默认会把无效 SQL 当作空字符串；validate_sql 已 mock 为失败，
    # 整条 retry chain 应该走到 clarify。
    graph = build_sql_graph()
    initial_state = {
        "schema_context": None,
        "user_query": "test",
        "query_plan": None,
        "generated_sql": "",
        "validation_result": {},
        "sql_result": "",
        "execution_status": "",
        "error": None,
        "retry_counters": {"plan": 0, "sql_generation": 0},
        "trace_id": "test-budget-lifecycle",
    }
    result = graph.invoke(initial_state)
    counters = result.get("retry_counters") or {}
    # 关键断言：budget 没被打破。
    # 预算语义：MAX_SQL_REPAIR_RETRIES=2 表示允许 2 次 retry_sql；
    # sql_generation 累加 = 1（首次）+ 2（retry）= 3。
    # DiagnosePolicy 用 `sql_retries < max_sql` 判断，等价 sql_generation <= max_sql + 1。
    assert counters.get("sql_generation", 0) <= 3, f"sql_generation over budget: {counters}"
    assert counters.get("plan", 0) <= 1, f"plan over budget: {counters}"
    # budget 耗尽 → clarify → END
    assert result.get("execution_status") == "NEED_CLARIFICATION"


def test_route_after_diagnose_prefers_action_over_execution_status():
    """F2 unit test：`_route_after_diagnose()` 函数本身按 action 路由，
    execution_status 仅作兼容兜底。属于函数级契约测试，不走 compiled graph。
    """
    # SUCCESS path
    s1 = {"execution_status": "SUCCESS", "diagnose_decision": {"action": "end"}}
    assert _route_after_diagnose(s1) == "build_output"
    # retry_sql
    s2 = {"execution_status": "SQL_SYNTAX_ERROR", "diagnose_decision": {"action": "retry_sql"}}
    assert _route_after_diagnose(s2) == "generate_sql"
    # replan
    s3 = {"execution_status": "SCHEMA_ERROR", "diagnose_decision": {"action": "replan"}}
    assert _route_after_diagnose(s3) == "plan"
    # fail
    s4 = {"execution_status": "FAILED", "diagnose_decision": {"action": "fail"}}
    assert _route_after_diagnose(s4) == "__end__"
    # clarify
    s5 = {"execution_status": "NEED_CLARIFICATION", "diagnose_decision": {"action": "clarify"}}
    assert _route_after_diagnose(s5) == "__end__"
    # F2 兼容：diagnose_decision 缺失时退化读 execution_status
    s6 = {"execution_status": "SUCCESS"}
    assert _route_after_diagnose(s6) == "build_output"
    s7 = {"execution_status": "SCHEMA_ERROR"}
    assert _route_after_diagnose(s7) == "plan"


def test_compiled_graph_routes_by_diagnose_action(monkeypatch):
    """R-graph-2：真 compiled graph `build_sql_graph().invoke()` 验证 4 种
    DiagnoseDecision.action 走 LangGraph conditional_edges 真的到正确 node。
    上一版 `test_compiled_graph_routes_by_diagnose_decision` 只调函数本身，
    名字与覆盖不符——本测试用 budget=0 + mock execute_sql 让单轮直接到
    terminal，覆盖 SUCCESS / FAIL_TIMEOUT / CLARIFY_BUDGET 三条路径。
    """
    from app.agent import sql_graph as sql_graph_module
    from app.agent.sql_graph import build_sql_graph

    monkeypatch.setenv("MAX_SQL_REPAIR_RETRIES", "0")
    monkeypatch.setenv("MAX_PLAN_RETRIES", "0")

    def fake_call_llm(*args, **kwargs):
        prompt = ""
        if args:
            if isinstance(args[0], str):
                prompt = args[0]
            elif isinstance(args[0], list) and args[0]:
                prompt = str(args[0][0].get("content", "")) if isinstance(args[0][0], dict) else ""
        if "SQL规划器" in prompt:
            return json.dumps({
                "target_metric": "x",
                "dimensions": ["a"],
                "filters": [],
                "aggregation": "sum",
                "time_range": None,
                "clarify_decision": {
                    "action": "run_direct",
                    "missing_dimensions": [],
                    "predicted_table": "fact_sales",
                    "confidence": 0.9,
                    "reasoning": "ok",
                },
            })
        return "SELECT 1"

    def fake_validate_sql(sql):
        return json.dumps({"valid": True, "error": ""})

    def make_fake_execute(result_payload: dict):
        def fake_execute_sql(sql):
            return json.dumps(result_payload)
        return fake_execute_sql

    monkeypatch.setattr(sql_graph_module, "call_llm", fake_call_llm)
    monkeypatch.setattr(sql_graph_module, "validate_sql", fake_validate_sql)

    initial_state = {
        "schema_context": None,
        "user_query": "test",
        "query_plan": None,
        "generated_sql": "",
        "validation_result": {},
        "sql_result": "",
        "execution_status": "",
        "error": None,
        "retry_counters": {"plan": 0, "sql_generation": 0},
        "trace_id": "test-graph-route",
    }

    graph = build_sql_graph()

    # Case A: SUCCESS rows → diagnose action="end" → build_output → END
    monkeypatch.setattr(
        sql_graph_module, "execute_sql",
        make_fake_execute({"columns": [], "rows": [{"a": 1}]}),
    )
    r_a = graph.invoke({**initial_state, "trace_id": "test-graph-success"})
    assert r_a["diagnose_decision"]["action"] == "end", f"expected end, got {r_a.get('diagnose_decision')}"
    assert r_a["execution_status"] == "SUCCESS", f"expected SUCCESS, got {r_a.get('execution_status')}"

    # Case B: TIMEOUT error → diagnose action="fail" → __end__
    monkeypatch.setattr(
        sql_graph_module, "execute_sql",
        make_fake_execute({"error": "canceling statement due to statement timeout", "error_kind": "timeout"}),
    )
    r_b = graph.invoke({**initial_state, "trace_id": "test-graph-timeout"})
    assert r_b["diagnose_decision"]["action"] == "fail", f"expected fail, got {r_b.get('diagnose_decision')}"
    assert r_b["execution_status"] == "FAILED", f"expected FAILED, got {r_b.get('execution_status')}"

    # Case C: budget=0 + syntax error → diagnose action="clarify" → __end__
    monkeypatch.setattr(
        sql_graph_module, "execute_sql",
        make_fake_execute({"error": "syntax error", "error_kind": "syntax"}),
    )
    r_c = graph.invoke({**initial_state, "trace_id": "test-graph-clarify"})
    assert r_c["diagnose_decision"]["action"] == "clarify", f"expected clarify, got {r_c.get('diagnose_decision')}"
    assert r_c["execution_status"] == "NEED_CLARIFICATION", f"expected NEED_CLARIFICATION, got {r_c.get('execution_status')}"
