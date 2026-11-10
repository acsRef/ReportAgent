"""需求槽位三态 + 口径确定性解析（plan 2026-11-04-requirement-slot-contract Phase 1）。

## 为什么要这个模块

2026-11-04 实测：用户问「2024年各区域销售额」，卡片 `target_metrics` 却可能是
`实收金额`——注册表里 `销售额` 明明 `default: true`。口径完全由模型选，于是
「第一轮答案乱」；而 supplement 轮只要模型没给值就继承上一轮的口径
（`_merge_prior_card`），猜错的口径被逐轮固化成「事实」。

这个模块把「口径」从「让模型理解」改成**确定性填槽**：

- 用户原话命中注册表 → `filled`（**最长优先**，避免「净销售额」被「销售额」抢）；
- 用户没提指标 → 候选 = {模型猜的 ∪ 注册表 default}：≥2 → `ambiguous`（交给带数据的
  澄清），=1 → `filled`；
- 都没有 → `missing`。

三态（filled / missing / ambiguous）同时是**呈现层「一次只问一个问题」的排序依据**
（`next_slot`）。

模块保持纯函数：不读 DB、不调 LLM、不执行 SQL——预览数值由 Execution 域的
`metric_preview_service` 负责（CLAUDE.md §4.1）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from app.knowledge import registry as knowledge
from app.knowledge.registry import MetricDef

SlotState = Literal["filled", "missing", "ambiguous"]
SlotSource = Literal["explicit", "registry_default", "model", "inherited"]

# 提问顺序（Phase 5 呈现层用它决定「下一个问哪个槽位」）：
# 口径 > 时间范围 > 粒度 > 范围 > 分析角度。
# 理由：口径决定「这个数是什么」，错了后面全错；范围/时间错了至少数还是同类量。
SLOT_ORDER: tuple[str, ...] = (
    "metric",
    "time_range",
    "granularity",
    "scope",
    "analysis_intent",
)

# 需要时间粒度的分析角度（趋势必须给粒度；异常要日/周才有密度）。
_GRANULARITY_METHODS = ("trend_analysis", "anomaly_detection")


def _norm(text: object) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


@dataclass(frozen=True)
class MetricCandidate:
    """一个口径候选（name/expr 来自注册表，hint 供用户识别差异）。"""

    name: str
    expr: str = ""
    unit: str = ""
    hint: str = ""
    source: SlotSource = "model"


@dataclass(frozen=True)
class MetricSlot:
    state: SlotState
    value: str | None = None
    candidates: tuple[MetricCandidate, ...] = ()
    source: SlotSource | None = None


@dataclass(frozen=True)
class SlotResolution:
    key: str
    state: SlotState
    value: str | None = None
    candidates: tuple[MetricCandidate, ...] = ()
    source: SlotSource | None = None
    extra: dict = field(default_factory=dict)


def _candidate(metric: MetricDef, source: SlotSource) -> MetricCandidate:
    return MetricCandidate(
        name=metric.name,
        expr=metric.expr,
        unit=metric.unit,
        hint=metric.note,
        source=source,
    )


def match_metric_terms(text: str) -> list[MetricDef]:
    """原话里点名的注册表指标——**多个不同口径同时出现时全部返回**。

    两条规则合起来用，缺一不可：

    1. **子串消歧**：同一个词能落在两个口径上时，只保留「被更长命中完整覆盖」的那个
       （「净销售额是多少」里的「销售额」被「净销售额」覆盖 → 只认净销售额）；
    2. **互不覆盖就都要**：`2024年各产品类别的销售额和退货金额对比` 要的是**两个**口径。

    2026-11-10（真机 `complex-sql-sales-vs-returns`）：此前只做「取最长」，
    于是「销售额和退货金额」只留下「退款金额」（4 字 > 3 字），需求卡只剩一个口径，
    被测 SQL 只出 `退款金额` 一列——**用户要的对比直接少了一半**。

    与 `knowledge.registry.metrics_mentioned_in` 的区别（有意保留两份实现）：
    那边回答「这段文本**围绕/定义**哪个口径」（记忆归并需要单主题语义），
    这里回答「用户**点了哪些**口径」（槽位需要多值）。
    """
    haystack = _norm(text)
    if not haystack:
        return []

    matched: list[tuple[MetricDef, str]] = []
    for metric in knowledge.all_metrics():
        best = ""
        for alias in (metric.name, *metric.synonyms):
            needle = _norm(alias)
            if needle and needle in haystack and len(needle) > len(best):
                best = needle
        if best:
            matched.append((metric, best))
    if not matched:
        return []

    spans_cache: dict[str, list[tuple[int, int]]] = {}

    def _spans(needle: str) -> list[tuple[int, int]]:
        cached = spans_cache.get(needle)
        if cached is None:
            cached, start = [], haystack.find(needle)
            while start != -1:
                cached.append((start, start + len(needle)))
                start = haystack.find(needle, start + 1)
            spans_cache[needle] = cached
        return cached

    found: list[MetricDef] = []
    seen: set[str] = set()
    for metric, needle in matched:
        if metric.name in seen:
            continue
        mine = _spans(needle)
        covered = any(
            other.name != metric.name
            and len(other_needle) > len(needle)
            and all(any(a <= s and e <= b for a, b in _spans(other_needle)) for s, e in mine)
            for other, other_needle in matched
        )
        if covered:
            continue
        seen.add(metric.name)
        found.append(metric)
    return found


def _default_metric() -> MetricDef | None:
    return next((m for m in knowledge.all_metrics() if m.default), None)


def resolve_metric_slot(
    user_query: str,
    model_metrics: list[str] | None,
    inherited: str | None = None,
) -> MetricSlot:
    """口径槽位的三态解析（用户词 > 上一轮已确认 > 注册表 default > 模型猜）。

    用户没点名口径时**一律问**（缺省值也一样）：口径是这个产品里最容易答错的
    槽位，注册表里同单位的指标往往就差在退款/支付语义上——不问就答等于替用户
    选。为了让「问」有价值，候选至少要凑到 2 个：
    模型猜的 ∪ 注册表 default ∪ **同单位的第一个其它指标**（数据驱动，不写死名字）。

    `inherited`：supplement 轮上一轮卡里的口径。**已确认的槽位不再问**——这正是
    「每轮只从已确认槽位出发」的落点；只有用户原话明确改写时才覆盖。
    """
    explicit = match_metric_terms(user_query)
    if len(explicit) == 1:
        metric = explicit[0]
        return MetricSlot(
            state="filled",
            value=metric.name,
            candidates=(_candidate(metric, "explicit"),),
            source="explicit",
        )
    if len(explicit) > 1:
        # 同一短语能归到多个口径（例如「金额」同时是几个指标的同义词）→ 值得问。
        return MetricSlot(
            state="ambiguous",
            value=None,
            candidates=tuple(_candidate(m, "explicit") for m in explicit),
            source="explicit",
        )

    if inherited:
        inherited_metric = knowledge.resolve_metric(inherited)
        if inherited_metric is None:
            # 旧卡 / 模型澄清选项可能写的是物理列名（order_amount）——归一到业务名再解析，
            # 否则「上一轮已确认」的口径会认不出来，用户被重复问一次口径
            # （2026-11-04 实测）。
            try:
                from app.agent.requirement_parser import _display_metric

                inherited_metric = knowledge.resolve_metric(_display_metric(str(inherited)))
            except Exception:  # noqa: BLE001 - 归一失败就当没继承，继续走提问
                inherited_metric = None
        if inherited_metric is not None:
            return MetricSlot(
                state="filled",
                value=inherited_metric.name,
                candidates=(_candidate(inherited_metric, "inherited"),),
                source="inherited",
            )

    # 用户没说清 → 模型猜的 + 注册表默认值 + 同单位备选（去重保序）。
    candidates: list[MetricCandidate] = []
    seen: set[str] = set()

    def _push(metric: MetricDef, source: SlotSource) -> None:
        if metric.name in seen:
            return
        seen.add(metric.name)
        candidates.append(_candidate(metric, source))

    for term in model_metrics or []:
        metric = knowledge.resolve_metric(term)
        if metric is not None:
            _push(metric, "model")
    default = _default_metric()
    if default is not None:
        _push(default, "registry_default")
    # 同单位里再挑 1-2 个（口径差异最常发生在同单位指标之间：销售额 / 净销售额 /
    # 实收金额），封顶 3 个——澄清是识别题，不该变成多选题。
    units = {c.unit for c in candidates if c.unit}
    alternates = 0
    for metric in knowledge.all_metrics():
        if alternates >= 2 or len(candidates) >= 3:
            break
        if metric.name in seen or metric.unit not in units:
            continue
        _push(metric, "registry_default")
        alternates += 1

    if len(candidates) >= 2:
        return MetricSlot(state="ambiguous", candidates=tuple(candidates[:3]))
    if len(candidates) == 1:
        chosen = candidates[0]
        return MetricSlot(
            state="filled", value=chosen.name, candidates=(chosen,), source=chosen.source,
        )
    return MetricSlot(state="missing")


def slot_states(card, metric_slot: MetricSlot | None = None) -> dict[str, SlotState]:
    """卡片 → 每个**适用**槽位的状态（不适用就不出现在字典里，避免假 missing）。

    适用性判定是确定性的：
    - `granularity` 只在需要时间分组时适用（趋势/异常方法，或非明细模式）；
    - `analysis_intent` 只在既没有分析方法、也不是明细时适用。
    """
    states: dict[str, SlotState] = {}

    if metric_slot is not None:
        states["metric"] = metric_slot.state
    else:
        states["metric"] = "filled" if getattr(card, "target_metrics", None) else "missing"

    states["time_range"] = "filled" if getattr(card, "time_range", None) else "missing"
    states["scope"] = (
        "filled" if getattr(card, "scope", None) else "missing"
    )

    methods = [str(m).lower() for m in (getattr(card, "analysis_methods", None) or [])]
    is_detail = getattr(card, "detail_level", None) == "detail"
    # 粒度只在「真要按时间分组」时才是槽位：趋势/异常方法必须给粒度；有具体时间范围
    # 的聚合查询也要粒度（否则 SQL 会自己挑一个）。明细模式不聚合 → 不问。
    # 注意这里看的是**卡体字段**，不是上面算出来的状态字符串（那会把 missing 当 True）。
    needs_granularity = (not is_detail) and (
        any(m in _GRANULARITY_METHODS for m in methods)
        or bool(getattr(card, "time_range", None))
    )
    if needs_granularity:
        states["granularity"] = (
            "filled" if getattr(card, "granularity", None) else "missing"
        )

    if not is_detail and not methods and getattr(card, "top_period", None) is None:
        # 2026-11-04：最高/最低的那一期本身就是明确的分析诉求（选期 + 取该期数据），
        # 不该再问「你想看哪种视角」。
        states["analysis_intent"] = "missing"

    # 已经列进 missing_fields 的 key 一律算「待补」（服务端/模型已判定缺失）。
    for mf in getattr(card, "missing_fields", None) or []:
        key = str(getattr(mf, "key", "") or "")
        normalized = _clarification_key_to_slot(key)
        if normalized:
            states[normalized] = "missing" if states.get(normalized) != "ambiguous" else "ambiguous"

    return states


_CLARIFICATION_KEY_SLOTS: dict[str, str] = {
    "metric": "metric",
    "time_range": "time_range",
    "scope": "scope",
    "granularity": "granularity",
    "analysis_intent": "analysis_intent",
}


def _clarification_key_to_slot(key: str) -> str | None:
    """澄清项 key → 槽位名（`intent:*` 这类模型现场生成的 key 归到分析角度）。"""
    if key in _CLARIFICATION_KEY_SLOTS:
        return _CLARIFICATION_KEY_SLOTS[key]
    if key.startswith("intent") or key.startswith("analysis"):
        return "analysis_intent"
    return None


def next_slot(states: dict[str, SlotState]) -> str | None:
    """按固定顺序返回第一个未填的槽位（呈现层「下一个问哪个」）。"""
    for key in SLOT_ORDER:
        if states.get(key) in ("missing", "ambiguous"):
            return key
    return None


def slot_order(states: dict[str, SlotState]) -> list[str]:
    """未填槽位的完整提问顺序（随卡下发给前端，前后端同一份顺序）。"""
    return [key for key in SLOT_ORDER if states.get(key) in ("missing", "ambiguous")]
