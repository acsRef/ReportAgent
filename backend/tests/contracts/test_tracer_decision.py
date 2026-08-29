from __future__ import annotations

import pytest

from app.infra.trace.sdk import Tracer, get_tracer, _local

pytestmark = pytest.mark.contracts


def test_add_decision_records_locally():
    tracer = Tracer(trace_id="t-dec-1")
    tracer.add_decision(name="sql_diagnose", action="retry_sql", reason="syntax", error_kind="syntax", retry_counters={"sql_generation": 0})
    assert len(tracer._decisions) == 1
    assert tracer._decisions[0]["name"] == "sql_diagnose"
    assert tracer._decisions[0]["action"] == "retry_sql"
    assert tracer._decisions[0]["error_kind"] == "syntax"


def test_add_decision_inside_span_has_span_id():
    tracer = Tracer(trace_id="t-dec-2")
    with tracer.span("outer"):
        tracer.add_decision(name="sql_diagnose", action="fail", reason="timeout", error_kind="timeout")
        assert tracer._decisions[0]["span_id"] != ""
        span_id = tracer._decisions[0]["span_id"]
        assert isinstance(span_id, str) and len(span_id) > 0


def test_add_decision_outside_span_has_empty_span_id():
    tracer = Tracer(trace_id="t-dec-3")
    tracer.add_decision(name="sql_diagnose", action="clarify", reason="budget exhausted", error_kind="object")
    assert tracer._decisions[0]["span_id"] == ""


def test_diagnose_node_writes_decision_via_current_tracer(monkeypatch):
    from app.agent.sql_graph import _diagnose
    import json

    _local.clear()
    state = {
        "trace_id": "trace-dec-node",
        "execution_status": "FAILED",
        "sql_result": json.dumps({"error": 'column "x" does not exist', "error_kind": "object"}),
        "validation_result": {"valid": True},
        "retry_counters": {"sql_generation": 0, "plan": 0},
        "evaluate_result": {"kind": "object", "status": "FAILED"},
    }
    tracer = get_tracer("trace-dec-node")
    result = _diagnose(state)
    assert "diagnose_decision" in result
    assert len(tracer._decisions) == 1
    assert tracer._decisions[0]["error_kind"] == "object"
    _local.clear()


def test_diagnose_success_still_records_decision(monkeypatch):
    # F3: SUCCESS pass-through 写 action="end"（不再是 "fail"），避免 P14
    # Evaluation 按 action 切片统计时被污染。
    from app.agent.sql_graph import _diagnose
    _local.clear()
    state = {
        "trace_id": "trace-dec-success",
        "execution_status": "SUCCESS",
        "sql_result": '{"columns":[],"rows":[{"a":1}]}',
        "retry_counters": {"sql_generation": 1, "plan": 0},
    }
    tracer = get_tracer("trace-dec-success")
    result = _diagnose(state)
    assert result["execution_status"] == "SUCCESS"
    assert result["diagnose_decision"]["action"] == "end"
    assert len(tracer._decisions) == 1
    _local.clear()


def test_diagnose_fail_records_action_fail_not_end(monkeypatch):
    # F3: 反向断言——真实 fail 决策仍是 action="fail"，与 SUCCESS pass-through
    # 的 action="end" 严格区分；P14 Evaluation 切片才能信。
    import json
    from app.agent.sql_graph import _diagnose
    _local.clear()
    state = {
        "trace_id": "trace-dec-fail",
        "execution_status": "FAILED",
        "sql_result": json.dumps({"error": "canceling statement due to statement timeout", "error_kind": "timeout"}),
        "validation_result": {"valid": True},
        "retry_counters": {"sql_generation": 0, "plan": 0},
        "evaluate_result": {"kind": "timeout", "status": "FAILED"},
    }
    tracer = get_tracer("trace-dec-fail")
    result = _diagnose(state)
    assert result["diagnose_decision"]["action"] == "fail"
    assert tracer._decisions[0]["action"] == "fail"
    _local.clear()
