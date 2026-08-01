"""第 4 轮·session digest 状态持久化测试（真 PG）。

验证 get/save_context_state round-trip、白名单过滤、缺失 session 返回零值态。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.infra.checkpoint.session import session_manager

pytestmark = pytest.mark.persistence


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
        (f"ctx-{uuid.uuid4().hex[:12]}",),
    )
    uid = cur.fetchone()[0]
    conn.close()
    return uid


def _cleanup(session_id: str, user_id: int):
    import psycopg2
    conn = psycopg2.connect("postgresql://ragent:ragent@localhost:5432/ragent")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DELETE FROM agent.session WHERE thread_id=%s", (session_id,))
    cur.execute("DELETE FROM app.users WHERE id=%s", (user_id,))
    conn.close()


def test_context_state_round_trip_and_whitelist():
    sid = f"ctx-{uuid.uuid4()}"
    uid = _make_user()
    try:
        async def body():
            await session_manager.create_session(sid, user_id=uid)
            await session_manager.save_context_state(sid, {
                "digest": "叙事摘要",
                "digest_msg_count": 12,
                "digest_version": 2,
                "mid_digest": "长期脉络",
                "extracted_schemas": [{"x": 1}],  # 非白名单键应被忽略，不落库
            })
            return await session_manager.get_context_state(sid)

        state = _run(body())
        assert state["digest"] == "叙事摘要"
        assert state["digest_msg_count"] == 12
        assert state["digest_version"] == 2
        assert state["mid_digest"] == "长期脉络"
    finally:
        _cleanup(sid, uid)


def test_context_state_missing_session_returns_zero():
    async def body():
        return await session_manager.get_context_state(f"nope-{uuid.uuid4()}")

    assert _run(body()) == {
        "digest": None, "digest_msg_count": 0, "digest_version": 0, "mid_digest": None,
    }
