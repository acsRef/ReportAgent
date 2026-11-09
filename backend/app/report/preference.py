"""Chart 偏好确定性应用（P16 D3，memory-architecture §三 Report = Semantic ✅ Preference）。

`ContextRuntime` 在 report 链召回 preference 类记忆（memory_type ∈ stable_preference /
temporary_preference → source=memory_preference）后，视觉化偏好经**确定性纯函数**
落到 chart_config——不走 LLM prompt、不改 `chart_advisor` 签名/词表。chart type 词表
与 `sql_tools.chart_advisor` 严格交集：`{pie, bar, table}`；词表外文本（"折线图"
"金额保留两位小数"…）诚实忽略（产品不支持的类型偏好不产生任何效果）。

语义 = 软约束、数据硬约束（对齐 §七 Conflict Priority：偏好低于数据事实）：
- 原 chart 可可视化（type ∈ {pie, bar}）且偏好 ∈ {pie, bar} → override type +
  dimensions 键归一（bar 用 `x/y`、pie 用 `category/value`，双向映射）
- 原 type = table（数据无分类+数值组合，不支持可视化）→ 可视化偏好让位，保持 table
- 偏好 table → 降级纯表格（保留原 rows）

多条偏好并存（历史 supersede 只防同 content 冲突）→ 取 preferences 列表**最先命中**
者（调用方以 recall score 降序传入，先命中 = 最相关偏好）。
"""
from __future__ import annotations

import copy
from typing import Any

# chart_advisor 词表交集；中文/英文值均识别。
# P16-LOW（review）：不 alias 单字「表」——「图表」等普通偏好文本会被宽 substring
# 误判成 table；「表格」完整词已覆盖。
_PREFERRED_TYPE_MAP: dict[str, str] = {
    "柱状图": "bar", "bar": "bar",
    "饼图": "pie", "pie": "pie",
    "表格": "table", "table": "table",
}
_VISUAL_TYPES = frozenset({"pie", "bar"})

# bar ↔ pie 的 dimensions 键归一映射：{源类型键: 目标类型键}
# 目标 bar 需 x/y（源 pie 的 category/value → x/y）；目标 pie 需 category/value（源 bar 的 x/y → category/value）
_DIMS_RENAME: dict[str, dict[str, str]] = {
    "bar": {"category": "x", "value": "y"},  # 源键 ← 改名为目标键
    "pie": {"x": "category", "y": "value"},
}


def _is_negated(text: str, index: int, window: int = 3) -> bool:
    """`text[index:]` 那个词是否被否定。

    词前 `window` 个字符里出现否定词即算否定。这是「当轮原话」与「召回的记忆偏好」
    **共用**的唯一判据——2026-11-09 之前只有原话通道有它，记忆通道漏了，
    于是「以后不要用柱状图」被当成「要柱状图」强制执行（意图反转，已复现）。
    """
    prefix = text[max(0, index - window):index]
    return any(neg in prefix for neg in _NEGATIONS)


def _parse_chart_preference(text: str) -> tuple[str | None, set[str]]:
    """解析一条偏好文本 →（正向命中的 chart type, 被否定的 chart type 集合）。

    语义定调：**否定句是“排除”，不是“要反面”**。用户说「不要柱状图」不等于「要饼图」；
    选哪个是 chart_advisor 的事，记忆只负责排除。同一条文本里可以同时出现肯定与否定
    （「以后都不要柱状图，都用饼图」）——两者都要收。
    """
    positive: str | None = None
    excluded: set[str] = set()
    for kw, chart_type in _PREFERRED_TYPE_MAP.items():
        start = text.find(kw)
        while start >= 0:
            if _is_negated(text, start):
                excluded.add(chart_type)
            elif positive is None:
                positive = chart_type
            start = text.find(kw, start + len(kw))
    return positive, excluded


def _preferred_chart_type(preferences: list[str]) -> tuple[str | None, set[str]]:
    """按召回分顺序取**第一条正向命中**，同时并集所有排除项。

    排除是约束而不是排序候选：不管它出现在第几条，都得生效。
    """
    positive: str | None = None
    excluded: set[str] = set()
    for text in preferences or []:
        if not text:
            continue
        hit, negated = _parse_chart_preference(text)
        excluded |= negated
        if positive is None and hit is not None:
            positive = hit
    return positive, excluded


