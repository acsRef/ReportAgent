"""会话硬删除的级联契约（真 PG，plan 2026-11-11-session-hard-delete）。

钉住三件容易写错、且写错了不报错只留下垃圾的事：

1. **外键顺序**：`report_version` 必须早于 `requirement_draft`
   （`report_version_requirement_draft_id_fkey`），`agent.session` 指向 draft 的指针
   必须先置空（`fk_session_latest_requirement`）——顺序错了直接抛 FK 错；
2. **checkpoint 也要删**：`public.checkpoints/blobs/writes` 按 `thread_id` 存，
   漏掉的话删完会话用同一个 thread_id 重建会读到上一轮的图状态；
3. **跨用户不可删**：别人的会话一律 SessionNotFound（HTTP 层翻 404），
   而不是「删了个寂寞」返回 200。
"""
from __future__ import annotations

import asyncio
import sys
import uuid

import pytest

pytestmark = pytest.mark.persistence


def _run(coro):
    from app.infra.db.postgres import close_pool, init_pool

    async def _body():
        await init_pool()
        try:
            return await coro
        finally:
            await close_pool()

    return asyncio.run(_body())


def _ensure_checkpoint_tables() -> None:
    """补建 LangGraph 的 checkpoint 三件套（幂等）。

    它们由 `AsyncPostgresSaver.setup()` 建，`init_pg.sql` 里没有；本地因为跑过应用所以
    碰巧存在，**CI 是全新库**——缺表的话本文件对 checkpoint 的断言全部落空。
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from app.infra.db.postgres import POSTGRES_DSN, close_pool, init_pool

    async def body():
        await init_pool()
        try:
            async with AsyncPostgresSaver.from_conn_string(POSTGRES_DSN) as saver:
                await saver.setup()
        finally:
            await close_pool()

    # Windows：psycopg 的 async 连接不支持默认的 ProactorEventLoop（Linux CI 无此问题）
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(body())


@pytest.fixture(scope="module", autouse=True)
def _checkpoint_schema():
    _ensure_checkpoint_tables()


def _sid() -> str:
    return f"test-del-{uuid.uuid4().hex[:10]}"


async def _make_user(pool) -> int:
    """自己造一个用户（`agent.requirement_draft.user_id` 有外键指向 `app.users`）。

    2026-11-16 CI 红转绿的修复：旧实现是「借库里现成的一条」（`SELECT ... LIMIT 1`），
    本地因为跑过应用有 admin 所以绿；**CI 是全新库、`app.users` 为空**，于是
    `assert value is not None` 必然失败（该红从 2026-11-11 的 907c74d 起一直挂着）。
    改成自己造 + 测试结束删掉，与其余 persistence 测试（`test_session_context_state.py` 等）一致。
    """
    return int(await pool.fetchval(
        "INSERT INTO app.users (username, password_hash) VALUES ($1, 'x') RETURNING id",
        f"del-test-{uuid.uuid4().hex[:12]}",
    ))


async def _drop_user(pool, user_id: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM app.users WHERE id = $1", user_id)


async def _seed_session(pool, session_id: str, user_id: int) -> str:
    """把 12 张表里能建的行都建一遍，返回 trace_id。"""
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO agent.session (thread_id, user_id, title, created_at, status) "
            "VALUES ($1, $2, 'del-test', NOW(), 'active')",
            session_id, user_id,
        )
        draft_id = await conn.fetchval(
            "INSERT INTO agent.requirement_draft "
            "(session_id, user_id, version, user_query, status, payload) "
            "VALUES ($1, $2, 1, 'test', 'complete', '{}'::jsonb) RETURNING id",
            session_id, user_id,
        )
        await conn.execute(
            "UPDATE agent.session SET latest_requirement_draft_id = $2 WHERE thread_id = $1",
            session_id, draft_id,
        )
        await conn.execute(
            "INSERT INTO agent.report_version "
            "(session_id, user_id, version, title, status, report_payload, requirement_draft_id) "
            "VALUES ($1, $2, 1, 't', 'done', '{}'::jsonb, $3)",
            session_id, user_id, draft_id,
        )
        await conn.execute(
            "INSERT INTO agent.analysis_task_revision (session_id, revision, act, task) "
            "VALUES ($1, 1, 'new_task', '{}'::jsonb)",
            session_id,
        )
        await conn.execute(
            "INSERT INTO app.conversations (session_id, user_id, role) VALUES ($1, $2, 'user')",
            session_id, user_id,
        )
        trace_id = f"tr-{uuid.uuid4().hex[:12]}"
        await conn.execute(
            "INSERT INTO observability.agent_trace (trace_id, session_id, status) "
            "VALUES ($1, $2, 'SUCCESS')",
            trace_id, session_id,
        )
        await conn.execute(
            "INSERT INTO observability.agent_trace_span "
            "(trace_id, span_id, span_name, span_type, start_time) "
            "VALUES ($1, 's1', 'n', 'NODE', NOW())",
            trace_id,
        )
        # LangGraph checkpoint 三件套（dev 下是 MemorySaver，这里手工造行模拟非 dev）
        await conn.execute(
            "INSERT INTO public.checkpoints "
            "(thread_id, checkpoint_ns, checkpoint_id, type, checkpoint, metadata) "
            "VALUES ($1, '', 'ck1', 'json', '{}'::jsonb, '{}'::jsonb)",
            session_id,
        )
        await conn.execute(
            "INSERT INTO public.checkpoint_blobs "
            "(thread_id, checkpoint_ns, channel, version, type, blob) "
            "VALUES ($1, '', 'ch', '1', 'json', '\\x00'::bytea)",
            session_id,
        )
        await conn.execute(
            "INSERT INTO public.checkpoint_writes "
            "(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, blob) "
            "VALUES ($1, '', 'ck1', 't1', 0, 'ch', 'json', '\\x00'::bytea)",
            session_id,
        )
    return trace_id


_COUNT_SQL = {
    "agent.session": "SELECT count(*) FROM agent.session WHERE thread_id = $1",
    "agent.requirement_draft": "SELECT count(*) FROM agent.requirement_draft WHERE session_id = $1",
    "agent.report_version": "SELECT count(*) FROM agent.report_version WHERE session_id = $1",
    "agent.analysis_task_revision": (
        "SELECT count(*) FROM agent.analysis_task_revision WHERE session_id = $1"
    ),
    "app.conversations": "SELECT count(*) FROM app.conversations WHERE session_id = $1",
    "observability.agent_trace": (
        "SELECT count(*) FROM observability.agent_trace WHERE session_id = $1"
    ),
    "public.checkpoints": "SELECT count(*) FROM public.checkpoints WHERE thread_id = $1",
    "public.checkpoint_blobs": "SELECT count(*) FROM public.checkpoint_blobs WHERE thread_id = $1",
    "public.checkpoint_writes": (
        "SELECT count(*) FROM public.checkpoint_writes WHERE thread_id = $1"
    ),
}


def test_delete_session_clears_every_table() -> None:
    from app.infra.db.postgres import get_pool
    from app.services.session_deletion import delete_session

    session_id = _sid()

    async def body():
        pool = get_pool()
        user_id = await _make_user(pool)
        try:
            await _seed_session(pool, session_id, user_id)
            async with pool.acquire() as conn:
                before = {
                    table: await conn.fetchval(sql, session_id)
                    for table, sql in _COUNT_SQL.items()
                }
                assert all(v >= 1 for v in before.values()), before

            counts = await delete_session(session_id, user_id=user_id)

            async with pool.acquire() as conn:
                after = {
                    table: await conn.fetchval(sql, session_id)
                    for table, sql in _COUNT_SQL.items()
                }
            return before, counts, after
        finally:
            # 断言失败时也别把半截会话留在库里（前面造行的脚本中途抛 FK 错就会漏）
            try:
                await delete_session(session_id, user_id=user_id)
            except Exception:  # noqa: BLE001 - 收尾尽力而为
                pass
            await _drop_user(pool, user_id)

    before, counts, after = _run(body())

    assert all(v == 0 for v in after.values()), f"残留: {after}"
    # 返回的行数要能对上——CLI 与端点都靠它做审计
    assert counts["agent.session"] == 1
    assert counts["agent.report_version"] == before["agent.report_version"]
    assert counts["public.checkpoints"] == 1
    assert counts["public.checkpoint_blobs"] == 1
    assert counts["public.checkpoint_writes"] == 1


def test_delete_session_refuses_other_users_session() -> None:
    from app.infra.db.postgres import get_pool
    from app.services.session_deletion import SessionNotFound, delete_session

    session_id = _sid()

    async def body():
        pool = get_pool()
        owner = await _make_user(pool)
        intruder = owner + 1000  # 不存在的 user：owner 不匹配即拒
        await _seed_session(pool, session_id, owner)
        try:
            with pytest.raises(SessionNotFound):
                await delete_session(session_id, user_id=intruder)
            async with pool.acquire() as conn:
                alive = await conn.fetchval(
                    "SELECT count(*) FROM agent.session WHERE thread_id = $1", session_id,
                )
        finally:
            # 用真正的 owner 收尾，失败也不留垃圾
            await delete_session(session_id, user_id=owner)
            await _drop_user(pool, owner)
        return alive

    assert _run(body()) == 1  # 越权那次一行都没删


def test_delete_session_missing_raises_not_found() -> None:
    from app.infra.db.postgres import get_pool
    from app.services.session_deletion import SessionNotFound, delete_session

    async def body():
        pool = get_pool()
        owner = await _make_user(pool)
        try:
            with pytest.raises(SessionNotFound):
                await delete_session(f"does-not-exist-{uuid.uuid4().hex[:8]}", user_id=owner)
        finally:
            await _drop_user(pool, owner)

    _run(body())
