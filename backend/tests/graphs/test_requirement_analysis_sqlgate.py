"""SQL-gate enforcement test for the requirement-analysis graph.

This test asserts the critical property of the requirement-analysis flow:
the schema-only graph must NEVER call validate_sql, execute_sql, or any
report tool. The check is structural — we monkeypatch the dangerous
functions to raise if invoked, then drive the graph end-to-end.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

pytestmark = pytest.mark.graphs

from app.models.requirement import RequirementCard, RequirementAssumption


def _tripwire(name: str):
    """Return a function that raises if called."""
    def _explode(*args, **kwargs):
        raise AssertionError(
            f"SQL/Report gate violated: {name}() was called from the "
            f"requirement-analysis graph (must be unreachable)"
        )
    return _explode


@pytest.fixture
def sql_gate(monkeypatch):
    """Patch the SQL/Report tool entry points to trip if called."""
    import app.tools.sql_tools as sql_tools_mod
    import app.tools.report_tools as report_tools_mod

    monkeypatch.setattr(sql_tools_mod, "validate_sql", _tripwire("validate_sql"))
    monkeypatch.setattr(sql_tools_mod, "execute_sql", _tripwire("execute_sql"))
    monkeypatch.setattr(sql_tools_mod, "chart_advisor", _tripwire("chart_advisor"))
    monkeypatch.setattr(sql_tools_mod, "insight_analyst", _tripwire("insight_analyst"))
    monkeypatch.setattr(
        report_tools_mod, "trend_analysis", _tripwire("trend_analysis"),
    )
    monkeypatch.setattr(
        report_tools_mod, "group_compare", _tripwire("group_compare"),
    )
    monkeypatch.setattr(
        report_tools_mod, "detect_anomaly", _tripwire("detect_anomaly"),
    )


def test_requirement_analysis_graph_does_not_call_sql_or_report_tools(
    monkeypatch, sql_gate,
) -> None:
    """Drive the requirement-analysis graph end-to-end and assert the SQL
    gate holds (no tripwires fire). LLM is stubbed; data_agent and
    persist_draft are stubbed to avoid network/DB dependencies (this
    test is purely about the structural gate).
    """
    # Stub the LLM so we don't hit the real API.
    import app.agent.requirement_parser as parser_mod
    monkeypatch.setattr(parser_mod, "call_llm", lambda *a, **k: '{"summary":"x","target_metrics":[],"time_range":null,"scope":[],"dimensions":[],"analysis_methods":[],"confidence":0.9,"missing_fields":[],"assumptions":[]}')

    # Stub the data_agent's MCP path so no real network call happens.
    from app.agent import data_graph as data_graph_mod
    from app.models.contracts import SchemaContext, TableSchema
    fake_schema = SchemaContext(
        tables=[TableSchema(name="fact_sales", description="sales")],
        confidence=1.0,
    )

    async def fake_data_ainvoke(_state: dict) -> dict:
        return {"schema_context": fake_schema}

    monkeypatch.setattr(data_graph_mod, "build_data_graph", lambda: type("G", (), {"ainvoke": staticmethod(fake_data_ainvoke)})())

    # Stub persist_draft so we don't need a live PG pool for this structural
    # test. The point is "no SQL/Report tools are reachable", not "writes
    # work end-to-end".
    import app.agent.requirement_analysis_graph as rag_mod

    async def fake_persist(state: dict) -> dict:
        return {"draft_id": 0, "execution_status": "SUCCESS"}

    monkeypatch.setattr(rag_mod, "_persist_draft", fake_persist)

    # Now run the graph
    from app.agent.requirement_analysis_graph import build_requirement_analysis_graph
    graph = build_requirement_analysis_graph()

    config = {"configurable": {"thread_id": f"test-{uuid.uuid4()}"}}
    state = {
        "user_query": "2024 华东销售额趋势",
        "user_id": 1,
        "session_id": f"test-{uuid.uuid4()}",
        "trace_id": "trace-test",
        "schema_context": None,
        "requirement_card": None,
        "draft_id": None,
        "security_score": 0,
        "security_level": "LOW",
        "security_warning": "",
        "error": None,
        "execution_status": "RUNNING",
    }
    result = asyncio.run(graph.ainvoke(state, config))
    assert result["execution_status"] in ("SUCCESS", "PERSIST_FAILED")
    # If we got here, no tripwire fired.
