"""P16 G3 + 2026-11-09 L1/L4：Memory 召回质量与稳定性 scoreboard。

在**真 embedding（SiliconFlow）**下复用 `evaluation/memory_gold_cases.json`（25 记忆 +
30 金标 queries）。与 pytest 版（词位化 fake embedding，
backend/tests/persistence/test_memory_gold_recall.py）分工不变：
- pytest = 链路正确性硬断言（CI/本地全量可跑，零外部依赖）；
- 本 runner = 真实语义空间下的**质量 + 稳定性**判决（手动门，不进 CI）。

## 三层判据（plan: 2026-11-09-memory-recall-scoreboard）

**L1 正确性（召回对不对）**：Recall@1 / Recall@3 / MRR / Clean。
按三个维度**分层**报，因为聚合数会藏起崩塌的那一类：
- `agent`：三档 policy 不同（Execution 档还 drop preference）；
- gold 记忆类型：`preference` / `fact` / `query`——「偏好记得住、业务事实记不住」是另一种缺陷；
- `memory_scope`：`semantic` / `query` 两套 domain view，要能分开问责。

**L4 可靠性（稳不稳）**：`--repeat N`，且**每轮清空 embedding 缓存**（不清就是在测 LRU）。
- `spread_recall1`：各轮 Recall@1 极差；
- `stable_top1`：top-1 命中与否跨轮完全一致的 case 占比；
- `mean_jaccard`：每 case 跨轮召回文本集合的两两 Jaccard（只看 top-1 会漏「集合在漂」）。

**L4 降级（降级名不副实吗）**：`--degrade` 强制 embedding 失败 → keyword 分支。
- 硬断言：不抛异常，且**观测到** `app.embedding.service` 的 fallback 警告（静默降级不算可靠）；
- 观测项：`any_items_rate` / `recall_at_3`——降级后到底还剩下多少能力。

用法（repo root，手动门）：
    D:/miniConda/envs/agent/python.exe evaluation/gold_memory_runner.py \
        --repeat 3 --degrade --html docs/reports/memory-scoreboard.html

env 需求：DATABASE_URL（缺省 localhost ragent/ragent）+ SILICONFLOW_API_KEY。
输出：终端 gate 表 + `evaluation/results/memory_gold_<ts>.json` 快照 + 可选自包含 HTML。
退出码：任一 gate 不过 → 2（本地判决用；不进 CI，所以不怕抖动卡流水线）。
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import logging
import os
import sys
import time
import uuid
from itertools import combinations
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "backend"))

_DATASET = _ROOT / "evaluation" / "memory_gold_cases.json"
_RESULTS_DIR = _ROOT / "evaluation" / "results"
_DEFAULT_HTML = _ROOT / "docs" / "reports" / "memory-scoreboard.html"


def _load_root_env() -> None:
    """手动跑时 shell 未必 export .env——从 repo 根 .env 补载（不覆盖已设环境变量）。"""
    dotenv_path = _ROOT / ".env"
    if not dotenv_path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path, override=False)
    except ImportError:
        for line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


_load_root_env()

_DS = json.loads(_DATASET.read_text(encoding="utf-8"))
_MEM = _DS["memories"]

# gate 阈值：以 2026-09-08 快照（Recall@1 0.84 / @3 0.96 / MRR 0.90 / Clean 1.0）为基线，
# 留出「一轮小幅波动不算退化」的余量。改阈值必须写清楚理由，否则门槛会慢慢变成橡皮筋。
DEFAULT_GATES: dict[str, float] = {
    "min_recall1": 0.80,
    "min_recall3": 0.95,
    "min_mrr": 0.85,
    "min_clean": 1.00,
    "max_spread": 0.10,
    "min_stable_top1": 0.90,
    "min_jaccard": 0.90,
}


def _fail(msg: str, code: int = 2):
    print(f"[gold_memory_runner] {msg}")
    sys.exit(code)


# ---------------------------------------------------------------- db plumbing


def _conn():
    import psycopg2

    return psycopg2.connect(
        os.getenv("DATABASE_URL", "postgresql://ragent:ragent@localhost:5432/ragent")
    )


def _make_user() -> int:
    conn = _conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO app.users (username, password_hash) VALUES (%s, 'x') RETURNING id",
        (f"goldrun-{uuid.uuid4().hex[:10]}",),
    )
    uid = cur.fetchone()[0]
    conn.close()
    return uid


def _cleanup(user_id: int):
    conn = _conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DELETE FROM memory.query_template WHERE user_id=%s", (user_id,))
    cur.execute("DELETE FROM memory.semantic_entry WHERE user_id=%s", (user_id,))
    cur.execute("DELETE FROM app.users WHERE id=%s", (user_id,))
    conn.close()


def _reset_access(user_id: int):
    """清零 LRU/LFU 副作用——排序纯由语义 ×0.6 + importance ×0.2 决定，可复现。"""
    conn = _conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "UPDATE memory.semantic_entry SET access_count=0, last_access_time=NULL "
        "WHERE user_id=%s",
        (user_id,),
    )
    cur.execute(
        "UPDATE memory.query_template SET access_count=0, last_used_at=NULL "
        "WHERE user_id=%s",
        (user_id,),
    )
    conn.close()


def _clear_embed_cache() -> None:
    from app.embedding.service import get_embedder

    get_embedder().clear_cache()


def _verify_seed(user_id: int) -> dict:
    """确认 25 条记忆都真的写进来了、且**都带上了向量**。

    为什么必须查：写入路径是 `embed_or_none`，embedding 超时/限流时它会降级成
    「写进去但没有向量」——那种行永远不会被语义召回命中，但指标上看起来就是
    「召回不行」。不查这一下，scoreboard 会安安静静地把环境问题报成设计问题。
    """
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT count(*), count(intent_embedding) FROM memory.semantic_entry "
        "WHERE user_id=%s AND status='active'",
        (user_id,),
    )
    rows, with_vec = cur.fetchone()
    cur.execute("SELECT count(*) FROM memory.query_template WHERE user_id=%s", (user_id,))
    queries = cur.fetchone()[0]
    conn.close()

    expected_semantic = sum(1 for m in _MEM.values() if m["type"] != "query")
    expected_query = sum(1 for m in _MEM.values() if m["type"] == "query")
    problems: list[str] = []
    if rows != expected_semantic:
        problems.append(f"semantic_entry 行数 {rows} != 期望 {expected_semantic}")
    if with_vec != rows:
        problems.append(
            f"semantic_entry 缺向量 {rows - with_vec} 行（embedding 超时/限流会被静默降级）"
        )
    if queries != expected_query:
        problems.append(f"query_template 行数 {queries} != 期望 {expected_query}")
    if problems:
        _fail("种子数据不可信，指标会失真：" + "；".join(problems))
    return {"semantic_rows": rows, "with_embedding": with_vec, "queries": queries}


# --------------------------------------------------------------------- recall


def _recall(case: dict, user_id: int) -> list[dict]:
    """按 case.memory_scope 调 domain view（与 ContextRuntime Step 4 同层、无 kind 重排）。"""

    async def _go() -> list[dict]:
        from app.infra.db.postgres import init_pool

        await init_pool()
        try:
            items: list[dict] = []
            from app.memory import query as query_view
            from app.memory import semantic as semantic_view

            if "semantic" in case["memory_scope"]:
                items.extend(
                    await semantic_view.recall_structured(
                        case["query"], str(user_id), top_k_preferences=3,
                    )
                )
            if "query" in case["memory_scope"]:
                items.extend(
                    await query_view.recall_structured(
                        case["query"], str(user_id), top_k_queries=2,
                    )
                )
            return items
        finally:
            from app.infra.db.postgres import close_pool

            await close_pool()

    return asyncio.run(_go())


def _gold_text(key: str) -> str:
    m = _MEM[key]
    return m.get("content") or m["question"]


def _score_case(case: dict, items: list[dict]) -> dict:
    """一个 case 的名次信息：每个金标各自的名次 + forbidden 命中。"""
    texts = [it["raw_text"] for it in items]
    joined = " ".join(texts)

    rank_by_key: dict[str, int | None] = {}
    for key in case["gold"]:
        needle = _gold_text(key)
        rank_by_key[key] = next(
            (rank for rank, t in enumerate(texts, start=1) if needle in t), None
        )
    ranks = [r for r in rank_by_key.values() if r is not None]
    first_rank = min(ranks) if ranks else None
    forbidden_hits = [k for k in case["forbidden"] if _gold_text(k) in joined]
    return {
        "id": case["id"],
        "query": case["query"],
        "agent": case["agent"],
        "scope": "+".join(case["memory_scope"]) or "none",
        "gold": case["gold"],
        "gold_rank_by_key": rank_by_key,
        "gold_rank_by_type": {
            _MEM[k]["type"]: rank_by_key[k] for k in case["gold"]
        },
        "gold_ranks": ranks,
        "recall_at_1": bool(first_rank == 1),
        "recall_at_3": bool(first_rank is not None and first_rank <= 3),
        "mrr": 1.0 / first_rank if first_rank else 0.0,
        "forbidden_hits": forbidden_hits,
        "texts": texts,
    }


def _run_pass(user_id: int, cases: list[dict], *, clear_cache: bool) -> list[dict]:
    if clear_cache:
        _clear_embed_cache()
    rows: list[dict] = []
    for case in cases:
        _reset_access(user_id)
        rows.append(_score_case(case, _recall(case, user_id)))
    return rows


# ------------------------------------------------------------------ aggregate


def _case_metrics(rows: list[dict], cases: list[dict]) -> dict:
    n = len(rows)
    forbidden_total = sum(len(r["forbidden_hits"]) for r in rows)
    forbidden_checks = sum(len(c["forbidden"]) for c in cases if c["forbidden"])
    return {
        "cases": n,
        "recall_at_1": sum(r["recall_at_1"] for r in rows) / n,
        "recall_at_3": sum(r["recall_at_3"] for r in rows) / n,
        "mrr": sum(r["mrr"] for r in rows) / n,
        "clean": 1.0 - (forbidden_total / forbidden_checks) if forbidden_checks else 1.0,
        "forbidden_hits": forbidden_total,
        "forbidden_checks": forbidden_checks,
    }


def _strata(passes: list[list[dict]], cases: list[dict]) -> dict:
    """同一份召回、三种切法（不多花 API 钱）。

    - agent / scope：按 case 分组，指标取各轮均值；
    - gold 类型：按**金标 key** 聚合（一个 case 可同时含偏好与事实两种金标，
      按 case 猜类型必然错），每个 key 的名次独立计入。
    """
    by_agent: dict[str, list[dict]] = {}
    by_scope: dict[str, list[dict]] = {}
    by_type: dict[str, list[int | None]] = {}
    case_by_id = {c["id"]: c for c in cases}

    for rows in passes:
        for row in rows:
            case = case_by_id[row["id"]]
            by_agent.setdefault(case["agent"], []).append(row)
            by_scope.setdefault(row["scope"], []).append(row)
            for key, rank in row["gold_rank_by_key"].items():
                by_type.setdefault(_MEM[key]["type"], []).append(rank)

    def _case_buckets(bucket: dict[str, list[dict]], name: str) -> list[dict]:
        out = []
        for k, rows in sorted(bucket.items()):
            out.append({
                "dimension": name,
                "bucket": k,
                "samples": len(rows),
                "recall_at_3": sum(1 for r in rows if r["recall_at_3"]) / len(rows),
                "mrr": sum(r["mrr"] for r in rows) / len(rows),
            })
        return out

    type_rows = []
    for k, ranks in sorted(by_type.items()):
        hits = [r for r in ranks if r is not None]
        type_rows.append({
            "dimension": "gold_type",
            "bucket": k,
            "samples": len(ranks),
            "recall_at_5": len(hits) / len(ranks),
            "mrr": sum(1.0 / r for r in hits) / len(ranks),
        })

    return {
        "rows": _case_buckets(by_agent, "agent")
        + _case_buckets(by_scope, "scope")
        + type_rows,
    }


def _stability(passes: list[list[dict]]) -> dict:
    """跨轮一致性：top-1 稳不稳、集合漂不漂。"""
    n = len(passes)
    if n < 2:
        return {"repeats": n, "mean_jaccard": None, "stable_top1": None, "per_case": []}

    ids = [r["id"] for r in passes[0]]
    per_case = []
    for idx, cid in enumerate(ids):
        sets = [set(p[idx]["texts"]) for p in passes]
        pairs = list(combinations(sets, 2))
        jac = []
        for a, b in pairs:
            union = a | b
            jac.append(len(a & b) / len(union) if union else 1.0)
        top1_flags = {p[idx]["recall_at_1"] for p in passes}
        per_case.append({
            "id": cid,
            "jaccard": sum(jac) / len(jac) if jac else 1.0,
            "stable_top1": len(top1_flags) == 1,
            "sizes": [len(s) for s in sets],
        })
    return {
        "repeats": n,
        "mean_jaccard": sum(c["jaccard"] for c in per_case) / len(per_case),
        "stable_top1": sum(c["stable_top1"] for c in per_case) / len(per_case),
        "per_case": per_case,
    }


# ------------------------------------------------------- degradation probe


class _WarningCounter(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.count = 0
        self.samples: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102 - Handler contract
        msg = record.getMessage()
        if "falling back to keyword search" in msg:
            self.count += 1
            if len(self.samples) < 3:
                self.samples.append(msg[:160])


def _degrade_probe(user_id: int, cases: list[dict]) -> dict:
    """强制 embedding 失败 → 走 keyword 分支，量出「降级」到底还剩多少能力。"""
    from app.embedding.service import get_embedder

    svc = get_embedder()
    original = svc.embed
    counter = _WarningCounter()
    target_logger = logging.getLogger("app.embedding.service")
    target_logger.addHandler(counter)

    async def _boom(text: str):
        raise RuntimeError("scoreboard probe: forced embedding failure")

    svc.embed = _boom  # 实例属性遮蔽绑定方法，embed_or_none 内部 self.embed 拿到它
    error: str | None = None
    try:
        rows = _run_pass(user_id, cases, clear_cache=False)
    except Exception as exc:  # noqa: BLE001 - 探针本身要如实报告异常
        rows = []
        error = f"{type(exc).__name__}: {exc}"
    finally:
        svc.embed = original
        target_logger.removeHandler(counter)

    metrics = _case_metrics(rows, cases) if rows else None
    with_items = sum(1 for r in rows if r["texts"]) / len(rows) if rows else 0.0
    return {
        "raised": error is not None,
        "error": error,
        "fallback_warnings": counter.count,
        "warning_samples": counter.samples,
        "cases": len(rows),
        "any_items_rate": with_items,
        "recall_at_1": metrics["recall_at_1"] if metrics else None,
        "recall_at_3": metrics["recall_at_3"] if metrics else None,
        "clean": metrics["clean"] if metrics else None,
    }


# --------------------------------------------------------------------- gates


def _gates(metrics: dict, stability: dict, degradation: dict, th: dict) -> list[dict]:
    def gate(name: str, value: float | None, ok: bool, target: str) -> dict:
        return {"name": name, "value": value, "ok": bool(ok), "target": target}

    rows = [
        gate("Recall@1", metrics["recall_at_1"], metrics["recall_at_1"] >= th["min_recall1"],
             f">= {th['min_recall1']:.2f}"),
        gate("Recall@3", metrics["recall_at_3"], metrics["recall_at_3"] >= th["min_recall3"],
             f">= {th['min_recall3']:.2f}"),
        gate("MRR", metrics["mrr"], metrics["mrr"] >= th["min_mrr"],
             f">= {th['min_mrr']:.2f}"),
        gate("Clean", metrics["clean"], metrics["clean"] >= th["min_clean"],
             f">= {th['min_clean']:.2f}"),
    ]
    if stability["stable_top1"] is not None and metrics.get("spreads"):
        rows.append(gate(
            "spread(Recall@1)", metrics["spreads"]["recall_at_1"],
            metrics["spreads"]["recall_at_1"] <= th["max_spread"],
            f"<= {th['max_spread']:.2f}",
        ))
        rows.append(gate(
            "stable top-1", stability["stable_top1"],
            stability["stable_top1"] >= th["min_stable_top1"],
            f">= {th['min_stable_top1']:.2f}",
        ))
        rows.append(gate(
            "mean Jaccard", stability["mean_jaccard"],
            stability["mean_jaccard"] >= th["min_jaccard"],
            f">= {th['min_jaccard']:.2f}",
        ))
    # 降级探针是**可靠性**的另一半：不抛异常 + 有可见告警，这两条是硬要求；
    # 命中率是观测项（先测量再定目标，不在本 plan 设门槛）。
    if degradation is not None:
        rows.append(gate(
            "degrade: 不抛异常", 1.0 if not degradation["raised"] else 0.0,
            not degradation["raised"], "= 1.0",
        ))
        rows.append(gate(
            "degrade: 降级告警可见", float(degradation["fallback_warnings"]),
            degradation["fallback_warnings"] > 0, "> 0",
        ))
    return rows


# ---------------------------------------------------------------------- html


_CSS = """
:root{--canvas:#f7f6f2;--paper:#fff;--ink:#1f2422;--muted:#6b7672;--line:#e2e0d8;
--teal:#2f6f63;--amber:#a9741a;--red:#a6363b;--green:#2c6e4f;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--canvas);color:var(--ink);
font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
font-size:13.5px;line-height:1.6;padding:28px 32px 60px}
h1{font-size:20px;margin:0 0 4px}
h2{font-size:15px;margin:26px 0 10px;letter-spacing:.02em}
.sub{color:var(--muted);font-size:12px;margin-bottom:18px}
.kpis{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:8px}
.kpi{background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:10px 16px;min-width:112px}
.kpi .k{font-size:11px;color:var(--muted);letter-spacing:.04em}
.kpi .v{font:600 19px/1.2 var(--mono)}
.card{background:var(--paper);border:1px solid var(--line);border-radius:12px;
margin-bottom:16px;overflow:hidden}
table{width:100%;border-collapse:collapse}
th,td{padding:7px 12px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:11px;color:var(--muted);letter-spacing:.05em;font-weight:600;
background:#fbfaf6;text-transform:uppercase}
tr:last-child td{border-bottom:none}
td.num{font-family:var(--mono);text-align:right;white-space:nowrap}
.badge{display:inline-block;font-size:11px;padding:1px 8px;border-radius:999px;
border:1px solid var(--line);color:var(--muted)}
.badge.ok{color:var(--green);border-color:#bfe0cc;background:#f1f9f4}
.badge.bad{color:var(--red);border-color:#f0c8ca;background:#fdf3f3}
.badge.obs{color:var(--amber);border-color:#eddcb8;background:#fdf8ec}
.bar{height:6px;border-radius:3px;background:#ecebe3;position:relative;min-width:80px}
.bar>i{position:absolute;inset:0 auto 0 0;border-radius:3px;background:var(--teal)}
code{font-family:var(--mono);font-size:11.5px;background:#eef2ef;padding:1px 5px;border-radius:4px}
pre{background:#f8faf8;border:1px solid var(--line);border-radius:8px;padding:10px;
overflow:auto;max-height:240px;margin:6px 0 0;font-family:var(--mono);font-size:11.5px}
.note{background:#fdf8ec;border:1px solid #eddcb8;border-radius:10px;padding:10px 14px;
font-size:12.5px;margin:10px 0}
.wrap{max-width:1080px}
th.n,td.n{text-align:right}
"""


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _num(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _bar(value: float | None) -> str:
    if value is None:
        return ""
    width = max(0.0, min(1.0, value)) * 100
    return f'<div class="bar"><i style="width:{width:.0f}%"></i></div>'


def _write_html(path: Path, *, args, metrics, passes, strata, stability,
                degradation, gates, snapshot_path: Path) -> None:
    all_ok = all(g["ok"] for g in gates)
    verdict = "PASS" if all_ok else "FAIL"
    rows = [
        "<tr>"
        f"<td>{_esc(g['name'])}</td>"
        f"<td class='num'>{_num(g['value'])}</td>"
        f"<td class='num'><code>{_esc(g['target'])}</code></td>"
        f"<td><span class='badge {'ok' if g['ok'] else 'bad'}'>"
        f"{'PASS' if g['ok'] else 'FAIL'}</span></td>"
        "</tr>"
        for g in gates
    ]
    strata_rows = [
        "<tr>"
        f"<td>{_esc(r['dimension'])}</td><td>{_esc(r['bucket'])}</td>"
        f"<td class='n'>{r['samples']}</td>"
        f"<td class='n'>{_pct(r.get('recall_at_3', r.get('recall_at_5')))}</td>"
        f"<td class='n'>{_pct(r.get('mrr'))}</td>"
        f"<td>{_bar(r.get('recall_at_3', r.get('recall_at_5')))}</td>"
        "</tr>"
        for r in strata["rows"]
    ]
    per_pass = [
        "<tr>"
        f"<td class='n'>#{i + 1}</td>"
        f"<td class='n'>{_pct(m['recall_at_1'])}</td>"
        f"<td class='n'>{_pct(m['recall_at_3'])}</td>"
        f"<td class='n'>{m['mrr']:.3f}</td>"
        f"<td class='n'>{_pct(m['clean'])}</td>"
        "</tr>"
        for i, m in enumerate(passes["per_pass"])
    ]
    worst = sorted(stability["per_case"], key=lambda c: c["jaccard"])[:6]
    worst_rows = [
        "<tr>"
        f"<td><code>{_esc(c['id'])}</code></td>"
        f"<td class='n'>{_pct(c['jaccard'])}</td>"
        f"<td>{'稳定' if c['stable_top1'] else '翻转'}</td>"
        f"<td class='n'>{_esc(c['sizes'])}</td>"
        "</tr>"
        for c in worst
    ]
    case_rows = []
    for r in passes["last"]:
        hits = len(r["forbidden_hits"])
        case_rows.append(
            "<tr>"
            f"<td><code>{_esc(r['id'])}</code></td>"
            f"<td>{_esc(r['agent'])}</td>"
            f"<td>{_esc(r['scope'])}</td>"
            f"<td>{_esc('、'.join(r['gold']))}</td>"
            f"<td>{_esc(r['gold_ranks'])}</td>"
            f"<td><span class='badge {'bad' if hits else 'ok'}'>"
            f"{hits if hits else '0'}</span></td>"
            "</tr>"
        )
    deg = degradation or {}
    deg_badge = "bad" if deg.get("raised") else "ok"
    html_doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>Memory 召回质量与稳定性 · scoreboard</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<h1>Memory 召回质量与稳定性 · scoreboard</h1>
<div class="sub">生成于 {_esc(time.strftime('%Y-%m-%d %H:%M:%S'))} ·
数据集 <code>{_esc(_DATASET.name)}</code>（{metrics['cases']} cases / {len(_MEM)} 记忆）·
重复 {args.repeat} 轮 · 判决 <span class="badge {'ok' if all_ok else 'bad'}">{verdict}</span></div>

<div class="kpis">
  <div class="kpi"><div class="k">Recall@1</div><div class="v">{_pct(metrics['recall_at_1'])}</div></div>
  <div class="kpi"><div class="k">Recall@3</div><div class="v">{_pct(metrics['recall_at_3'])}</div></div>
  <div class="kpi"><div class="k">MRR</div><div class="v">{metrics['mrr']:.3f}</div></div>
  <div class="kpi"><div class="k">Clean</div><div class="v">{_pct(metrics['clean'])}</div></div>
  <div class="kpi"><div class="k">stable top-1</div><div class="v">{_pct(stability['stable_top1'])}</div></div>
  <div class="kpi"><div class="k">mean Jaccard</div><div class="v">{_pct(stability['mean_jaccard'])}</div></div>
</div>

<h2>一、判据（gate）</h2>
<div class="card"><table>
<thead><tr><th>指标</th><th class="n">实测</th><th class="n">阈值</th><th>结论</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>

<h2>二、分层（同一份召回，三种切法）</h2>
<div class="card"><table>
<thead><tr><th>维度</th><th>分桶</th><th class="n">样本</th><th class="n">命中率</th>
<th class="n">MRR</th><th>分布</th></tr></thead>
<tbody>{''.join(strata_rows)}</tbody></table></div>
<div class="note">分层是这份报告的重点：总分 96% 可能掩盖「法务类事实一个都没召回」。
<b>gold_type</b> 按金标 key 聚合（一个 case 可同时有偏好与事实两种金标）。</div>

<h2>三、稳定性（每轮清空 embedding 缓存）</h2>
<div class="card"><table>
<thead><tr><th class="n">轮次</th><th class="n">Recall@1</th><th class="n">Recall@3</th>
<th class="n">MRR</th><th class="n">Clean</th></tr></thead>
<tbody>{''.join(per_pass)}</tbody></table></div>
<div class="card"><table>
<thead><tr><th>最不稳的 case</th><th class="n">跨轮 Jaccard</th><th>top-1</th>
<th class="n">召回条数/轮</th></tr></thead>
<tbody>{''.join(worst_rows)}</tbody></table></div>
<div class="note">清缓存是必须的：不清的话第二轮起向量直接命中进程内 LRU，
测出来的「稳定」是<b>缓存稳定</b>。</div>

<h2>四、降级探针（强制 embedding 失败）</h2>
<div class="card"><table>
<thead><tr><th>项</th><th>实测</th></tr></thead>
<tbody>
<tr><td>是否抛异常</td><td><span class="badge {deg_badge}">{'抛了' if deg.get('raised') else '没抛（符合预期）'}</span>
 {_esc(deg.get('error') or '')}</td></tr>
<tr><td>降级告警次数</td><td><code>{deg.get('fallback_warnings', 0)}</code>
 （<code>Embedding failed, falling back to keyword search</code>）</td></tr>
<tr><td>降级后仍有召回的 case 占比</td><td><code>{_pct(deg.get('any_items_rate'))}</code></td></tr>
<tr><td>降级后 Recall@3</td><td><code>{_pct(deg.get('recall_at_3'))}</code></td></tr>
</tbody></table></div>
<div class="note">降级「优雅」与否看中间那两行：有告警 = 可定位；但
<b>有召回的 case 占比</b>才说明还剩多少能力。keyword 分支按空白字符切词，
中文 query 没有空格 → 整句当一个 token → 语义记忆可能等于全失效。</div>

<h2>五、逐例明细</h2>
<div class="card"><table>
<thead><tr><th>case</th><th>agent</th><th>scope</th><th>gold</th>
<th>名次</th><th>forbidden 命中</th></tr></thead>
<tbody>{''.join(case_rows)}</tbody></table></div>

<h2>复现</h2>
<pre>D:/miniConda/envs/agent/python.exe evaluation/gold_memory_runner.py --repeat {args.repeat}{' --degrade' if args.degrade else ''} --html docs/reports/memory-scoreboard.html</pre>
<div class="sub">快照：<code>{_esc(snapshot_path.name)}</code> ·
本报告只回答「<b>召回对不对、稳不稳</b>」；「召回有没有真的改变答案」属于行为层（L2），
需要 ON/OFF 配对真 LLM 实验，不在这份报告里。</div>
</div></body></html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_doc, encoding="utf-8")


# ---------------------------------------------------------------------- main


def _render_from_snapshot(snapshot_path: Path, html_path: Path) -> int:
    """只重渲染 HTML（不重跑 embedding）——调样式/文案时不必再烧一遍 API。"""
    snap = json.loads(snapshot_path.read_text(encoding="utf-8"))

    class _Args:
        repeat = snap.get("repeats", 1)
        degrade = snap.get("degradation") is not None

    metrics = {
        "cases": snap["cases"],
        "recall_at_1": snap["recall_at_1"],
        "recall_at_3": snap["recall_at_3"],
        "mrr": snap["mrr"],
        "clean": snap["clean"],
        "spreads": snap.get("spreads") or {},
    }
    _write_html(
        html_path,
        args=_Args(),
        metrics=metrics,
        passes={"per_pass": snap.get("per_pass", []), "last": snap.get("per_case", [])},
        strata=snap.get("strata", {"rows": []}),
        stability=snap.get(
            "stability", {"per_case": [], "stable_top1": None, "mean_jaccard": None}
        ),
        degradation=snap.get("degradation"),
        gates=snap.get("gates", []),
        snapshot_path=snapshot_path,
    )
    print(f"判决书（由快照重渲染）: {html_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Memory 召回质量与稳定性 scoreboard")
    ap.add_argument("--repeat", type=int, default=3, help="重复轮数（每轮清 embedding 缓存）")
    ap.add_argument("--degrade", action="store_true", help="跑一次降级探针（强制 embedding 失败）")
    ap.add_argument("--html", type=Path, default=None, help="输出自包含 HTML（默认 docs/reports/memory-scoreboard.html）")
    ap.add_argument("--no-html", action="store_true", help="不写 HTML")
    ap.add_argument("--from-snapshot", type=Path, default=None,
                    help="只从既有 JSON 快照重渲染 HTML（不重跑 embedding）")
    for key, value in DEFAULT_GATES.items():
        ap.add_argument(f"--{key.replace('_', '-')}", type=float, default=value)
    args = ap.parse_args(argv)

    if args.from_snapshot is not None:
        return _render_from_snapshot(args.from_snapshot, args.html or _DEFAULT_HTML)

    if not os.getenv("SILICONFLOW_API_KEY"):
        _fail("SILICONFLOW_API_KEY 未设——真 embedding runner 需要 embedding key。")
    if not os.getenv("DATABASE_URL"):
        print("[gold_memory_runner] DATABASE_URL 未设，回退默认 localhost ragent/ragent")

    thresholds = {k: getattr(args, k) for k in DEFAULT_GATES}
    cases = [
        c for c in _DS["queries"]
        if c.get("memory_scope") and c["gold"]   # expect_none 由 policy 层负责，view 指标不参与
    ]

    async def _seed(uid: int) -> None:
        from app.infra.db.postgres import init_pool

        await init_pool()
        try:
            from app.infra.memory.memory_manager import MemoryManager

            mm = MemoryManager()
            for _key, m in _MEM.items():
                if m["type"] == "query":
                    await mm.remember_query(
                        question=m["question"], sql=m["sql"], schema=None,
                        target_metric=m.get("target_metric", ""), user_id=uid,
                    )
                else:
                    await mm.remember_preference(
                        user_id=str(uid), content=m["content"],
                        memory_type="stable_preference" if m["type"] == "preference" else "insight",
                        importance=m.get("importance", 0.8),
                        source="gold_runner", status="active", confidence="high",
                    )
        finally:
            from app.infra.db.postgres import close_pool

            await close_pool()

    uid = _make_user()
    try:
        asyncio.run(_seed(uid))
        seed_info = _verify_seed(uid)
        print(f"  种子校验: {seed_info}")

        all_passes: list[list[dict]] = []
        for i in range(max(1, args.repeat)):
            rows = _run_pass(uid, cases, clear_cache=i > 0)
            all_passes.append(rows)
            m = _case_metrics(rows, cases)
            print(
                f"  轮 #{i + 1}: R@1={m['recall_at_1']:.3f} R@3={m['recall_at_3']:.3f} "
                f"MRR={m['mrr']:.3f} Clean={m['clean']:.3f}"
            )

        per_pass = [_case_metrics(rows, cases) for rows in all_passes]
        last = all_passes[-1]
        mean = {
            k: sum(p[k] for p in per_pass) / len(per_pass)
            for k in ("recall_at_1", "recall_at_3", "mrr", "clean")
        }
        mean["cases"] = len(cases)
        mean["forbidden_hits"] = per_pass[-1]["forbidden_hits"]
        mean["forbidden_checks"] = per_pass[-1]["forbidden_checks"]
        mean["spreads"] = {
            k: max(p[k] for p in per_pass) - min(p[k] for p in per_pass)
            for k in ("recall_at_1", "recall_at_3", "mrr", "clean")
        } if len(per_pass) > 1 else {}

        stability = _stability(all_passes)
        strata = _strata(all_passes, cases)
        degradation = _degrade_probe(uid, cases) if args.degrade else None
        gates = _gates(mean, stability, degradation, thresholds)

        print("=" * 64)
        print(f"Memory Gold Set（真 embedding）: {len(cases)} cases × {len(all_passes)} 轮")
        print(f"  Recall@1 = {mean['recall_at_1']:.3f}   Recall@3 = {mean['recall_at_3']:.3f}")
        print(f"  MRR      = {mean['mrr']:.3f}   Clean    = {mean['clean']:.3f}"
              f" (forbidden 命中 {mean['forbidden_hits']}/{mean['forbidden_checks']})")
        if stability["stable_top1"] is not None:
            print(f"  spread(R@1) = {mean['spreads']['recall_at_1']:.3f}"
                  f"   stable top-1 = {stability['stable_top1']:.3f}"
                  f"   mean Jaccard = {stability['mean_jaccard']:.3f}")
        print("-" * 64)
        print("  分层：" + " | ".join(
            f"{r['dimension']}:{r['bucket']}={_pct(r.get('recall_at_3', r.get('recall_at_5')))}"
            for r in strata["rows"]
        ))
        if degradation is not None:
            print(
                f"  降级探针：raised={degradation['raised']} "
                f"warnings={degradation['fallback_warnings']} "
                f"any_items={degradation['any_items_rate']:.2f} "
                f"R@3={_pct(degradation['recall_at_3'])}"
            )
        print("-" * 64)
        for g in gates:
            flag = "PASS" if g["ok"] else "FAIL"
            value = "—" if g["value"] is None else f"{g['value']:.3f}"
            print(f"  [{flag}] {g['name']:<22} 实测 {value:>7}  阈值 {g['target']}")

        per_case_out = []
        for idx, row in enumerate(last):
            entry = {k: v for k, v in row.items() if k != "texts"}
            entry["jaccard"] = (
                stability["per_case"][idx]["jaccard"] if stability["per_case"] else None
            )
            entry["stable_top1"] = (
                stability["per_case"][idx]["stable_top1"] if stability["per_case"] else None
            )
            per_case_out.append(entry)

        _RESULTS_DIR.mkdir(exist_ok=True)
        snap_path = _RESULTS_DIR / f"memory_gold_{time.strftime('%Y%m%d_%H%M%S')}.json"
        snapshot = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "dataset": str(_DATASET),
            "cases": len(cases),
            "repeats": len(all_passes),
            "recall_at_1": mean["recall_at_1"],
            "recall_at_3": mean["recall_at_3"],
            "mrr": mean["mrr"],
            "clean": mean["clean"],
            "thresholds": thresholds,
            "gates": gates,
            "seed": seed_info,
            "per_pass": per_pass,
            "spreads": mean["spreads"],
            "stability": stability,
            "strata": strata,
            "degradation": degradation,
            "per_case": per_case_out,
        }
        snap_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"快照: {snap_path}")

        if not args.no_html:
            out = args.html or _DEFAULT_HTML
            _write_html(
                out, args=args, metrics=mean,
                passes={"per_pass": per_pass, "last": last},
                strata=strata, stability=stability, degradation=degradation,
                gates=gates, snapshot_path=snap_path,
            )
            print(f"判决书: {out}")

        return 0 if all(g["ok"] for g in gates) else 2
    finally:
        _cleanup(uid)


if __name__ == "__main__":
    sys.exit(main())
