"""需求槽位三态 + 口径确定性填充（plan 2026-11-04-requirement-slot-contract Phase 1）。

钉三件事：
1. 口径匹配**最长优先**（「净销售额」不许被「销售额」抢；「到账金额」→ 实收金额）；
2. 用户没点名口径时，`{模型猜 ∪ 注册表 default}` 决定「直接填」还是「问用户」；
3. 三态落进卡片（`slot_states` / `slot_order`），且歧义澄清项带注册表候选与差异说明。
"""
from __future__ import annotations

import pytest

from app.agent import requirement_parser as rp
from app.agent.requirement_slots import (
    match_metric_terms,
    next_slot,
    resolve_metric_slot,
    slot_order,
    slot_states,
)
from app.models.requirement import RequirementCard

pytestmark = pytest.mark.graphs


# --- 原话 → 注册表指标（最长优先） ------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("2024年各区域销售额", ["销售额"]),
        ("净销售额是多少", ["净销售额"]),      # 不许被「销售额」抢
        ("扣退款销售额", ["净销售额"]),
        ("到账金额", ["实收金额"]),
        ("退款率", ["退款率"]),
        ("客户数", []),                      # 注册表里没有这个口径
    ],
)
def test_match_metric_terms_longest_priority(query: str, expected: list[str]) -> None:
    assert [m.name for m in match_metric_terms(query)] == expected


# --- 三态判定 ---------------------------------------------------------------


def test_explicit_metric_is_filled_and_overrides_model() -> None:
    slot = resolve_metric_slot("2024年各区域销售额", ["实收金额"])

    assert slot.state == "filled"
    assert slot.value == "销售额"
    assert slot.source == "explicit"


def test_model_guess_differs_from_default_is_ambiguous() -> None:
    slot = resolve_metric_slot("2024年各区域表现", ["实收金额"])

    assert slot.state == "ambiguous"
    names = [c.name for c in slot.candidates]
    assert names[:2] == ["实收金额", "销售额"]     # 模型猜 + 注册表 default
    assert len(names) <= 3
    assert all(c.hint for c in slot.candidates), "候选必须带差异说明"


def test_model_guess_equals_default_still_asks() -> None:
    """用户没点名口径 → 即使模型猜的正是默认值也要问（口径最容易答错）。

    候选来自数据：模型猜的 + 注册表 default + **同单位的第一个其它指标**，
    所以这里会补出「净销售额」——正是口径差异最大的那个对手。
    """
    slot = resolve_metric_slot("2024年各区域表现", ["销售额"])

    assert slot.state == "ambiguous"
    names = [c.name for c in slot.candidates]
    assert names[:2] == ["销售额", "净销售额"]
    assert len(names) == 3 and "实收金额" in names, "同单位备选封顶 3 个（识别题不是多选题）"


def test_same_unit_alternate_is_data_driven() -> None:
    """换一个单位（件）时补的是同单位指标，不会硬塞「销售额」。"""
    slot = resolve_metric_slot("看看卖了多少件", ["销量"])

    assert slot.state == "ambiguous"
    names = [c.name for c in slot.candidates]
    assert "销量" in names


def test_no_metric_anywhere_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.knowledge.registry.all_metrics", lambda: (),
    )
    slot = resolve_metric_slot("2024年各区域表现", [])

    assert slot.state == "missing"


# --- 槽位状态与提问顺序 -----------------------------------------------------


def _card(**kwargs) -> RequirementCard:
    base = dict(id="draft-s1", version=1, status="missing", summary="x")
    base.update(kwargs)
    return RequirementCard(**base)


def test_slot_states_cover_applicable_slots() -> None:
    card = _card(
        target_metrics=["销售额"],
        time_range="2024年",
        scope=["华东"],
        granularity="week",
        analysis_methods=["anomaly_detection"],
    )
    states = slot_states(card)

    assert states["metric"] == "filled"
    assert states["time_range"] == "filled"
    assert states["scope"] == "filled"
    assert states["granularity"] == "filled"
    assert "analysis_intent" not in states, "已有分析角度时不该问视角"


def test_slot_states_skip_granularity_for_detail_mode() -> None:
    card = _card(
        target_metrics=["销售额"],
        time_range="2024年7月",
        detail_level="detail",
    )
    states = slot_states(card)

    assert "granularity" not in states, "明细模式不聚合，不该问粒度"
    assert "analysis_intent" not in states


def test_slot_order_puts_metric_first() -> None:
    card = _card(time_range=None, scope=[])
    states = slot_states(card)

    assert next_slot(states) == "metric"
    assert slot_order(states)[:3] == ["metric", "time_range", "scope"]


# --- parse_requirement 端到端（LLM 输出固定） --------------------------------


