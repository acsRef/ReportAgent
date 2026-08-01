"""第 3 轮·UserMemory 容量上限淘汰测试（真 PG）。

验证：超过 capacity 时按 LFU/LRU+重要性混合分删除最冷的若干条；高重要性记忆
受保护不被误删；evict_over_capacity 返回正确删除数。embedder 用 fake 隔离，
避免测试走真实 embedding API（淘汰逻辑不依赖向量）。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.infra.memory import user_memory as um_mod
from app.infra.memory.user_memory import UserMemory

pytestmark = pytest.mark.persistence


class _FakeEmbedder:
    async def embed_or_none(self, text):
        return None  # 淘汰逻辑不用向量；返回 None 让 intent_embedding 存 NULL


def _run(coro):
    from app.infra.db.postgres import init_pool, close_pool

    async def _body():
        await init_pool()
        try:
            return await coro
        finally:
            await close_pool()

    return asyncio.run(_body())


def _make_user() -> int:
    import psycopg2
    conn = psycopg2.connect("postgresql://ragent:ragent@localhost:5432/ragent")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO app.users (username, password_hash) VALUES (%s, 'x') RETURNING id",
        (f"evict-{uuid.uuid4().hex[:12]}",),
    )
    uid = cur.fetchone()[0]
    conn.close()
    return uid


def _cleanup(user_id: int):
    import psycopg2
    conn = psycopg2.connect("postgresql://ragent:ragent@localhost:5432/ragent")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DELETE FROM memory.semantic_entry WHERE user_id=%s", (user_id,))
    cur.execute("DELETE FROM app.users WHERE id=%s", (user_id,))
    conn.close()


def _count(user_id: int) -> int:
    import psycopg2
    conn = psycopg2.connect("postgresql://ragent:ragent@localhost:5432/ragent")
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM memory.semantic_entry WHERE user_id=%s", (user_id,))
    n = cur.fetchone()[0]
    conn.close()
    return n


def test_eviction_keeps_capacity_and_protects_importance(monkeypatch):
    monkeypatch.setattr(um_mod, "get_embedder", lambda: _FakeEmbedder())
    user_id = _make_user()
    try:
        async def body():
            um = UserMemory(capacity=3)
            # 按重要性降序写入 5 条 → 淘汰后应留下重要性最高的 3 条
            for imp in (1.0, 0.8, 0.6, 0.4, 0.2):
                await um.save(
                    user_id=user_id, content=f"mem-{imp}",
                    memory_type="insight", importance_score=imp,
                )
            return um

        _run(body())
        assert _count(user_id) == 3  # 不超过容量

        import psycopg2
        conn = psycopg2.connect("postgresql://ragent:ragent@localhost:5432/ragent")
        cur = conn.cursor()
        cur.execute(
            "SELECT content FROM memory.semantic_entry WHERE user_id=%s ORDER BY importance_score DESC",
            (user_id,),
        )
        survivors = {r[0] for r in cur.fetchall()}
        conn.close()
        # 重要性最高的 3 条存活（高重要性受保护），最低的 2 条被淘汰
        assert survivors == {"mem-1.0", "mem-0.8", "mem-0.6"}
    finally:
        _cleanup(user_id)


def test_evict_over_capacity_returns_deleted_count(monkeypatch):
    monkeypatch.setattr(um_mod, "get_embedder", lambda: _FakeEmbedder())
    user_id = _make_user()
    try:
        async def body():
            big = UserMemory(capacity=10000)
            for i in range(5):
                await big.save(user_id=user_id, content=f"c{i}", importance_score=0.5)
            # 此刻 5 条，容量内不淘汰
            evictor = UserMemory(capacity=2)
            deleted = await evictor.evict_over_capacity(user_id)
            return deleted

        deleted = _run(body())
        assert deleted == 3  # 5 - 2
        assert _count(user_id) == 2
    finally:
        _cleanup(user_id)


def test_evict_noop_within_capacity(monkeypatch):
    monkeypatch.setattr(um_mod, "get_embedder", lambda: _FakeEmbedder())
    user_id = _make_user()
    try:
        async def body():
            um = UserMemory(capacity=10)
            await um.save(user_id=user_id, content="only", importance_score=0.5)
            return await um.evict_over_capacity(user_id)

        assert _run(body()) == 0  # 容量内不删
        assert _count(user_id) == 1
    finally:
        _cleanup(user_id)
