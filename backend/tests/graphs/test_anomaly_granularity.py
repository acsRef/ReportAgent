"""异常类问法的粒度确定性（plan 2026-11-03-anomaly-granularity）。

2026-11-03 实测病灶：
- 「2024年哪几天销售额异常」模型返回 granularity=None → `GROUP BY year` → **1 行**；
- 「2024年销售额有没有异常波动」模型自己给了 month → 12 点（2σ 检不出日/周级异常）；
- 「2024年各品类销售额有没有异常」把品类和时间一起分组 → `detect_anomaly` 拿品类总量
  跟周销量比大小，叙述变成「手机比平板高 26%，未发现异常」（答非所问）。

本文件钉三件事：原话粒度优先 / 异常类默认粒度（≤45 天按日、否则按周）/
时间序列与横截面分流。
"""
from __future__ import annotations

import pytest

from app.agent import requirement_parser as rp
from app.models.requirement import RequirementAssumption, RequirementCard

pytestmark = pytest.mark.graphs


# --- 原话粒度判定 -----------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("2024年哪几天销售额异常", "day"),
        ("2024年每天销售额", "day"),
        ("2024年按日看销售额", "day"),
        ("那按周看看波动", "week"),
        ("2024年每周销售额", "week"),
        ("2024年各月销售额有没有异常", "month"),
        ("2024年按季度看销售额", "quarter"),
        ("2024年每年销售额", "year"),
        ("2024年销售额有没有异常波动", None),  # 没说粒度 → 交给默认值
    ],
)
def test_granularity_from_query(query: str, expected: str | None) -> None:
    assert rp._granularity_from_query(query) == expected


# --- 跨度估算 + 默认粒度 -----------------------------------------------------


@pytest.mark.parametrize(
    ("time_range", "days"),
    [
        ("2024年", 366),
        ("2024年7月", 31),
        ("2024年7月-9月", 93),
        ("2024年1月-12月", 372),
        ("最近三个月", 93),
        ("最近7天", 7),
        ("最近2周", 14),
        ("今年", 366),
        ("上半年", 183),
        ("昨天", None),
        (None, None),
    ],
)
def test_range_days(time_range: str | None, days: int | None) -> None:
    assert rp._range_days(time_range) == days


@pytest.mark.parametrize(
    ("time_range", "expected"),
    [
        ("2024年", "week"),      # 整年 = 52 个周点（日点 366 太糊）
        ("2024年7月", "day"),     # 单月 = 31 点，按日
        ("最近7天", "day"),
        ("最近三个月", "week"),
        ("2024年7月-9月", "week"),
        (None, "week"),          # 认不出按长跨度处理
    ],
)
def test_default_anomaly_granularity(time_range: str | None, expected: str) -> None:
    assert rp._default_anomaly_granularity(time_range) == expected


# --- 横截面维度识别 ---------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("2024年各品类销售额有没有异常", "品类"),
        ("哪个区域异常", "区域"),
        ("各门店有没有异常波动", "门店"),
        ("2024年华东销售趋势和异常", None),  # 华东是过滤范围，不是横截面维度
        ("看看有没有异常", None),
        ("那按周看看波动", None),
    ],
)
def test_anomaly_cross_dimension(query: str, expected: str | None) -> None:
    assert rp._anomaly_cross_dimension(query) == expected


# --- 分流：时间序列 vs 横截面 ------------------------------------------------


def _card(**kwargs) -> RequirementCard:
    base = dict(
        id="draft-x",
        version=1,
        status="missing",
        summary="x",
        time_range="2024年",
    )
    base.update(kwargs)
    return RequirementCard(**base)


def test_anomaly_time_series_drops_scope_constant_dim_and_defaults_to_week() -> None:
    """"2024年华东销售趋势和异常"：模型给的「区域」是 scope 造成的常量列，必须丢掉。"""
    card = _card(dimensions=["时间", "区域"], scope=["华东"], analysis_methods=[])

    rp._apply_deterministic_intent(card, "anomaly", user_query="2024年华东销售趋势和异常")

    assert card.dimensions == ["时间"]
    assert card.granularity == "week"
    assert card.analysis_methods == ["anomaly_detection"]


def test_anomaly_cross_section_keeps_dimension_and_drops_granularity() -> None:
    card = _card(dimensions=["品类", "时间"], analysis_methods=["group_compare"])

    rp._apply_deterministic_intent(card, "anomaly", user_query="2024年各品类销售额有没有异常")

    assert card.dimensions == ["品类"]
    assert card.granularity is None
    # 横截面保留既有方法（排名式摘要仍有意义），并加上异常检测
    assert card.analysis_methods == ["group_compare", "anomaly_detection"]


def test_anomaly_cross_section_respects_explicit_granularity() -> None:
    card = _card(dimensions=["品类"], analysis_methods=[])

    rp._apply_deterministic_intent(card, "anomaly", user_query="各品类每周销售额有没有异常")

    assert card.dimensions == ["品类"]
    assert card.granularity == "week"  # 用户明说按周 → 不能被清空


