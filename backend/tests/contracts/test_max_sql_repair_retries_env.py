from __future__ import annotations

import pytest

from app.agent import sql_graph
from app.agent.sql_graph import DiagnosePolicy

pytestmark = pytest.mark.contracts


def test_default_is_two(monkeypatch):
    monkeypatch.delenv("MAX_SQL_REPAIR_RETRIES", raising=False)
    monkeypatch.delenv("MAX_PLAN_RETRIES", raising=False)
    assert sql_graph._get_max_sql_retries() == 2
    assert sql_graph._get_max_plan_retries() == 1


def test_env_overrides_sql_retries(monkeypatch):
    monkeypatch.setenv("MAX_SQL_REPAIR_RETRIES", "5")
    assert sql_graph._get_max_sql_retries() == 5
    d = DiagnosePolicy.decide(error_kind="syntax", retry_counters={"sql_generation": 4, "plan": 0})
    assert d.action == "retry_sql"
    d2 = DiagnosePolicy.decide(error_kind="syntax", retry_counters={"sql_generation": 5, "plan": 0})
    assert d2.action == "replan"


def test_env_overrides_plan_retries(monkeypatch):
    monkeypatch.setenv("MAX_SQL_REPAIR_RETRIES", "1")
    monkeypatch.setenv("MAX_PLAN_RETRIES", "3")
    assert sql_graph._get_max_plan_retries() == 3
    d = DiagnosePolicy.decide(error_kind="syntax", retry_counters={"sql_generation": 1, "plan": 2})
    assert d.action == "replan"
    d2 = DiagnosePolicy.decide(error_kind="syntax", retry_counters={"sql_generation": 1, "plan": 3})
    assert d2.action == "clarify"


def test_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("MAX_SQL_REPAIR_RETRIES", "not-an-int")
    assert sql_graph._get_max_sql_retries() == 2


def test_monkeypatch_delenv_restores_default(monkeypatch):
    monkeypatch.setenv("MAX_SQL_REPAIR_RETRIES", "10")
    assert sql_graph._get_max_sql_retries() == 10
    monkeypatch.delenv("MAX_SQL_REPAIR_RETRIES", raising=False)
    assert sql_graph._get_max_sql_retries() == 2
