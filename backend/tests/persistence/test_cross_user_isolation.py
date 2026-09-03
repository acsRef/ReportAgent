"""Final Hardening ⑩：cross-user contamination 负向集成测试（真 PG）。

审查指出的缺口：单用户链路充分，但「B 永远看不到 A 的数据」只有 template
一处有负向测试（persistence/test_session_user_id.py）。本文件补四个读侧隔离
契约，全部以「user B 视角不得看到 user A 数据」为断言：
  1. conversation 读取：B 读 A 的 session 消息 → 空；
  2. session 列表：B list_sessions 看不到 A 的 session；
  3. memory 召回：A 写入稳定偏好后，B 用同一查询词召回 → 空（语义检索在
     SQL 层按 user_id 过滤，B 无任何可召回行）；
  4. semantic_entry 落库归属：行带 user_id，且按用户计数互不渗透。
trace 的 user 隔离由 repo 层 SQL 过滤保证（与 conversation 同构），此处不复测。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

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


def _unique(tag: str) -> str:
    return f"{tag}_{uuid.uuid4().hex[:10]}"


def test_user_b_cannot_read_user_a_data() -> None:
    from app.infra.auth.repository import hash_password
    from app.infra.checkpoint.session import session_manager
    from app.infra.conversation.repository import get_messages, list_sessions
    from app.infra.db.postgres import get_pool
    from app.infra.memory.memory_manager import MemoryManager
    from app.memory.semantic import recall_structured as semantic_recall

    user_a = _unique("usera")
    user_b = _unique("userb")
    session_a = _unique("sessA")
    marker = f"A-PREF-{uuid.uuid4().hex[:8]}"

    async def body():
        pool = get_pool()
        try:
            async with pool.acquire() as conn:
                id_a = await conn.fetchval(
                    "INSERT INTO app.users (username, password_hash) "
                    "VALUES ($1, $2) RETURNING id", user_a, hash_password("pw-a-12345"),
                )
                id_b = await conn.fetchval(
                    "INSERT INTO app.users (username, password_hash) "
                    "VALUES ($1, $2) RETURNING id", user_b, hash_password("pw-b-12345"),
                )
            await session_manager.create_session(session_a, user_id=id_a)
            # A 的会话消息
            from app.infra.conversation.repository import save_message
            await save_message(session_a, id_a, "user", "A 的私密问题", "text")
            await save_message(session_a, id_a, "assistant", "A 的私密回答", "text")
            # A 的稳定偏好（用户级 memory）
            await MemoryManager().remember_preference(
                user_id=str(id_a), content=marker,
                memory_type="stable_preference", importance=1.0,
                source="test", scope="user", status="active",
            )

            # 1) B 读 A 的 session 消息 → 空（session + user 双 scoping）
            b_reading_a = await get_messages(session_a, id_b)
            assert b_reading_a == [], "B 不得读到 A 的会话消息"
            # A 自己仍能读到（正控制）
            assert len(await get_messages(session_a, id_a)) == 2

            # 2) B 的 session 列表不含 A 的 session
            b_sessions = await list_sessions(id_b)
            assert session_a not in {s["session_id"] for s in b_sessions}

            # 3) B 用 A 偏好的原文召回 → 空（B 名下无任何行可召回）
            b_recall = await semantic_recall(marker, str(id_b), top_k_preferences=3)
            assert b_recall == [], "B 不得召回 A 的记忆偏好"

            # 4) 落库归属 + 计数互不渗透
            async with pool.acquire() as conn:
                a_rows = await conn.fetchval(
                    "SELECT count(*) FROM memory.semantic_entry WHERE user_id=$1",
                    id_a,
                )
                b_rows = await conn.fetchval(
                    "SELECT count(*) FROM memory.semantic_entry WHERE user_id=$1",
                    id_b,
                )
                marker_a_only = await conn.fetchval(
                    "SELECT count(*) FROM memory.semantic_entry "
                    "WHERE user_id=$2 AND content=$1",
                    marker, id_b,
                )
            assert a_rows >= 1
            assert b_rows == 0
            assert marker_a_only == 0
        finally:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM app.conversations WHERE session_id = $1", session_a)
                await conn.execute("DELETE FROM memory.semantic_entry WHERE content = $1", marker)
                await conn.execute("DELETE FROM app.users WHERE username IN ($1, $2)", user_a, user_b)
            # session 行由 session_manager 创建，补清理
            try:
                from app.infra.db.postgres import get_pool as _gp
                async with _gp().acquire() as conn:
                    await conn.execute("DELETE FROM agent.session WHERE thread_id = $1", session_a)
            except Exception:
                pass

    _run(body())
