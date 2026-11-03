"""补充轮的时间继承（2026-11-02）。

缺陷链：首轮「2024年华东销售趋势」→ 追问「详细讲一下七月份」→ 新卡的
`time_range` 变成「七月份」（年份丢了）→ SQL 生成器按 today(2026) 解析成
`order_date >= '2026-07-01'` → **静默返回另一年的数字**。

这类错误比报错危险得多：报表看起来完全正常。所以年份继承放在需求层做确定性补齐。
"""
from __future__ import annotations

import pytest

from app.agent.requirement_parser import _merge_prior_card
from app.models.requirement import RequirementCard

pytestmark = pytest.mark.contracts


def _card(**overrides) -> RequirementCard:
    base = dict(
        id="d1", version=1, status="complete", summary="s",
        target_metrics=["销售额"], time_range=None, granularity=None,
        scope=[], dimensions=[], analysis_methods=[],
        expected_blocks=[], missing_fields=[], assumptions=[],
        confidence=0.9, confirmed_at=None,
    )
    base.update(overrides)
    return RequirementCard(**base)


def test_bare_month_inherits_prior_year():
    prior = _card(time_range="2024年", scope=["华东"])
    card = _card(time_range="七月份")
    _merge_prior_card(card, prior)
    assert card.time_range == "2024年7月"
    assert card.scope == ["华东"]


def test_explicit_year_is_not_overwritten():
    prior = _card(time_range="2024年")
    card = _card(time_range="2025年")
    _merge_prior_card(card, prior)
    assert card.time_range == "2025年"


def test_missing_time_range_inherits_wholesale():
    prior = _card(time_range="2024年")
    card = _card(time_range=None)
    _merge_prior_card(card, prior)
    assert card.time_range == "2024年"


def test_no_year_in_prior_leaves_card_untouched():
    prior = _card(time_range="上个月")
    card = _card(time_range="七月份")
    _merge_prior_card(card, prior)
    assert card.time_range == "七月份"


def test_arabic_month_keeps_prefix():
    prior = _card(time_range="2024年")
    card = _card(time_range="7月")
    _merge_prior_card(card, prior)
    assert card.time_range == "2024年7月"


def test_relative_span_resolves_to_discussed_months():
    """「这两个月」指刚聊过的月份，不是「最近两个月」。"""
    prior = _card(time_range="2024年8月")
    card = _card(time_range="2024年最近两个月")
    _merge_prior_card(card, prior)
    assert card.time_range == "2024年7月-8月"


def test_relative_span_keeps_explicit_months():
    prior = _card(time_range="2024年8月")
    card = _card(time_range="2024年3月-4月")
    _merge_prior_card(card, prior)
    assert card.time_range == "2024年3月-4月"


# --- 时间筛选提示（确定性，避免月份被写成日期字符串） ----------------------

def test_period_hint_month_range_and_single_month():
    from app.agent.confirmed_execution_graph import _period_filter_hint

    ranged = _period_filter_hint("2024年7月-8月")
    assert ranged is not None and "BETWEEN 7 AND 8" in ranged

    single = _period_filter_hint("2024年7月")
    assert single is not None and "month = 7" in single
    assert "整数" in single  # 明确 month 是 int，禁止 '2024-07'

    year_only = _period_filter_hint("2024年")
    assert year_only is not None and "year = 2024" in year_only
    assert "month" not in year_only
    assert _period_filter_hint("去年") is None
