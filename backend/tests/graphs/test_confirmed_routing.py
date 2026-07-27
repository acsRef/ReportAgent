"""Graph-level routing tests for the confirmed execution graph.

A FAILED report (no rows) must skip `persist_report` so that:
1. no empty v1 report row is written to `agent.report_version`, and
2. the final `execution_status` stays "FAILED" so main.py's /confirm
   SSE handler emits an `error` event instead of a fake `report`.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.graphs

_BASE_STATE = {
    "user_id": 1,
    "user_query": "",
    "session_id": "routing-test",
    "trace_id": "t",
    "requirement_card": None,
    "base_report_version": None,
    "adjustment_text": None,
    "schema_context": None,
    "query_result": None,
    "report_payload": None,
    "execution_status": "",
    "error": None,
}


class _FakeReportGraph:
    async def ainvoke(self, state, *a, **k):
        return {"chart_config": None, "insight_text": ""}


def _patch_upstream(monkeypatch, ceg, query_result):
    """Stub every node upstream of report_agent; record persist calls."""
    calls: list[str] = []

    async def fake_load(state):
        return {}

    async def fake_gate(state):
        return {"execution_status": "RUNNING"}

    async def fake_data(state):
        return {"schema_context": None}

    async def fake_sql(state):
        return {"query_result": query_result, "execution_status": "FAILED"}

    async def fake_persist(state):
        calls.append("persist")
        return {
            "execution_status": "DONE",
            "report_payload": {**(state.get("report_payload") or {}), "version": 1},
        }

    monkeypatch.setattr(ceg, "_load_confirmed_requirement", fake_load)
    monkeypatch.setattr(ceg, "_sql_gate", fake_gate)
    monkeypatch.setattr(ceg, "_confirmed_data_agent", fake_data)
    monkeypatch.setattr(ceg, "_confirmed_sql_agent", fake_sql)
    monkeypatch.setattr(ceg, "_persist_report", fake_persist)
    monkeypatch.setattr(ceg, "build_report_graph", lambda: _FakeReportGraph())
    return calls


async def test_failed_report_skips_persist(monkeypatch) -> None:
    """query_result=None → report_agent FAILED → END without persist."""
    import app.agent.confirmed_execution_graph as ceg

    calls = _patch_upstream(monkeypatch, ceg, query_result=None)
    graph = ceg.build_confirmed_execution_graph()
    result = await graph.ainvoke(
        dict(_BASE_STATE), {"configurable": {"thread_id": "routing-fail"}}
    )

    assert calls == [], "persist_report must be skipped when the report FAILED"
    assert result["execution_status"] == "FAILED", (
        "execution_status must stay FAILED so /confirm emits an error event"
    )


async def test_empty_rows_report_skips_persist(monkeypatch) -> None:
    """Executed SQL but zero rows → FAILED → END without persist."""
    import app.agent.confirmed_execution_graph as ceg

    calls = _patch_upstream(
        monkeypatch,
        ceg,
        query_result={
            "sql": "SELECT 1 WHERE FALSE",
            "columns": [{"name": "x"}],
            "rows": [],
            "row_count": 0,
        },
    )
    graph = ceg.build_confirmed_execution_graph()
    result = await graph.ainvoke(
        dict(_BASE_STATE), {"configurable": {"thread_id": "routing-empty"}}
    )

    assert calls == [], "zero-row result must not persist a hollow report"
    assert result["execution_status"] == "FAILED"


async def test_success_report_persists(monkeypatch) -> None:
    """Rows present → SUCCESS → persist_report runs → DONE."""
    import app.agent.confirmed_execution_graph as ceg

    calls = _patch_upstream(
        monkeypatch,
        ceg,
        query_result={
            "sql": "SELECT region, SUM(total) FROM fact_sales GROUP BY region",
            "columns": [{"name": "region"}, {"name": "total"}],
            "rows": [{"region": "华东", "total": 100}],
            "row_count": 1,
        },
    )
    graph = ceg.build_confirmed_execution_graph()
    result = await graph.ainvoke(
        dict(_BASE_STATE), {"configurable": {"thread_id": "routing-ok"}}
    )

    assert calls == ["persist"], "a successful report must be persisted"
    assert result["execution_status"] == "DONE"
    assert result["report_payload"]["answer"]["table"]["rows"] == [
        {"region": "华东", "total": 100}
    ]
