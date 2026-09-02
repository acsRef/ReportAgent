"""P15 e2e bug ③ 钉子：vector codec 注册后，真 PG 上 memory 语义/查询召回真正可用。

背景（2026-09-02）：asyncpg 无 pgvector codec，`user_memory.py` / `query_memory.py` 把
float list 绑 `$1::vector` → 每次 `DataError (expected str, got list)` →
`ContextRuntime.build` 全断、assembled_context 恒空、semantic/query 两表 0 行。
修复：`init_pool` 注册 vector text codec（list ↔ vector 字面量）+ `UserMemory` user_id
归 int（str 直绑 int 列同病）。

本测试镜像生产完整调用链：`semantic_memory.recall_structured(query, str(user_id))`
→ `MemoryManager` → 两分支都走真 `<=>` 向量检索，证明 codec + str→int 全通。
fake embedder 维度从 `EMBEDDING_DIM` 读取（同一来源：init_pg VECTOR(n) / main 启动校验），
不硬编码——config↔列不匹配时 INSERT 会大声失败。
"""
from __future__ import annotations

import os
import uuid

import pytest

from app.infra.memory.memory_manager import MemoryManager

pytestmark = pytest.mark.persistence

_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))


class _FakeEmbedder:
    """确定性伪向量：所有维度 0.01 + v[0]=0.5；text 含 'bucket_a' 时 v[5]=1.0。

    保证「同含 bucket_a 的行/query」互为最近邻（<=> 距离最小），检索结果可预期。
    无零向量 → 不触发 pgvector 余弦除零。
    """

    async def embed_or_none(self, text: str):
        v = [0.01] * _DIM
        v[0] = 0.5
        if "bucket_a" in text:
            v[5] = 1.0
        return v


def _conn():
    import psycopg2
    return psycopg2.connect("postgresql://ragent:ragent@localhost:5432/ragent")


def _make_user_id() -> int:
    conn = _conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO app.users (username, password_hash) VALUES (%s, 'x') RETURNING id",
        (f"vec-{uuid.uuid4().hex[:12]}",),
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


def _run(coro):
    from app.infra.db.postgres import init_pool, close_pool

    async def _body():
        await init_pool()  # init= 注册 vector codec（每连接）
        try:
            return await coro
        finally:
            await close_pool()

    return asyncio_run(_body())


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


def test_memory_recall_works_on_real_pg_with_vector(monkeypatch):
    """生产召回链真跑通：写 semantic(2)+query(1) 行 → recall_structured(str user) 命中两分支。"""
    from app.infra.memory import query_memory as qm_mod
    from app.infra.memory import user_memory as um_mod

    monkeypatch.setattr(um_mod, "get_embedder", lambda: _FakeEmbedder())
    monkeypatch.setattr(qm_mod, "get_embedder", lambda: _FakeEmbedder())

    user_id = _make_user_id()
    try:
        async def body():
            mm = MemoryManager()
            # save 侧传 str（生产 L3 路径形态）→ 覆盖 UserMemory.save 的 int 归一
            await mm.remember_preference(
                user_id=str(user_id), content="bucket_a 华东销售额用折线图",
                memory_type="stable_preference", confidence="high",
            )
            await mm.remember_preference(
                user_id=str(user_id), content="华南退货情况用表格",
                memory_type="stable_preference", confidence="high",
            )
            await mm.remember_query(
                question="bucket_a 各区域销售额", sql="SELECT region, SUM(order_amount) ...",
                target_metric="销售额", user_id=user_id,
            )
            # 读侧传 str（生产 ContextRuntime 形态）→ 覆盖 UserMemory.search 的 int 归一
            return await mm.recall_structured(
                "bucket_a 华东销售额", str(user_id),
                top_k_queries=2, top_k_preferences=3,
            )

        items = _run(body())
        # 两分支都真命中（不是空 —— codec 修好后 vector 检索不再 DataError）
        assert items, "recall_structured returned empty — vector codec/绑定仍断?"
        sources = {i["source"] for i in items}
        assert sources & {"memory_semantic", "memory_preference"}, f"semantic 分支未命中: {sources}"
        assert "memory_query" in sources, f"query 分支未命中: {sources}"
        # bucket_a 行是 semantic 最近邻 → raw_text 带其内容
        assert any(
            "bucket_a" in i["raw_text"] for i in items if i["source"] != "memory_query"
        ), "bucket_a semantic 行未作为最近邻召回"
        assert any(
            "bucket_a" in i["raw_text"] for i in items if i["source"] == "memory_query"
        ), "bucket_a query 行未召回"
    finally:
        _cleanup(user_id)
