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


def _preferred_chart_type(preferences: list[str]) -> str | None:
    """从 preference 文本解析首选 chart type（词表外 → None）。"""
    for text in preferences:
        if not text:
            continue
        for kw, chart_type in _PREFERRED_TYPE_MAP.items():
            if kw in text:
                return chart_type
    return None


def apply_chart_preference(chart_config: dict, preferences: list[str]) -> dict:
    """把视觉化偏好应用到 chart_advisor 产出的 chart_config（纯函数，不 mut 输入）。

    无偏好 / 偏好词表外 → 原样返回（identity）。
    """
    target = _preferred_chart_type(preferences or [])
    if target is None or not isinstance(chart_config, dict) or not chart_config:
        return chart_config

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
        return out  # 偏好与 chart_advisor 一致 → 仅拷贝返回（含其余字段）

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
