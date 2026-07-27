"""Tests for _confirmed_report_agent.

Verifies that answer.table is built from query_result, and that
execution_status reflects actual data presence.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.graphs


def test_report_agent_with_query_result() -> None:
    """有 query_result 时 → answer.table 含 columns/rows, SUCCESS."""
    from app.agent.confirmed_execution_graph import _confirmed_report_agent
    import asyncio

    def _stub_report_graph():
        class FakeGraph:
            async def ainvoke(self, state, *a, **k):
                return {"chart_config": None, "insight_text": "销售额增长12%"}
        return FakeGraph()

    import app.agent.confirmed_execution_graph as ceg
    original = ceg.build_report_graph
    ceg.build_report_graph = _stub_report_graph
    try:
        coro = _confirmed_report_agent({
            "user_query": "销售趋势",
            "user_id": 1,
            "session_id": "sid",
            "trace_id": "t",
            "query_result": {
                "sql": "SELECT ...",
                "columns": [{"name": "region", "type": "text"}],
                "rows": [{"region": "华东", "total": 100}],
                "row_count": 1,
                "status": "SUCCESS",
            },
            "report_payload": None,
            "execution_status": "",
            "error": None,
        })
        result = asyncio.run(coro)
    finally:
        ceg.build_report_graph = original

    payload = result.get("report_payload", {})
    answer = payload.get("answer", {})
    assert answer.get("table") is not None
    assert "columns" in answer["table"]
    assert "rows" in answer["table"]
    assert answer["table"]["rows"] == [{"region": "华东", "total": 100}]
    assert result["execution_status"] == "SUCCESS"


def test_report_agent_empty_result() -> None:
    """空 query_result → execution_status=FAILED, table=None."""
    from app.agent.confirmed_execution_graph import _confirmed_report_agent
    import asyncio

    def _stub_report_graph():
        class FakeGraph:
            async def ainvoke(self, state, *a, **k):
                return {"chart_config": None, "insight_text": ""}
        return FakeGraph()

    import app.agent.confirmed_execution_graph as ceg
    original = ceg.build_report_graph
    ceg.build_report_graph = _stub_report_graph
    try:
        coro = _confirmed_report_agent({
            "user_query": "销售趋势",
            "user_id": 1,
            "session_id": "sid",
            "trace_id": "t",
            "query_result": {
                "sql": "SELECT ...",
                "columns": [{"name": "region", "type": "text"}],
                "rows": [],
                "row_count": 0,
                "status": "SUCCESS",
            },
            "report_payload": None,
            "execution_status": "",
            "error": None,
        })
        result = asyncio.run(coro)
    finally:
        ceg.build_report_graph = original

    payload = result.get("report_payload", {})
    answer = payload.get("answer", {})
    assert answer.get("table") is None
    assert result["execution_status"] == "FAILED"


def test_report_agent_no_result() -> None:
    """query_result 完全不存在 → execution_status=FAILED."""
    from app.agent.confirmed_execution_graph import _confirmed_report_agent
    import asyncio

    def _stub_report_graph():
        class FakeGraph:
            async def ainvoke(self, state, *a, **k):
                return {"chart_config": None, "insight_text": ""}
        return FakeGraph()

    import app.agent.confirmed_execution_graph as ceg
    original = ceg.build_report_graph
    ceg.build_report_graph = _stub_report_graph
    try:
        coro = _confirmed_report_agent({
            "user_query": "销售趋势",
            "user_id": 1,
            "session_id": "sid",
            "trace_id": "t",
            "query_result": None,
            "report_payload": None,
            "execution_status": "",
            "error": None,
        })
        result = asyncio.run(coro)
    finally:
        ceg.build_report_graph = original

    assert result["execution_status"] == "FAILED"
