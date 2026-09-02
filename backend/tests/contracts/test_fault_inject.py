"""P15 e2e T4：fault injection seam 契约。

钉：双 gate fail-closed、kind 白名单、once/persistent 与 counter=0/1/2 判定（防 off-by-one）、
以及 _evaluate 顶部按 override 注入的两种形态（object_not_found→VALIDATION_FAILED /
permission→execution FAILED）。
"""
from __future__ import annotations

import pytest

from app.reliability import fault_inject

pytestmark = pytest.mark.contracts


def test_gate_fail_closed(monkeypatch):
    """REPORTAGENT_E2E 未设 → header 再合法也返回 None（生产零行为变化）。"""
    monkeypatch.delenv("REPORTAGENT_E2E", raising=False)
    assert fault_inject.parse_header("kind=permission;mode=persistent") is None
    assert fault_inject.kind_override(
        {"fault_override": {"kind": "permission", "mode": "persistent"}}
    ) is None


def test_parse_header_allows_valid_kind(monkeypatch):
    monkeypatch.setenv("REPORTAGENT_E2E", "1")
    assert fault_inject.parse_header("kind=object_not_found;mode=once") == {
        "kind": "object_not_found", "mode": "once",
    }
    assert fault_inject.parse_header("kind=permission;mode=persistent") == {
        "kind": "permission", "mode": "persistent",
    }


def test_parse_header_allowlist_and_format(monkeypatch):
    monkeypatch.setenv("REPORTAGENT_E2E", "1")
    assert fault_inject.parse_header("kind=timeout;mode=persistent") is None, "白名单外 kind 拒绝"
    assert fault_inject.parse_header("kind=object_not_found") is None, "缺 mode 拒绝"
    assert fault_inject.parse_header("garbage") is None


def test_once_counter_matrix(monkeypatch):
    """once：counter==1 才注入；0（未 generate）/2（修后重试）都不注入 → 无 off-by-one。"""
    monkeypatch.setenv("REPORTAGENT_E2E", "1")
    spec = {"kind": "object_not_found", "mode": "once"}
    assert fault_inject.kind_override({"fault_override": spec, "retry_counters": {"sql_generation": 0}}) is None
    assert fault_inject.kind_override({"fault_override": spec, "retry_counters": {"sql_generation": 1}}) == "object_not_found"
    assert fault_inject.kind_override({"fault_override": spec, "retry_counters": {"sql_generation": 2}}) is None
    # persistent 恒注入
    assert fault_inject.kind_override(
        {"fault_override": {"kind": "permission", "mode": "persistent"},
         "retry_counters": {"sql_generation": 5}}
    ) == "permission"


def test_evaluate_injects_object_not_found_as_validation(monkeypatch):
    """object_not_found + once(counter=1) → EvaluateResult VALIDATION_FAILED kind=object_not_found。"""
    from app.agent.sql_graph import _evaluate

    monkeypatch.setenv("REPORTAGENT_E2E", "1")
    out = _evaluate({
        "fault_override": {"kind": "object_not_found", "mode": "once"},
        "retry_counters": {"sql_generation": 1},
        "validation_result": {"valid": True}, "sql_result": "",
    })
    ev = out["evaluate_result"]
    assert ev["status"] == "VALIDATION_FAILED"
    assert ev["kind"] == "object_not_found"


def test_evaluate_injects_permission_as_execution_failed(monkeypatch):
    """permission + persistent → EvaluateResult FAILED kind=permission（→ DiagnosePolicy fail-fast）。"""
    from app.agent.sql_graph import _evaluate

    monkeypatch.setenv("REPORTAGENT_E2E", "1")
    out = _evaluate({
        "fault_override": {"kind": "permission", "mode": "persistent"},
        "retry_counters": {"sql_generation": 3},
        "validation_result": {"valid": True}, "sql_result": "",
    })
    ev = out["evaluate_result"]
    assert ev["status"] == "FAILED"
    assert ev["kind"] == "permission"


def test_evaluate_no_override_when_once_counter_advanced(monkeypatch):
    """once 已消费（counter=2）→ 不再注入，走真实路径（此处空 sql_result → kind other）。"""
    from app.agent.sql_graph import _evaluate

    monkeypatch.setenv("REPORTAGENT_E2E", "1")
    out = _evaluate({
        "fault_override": {"kind": "object_not_found", "mode": "once"},
        "retry_counters": {"sql_generation": 2},
        "validation_result": {}, "sql_result": "",
    })
    assert out["evaluate_result"]["kind"] != "object_not_found"
