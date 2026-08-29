"""post-review 钉子：top_k=0 真「禁止召回」语义（user_memory.search / get_user_preferences）。

P3 cumulative review #post-review：plan §F3 让 query/semantic view 传 top_k=0 给
MemoryManager.recall_structured，意图是「这一类不要召回」。但
UserMemory.search / get_user_preferences 用 `k = top_k or self._top_k`，0 当 falsy
兜底为默认 5，导致：
  - SQL 仍跑（limit 默认 5）
  - record_access 副作用仍触发（污染 LRU/LFU 排序）

修后短路返回 []，record_access 不跑。
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
        return None


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
        (f"topk0-{uuid.uuid4().hex[:12]}",),
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


def _read_access_count(user_id: int, content: str) -> int:
    import psycopg2
    conn = psycopg2.connect("postgresql://ragent:ragent@localhost:5432/ragent")
    cur = conn.cursor()
    cur.execute(
        "SELECT access_count FROM memory.semantic_entry "
        "WHERE user_id=%s AND content=%s",
        (user_id, content),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else -1


def test_search_top_k_zero_returns_empty_and_skips_record_access(monkeypatch):
    """top_k=0 表达「禁止召回」——search 短路返回 []，且 record_access 不跑（access_count 不增）。"""
    monkeypatch.setattr(um_mod, "get_embedder", lambda: _FakeEmbedder())
    user_id = _make_user()
    try:
        async def body():
            um = UserMemory()
            # 写 1 条 active 行（不带 memory_type='insight' 走默认）
            await um.save(
                user_id=user_id, content="x",
                memory_type="stable_preference", status="active", confidence="high",
            )
            before = _read_access_count(user_id, "x")
            # search top_k=0 → 短路返回 []，不查 SQL，不调 record_access
            results = await um.search(user_id, query="", top_k=0)
            return results, before

        results, before = _run(body())
        assert results == []
        after = _read_access_count(user_id, "x")
        # access_count 不变——证明 record_access 没跑
        assert after == before, f"top_k=0 should not invoke record_access: before={before}, after={after}"
    finally:
        _cleanup(user_id)


def test_get_user_preferences_top_k_zero_returns_empty(monkeypatch):
    """get_user_preferences top_k=0 也短路——只看 stable_preference/temporary_preference 的偏好路径。"""
    monkeypatch.setattr(um_mod, "get_embedder", lambda: _FakeEmbedder())
    user_id = _make_user()
    try:
        async def body():
            um = UserMemory()
            await um.save(
                user_id=user_id, content="bar charts",
                memory_type="stable_preference", status="active", confidence="high",
            )
            return await um.get_user_preferences(user_id, top_k=0)

        results = _run(body())
        assert results == []
    finally:
        _cleanup(user_id)


def test_search_top_k_none_uses_default(monkeypatch):
    """top_k=None 仍走默认（self._top_k）——确认 falsy 修复没破坏 None 路径。"""
    monkeypatch.setattr(um_mod, "get_embedder", lambda: _FakeEmbedder())
    user_id = _make_user()
    try:
        async def body():
            um = UserMemory(top_k=3)
            await um.save(
                user_id=user_id, content="y",
                memory_type="stable_preference", status="active", confidence="high",
            )
            return await um.search(user_id, query="", top_k=None)

        results = _run(body())
        assert len(results) >= 1  # default 走通
    finally:
        _cleanup(user_id)