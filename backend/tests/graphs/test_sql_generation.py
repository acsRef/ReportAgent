"""Unit tests for SQL generation sanitisation.

Tests the `extract_sql` / `strip_think` helpers and the `_generate_sql`
node's handling of think-block variants (closed, unclosed, absent).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.graphs

from app.utils.text import extract_sql, strip_think


# ── strip_think ─────────────────────────────────────────────────────────

def test_strip_think_removes_closed_block() -> None:
    result = strip_think("<think>some reasoning</think>\nSELECT * FROM t")
    assert result == "SELECT * FROM t"


def test_strip_think_unclosed_block_unchanged() -> None:
    """No </think> — the tag is left in place; extract_sql handles it later."""
    text = "<think>some reasoning\nSELECT * FROM t"
    assert strip_think(text) == text


def test_strip_think_no_block_unchanged() -> None:
    text = "SELECT * FROM t"
    assert strip_think(text) == text


def test_strip_think_multiple_blocks() -> None:
    result = strip_think(
        "<think>first</think>garbage<think>second</think>\nSELECT * FROM t"
    )
    assert result == "garbage\nSELECT * FROM t"


# ── extract_sql ─────────────────────────────────────────────────────────

def test_extract_sql_closed_think() -> None:
    result = extract_sql("<think>reasoning</think>\nSELECT * FROM t")
    assert result == "SELECT * FROM t"


def test_extract_sql_unclosed_think_finds_select() -> None:
    """No </think> — strips to first SELECT."""
    result = extract_sql("<think>some reasoning\nSELECT * FROM t")
    assert result == "SELECT * FROM t"


def test_extract_sql_pure_sql() -> None:
    result = extract_sql("SELECT * FROM t")
    assert result == "SELECT * FROM t"


def test_extract_sql_truncated_no_sql() -> None:
    assert extract_sql("<think>truncated reasoning") == ""


def test_extract_sql_empty() -> None:
    assert extract_sql("") == ""


def test_extract_sql_markdown_fence() -> None:
    result = extract_sql("```sql\nSELECT * FROM t\n```")
    assert result == "SELECT * FROM t"


def test_extract_sql_garbage_before_select() -> None:
    result = extract_sql("Here is your SQL:\nSELECT * FROM t")
    assert result == "SELECT * FROM t"


# ── _generate_sql integration ───────────────────────────────────────────

def _patch_llm(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    """Force both the consumer module and app.llm to return fixed text."""
    import app.agent.sql_graph as graph_mod
    import app.llm as llm_mod
    monkeypatch.setattr(graph_mod, "call_llm", lambda *a, **k: text)
    monkeypatch.setattr(llm_mod, "call_llm", lambda *a, **k: text)


def _minimal_state() -> dict:
    return {
        "query_plan": None,
        "schema_context": None,
        "generated_sql": "",
        "retry_counters": {"sql_generation": 0, "plan": 0},
    }


def test_generate_sql_closed_think(monkeypatch) -> None:
    _patch_llm(monkeypatch, "<think>use fact_sales</think>\nSELECT * FROM t")
    from app.agent.sql_graph import _generate_sql
    result = _generate_sql(_minimal_state())
    assert result["generated_sql"] == "SELECT * FROM t"
    assert result["retry_counters"]["sql_generation"] == 1


def test_generate_sql_unclosed_think(monkeypatch) -> None:
    _patch_llm(monkeypatch, "<think>some reasoning\nSELECT * FROM t")
    from app.agent.sql_graph import _generate_sql
    result = _generate_sql(_minimal_state())
    assert result["generated_sql"] == "SELECT * FROM t"


def test_generate_sql_pure_sql(monkeypatch) -> None:
    _patch_llm(monkeypatch, "SELECT * FROM t")
    from app.agent.sql_graph import _generate_sql
    result = _generate_sql(_minimal_state())
    assert result["generated_sql"] == "SELECT * FROM t"


def test_generate_sql_truncated_returns_empty(monkeypatch) -> None:
    _patch_llm(monkeypatch, "<think>truncated")
    from app.agent.sql_graph import _generate_sql
    result = _generate_sql(_minimal_state())
    assert result["generated_sql"] == ""


def test_generate_sql_empty_response(monkeypatch) -> None:
    _patch_llm(monkeypatch, "")
    from app.agent.sql_graph import _generate_sql
    result = _generate_sql(_minimal_state())
    assert result["generated_sql"] == ""
