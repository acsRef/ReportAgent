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
