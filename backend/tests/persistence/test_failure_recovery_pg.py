"""启动收敛 + 重试记账的真 PG 契约（plan 2026-11-11-failure-recovery-durability P0/P1）。

P0 的核心断言是「进程被强杀后，重启能把会话从 generating 收敛成一个可重试的错误态」——
这是真机实验里唯一缺失的一环（崩溃后库里只有 phase=generating，其余全空），
所以必须用真库验证，mock 不出这个语义。

P1 的断言是「重试次数能跨进程读回来」：原先它只活在图的 State 里（dev=MemorySaver），
重启即归零、预算可被绕过。
"""
from __future__ import annotations

import asyncio
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


async def _user_id(pool) -> int:
    value = await pool.fetchval("SELECT id FROM app.users ORDER BY id LIMIT 1")
    assert value is not None
    return int(value)


async def _seed_stuck(pool, session_id: str, user_id: int) -> int:
    """造一个「上个进程跑到一半就死了」的会话：phase=generating，无任何版本行。"""
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO agent.session (thread_id, user_id, title, created_at, status, current_phase) "
            "VALUES ($1, $2, 'stuck', NOW(), 'active', 'generating')",
            session_id, user_id,
        )
        draft_id = await conn.fetchval(
            "INSERT INTO agent.requirement_draft "
            "(session_id, user_id, version, user_query, status, payload) "
            "VALUES ($1, $2, 1, 'q', 'complete', '{}'::jsonb) RETURNING id",
            session_id, user_id,
        )
        await conn.execute(
            "UPDATE agent.session SET latest_requirement_draft_id = $2 WHERE thread_id = $1",
            session_id, draft_id,
        )
    return int(draft_id)


async def _cleanup(pool, session_id: str) -> None:
    from app.services.session_deletion import delete_session

    async with pool.acquire() as conn:
        user_id = await conn.fetchval(
            "SELECT user_id FROM agent.session WHERE thread_id = $1", session_id,
        )
    if user_id is not None:
        try:
            await delete_session(session_id, user_id=int(user_id))
        except Exception:  # noqa: BLE001 - 收尾尽力而为
            pass


def test_reconcile_marks_interrupted_and_retryable() -> None:
    from app.infra.db.postgres import get_pool
    from app.services.startup_recovery import reconcile_orphan_sessions

    session_id = f"orphan-{uuid.uuid4().hex[:10]}"

    async def body():
        pool = get_pool()
        user_id = await _user_id(pool)
        await _seed_stuck(pool, session_id, user_id)
        try:
            stats = await reconcile_orphan_sessions()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT current_phase, last_failed_action FROM agent.session WHERE thread_id = $1",
                    session_id,
                )
                versions = await conn.fetch(
                    "SELECT status, report_payload FROM agent.report_version WHERE session_id = $1",
                    session_id,
                )
            return stats, dict(row), versions
        finally:
            await _cleanup(pool, session_id)

    stats, session_row, versions = _run(body())

    assert session_row["current_phase"] == "error"
    assert session_row["last_failed_action"] == "confirm"
    assert len(versions) == 1
    assert versions[0]["status"] == "error"
    payload = versions[0]["report_payload"]
    if isinstance(payload, str):  # asyncpg 把 jsonb 原样返回成文本
        import json

        payload = json.loads(payload)
    assert payload["error"]["kind"] == "interrupted"
    assert payload["execution_status"] == "FAILED"
    assert stats["marked_interrupted"] >= 1


def test_reconcile_only_repairs_phase_when_work_actually_finished() -> None:
    """写完版本行、还没来得及改 phase 就被杀 → 不该再加一条失败记录。"""
    from app.infra.db.postgres import get_pool
    from app.services.startup_recovery import reconcile_orphan_sessions

    session_id = f"orphan-done-{uuid.uuid4().hex[:8]}"

    async def body():
        pool = get_pool()
        user_id = await _user_id(pool)
        draft_id = await _seed_stuck(pool, session_id, user_id)
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO agent.report_version "
                "(session_id, user_id, version, title, status, report_payload, requirement_draft_id) "
                "VALUES ($1, $2, 1, 't', 'done', '{\"execution_status\":\"SUCCESS\"}'::jsonb, $3)",
                session_id, user_id, draft_id,
            )
        try:
            await reconcile_orphan_sessions()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT current_phase, last_failed_action, latest_report_version "
                    "FROM agent.session WHERE thread_id = $1",
                    session_id,
                )
                count = await conn.fetchval(
                    "SELECT count(*) FROM agent.report_version WHERE session_id = $1",
                    session_id,
                )
            return dict(row), count
        finally:
            await _cleanup(pool, session_id)

    session_row, version_count = _run(body())

    assert session_row["current_phase"] == "report_ready"
    assert session_row["last_failed_action"] is None
    assert session_row["latest_report_version"] == 1
    assert version_count == 1  # 没有多写一条 error


def test_retry_attempt_roundtrip() -> None:
    """记录 → 计数 → 自动重试预算，三个读法要对得上。"""
    from app.infra.db import retry_attempt_repository as repo
    from app.infra.db.postgres import get_pool

    session_id = f"retry-{uuid.uuid4().hex[:10]}"

    async def body():
        pool = get_pool()
        user_id = await _user_id(pool)
        await repo.record(
            session_id=session_id, user_id=user_id, requirement_draft_id=7,
            budget_key="sql_generation", action="retry_sql",
            error_kind="syntax", reason="first",
        )
        await repo.record(
            session_id=session_id, user_id=user_id, requirement_draft_id=7,
            budget_key="sql_generation", action="retry_sql",
            error_kind="syntax", reason="second",
        )
        await repo.record(
            session_id=session_id, user_id=user_id, requirement_draft_id=7,
            budget_key="auto", action="auto_retry", error_kind="timeout",
        )
        counters = await repo.counters_for(session_id, 7)
        auto = await repo.count_auto(session_id, 7)
        attempts = await repo.list_attempts(session_id)
        try:
            return counters, auto, attempts
        finally:
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM agent.retry_attempt WHERE session_id = $1", session_id,
                )

    counters, auto, attempts = _run(body())

    assert counters == {"sql_generation": 2}
    assert auto == 1
    # attempt 序号在同一 (session, draft, budget) 维度内递增
    sql_rows = [a for a in attempts if a["budget_key"] == "sql_generation"]
    assert sorted(r["attempt"] for r in sql_rows) == [1, 2]


def test_retry_attempt_record_is_noop_without_session() -> None:
    """legacy / 工具直调没有 session_id → 不写无主行。"""
    from app.infra.db import retry_attempt_repository as repo

    async def body():
        await repo.record(
            session_id="", user_id=1, requirement_draft_id=None,
            budget_key="sql_generation", action="retry_sql",
        )
        return await repo.counters_for("", None)

    assert _run(body()) == {}
