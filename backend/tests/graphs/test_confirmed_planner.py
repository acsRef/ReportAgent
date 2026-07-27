"""Tests for the confirmed-execution planner signal.

Verifies that _format_confirmed_requirement synthesises a non-empty
authoritative query from a confirmed RequirementCard, and that the
synthesis logic correctly handles empty/missing fields.
"""
from __future__ import annotations

from datetime import datetime

import pytest

pytestmark = pytest.mark.graphs

from app.models.requirement import RequirementAssumption, RequirementCard


def _complete_card(**overrides) -> RequirementCard:
    base = {
        "id": "draft-1",
        "status": "complete",
        "summary": "Test",
        "missing_fields": [],
        "assumptions": [RequirementAssumption(key="a1", text="默认华东", accepted=True)],
        "confirmed_at": None,
    }
    base.update(overrides)
    return RequirementCard(**base)


def test_format_confirmed_requirement_all_fields() -> None:
    from app.agent.confirmed_execution_graph import _format_confirmed_requirement
    card = _complete_card(
        time_range="今年",
        scope=["华东"],
        target_metrics=["销售额"],
        dimensions=["区域", "时间"],
        analysis_methods=["同比", "环比"],
    )
    result = _format_confirmed_requirement(card)
    assert result is not None
    assert "time_range = 今年" in result
    assert "scope = [华东]" in result
    assert "metrics = [销售额]" in result
    assert "dimensions = [区域, 时间]" in result
    assert "analysis_methods = [同比, 环比]" in result
    assert "默认华东" in result


def test_format_confirmed_requirement_minimal() -> None:
    from app.agent.confirmed_execution_graph import _format_confirmed_requirement
    result = _format_confirmed_requirement(_complete_card(time_range="本月"))
    assert result is not None
    assert "time_range = 本月" in result


def test_format_confirmed_requirement_none_card() -> None:
    from app.agent.confirmed_execution_graph import _format_confirmed_requirement
    assert _format_confirmed_requirement(None) is None


def test_format_confirmed_requirement_only_unaccepted_assumptions() -> None:
    """No structured fields + no accepted assumptions = returns None."""
    from app.agent.confirmed_execution_graph import _format_confirmed_requirement
    card = RequirementCard(
        id="draft-1", status="complete", summary="Test",
        missing_fields=[],
        assumptions=[RequirementAssumption(key="a1", text="默认", accepted=False)],
        confirmed_at=None,
    )
    assert _format_confirmed_requirement(card) is None


def test_format_confirmed_requirement_includes_assumptions() -> None:
    from app.agent.confirmed_execution_graph import _format_confirmed_requirement
    card = _complete_card(
        time_range="今年",
        assumptions=[
            RequirementAssumption(key="a1", text="默认华东", accepted=True),
            RequirementAssumption(key="a2", text="排除港澳台", accepted=True),
            RequirementAssumption(key="a3", text="考虑节假日影响", accepted=None),
        ],
    )
    result = _format_confirmed_requirement(card)
    assert result is not None
    assert "默认华东" in result
    assert "排除港澳台" in result
    assert "考虑节假日影响" not in result  # not accepted


def test_planner_synthesises_query_when_user_query_empty(monkeypatch) -> None:
    """_confirmed_sql_agent should build a non-empty user_query
    when the incoming state has user_query="" and a confirmed card."""
    import app.agent.confirmed_execution_graph as ceg

    captured: dict = {}

    async def fake_ainvoke(state, *a, **k):
        captured["user_query"] = state.get("user_query", "")
        captured["confirmed_requirement"] = state.get("confirmed_requirement")
        return {"query_result": None, "execution_status": "SUCCESS"}

    class FakeGraph:
        async def ainvoke(self, state, *a, **k):
            return await fake_ainvoke(state, *a, **k)

    monkeypatch.setattr(
        "app.agent.confirmed_execution_graph.build_sql_graph",
        lambda: FakeGraph(),
    )

    import asyncio

    card = _complete_card(
        time_range="今年",
        scope=["华东"],
        target_metrics=["销售额"],
    )
    coro = ceg._confirmed_sql_agent({
        "user_query": "",
        "user_id": 1,
        "session_id": "test-sid",
        "trace_id": "test-trace",
        "requirement_card": card,
        "schema_context": None,
        "query_result": None,
        "report_payload": None,
        "execution_status": "",
        "error": None,
    })
    asyncio.run(coro)

    assert "user_query" in captured
    assert captured["user_query"] != ""
    assert "今年" in captured["user_query"]
    assert "华东" in captured["user_query"]
    assert "销售额" in captured["user_query"]
    assert captured["confirmed_requirement"] is not None
