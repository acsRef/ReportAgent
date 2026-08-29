from __future__ import annotations

import pytest

from app.agent.sql_graph import DiagnosePolicy

pytestmark = pytest.mark.contracts


def test_syntax_retry_sql_when_budget_available():
    d = DiagnosePolicy.decide(error_kind="syntax", retry_counters={"sql_generation": 0, "plan": 0})
    assert d.action == "retry_sql"
    assert d.recoverable is True
    assert d.retry_target == "generate_sql"
    assert d.error_kind == "syntax"


def test_syntax_replan_when_sql_exhausted():
    d = DiagnosePolicy.decide(error_kind="syntax", retry_counters={"sql_generation": 2, "plan": 0})
    assert d.action == "replan"
    assert d.retry_target == "plan"


def test_syntax_clarify_when_all_exhausted():
    d = DiagnosePolicy.decide(error_kind="syntax", retry_counters={"sql_generation": 2, "plan": 1})
    assert d.action == "clarify"
    assert d.retry_target == "end"
    assert d.recoverable is False


def test_object_retry_sql_first():
    d = DiagnosePolicy.decide(error_kind="object", retry_counters={"sql_generation": 0, "plan": 0})
    assert d.action == "retry_sql"
    assert d.error_kind == "object"


def test_object_replan_after_sql_budget():
    d = DiagnosePolicy.decide(error_kind="object", retry_counters={"sql_generation": 2, "plan": 0})
    assert d.action == "replan"
    assert d.retry_target == "plan"


def test_object_clarify_when_both_exhausted():
    d = DiagnosePolicy.decide(error_kind="object", retry_counters={"sql_generation": 2, "plan": 1})
    assert d.action == "clarify"


def test_timeout_always_fail():
    for kind in ("timeout", "connection", "permission"):
        d = DiagnosePolicy.decide(error_kind=kind, retry_counters={"sql_generation": 0, "plan": 0})
        assert d.action == "fail"
        assert d.recoverable is False
        assert d.retry_target == "end"
        assert d.error_kind == kind


def test_timeout_fail_even_when_retries_available():
    d = DiagnosePolicy.decide(error_kind="timeout", retry_counters={"sql_generation": 0, "plan": 0})
    assert d.action == "fail"
    d2 = DiagnosePolicy.decide(error_kind="timeout", retry_counters={"sql_generation": 5, "plan": 5})
    assert d2.action == "fail"


def test_other_retry_sql_then_replan_then_clarify():
    assert DiagnosePolicy.decide(error_kind="other", retry_counters={"sql_generation": 0, "plan": 0}).action == "retry_sql"
    assert DiagnosePolicy.decide(error_kind="other", retry_counters={"sql_generation": 2, "plan": 0}).action == "replan"
    assert DiagnosePolicy.decide(error_kind="other", retry_counters={"sql_generation": 2, "plan": 1}).action == "clarify"


def test_validation_failed_path_uses_same_policy():
    d = DiagnosePolicy.decide(error_kind="syntax", retry_counters={"sql_generation": 0, "plan": 0}, validation_failed=True)
    assert d.action == "retry_sql"
    d2 = DiagnosePolicy.decide(error_kind="syntax", retry_counters={"sql_generation": 2, "plan": 1}, validation_failed=True)
    assert d2.action == "clarify"


def test_raw_empty_path():
    d = DiagnosePolicy.decide(error_kind="other", retry_counters={"sql_generation": 0, "plan": 0}, raw_empty=True)
    assert d.action == "retry_sql"
    d2 = DiagnosePolicy.decide(error_kind="other", retry_counters={"sql_generation": 2, "plan": 1}, raw_empty=True)
    assert d2.action == "clarify"


def test_unknown_kind_defaults_to_other():
    d = DiagnosePolicy.decide(error_kind="unknown_kind", retry_counters={"sql_generation": 0, "plan": 0})
    assert d.error_kind == "other"
    assert d.action == "retry_sql"
