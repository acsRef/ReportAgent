"""P16 G3：Memory Gold Set 真 embedding 评测 runner（Recall@1/@3/MRR/Clean）。

在**真 embedding（SiliconFlow）**下复用 `evaluation/memory_gold_cases.json`（25 记忆 +
30 金标 queries），产出面试可引用的检索质量指标。与 pytest 版（词位化 fake embedding，
backend/tests/persistence/test_memory_gold_recall.py）分工：
- pytest = 链路正确性硬断言（CI/本地全量可跑）；
- 本 runner = 真实语义空间下的位次指标（embedding 有外部效度，数字可引用）。

指标定义（per case，按 `memory_scope` 在对应 domain view 层计算——view 返回序即
score 序，无 assembler kind 重排干扰）：
- Recall@1 / Recall@3：gold 记忆是否命中 view top-1 / top-3；
- MRR：1 / rank(首个命中的 gold)；
- Clean：forbidden（noise 类 / 档位排除类）未被召回的比例。
expect_none cases（policy 挡在 view 之前）不参与 view 指标。

用法（repo root，手动门）：
    D:/miniConda/envs/agent/python.exe evaluation/gold_memory_runner.py
env 需求：DATABASE_URL（缺省 localhost ragent/ragent）+ SILICONFLOW_API_KEY。
输出：终端汇总 + `evaluation/results/memory_gold_<ts>.json` 快照。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "backend"))

_DATASET = _ROOT / "evaluation" / "memory_gold_cases.json"
_RESULTS_DIR = _ROOT / "evaluation" / "results"


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


def _fail(msg: str, code: int = 2):
    print(f"[gold_memory_runner] {msg}")
    sys.exit(code)


def _conn():
    import psycopg2

    return psycopg2.connect(os.getenv("DATABASE_URL", "postgresql://ragent:ragent@localhost:5432/ragent"))


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
                items.extend(await semantic_view.recall_structured(
                    case["query"], str(user_id), top_k_preferences=3,
                ))
            if "query" in case["memory_scope"]:
                items.extend(await query_view.recall_structured(
                    case["query"], str(user_id), top_k_queries=2,
                ))
            return items
        finally:
            from app.infra.db.postgres import close_pool

            await close_pool()

    return asyncio.run(_go())


def _main() -> None:
    if not os.getenv("SILICONFLOW_API_KEY"):
        _fail("SILICONFLOW_API_KEY 未设——真 embedding runner 需要 embedding key。")
    if not os.getenv("DATABASE_URL"):
        print("[gold_memory_runner] DATABASE_URL 未设，回退默认 localhost ragent/ragent")

    async def _seed(uid: int) -> None:
        from app.infra.db.postgres import init_pool

        await init_pool()
        try:
            from app.infra.memory.memory_manager import MemoryManager

            mm = MemoryManager()
            for key, m in _DS["memories"].items():
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
        mem = _DS["memories"]
        per_case: list[dict] = []
        for case in _DS["queries"]:
            if not case.get("memory_scope") or not case["gold"]:
                continue  # expect_none / 无金标：policy 层语义，view 指标不参与
            _reset_access(uid)
            items = _recall(case, uid)
            texts = [it["raw_text"] for it in items]

            def _gold_rank(key: str) -> int | None:
                content = mem[key].get("content") or mem[key]["question"]
                for rank, t in enumerate(texts):
                    if content in t:
                        return rank + 1
                return None

            ranks = [r for r in (_gold_rank(k) for k in case["gold"]) if r is not None]
            first_rank = min(ranks) if ranks else None
            forbidden_hits = [
                k for k in case["forbidden"]
                if (mem[k].get("content") or mem[k]["question"]) in " ".join(texts)
            ]
            per_case.append({
                "id": case["id"], "query": case["query"], "agent": case["agent"],
                "gold": case["gold"], "gold_ranks": ranks,
                "recall_at_1": bool(first_rank == 1),
                "recall_at_3": bool(first_rank is not None and first_rank <= 3),
                "mrr": 1.0 / first_rank if first_rank else 0.0,
                "forbidden_hits": forbidden_hits,
            })

        n = len(per_case)
        recall1 = sum(c["recall_at_1"] for c in per_case) / n
        recall3 = sum(c["recall_at_3"] for c in per_case) / n
        mrr = sum(c["mrr"] for c in per_case) / n
        forbidden_total = sum(len(c["forbidden_hits"]) for c in per_case)
        forbidden_checks = sum(len(case["forbidden"]) for case in _DS["queries"] if case["forbidden"])
        clean = 1.0 - (forbidden_total / forbidden_checks) if forbidden_checks else 1.0

        print("=" * 60)
        print(f"Memory Gold Set（真 embedding）: {n} cases")
        print(f"  Recall@1 = {recall1:.3f} ({sum(c['recall_at_1'] for c in per_case)}/{n})")
        print(f"  Recall@3 = {recall3:.3f} ({sum(c['recall_at_3'] for c in per_case)}/{n})")
        print(f"  MRR      = {mrr:.3f}")
        print(f"  Clean    = {clean:.3f} (forbidden 命中 {forbidden_total}/{forbidden_checks})")
        print("=" * 60)
        for c in per_case:
            flag = "OK " if c["recall_at_3"] and not c["forbidden_hits"] else "!! "
            print(f"  {flag}{c['id']:<6} gold_ranks={c['gold_ranks']}"
                  f" forbidden_hits={c['forbidden_hits']}")

        _RESULTS_DIR.mkdir(exist_ok=True)
        snapshot = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "dataset": str(_DATASET),
            "cases": n,
            "recall_at_1": recall1, "recall_at_3": recall3, "mrr": mrr, "clean": clean,
            "per_case": per_case,
        }
        snap_path = _RESULTS_DIR / f"memory_gold_{time.strftime('%Y%m%d_%H%M%S')}.json"
        snap_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"快照: {snap_path}")
    finally:
        _cleanup(uid)


if __name__ == "__main__":
    _main()
