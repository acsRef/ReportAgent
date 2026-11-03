"""报告标题必须来自需求（2026-11-03）。

缺陷：所有版本都叫「报告」/「报告（调整）」，左栏十条会话十个「报告」，
用户翻历史完全分不清。标题是给人看的索引，应该来自需求卡摘要。
"""
from __future__ import annotations

import pytest

from app.agent.confirmed_execution_graph import _report_title
from app.models.requirement import RequirementCard

pytestmark = pytest.mark.contracts


def _card(summary: str) -> RequirementCard:
    return RequirementCard(
        id="d1", version=1, status="complete", summary=summary,
        target_metrics=[], time_range=None, granularity=None,
        scope=[], dimensions=[], analysis_methods=[],
        expected_blocks=[], missing_fields=[], assumptions=[],
        confidence=0.9, confirmed_at=None,
    )


def test_title_comes_from_card_summary():
    state = {"requirement_card": _card("分析2024年华东地区的销售趋势和异常情况")}
    assert _report_title(state) == "分析2024年华东地区的销售趋势和异常情况"


def test_title_is_truncated_with_ellipsis():
    state = {"requirement_card": _card("分析" + "很长的需求" * 10)}
    title = _report_title(state)
    assert title.endswith("…")
    assert len(title) <= 25


def test_title_normalizes_whitespace():
    state = {"requirement_card": _card("  分析 2024年\n华东  ") }
    assert _report_title(state) == "分析 2024年 华东"


def test_title_falls_back_when_no_card_or_summary():
    assert _report_title({}) == "报告"
    assert _report_title({"requirement_card": _card("   ")}) == "报告"


def test_title_accepts_dict_card():
    """checkpoint 反序列化后可能是 dict，不能因此退回「报告」。"""
    state = {"requirement_card": {"summary": "各品类销售额对比"}}
    assert _report_title(state) == "各品类销售额对比"
