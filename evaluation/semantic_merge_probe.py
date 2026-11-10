"""语义归并阈值探针（plan `2026-11-10-semantic-memory-merge` 前置测量）。

要回答的问题：**同一件事换个说法**，余弦相似度到底落在什么区间？而**不同的事**、
**相反的说法**又落在哪？只有先量出这三类分布的重叠程度，才谈得上定阈值——
否则「语义归并」就是拿一个拍脑袋的 0.85 去覆盖用户的记忆。

用法（repo root，需要 SILICONFLOW_API_KEY）：

    D:/miniConda/envs/agent/python.exe evaluation/semantic_merge_probe.py
    ... --json out.json          # 落盘完整结果

输出：每类（must-merge / must-not-merge / opposite）的 min/median/max、两两重叠情况，
以及在候选阈值下的「错误合并 / 错误拆分」枚数。退出码恒 0（这是测量，不是门禁）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load_root_env() -> None:
    dotenv_path = _ROOT / ".env"
    if not dotenv_path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path, override=False)
    except ImportError:  # pragma: no cover
        pass


_load_root_env()

# ---------------------------------------------------------------------------
# 标注集：每一对都是「产品里真实会出现的两种说法」。
#   merge    = 同一件事换说法（应该归到一个槽位 / 累加证据）
#   distinct = 不同的事（必须分开）
#   opposite = 相反的说法（**最危险**：字面几乎一样，语义完全相反）
# 组内还标了 `bucket`，方便看「图表 / 粒度 / 口径 / 事实」各桶的分离度。
# ---------------------------------------------------------------------------
PAIRS: list[dict] = [
    # --- 图表偏好：同一件事，四种说法 -------------------------------------
    {"label": "merge", "bucket": "chart", "a": "以后都用柱状图显示", "b": "以后报告默认给柱状图"},
    {"label": "merge", "bucket": "chart", "a": "以后都用柱状图显示", "b": "图表默认用柱形图"},
    {"label": "merge", "bucket": "chart", "a": "以后都用柱状图显示", "b": "报告里用条形图就行"},
    {"label": "merge", "bucket": "chart", "a": "以后都用饼图", "b": "以后报告用饼状图"},
    {"label": "merge", "bucket": "chart", "a": "以后都用表格", "b": "以后报告直接给表格吧"},
    # --- 粒度偏好 -----------------------------------------------------------
    {"label": "merge", "bucket": "granularity", "a": "以后按月的粒度来显示", "b": "以后每个月看一次"},
    {"label": "merge", "bucket": "granularity", "a": "以后按周看", "b": "默认每周汇总一次"},
    {"label": "merge", "bucket": "granularity", "a": "以后按季度看", "b": "默认按季汇总"},
    # --- 口径/业务定义 ------------------------------------------------------
    {"label": "merge", "bucket": "fact", "a": "GMV 按净销售额算", "b": "GMV 就是扣掉退款之后的销售额"},
    {"label": "merge", "bucket": "fact", "a": "净销售额是扣退款后的口径", "b": "净销售额等于销售额减掉退款"},
    {"label": "merge", "bucket": "fact", "a": "退货率按订单数算", "b": "退货率的分母用订单数量"},
    {"label": "merge", "bucket": "fact", "a": "华南包含福建", "b": "福建算在华南区域里"},
    # --- 不同维度：必须分开 -------------------------------------------------
    {"label": "distinct", "bucket": "dimension", "a": "华东区域的销售额怎么算", "b": "华南区域的销售额怎么算"},
    {"label": "distinct", "bucket": "dimension", "a": "以后按周看", "b": "以后按天看"},
    {"label": "distinct", "bucket": "chart", "a": "以后都用柱状图", "b": "以后都用饼图"},
    {"label": "distinct", "bucket": "chart", "a": "以后都用表格", "b": "以后都用柱状图"},
    # --- 不同指标：必须分开 -------------------------------------------------
    {"label": "distinct", "bucket": "metric", "a": "净销售额是扣退款后的口径", "b": "实收金额是实际到账的口径"},
    {"label": "distinct", "bucket": "metric", "a": "客单价怎么算", "b": "退款率怎么算"},
    {"label": "distinct", "bucket": "metric", "a": "销量按件数计算", "b": "销售额按金额计算"},
    # --- 跨桶：相似但不同类 ------------------------------------------------
    {"label": "distinct", "bucket": "cross", "a": "以后按月的粒度来显示", "b": "以后都用柱状图显示"},
    {"label": "distinct", "bucket": "cross", "a": "以后报告都用表格", "b": "以后报告按季度看"},
    # --- 相反说法：字面极像，语义相反（归并的致命误判源）-------------------
    {"label": "opposite", "bucket": "chart", "a": "以后都用柱状图", "b": "以后不要用柱状图"},
    {"label": "opposite", "bucket": "chart", "a": "以后报告都用表格", "b": "以后报告别用表格"},
    {"label": "opposite", "bucket": "chart", "a": "以后都用饼图", "b": "以后别用饼图"},
    {"label": "opposite", "bucket": "granularity", "a": "以后按月看", "b": "以后不要按月看"},
    {"label": "opposite", "bucket": "fact", "a": "净销售额包含退款", "b": "净销售额不包含退款"},
]

THRESHOLDS = [0.80, 0.84, 0.86, 0.88, 0.90, 0.92, 0.94]


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def _embed_all(texts: list[str]) -> dict[str, list[float]]:
    from app.embedding.service import get_embedder

    svc = get_embedder()
    out: dict[str, list[float]] = {}
    for text in texts:
        vec = await svc.embed_or_none(text)
        if vec is None:
            raise SystemExit(f"embedding 失败：{text!r}（检查 SILICONFLOW_API_KEY / 网络）")
        out[text] = vec
    return out


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    mid = len(ordered) // 2
    median = (
        ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    )
    return {
        "n": len(ordered),
        "min": round(ordered[0], 4),
        "median": round(median, 4),
        "max": round(ordered[-1], 4),
    }


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="语义归并阈值探针")
    ap.add_argument("--json", type=Path, default=None, help="把完整结果写成 JSON")
    args = ap.parse_args(argv)

    texts = sorted({p["a"] for p in PAIRS} | {p["b"] for p in PAIRS})
    print(f"句子 {len(texts)} 句 / 标注对 {len(PAIRS)} 对，真实 embedding 计算中…")
    t0 = time.time()
    vectors = await _embed_all(texts)
    print(f"完成（{time.time() - t0:.1f}s）\n")

    rows: list[dict] = []
    for pair in PAIRS:
        sim = _cos(vectors[pair["a"]], vectors[pair["b"]])
        rows.append({**pair, "similarity": round(sim, 4)})

    by_label: dict[str, list[float]] = {}
    for row in rows:
        by_label.setdefault(row["label"], []).append(row["similarity"])

    print("== 三类相似度分布 ==")
    for label in ("merge", "distinct", "opposite"):
        print(f"  {label:<9} {_stats(by_label.get(label, []))}")

    print("\n== 每个标注对（按相似度降序）==")
    print(f"  {'sim':>6}  {'label':<9} {'bucket':<12} pair")
    for row in sorted(rows, key=lambda r: r["similarity"], reverse=True):
        print(
            f"  {row['similarity']:>6.3f}  {row['label']:<9} {row['bucket']:<12} "
            f"{row['a']}   |   {row['b']}"
        )

    # 阈值扫描：把「相似度 ≥ θ」当作判定归并
    print("\n== 阈值扫描（θ 以上判归并）==")
    print(f"  {'θ':>5}  {'错误合并':>8}  {'错误拆分':>8}  {'合并通过':>8}  说明")
    sweep: list[dict] = []
    for theta in THRESHOLDS:
        false_merge = [r for r in rows if r["label"] != "merge" and r["similarity"] >= theta]
        false_split = [r for r in rows if r["label"] == "merge" and r["similarity"] < theta]
        merges = [r for r in rows if r["label"] == "merge"]
        sweep.append({
            "theta": theta,
            "false_merge": len(false_merge),
            "false_split": len(false_split),
            "merge_pass": len(merges) - len(false_split),
            "false_merge_pairs": [f"{r['label']}: {r['a']} | {r['b']}" for r in false_merge],
        })
        note = ""
        if false_merge:
            kinds = sorted({r["label"] for r in false_merge})
            note = "含 " + "/".join(kinds)
        print(
            f"  {theta:>5.2f}  {len(false_merge):>8}  {len(false_split):>8}  "
            f"{len(merges) - len(false_split):>8}  {note}"
        )

    opp = sorted(by_label.get("opposite", []))
    mer = sorted(by_label.get("merge", []))
    print("\n== 结论提示 ==")
    if opp and mer:
        print(
            f"  相反说法的相似度 {opp[0]:.3f}~{opp[-1]:.3f}，同义说法 {mer[0]:.3f}~{mer[-1]:.3f}"
        )
        if opp[-1] > mer[0]:
            print(
                "  → 两者**重叠**：单靠余弦阈值一定会在某些句子对上报错，"
                "必须叠加字面护栏（否定词 / 数字 / 实体不匹配就拒绝归并）。"
            )
        else:
            print("  → 两类不重叠，单阈值可行。")

    if args.json:
        args.json.write_text(
            json.dumps(
                {"rows": rows, "stats": {k: _stats(v) for k, v in by_label.items()},
                 "sweep": sweep},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n结果已写入 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
