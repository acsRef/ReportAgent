"""SQL regeneration must feed back the previous failure.

Without the failed SQL + validation error in the regeneration prompt,
the 3-retry loop is blind: the LLM receives the same prompt and tends
to reproduce the same invalid SQL (e.g. a hallucinated column), burning
all retries and failing the whole confirm run.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.graphs


def _state_with_failed_sql() -> dict:
    return {
        "user_query": "2024年各区域销售额",
        "query_plan": None,
        "schema_context": None,
        "generated_sql": 'SELECT d.date AS "日期" FROM dim_date d',
        "validation_result": {"valid": False, "error": "column d.date does not exist"},
        "sql_result": "",
        "execution_status": "SQL_SYNTAX_ERROR",
        "error": None,
        "retry_counters": {"sql_generation": 1},
        "trace_id": "",
        "chosen_tool": None,
    }


def test_regeneration_includes_previous_sql_and_error(monkeypatch) -> None:
    import app.agent.sql_graph as sg

    captured: list[str] = []

    def fake_call_llm(prompt, *a, **k):
        captured.append(prompt[0]["content"] if isinstance(prompt, list) else prompt)
        return "SELECT 1"

    monkeypatch.setattr(sg, "call_llm", fake_call_llm)
    monkeypatch.setattr(sg, "extract_sql", lambda s: s)

    sg._generate_sql(_state_with_failed_sql())

    assert len(captured) == 1
    assert "d.date" in captured[0], "previous failed SQL must be fed back to the LLM"
    assert "column d.date does not exist" in captured[0], (
        "validation error must be fed back to the LLM"
    )


def test_first_generation_has_no_feedback_block(monkeypatch) -> None:
    """A clean first attempt must not carry a bogus failure block."""
    import app.agent.sql_graph as sg

    captured: list[str] = []

    def fake_call_llm(prompt, *a, **k):
        captured.append(prompt[0]["content"] if isinstance(prompt, list) else prompt)
        return "SELECT 1"

    monkeypatch.setattr(sg, "call_llm", fake_call_llm)
    monkeypatch.setattr(sg, "extract_sql", lambda s: s)

    state = _state_with_failed_sql()
    state["generated_sql"] = ""
    state["validation_result"] = {}
    state["retry_counters"] = {"sql_generation": 0}
    sg._generate_sql(state)

    assert "上一次生成失败" not in captured[0]
