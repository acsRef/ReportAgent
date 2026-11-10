"""语义归并「护栏版」判定器的离线评估（plan `2026-11-10-semantic-memory-merge`）。

背景（先测后设计）：`evaluation/semantic_merge_probe.py` 在真 embedding 上量出三类分布的**重叠**：

    同义说法   0.695 ~ 0.933（中位 0.881）
    不同的事   0.642 ~ 0.950（中位 0.809）  ← 华东 vs 华南 高达 0.950
    相反说法   0.906 ~ 0.962（中位 0.933）  ← 永远比多数同义说法还高

所以**单靠余弦阈值必错**：θ=0.90 时有 7 个错误合并、8 个错误拆分。本脚本评估的是替代方案——
**结构优先**：先抽「原子值」（图形类型 / 时间粒度 / 指标 / 区域 / 数字）和极性，
结构一致才合并，结构冲突一律分开；embedding 只用于「两边都没抽到原子值」时的灰区提示，
**且灰区不自动合并**。

用法（先跑 probe 生成 JSON，再跑本脚本；不需要 embedding）：

    python evaluation/semantic_merge_probe.py --json evaluation/results/semantic_merge_probe_20261110.json
    python evaluation/semantic_merge_guard_eval.py evaluation/results/semantic_merge_probe_20261110.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- 原子值抽取
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
METRIC_WORDS: dict[str, str] = {
    "净销售额": "net_sales", "实收金额": "received", "退款金额": "refund_amount",
    "销售额": "sales", "客单价": "aov", "退款率": "refund_rate",
    "订单数": "orders", "销量": "units",
}
# 注意顺序：长词优先匹配，否则「净销售额」会被「销售额」抢先命中
_ATOM_KINDS: list[tuple[str, dict[str, str]]] = [
    ("metric", METRIC_WORDS),
    ("chart", CHART_WORDS),
    ("granularity", GRANULARITY_WORDS),
    ("region", REGION_WORDS),
]

NEGATIONS = ("不", "别", "无需", "不是", "非", "停止")


def polarity(text: str) -> bool:
    """True = 含否定词（极性为负）。"""
    return any(neg in (text or "") for neg in NEGATIONS)


def atoms(text: str) -> dict[str, set[str]]:
    """抽原子值：{kind: {value}}。同一 kind 里出现多个值也算集合。"""
    out: dict[str, set[str]] = {}
    body = text or ""
    for kind, words in _ATOM_KINDS:
        for word, value in sorted(words.items(), key=lambda kv: len(kv[0]), reverse=True):
            if word in body:
                out.setdefault(kind, set()).add(value)
    return out


def numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text or ""))


def decide(a: str, b: str, *, similarity: float | None = None) -> tuple[str, str]:
    """判定两条记忆的关系：`merge` / `distinct` / `review`。

    规则顺序（结构优先，语义只做灰区提示）：

    1. 极性不同 → `distinct`（「用柱状图」vs「不要柱状图」字面极像但语义相反）；
    2. 数字集合不同 → `distinct`（数值是硬事实，不许归一）；
    3. 任一 kind 两边都有值但**取值不同** → `distinct`（华东/华南、按周/按天、柱状图/饼图）；
    4. 两边都抽到原子值且**全部一致** → `merge`（同义说法，不依赖余弦）；
    5. 其余：余弦 ≥ 0.90 → `review`（只提示，不自动合并）；否则 `distinct`。
    """
    if polarity(a) != polarity(b):
        return "distinct", "polarity"
    if numbers(a) != numbers(b) and (numbers(a) or numbers(b)):
        return "distinct", "numbers"
    aa, ab = atoms(a), atoms(b)
    for kind in set(aa) & set(ab):
        if aa[kind] != ab[kind]:
            return "distinct", f"atom-conflict:{kind}"
    structural = bool(aa) and set(aa) == set(ab) and all(aa[k] == ab[k] for k in aa)
    if structural:
        return "merge", "atoms-equal"
    if similarity is not None and similarity >= 0.90:
        return "review", "gray-zone"
    return "distinct", "no-structure"


def main(argv: list[str]) -> int:
    path = Path(argv[1])
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["rows"]

    print(f"{'sim':>6}  {'标签':<9} {'判定':<9} {'理由':<22} pair")
    counts = {"merge": 0, "distinct": 0, "review": 0}
    hits = {"merge": 0, "distinct": 0, "review": 0}
    for row in rows:
        decision, reason = decide(row["a"], row["b"], similarity=row["similarity"])
        counts[decision] += 1
        if decision == row["label"]:
            hits[decision] += 1
        print(
            f"  {row['similarity']:>6.3f}  {row['label']:<9} {decision:<9} {reason:<22} "
            f"{row['a']}   |   {row['b']}"
        )

    # 以「merge」为正类算精确率/召回率；复审(review)算“不下结论”，既不误也漏
    tp = sum(1 for r in rows if decide(r["a"], r["b"], similarity=r["similarity"])[0] == "merge"
             and r["label"] == "merge")
    fp = sum(1 for r in rows if decide(r["a"], r["b"], similarity=r["similarity"])[0] == "merge"
             and r["label"] != "merge")
    fn = sum(1 for r in rows if decide(r["a"], r["b"], similarity=r["similarity"])[0] != "merge"
             and r["label"] == "merge")
    review = sum(1 for r in rows if decide(r["a"], r["b"], similarity=r["similarity"])[0] == "review")
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    print("\n== 结构优先判定器 ==")
    print(f"  错误合并(FP) = {fp}    漏并(FN) = {fn}    送复审 = {review}")
    print(f"  精确率 = {precision:.2f}    召回率 = {recall:.2f}")
    print("  口径：以「merge」为正类；复审 = 不自动合并，所以不计入 FP。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
