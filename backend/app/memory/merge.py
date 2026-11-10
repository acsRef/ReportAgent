"""记忆归并判定：**结构优先，embedding 只做灰区提示**。

设计依据是 `evaluation/semantic_merge_probe.py` 的真 embedding 测量（26 对标注 / 40 句）：

| 类别 | 相似度区间 | 中位 |
|---|---|---|
| 同义说法（应合并） | 0.695 ~ 0.933 | 0.881 |
| 不同的事实（必须分开） | 0.642 ~ **0.950** | 0.809 |
| 相反说法 | **0.906 ~ 0.962** | 0.933 |

类间**重叠**：`华东 vs 华南` 0.950 高过 10/12 条真正的同义说法；
`都用表格 vs 别用表格` 0.962 —— 余弦对否定完全无感。
所以「按相似度阈值自动合并」= 静默覆盖用户记忆，本项目**不做**。

替代方案（本模块）：抽出「原子值」（图形类型 / 时间粒度 / 区域 / 业务指标 / 数字）与极性，
结构一致才归并，结构冲突一律分开；没有结构信号时才把余弦 ≥ 0.90 的记为**待复审**，
既不自动合并，也不当作不同事实处理。

实测（同一批 26 对，见 `evaluation/semantic_merge_guard_eval.py`）：错误合并 0 / 精确率 1.00 / 召回 0.92。
唯一漏并的是「GMV 按净销售额算」vs「GMV 就是扣掉退款之后的销售额」——后者没用术语，
属于**业务词表缺口**（`metrics.yaml` 的 `synonyms` 里补一条即可），不是判定器的问题。
"""
from __future__ import annotations

import re

# 否定词（与图表应用层共用同一份真源，避免「A 通道有防护、B 通道漏了」）
NEGATION_WORDS: tuple[str, ...] = ("不", "别", "无需", "不是", "非", "停止")

CHART_WORDS: dict[str, str] = {
    "柱状图": "bar", "柱形图": "bar", "条形图": "bar", "柱图": "bar",
    "饼图": "pie", "饼状图": "pie",
    "折线图": "line", "曲线图": "line",
    "表格": "table",
}
GRANULARITY_WORDS: dict[str, str] = {
    "按天": "day", "按日": "day", "每天": "day", "每日": "day", "每一天": "day",
    "按周": "week", "每周": "week", "每个星期": "week", "每星期": "week",
    "按月": "month", "每月": "month", "每个月": "month", "每月份": "month",
    "按季度": "quarter", "按季": "quarter", "每季": "quarter", "每季度": "quarter",
    "每个季度": "quarter",
    "按年": "year", "每年": "year", "每一年": "year",
}
REGION_WORDS: dict[str, str] = {
    "华东": "east", "华南": "south", "华北": "north", "华中": "central",
    "西南": "southwest", "西北": "northwest", "东北": "northeast",
}

# 灰区阈值：只用于「没有结构信号时是否值得记一笔复审」，**从不用于自动合并**
GRAY_ZONE_THRESHOLD = 0.90


def polarity(text: str) -> bool:
    """True = 含否定词（极性为负）。"""
    return any(neg in (text or "") for neg in NEGATION_WORDS)


def _match_words(text: str, words: dict[str, str]) -> set[str]:
    """长词优先匹配（否则「净销售额」会被「销售额」抢先命中）。"""
    out: set[str] = set()
    for word, value in sorted(words.items(), key=lambda kv: len(kv[0]), reverse=True):
        if word in text:
            out.add(value)
    return out


def atoms(text: str) -> dict[str, set[str]]:
    """抽原子值：`{kind: {value}}`。

    业务指标直接读 `app.knowledge.registry`（`metrics.yaml` 的 `name` + `synonyms`），
    不再维护第二份指标词表——**同义词的真源是业务词表，不是相似度阈值**。
    """
    body = text or ""
    out: dict[str, set[str]] = {}
    for kind, words in (
        ("chart", CHART_WORDS),
        ("granularity", GRANULARITY_WORDS),
        ("region", REGION_WORDS),
    ):
        values = _match_words(body, words)
        if values:
            out[kind] = values
    try:
        from app.knowledge import registry as metric_registry

        mentioned = {m.name for m in metric_registry.metrics_mentioned_in(body)}
    except Exception:  # noqa: BLE001 - 词表加载失败不该让归并判定崩掉
        mentioned = set()
    if mentioned:
        out["metric"] = mentioned
    return out


def numbers(text: str) -> set[str]:
    """文本里的数字（数值是硬事实，归一化时不许动）。"""
    return set(re.findall(r"\d+(?:\.\d+)?", text or ""))


def decide(
    a: str, b: str, *, similarity: float | None = None
) -> tuple[str, str]:
    """判定两条记忆的关系 → `(decision, reason)`。

    `decision ∈ {merge, distinct, review}`：

    1. `merge`：同一件事的两种说法 → 可累加证据；
    2. `distinct`：不同的事 / 相反的说法 / 数值不同 → 绝不合并；
    3. `review`：没有结构信号但余弦 ≥ 0.90 → 只记待复审，不自动合并。
    """
    if polarity(a) != polarity(b):
        return "distinct", "polarity"
    if numbers(a) != numbers(b) and (numbers(a) or numbers(b)):
        return "distinct", "numbers"

    aa, ab = atoms(a), atoms(b)
    for kind in sorted(set(aa) & set(ab)):
        if aa[kind] != ab[kind]:
            return "distinct", f"atom-conflict:{kind}"

    structural = bool(aa) and set(aa) == set(ab) and all(aa[k] == ab[k] for k in aa)
    if structural:
        return "merge", "atoms-equal"
    if similarity is not None and similarity >= GRAY_ZONE_THRESHOLD:
        return "review", "gray-zone"
    return "distinct", "no-structure"
