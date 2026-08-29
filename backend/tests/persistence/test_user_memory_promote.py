"""F1 修复钉子：UserMemory.save promote 路径必须同步更新 memory_type。

失败场景（p3 cumulative review Finding #1）：
  1. LLM-inferred 写 "I prefer bar charts" → memory_type='insight', status='candidate', confidence='low'
  2. 用户显式重申同一内容，用 stable_preference/active/high 入参
  3. promote 命中（candidate→active），但 UPDATE 不改 memory_type
  4. 行变 status='active', confidence='high', memory_type='insight'
  5. get_user_preferences() 过滤 IN ('stable_preference','temporary_preference') → 该行被排除
     → 显式偏好**永远**不被偏好召回路径命中

修复后：promote 同步写 memory_type=$6 → stable_preference 行能被偏好召回命中。

Fixture 模式复用 test_user_memory_eviction.py（psycopg2 直连真 PG + monkeypatch 隔离 embedder）。
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
        (f"promote-{uuid.uuid4().hex[:12]}",),
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


def _read_row(user_id: int, content: str) -> dict | None:
    import psycopg2
    conn = psycopg2.connect("postgresql://ragent:ragent@localhost:5432/ragent")
    cur = conn.cursor()
    cur.execute(
        "SELECT memory_type, status, confidence FROM memory.semantic_entry "
        "WHERE user_id=%s AND content=%s",
        (user_id, content),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {"memory_type": row[0], "status": row[1], "confidence": row[2]}


def test_promote_updates_memory_type_so_preference_visible(monkeypatch):
    """候选 insight 行被显式 stable_preference 重申 → promote 后 memory_type 同步变，
    get_user_preferences() 必须能命中该行。"""
    monkeypatch.setattr(um_mod, "get_embedder", lambda: _FakeEmbedder())
    user_id = _make_user()
    try:
        async def body():
            um = UserMemory()
            # 步骤 1: LLM-inferred insight/candidate/low
            await um.save(
                user_id=user_id, content="bar charts",
                memory_type="insight", status="candidate", confidence="low",
            )
            # 步骤 2: 显式重申 → stable_preference/active/high → promote=True
            await um.save(
                user_id=user_id, content="bar charts",
                memory_type="stable_preference", status="active", confidence="high",
            )
            # 步骤 3: get_user_preferences() 必须能命中
            return await um.get_user_preferences(user_id)

        prefs = _run(body())
        assert len(prefs) == 1, f"expected 1 preference row, got {len(prefs)}"
        assert prefs[0].memory_type == "stable_preference"
        assert prefs[0].status == "active"
        assert prefs[0].confidence == "high"

        # DB 行也确认同步更新成功
        row = _read_row(user_id, "bar charts")
        assert row is not None
        assert row["memory_type"] == "stable_preference"
        assert row["status"] == "active"
        assert row["confidence"] == "high"
    finally:
        _cleanup(user_id)


def test_non_promote_update_keeps_existing_memory_type(monkeypatch):
    """非 promote 路径（既存已 active 再写）→ memory_type 不应被覆盖。

    守住「UPDATE 不引入意外变更」边界——既已 stable_preference 的行不应被非 promote 写
    路径悄悄改成别的类型。
    """
    monkeypatch.setattr(um_mod, "get_embedder", lambda: _FakeEmbedder())
    user_id = _make_user()
    try:
        async def body():
            um = UserMemory()
            await um.save(
                user_id=user_id, content="stable-x",
                memory_type="stable_preference", status="active", confidence="high",
            )
            # 既存已 active → promote=False → memory_type 不应被改
            await um.save(
                user_id=user_id, content="stable-x",
                memory_type="insight", status="active", confidence="medium",
            )

        _run(body())
        row = _read_row(user_id, "stable-x")
        assert row is not None
        # 非 promote 路径只更新 access_count + last_access_time，memory_type 不动
        assert row["memory_type"] == "stable_preference"
    finally:
        _cleanup(user_id)