def _payload(target_metrics: list[str], **overrides) -> dict:
    data = {
        "summary": "分析区域销售",
        "target_metrics": target_metrics,
        "time_range": "2024年",
        "granularity": None,
        "scope": [],
        "dimensions": ["区域"],
        "analysis_methods": ["group_compare"],
        "confidence": 0.8,
        "clarifications": [],
        "assumptions": [],
    }
    data.update(overrides)
    return data


def _patch_parse(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(rp, "_call_llm_for_parse", lambda *a, **k: payload)


def test_parse_requirement_forces_user_named_metric(monkeypatch) -> None:
    """用户说「销售额」，模型若给「实收金额」必须被覆盖——这是第一轮答案乱的根因。"""
    _patch_parse(monkeypatch, _payload(["实收金额"]))

    card = rp.parse_requirement(user_query="2024年各区域销售额", schema_context=None)

    assert card.target_metrics == ["销售额"]
    assert card.slot_states["metric"] == "filled"
    assert not [mf for mf in card.missing_fields if mf.key == "metric"]


def test_parse_requirement_defaults_metric_when_user_named_none(monkeypatch) -> None:
    """没点名口径 → 按注册表默认口径前进 + 一条已接受假设（不拦下来问选择题）。

    2026-11-04 23:19 的 `5337f3a`（plan `clarify-default-instead-of-ask`）把设计从
    「一律问」改成「有默认就不问」，但当时的两个测试没跟着改——它们从此一直红着，
    也是 CI 长期红的根因之一。这里按最终设计钉住行为。
    """
    _patch_parse(monkeypatch, _payload(["实收金额"]))

    card = rp.parse_requirement(user_query="2024年各区域表现", schema_context=None)

    assert card.target_metrics == ["销售额"], "应落到注册表默认口径"
    assert not [mf for mf in card.missing_fields if mf.key == "metric"]
    assert any(
        a.key.startswith("metric_default:") and a.accepted is True for a in card.assumptions
    ), "必须留一条已接受的默认口径假设，用户随时能改"
    assert card.slot_states["metric"] == "ambiguous", "状态仍记为『用户没点名』"


def test_parse_requirement_asks_when_registry_has_no_default(monkeypatch) -> None:
    """只有注册表连默认口径都没有时，才退回「问用户」。"""
    monkeypatch.setattr(rp, "_default_registry_metric", lambda: None)
    _patch_parse(monkeypatch, _payload(["实收金额"]))

    card = rp.parse_requirement(user_query="2024年各区域表现", schema_context=None)

    metric_fields = [mf for mf in card.missing_fields if mf.key == "metric"]
    assert metric_fields, "没有默认口径时必须问"
    field = metric_fields[0]
    assert field.state == "ambiguous"
    assert all(o.value.startswith("metric:") for o in field.options)
    assert len(field.options) <= 3
    assert card.status == "missing"


def test_parse_requirement_no_metric_clarification_when_user_named_it(monkeypatch) -> None:
    _patch_parse(monkeypatch, _payload(["销售额"]))

    card = rp.parse_requirement(user_query="2024年各区域销售额", schema_context=None)

    assert card.target_metrics == ["销售额"]
    assert not [mf for mf in card.missing_fields if mf.key == "metric"]


def test_parse_requirement_slots_survive_deterministic_intent(monkeypatch) -> None:
    """确定性意图（各区域对比）与槽位解析共存：口径仍按用户原话落位。"""
    _patch_parse(monkeypatch, _payload(["实收金额"], analysis_methods=[]))

    card = rp.parse_requirement(user_query="2024年各区域销售额对比", schema_context=None)

    assert card.target_metrics == ["销售额"]
    assert card.analysis_methods == ["group_compare"]
    assert card.slot_states["metric"] == "filled"


def test_filled_metric_drops_stale_clarification(monkeypatch) -> None:
    """模型既给了口径又把它列进 missing_fields → 口径已定型后不许再问。"""
    _patch_parse(
        monkeypatch,
        _payload(["实收金额"], missing_fields=["metric", "scope"]),
    )

    card = rp.parse_requirement(user_query="2024年各区域销售额", schema_context=None)

    assert card.target_metrics == ["销售额"]
    assert not [mf for mf in card.missing_fields if mf.key == "metric"]
    # time_range 由 payload 给了值（已被 `_drop_satisfied_missing_fields` 清掉），
    # 只剩真正没填的 scope。
    assert [mf.key for mf in card.missing_fields] == ["scope"]


def test_metric_options_come_from_registry() -> None:
    """澄清选项的唯一事实源是 metrics.yaml（此前是手写静态表，含注册表没有的「客户数」）。"""
    from app.agent import requirement_options as opts
    from app.knowledge import registry as knowledge

    options = opts.metric_options()

    assert [o.label for o in options] == [m.name for m in knowledge.all_metrics()]
    assert all(o.value == f"metric:{o.label}" for o in options)
    assert all(o.hint for o in options), "每个口径都要有差异说明"
    assert "客户数" not in [o.label for o in options]