def _first_not_negated(current: str, excluded: set[str]) -> str | None:
    """当前类型被排除时，退到另一个未被排除的可视化类型（bar ↔ pie）。"""
    other = "pie" if current == "bar" else "bar"
    return other if other not in excluded else None


def apply_chart_preference(chart_config: dict, preferences: list[str]) -> dict:
    """把视觉化偏好应用到 chart_advisor 产出的 chart_config（纯函数，不 mut 输入）。

    无偏好 / 偏好词表外 / 正向命中被自己否定 → 原样返回（identity）；
    当前类型被排除 → 降级到另一个可视化类型；数据不支持可视化 → 偏好让位（§七）。
    """
    if not preferences or not isinstance(chart_config, dict) or not chart_config:
        return chart_config

    target, excluded = _preferred_chart_type(preferences)
    if target is not None and target not in excluded:
        return _apply_type(chart_config, target)

    cur_type = chart_config.get("type")
    if cur_type in excluded:
        fallback = _first_not_negated(cur_type, excluded)
        if fallback is not None:
            return _apply_type(chart_config, fallback)
    return chart_config


# ── 用户本轮**原话**里的显式图表要求（2026-11-05，plan live-session-issues D4）─────────
#
# 背景：`chart_advisor` 只看 QueryResult 的列形态；`apply_chart_preference` 只吃**召回的记忆
# 偏好**。于是「按月份看，画饼图」这种**当轮明说**的要求没有任何通道，结果还是柱状图。
#
# 这里给「当轮原话」开一条确定性通道：词表与 chart_advisor 交集（pie/bar/table），
# 优先级最高（显式指令 > 记忆偏好 > advisor 默认），数据不支持可视化时仍让位。

_EXPLICIT_CHART_WORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("饼图", "饼状图"), "pie"),
    (("柱状图", "条形图", "柱形图"), "bar"),
    (("表格",), "table"),
)

# 「不要表格 / 不用柱状图」不能被读成要它——词前 3 个字里出现否定词就跳过。
_NEGATIONS = ("不", "别", "无需", "不是", "非")

_TYPE_TO_WORD: dict[str, str] = {"pie": "饼图", "bar": "柱状图", "table": "表格"}


def explicit_chart_type(text: str | None) -> str | None:
    """从用户原话里读显式图表要求（`pie` / `bar` / `table`）；没有或是否定 → None。"""
    if not text:
        return None
    for words, chart_type in _EXPLICIT_CHART_WORDS:
        for word in words:
            start = text.find(word)
            while start >= 0:
                if not _is_negated(text, start):
                    return chart_type
                start = text.find(word, start + len(word))
    return None


def apply_explicit_chart_request(chart_config: dict, user_query: str | None) -> dict:
    """按用户**当轮原话**覆盖图表类型（显式要求优先于记忆偏好）。"""
    target = explicit_chart_type(user_query)
    if target is None or not isinstance(chart_config, dict) or not chart_config:
        return chart_config
    return _apply_type(chart_config, target)


def _apply_type(chart_config: dict, target: str) -> dict:
    """把目标类型落到 chart_config 上（类型覆盖 + dimensions 键归一；不支持的形态让位）。"""
    cur_type = chart_config.get("type")
    if cur_type not in (_VISUAL_TYPES | {"table"}):
        return chart_config  # 无法识别的 chart_config 形态——防御，不碰

    out = copy.deepcopy(chart_config)

    if target == "table":
        if cur_type in _VISUAL_TYPES:
            rows = ((out.get("config") or {}).get("data") or [])
            out = {"type": "table", "config": {"data": rows}}
        return out  # 已是 table → 保持

    # target ∈ {pie, bar}
    if cur_type not in _VISUAL_TYPES:
        return chart_config  # 数据不支持可视化 → 偏好让位，保持 table（§七）
    if cur_type == target:
        return out  # 与 chart_advisor 一致 → 仅拷贝返回（含其余字段）

    # 跨类型 override：type + dimensions 键归一
    out["type"] = target
    config = out.get("config")
    if isinstance(config, dict):
        dims = config.get("dimensions")
        if isinstance(dims, dict):
            rename = _DIMS_RENAME[target]  # 目标类型所需键 ← 源类型键
            config["dimensions"] = {
                rename.get(k, k): v for k, v in dims.items()
            }
    return out
