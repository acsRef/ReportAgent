"""Unit tests for `requirement_parser.parse_requirement`.

The LLM is stubbed via monkeypatch; we assert the parser's *plumbing*:
- A specific LLM response is normalized into the correct card status.
- Missing-field options come from the server-side `requirement_options`,
  never from the LLM string.
- Assumptions land with `accepted=None`.
- The fallback path activates when the LLM returns non-JSON.
"""
from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.graphs


def _patch_llm(monkeypatch: pytest.MonkeyPatch, payload: dict | str) -> None:
    """Force `app.llm.call_llm` to return a fixed string."""
    import app.agent.requirement_parser as parser_mod
    import app.llm as llm_mod

    if isinstance(payload, dict):
        import json
        text = json.dumps(payload, ensure_ascii=False)
    else:
        text = payload

    monkeypatch.setattr(parser_mod, "call_llm", lambda *a, **k: text)
    # Also patch the module-level import in case anything reaches in directly.
    monkeypatch.setattr(llm_mod, "call_llm", lambda *a, **k: text)


def test_vague_query_yields_missing_with_all_dimensions(monkeypatch) -> None:
    _patch_llm(monkeypatch, {
        "summary": "帮我分析一下销量",
        "target_metrics": [],
        "time_range": None,
        "scope": [],
        "dimensions": [],
        "analysis_methods": [],
        "confidence": 0.3,
        "missing_fields": ["time_range", "scope", "metric", "granularity", "comparison"],
        "assumptions": [],
    })

    from app.agent.requirement_parser import parse_requirement
    card = parse_requirement(user_query="帮我分析一下销量", schema_context=None)
    assert card.status == "missing"
    keys = {mf.key for mf in card.missing_fields}
    assert keys == {"time_range", "scope", "metric", "granularity", "comparison"}
    # Options come from the server-side controlled list, not the LLM.
    metric_mf = next(mf for mf in card.missing_fields if mf.key == "metric")
    assert any(opt.value == "销售额" for opt in metric_mf.options)
    assert card.confidence < 0.5


def test_specific_query_yields_complete(monkeypatch) -> None:
    _patch_llm(monkeypatch, {
        "summary": "2024 华东销售额趋势",
        "target_metrics": ["销售额"],
        "time_range": "今年",
        "scope": ["华东"],
        "dimensions": ["时间", "区域"],
        "analysis_methods": ["trend_analysis", "group_compare"],
        "confidence": 0.9,
        "missing_fields": [],
        "assumptions": [
            {
                "key": "granularity",
                "text": "默认按月统计",
                "accepted": True,  # server treats accepted=True as resolved
                "alternatives": [
                    {"label": "日", "value": "day"},
                    {"label": "周", "value": "week"},
                ],
            },
        ],
    })

    from app.agent.requirement_parser import parse_requirement
    card = parse_requirement(user_query="2024 华东销售额趋势", schema_context=None)
    assert card.status == "complete"
    assert card.missing_fields == []
    assert card.time_range == "今年"
    assert card.scope == ["华东"]
    assert card.target_metrics == ["销售额"]
    assert len(card.assumptions) == 1
    assert card.assumptions[0].accepted is True
    assert len(card.assumptions[0].alternatives) == 2


def test_unresolved_assumptions_keep_status_missing(monkeypatch) -> None:
    """When the LLM leaves an assumption unresolved, the card stays
    'missing' so the frontend prompts the user to accept/reject before
    /confirm becomes available.
    """
    _patch_llm(monkeypatch, {
        "summary": "今年销量",
        "target_metrics": ["销售额"],
        "time_range": "今年",
        "scope": ["ALL"],
        "dimensions": ["时间"],
        "analysis_methods": ["trend_analysis"],
        "confidence": 0.8,
        "missing_fields": [],
        "assumptions": [
            {
                "key": "granularity",
                "text": "默认按月统计",
                "accepted": None,  # explicitly unresolved
                "alternatives": [],
            },
        ],
    })

    from app.agent.requirement_parser import parse_requirement
    card = parse_requirement(user_query="今年销量", schema_context=None)
    assert card.status == "missing"
    assert len(card.assumptions) == 1
    assert card.assumptions[0].accepted is None


def test_llm_returns_unparseable_triggers_fallback(monkeypatch) -> None:
    _patch_llm(monkeypatch, "this is not JSON at all")

    from app.agent.requirement_parser import parse_requirement
    card = parse_requirement(user_query="不告诉你", schema_context=None)
    assert card.status == "missing"
    # All five canonical keys are required by the fallback.
    assert len(card.missing_fields) == 5


def test_uses_options_provider(monkeypatch) -> None:
    """When the LLM declares a missing field, the options must come from
    `requirement_options`, not from the LLM string. We patch the LLM to
    return a missing_fields list and verify the options match the
    controlled vocabulary.
    """
    _patch_llm(monkeypatch, {
        "summary": "test",
        "target_metrics": [],
        "time_range": None,
        "scope": [],
        "dimensions": [],
        "analysis_methods": [],
        "confidence": 0.5,
        "missing_fields": ["time_range"],
        "assumptions": [],
    })

    from app.agent.requirement_parser import parse_requirement
    from app.agent import requirement_options
    card = parse_requirement(user_query="x", schema_context=None)
    time_mf = card.missing_fields[0]
    assert time_mf.key == "time_range"
    expected_values = {opt.value for opt in requirement_options.time_options(None)}
    actual_values = {opt.value for opt in time_mf.options}
    assert actual_values == expected_values
