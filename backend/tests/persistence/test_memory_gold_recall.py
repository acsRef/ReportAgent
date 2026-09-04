"""P16 W3 G2：Memory Gold Set 召回评测（真 PG + 词位化 fake embedder + 30 queries 全链）。

与 W2 行为测试的分工：本文件测 **Retrieval Quality**——固定记忆集（25 条，用户 A）下
30 条金标 query 经 `ContextRuntime.build`（全链：policy → recall → filter）逐条断言：
- gold 记忆必须进入召回/注入（Recall 层正确）；
- forbidden 记忆必须不进入（Clean 层正确：零词交或档位契约排除）；
- expect_none case 必须完全空召回（policy 保守性：chitchat / 无触发词）。

embedding 用词位化 fake：`memory_bucket_keywords` 中词命中即打 1.0 特征位（base 全维
0.01 + v[0]=0.3）——cos 相似度随「query 与记忆共享词数」单调，dataset 按词交设计
（gold = 词交最高的记忆，forbidden = 零词交/档位排除），链路正确时判定确定。
每个 case 前重置 access 副作用（LRU/LFU 清零），排序纯由语义+importance 决定。
位次指标（Recall@1/@3/MRR）由 gold_memory_runner.py 在真 embedding 下产出（fake 无外部
效度，逐 case 硬断言已足够）。
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

import pytest

from app.infra.memory.memory_manager import MemoryManager

pytestmark = pytest.mark.persistence

_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))

_DATASET_PATH = Path(__file__).resolve().parents[3] / "evaluation" / "memory_gold_cases.json"


def _load_dataset() -> dict:
    with open(_DATASET_PATH, encoding="utf-8") as f:
        return json.load(f)


_DS = _load_dataset()


class _KeywordEmbedder:
    """词位化确定性 embedder：命中 dataset 关键词 → 特征位 1.0。

    base 全维 0.0001（仅防 pgvector 余弦除零，可忽略）——词位 1.0 主导向量模，
    cos ≈ k/√(n_query·n_mem)（k=共享词数），随共享率严格递减：
    1 词共享必高于 0 词共享（无论 importance——0.6 语义权重支配）、
    高共享率行必先于低共享率行，词交排序可推演。
    """

    def __init__(self, keywords: list[str]):
        self._kw = keywords

    async def embed_or_none(self, text: str):
        v = [0.0001] * _DIM
        for i, kw in enumerate(self._kw):
            if kw in text:
                v[8 + i] = 1.0  # 位 8 起
        return v


def _conn():
    import psycopg2
    return psycopg2.connect("postgresql://ragent:ragent@localhost:5432/ragent")


def _make_user() -> int:
    conn = _conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO app.users (username, password_hash) VALUES (%s, 'x') RETURNING id",
        (f"p16gold-{uuid.uuid4().hex[:10]}",),
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
    """清零 LRU/LFU 副作用——混合分只剩 语义×0.6 + importance×0.2，排序确定。"""
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


def _run(coro):
    from app.infra.db.postgres import close_pool, init_pool

    async def _body():
        await init_pool()
        try:
            return await coro
        finally:
            await close_pool()

    return asyncio.run(_body())


async def _build(user_id: int, query: str, agent: str) -> dict:
    from app.context.runtime import ContextRuntime

    return await ContextRuntime().build(
        session_id=f"sess-gold-{uuid.uuid4().hex[:10]}",
        user_id=user_id,
        query=query,
        agent=agent,
        state_dict={"user_id": user_id},
    )


@pytest.fixture(scope="module")
def gold_env():
    """module 级：patch 双模块 embedder + 建用户 + 插全部 25 条记忆（一次），
    teardown 恢复 + 清理。每 case 前 `_reset_access` 隔离 record_access 副作用。"""
    from app.infra.memory import query_memory as qm_mod
    from app.infra.memory import user_memory as um_mod

    embedder = _KeywordEmbedder(_DS["memory_bucket_keywords"])
    orig_um, orig_qm = um_mod.get_embedder, qm_mod.get_embedder
    um_mod.get_embedder = lambda: embedder
    qm_mod.get_embedder = lambda: embedder

    uid = _make_user()
    mem = _DS["memories"]

    async def seed():
        mm = MemoryManager()
        for key, m in mem.items():
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
                    source="gold_seed", status="active", confidence="high",
                )
        _reset_access(uid)

    try:
        _run(seed())
        yield uid
    finally:
        um_mod.get_embedder = orig_um
        qm_mod.get_embedder = orig_qm
        _cleanup(uid)


def _case_params():
    return [
        pytest.param(c, id=c["id"])
        for c in _DS["queries"]
    ]


@pytest.mark.parametrize("case", _case_params())
def test_gold_query_recall(case, gold_env):
    uid = gold_env
    _reset_access(uid)  # 每 case 前重置（record_access 副作用隔离）

    def body():
        return _build(uid, case["query"], case["agent"])

    bundle = _run(body())
    texts = [it["raw_text"] for it in bundle["recall_items"]]
    joined = " ".join(texts)
    mem = _DS["memories"]

    if case.get("expect_none"):
        assert not texts, (
            f"{case['id']} 期望空召回（policy 保守），实际召回: {texts}"
        )
        return

    for key in case["gold"]:
        content = mem[key].get("content") or mem[key]["question"]
        assert content in joined, (
            f"{case['id']} gold 记忆 {key} 未进入上下文 —— recall/排序断了?\n"
            f"recall_items: {texts}"
        )
    for key in case["forbidden"]:
        content = mem[key].get("content") or mem[key]["question"]
        assert content not in joined, (
            f"{case['id']} forbidden 记忆 {key} 被召回（零词交/档位排除应生效）:\n{texts}"
        )