def test_anomaly_short_range_uses_day() -> None:
    card = _card(time_range="2024年7月", dimensions=["时间"], analysis_methods=[])

    rp._apply_deterministic_intent(card, "anomaly", user_query="7月销售额异常吗")

    assert card.granularity == "day"


def test_anomaly_explicit_day_word_wins() -> None:
    card = _card(time_range="2024年", dimensions=["时间"], analysis_methods=[])

    rp._apply_deterministic_intent(card, "anomaly", user_query="2024年哪几天销售额异常")

    assert card.granularity == "day"


# --- parse_requirement 端到端（LLM 输出固定，不动真实模型）-------------------


def _patch_parse(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    monkeypatch.setattr(rp, "_call_llm_for_parse", lambda *a, **k: payload)


_MODEL_ANOMALY_PAYLOAD = {
    "summary": "分析2024年华东地区的销售趋势和异常",
    "target_metrics": ["order_amount"],
    "time_range": "2024年",
    "granularity": None,          # 模型没给——本轮就是修这个
    "scope": ["华东"],
    "dimensions": ["时间", "区域"],
    "analysis_methods": ["anomaly_detection"],
    "confidence": 0.8,
    "clarifications": [],
    "assumptions": [],
}


def test_parse_requirement_anomaly_defaults_to_week(monkeypatch) -> None:
    _patch_parse(monkeypatch, dict(_MODEL_ANOMALY_PAYLOAD))

    card = rp.parse_requirement(
        user_query="2024年华东销售趋势和异常", schema_context=None,
    )

    assert card.granularity == "week"
    assert card.dimensions == ["时间"]
    assert card.analysis_methods == ["anomaly_detection"]
    assert card.status == "complete"


def test_parse_requirement_explicit_day_is_honoured(monkeypatch) -> None:
    _patch_parse(monkeypatch, {**_MODEL_ANOMALY_PAYLOAD, "scope": [], "summary": "哪几天异常"})

    card = rp.parse_requirement(user_query="2024年哪几天销售额异常", schema_context=None)

    assert card.granularity == "day"


def test_parse_requirement_explicit_month_not_overridden(monkeypatch) -> None:
    _patch_parse(monkeypatch, {**_MODEL_ANOMALY_PAYLOAD, "granularity": "month"})

    card = rp.parse_requirement(user_query="2024年各月销售额有没有异常", schema_context=None)

    assert card.granularity == "month"


def test_parse_requirement_anomaly_cross_section(monkeypatch) -> None:
    _patch_parse(
        monkeypatch,
        {
            "summary": "各品类销售额异常",
            "target_metrics": ["order_amount"],
            "time_range": "2024年",
            "granularity": None,
            "scope": [],
            "dimensions": ["品类", "时间"],
            "analysis_methods": ["anomaly_detection"],
            "confidence": 0.8,
            "clarifications": [],
            "assumptions": [],
        },
    )

    card = rp.parse_requirement(
        user_query="2024年各品类销售额有没有异常", schema_context=None,
    )

    assert card.dimensions == ["品类"]
    assert card.granularity is None


def test_parse_requirement_anomaly_keeps_accepted_assumptions(monkeypatch) -> None:
    """粒度确定性别把假设清掉（横截面 + 已接受假设仍走 complete）。"""
    payload = dict(_MODEL_ANOMALY_PAYLOAD)
    payload["assumptions"] = [
        {"key": "field_meaning:销售额", "text": "取订单金额", "accepted": True, "alternatives": []},
    ]
    _patch_parse(monkeypatch, payload)

    card = rp.parse_requirement(user_query="2024年华东销售趋势和异常", schema_context=None)

    assert [a.key for a in card.assumptions] == ["field_meaning:销售额"]
    assert card.status == "complete"


# --- PATCH 路径：用户选「异常」意图时同样落粒度 ------------------------------


def test_apply_intent_anomaly_sets_default_granularity() -> None:
    from app.services.requirement_service import _apply_intent

    card = {
        "time_range": "2024年",
        "granularity": None,
        "dimensions": [],
        "analysis_methods": [],
    }
    _apply_intent(card, "anomaly")

    assert card["granularity"] == "week"
    assert card["analysis_methods"] == ["anomaly_detection"]
    assert "时间" in card["dimensions"]


def test_apply_intent_anomaly_short_range_day() -> None:
    from app.services.requirement_service import _apply_intent

    card = {
        "time_range": "最近7天",
        "granularity": None,
        "dimensions": [],
        "analysis_methods": [],
    }
    _apply_intent(card, "anomaly")

    assert card["granularity"] == "day"


def test_card_model_accepts_week_granularity() -> None:
    """RequirementGranularity Literal 必须认 week（否则落库/序列化会炸）。"""
    card = RequirementCard(
        id="draft-y",
        version=1,
        status="complete",
        summary="x",
        granularity="week",
        assumptions=[RequirementAssumption(key="k", text="t", accepted=True)],
    )
    assert card.model_dump(mode="json")["granularity"] == "week"
