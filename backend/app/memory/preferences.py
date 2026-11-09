"""偏好维度（§六 lifecycle：新旧偏好冲突时旧 → superseded）。

问题：`stable_preference` 只有自由文本，没有维度标签。于是「以后都用柱状图」和
「以后都用饼图」是两条互不相同的 content，同内容 supersede 覆盖不到它们 →
**两条 active 同时生效**，召回时给出互相矛盾的偏好（2026-11-09 契约测试 C4 实测）。

V1 的折中（plan memory-system-conformance D3）：用一个**确定性词表**把偏好归到「维度」，
只 supersede **同维度**的旧条目。为什么不是全量 supersede：用户说「以后都用饼图」时，
「每月看」这条粒度偏好不该被顶掉——它们不冲突。

为什么不给 stable_preference 加 `dimension` 字段：
- 要改 `semantic_entry` DDL + 写入契约 + 记忆写入的 LLM 抽取，属于设计变更；
- 词表是目前唯一能**确定性**（可测、可解释、无模型彩票）落地的办法。

局限（诚实记账）：词表覆盖不到新说法（「要那种带柱子的图」）时 dimension 返回 None，
退化成旧行为（同内容 supersede）。扩充词表时请同时补 `preferences` 的测试。
"""
from __future__ import annotations

import re

# 维度 → 关键词家族。顺序无依赖；命中多个维度时取**第一个命中**（互斥语义）。
_DIMENSION_KEYWORDS: dict[str, tuple[str, ...]] = {
    # 呈现形态：报告里「用什么图 / 用不用表」
    "chart": (
        "柱状图", "条形图", "折线图", "曲线图", "饼图", "环形图", "面积图",
        "散点图", "雷达图", "热力图", "表格", "图表", "可视化", "柱图", "饼状图",
    ),
    # 时间聚合：报告按什么粒度切
    "granularity": (
        "按天", "按日", "每天", "按周", "每周", "按月", "每月", "按季度", "按季",
        "每季度", "按年", "每年", "日粒度", "周粒度", "月粒度", "季度粒度", "年粒度",
    ),
}


def preference_dimension(content: str) -> str | None:
    """把一条偏好正文归到维度；归不到返回 None（沿用旧的同内容 supersede）。"""
    text = (content or "").lower()
    if not text:
        return None
    for dimension, keywords in _DIMENSION_KEYWORDS.items():
        if any(k in text for k in keywords):
            return dimension
    return None


def conflicting_contents(
    new_content: str, active_contents: list[str]
) -> list[str]:
    """在同维度里找出「被新偏好取代」的旧正文。

    规则：同维度、且不是同一条正文 → 旧条目变 superseded。
    同一条正文由 `supersede_stable_preferences`（同内容）负责，不在这里重复。

    跨维度的「复合偏好」（「以后按月看柱状图」）会同时命中 chart 与 granularity 的
    第一命中——这里只用第一命中，避免一句话把两个维度都清掉；退货见模块 docstring。
    """
    dimension = preference_dimension(new_content)
    if dimension is None:
        return []
    out: list[str] = []
    for content in active_contents:
        if content == new_content:
            continue
        if preference_dimension(content) == dimension:
            out.append(content)
    return out


# 供测试/文档用的正则：确认「维度」概念不会被误当成自由文本匹配
_DIMENSION_RE = re.compile("|".join(sorted(_DIMENSION_KEYWORDS, key=len, reverse=True)))
