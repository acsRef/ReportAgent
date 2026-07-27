"""Persistence tests that require a real PostgreSQL instance.

These tests connect to the `ragent-postgres` container via `DATABASE_URL`
(loaded from `.env` in conftest.py). They use a per-test session_id / user_id
to keep tests isolated, and clean up their own rows in a finally block.

Marked with `@pytest.mark.persistence` so `pytest -k "not persistence"`
runs only the unit/smoke tests.

Implementation note: each test is a SYNC function that runs its own
`asyncio.run` inside, opening and closing a fresh asyncpg pool. This avoids
a known Windows + asyncpg interaction where the default executor is bound
to one event loop while asyncpg's DNS lookup creates futures on a different
loop. The pattern is slower per test but reliable.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest


pytestmark = pytest.mark.persistence


def _run(coro):
    """Drive a coroutine in a fresh event loop with its own asyncpg pool."""
    from app.infra.db.postgres import init_pool, close_pool

    async def _body():
        await init_pool()
        try:
            return await coro
        finally:
            await close_pool()

    return asyncio.run(_body())


# ---------------------------------------------------------------------------
# Session manager user_id typing
# ---------------------------------------------------------------------------


def test_session_create_user_id_persists() -> None:
    from app.infra.checkpoint.session import session_manager
    from app.infra.db.postgres import get_pool

    sid = f"test-{uuid.uuid4()}"
    user_id = 1

    async def body():
        try:
            await session_manager.create_session(sid, user_id=user_id)
            row = await session_manager.get_session(sid)
            assert row is not None
            assert row["user_id"] == user_id
            assert isinstance(row["user_id"], int)
        finally:
            pool = get_pool()
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM agent.session WHERE thread_id = $1", sid)

    _run(body())


def test_session_update_phase_and_failed_action() -> None:
    from app.infra.checkpoint.session import session_manager
    from app.infra.db.postgres import get_pool

    sid = f"test-{uuid.uuid4()}"

    async def body():
        try:
            await session_manager.create_session(sid, user_id=1)
            await session_manager.update_phase(sid, "awaiting_missing", failed_action="new")
            row = await session_manager.get_session(sid)
            assert row["current_phase"] == "awaiting_missing"
            assert row["last_failed_action"] == "new"

            await session_manager.update_phase(sid, "idle")
            row = await session_manager.get_session(sid)
            assert row["current_phase"] == "idle"
            assert row["last_failed_action"] is None
        finally:
            pool = get_pool()
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM agent.session WHERE thread_id = $1", sid)

    _run(body())


# ---------------------------------------------------------------------------
# Requirement draft repository
# ---------------------------------------------------------------------------


def test_create_and_get_latest_draft() -> None:
    from app.infra.db import requirement_repository
    from app.infra.db.postgres import get_pool
    from app.models.requirement import RequirementAssumption, RequirementCard

    sid = f"test-{uuid.uuid4()}"

    async def body():
        card = RequirementCard(
            id="draft-test",
            status="complete",
            summary="test",
            target_metrics=["销售额"],
            missing_fields=[],
            assumptions=[
                RequirementAssumption(key="a1", text="默认华东", accepted=True),
            ],
        )
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                draft_id = await requirement_repository.create_draft(
                    conn,
                    session_id=sid, user_id=1, user_query="华东销售", card=card,
                )
                assert draft_id > 0

                latest = await requirement_repository.get_latest(
                    conn, session_id=sid, user_id=1,
                )
                assert latest is not None
                assert latest["id"] == draft_id
                assert latest["version"] == 1
                assert latest["status"] == "complete"
        finally:
            pool = get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM agent.requirement_draft WHERE session_id = $1", sid,
                )
                await conn.execute(
                    "DELETE FROM agent.session WHERE thread_id = $1", sid,
                )

    _run(body())


def test_lock_for_execution_recovers_stale_lock() -> None:
    """lock_for_execution 应自动恢复陈旧锁（locked 但无 report_version）。"""
    from app.infra.db import requirement_repository
    from app.infra.db.postgres import get_pool
    from app.infra.checkpoint.session import session_manager
    from app.models.requirement import RequirementAssumption, RequirementCard
    from app.services.requirement_service import lock_for_execution

    sid = f"test-{uuid.uuid4()}"

    async def body():
        try:
            pool = get_pool()
            # 创建 session + 一个 complete 草稿
            await session_manager.create_session(sid, user_id=1)
            card = RequirementCard(
                id="draft-stale",
                status="complete", summary="test",
                target_metrics=["销售额"], missing_fields=[],
                assumptions=[RequirementAssumption(key="a1", text="默认", accepted=True)],
            )
            async with pool.acquire() as conn:
                draft_id = await requirement_repository.create_draft(
                    conn, session_id=sid, user_id=1, user_query="销售", card=card,
                )
                # 模拟崩溃场景：直接把草稿标记为 locked，但无 report_version
                await conn.execute(
                    """UPDATE agent.requirement_draft
                          SET status = 'locked', confirmed_at = NOW(), updated_at = NOW()
                        WHERE id = $1""",
                    draft_id,
                )
            # lock_for_execution 应检测到陈旧锁并恢复
            row = await lock_for_execution(
                session_id=sid, user_id=1, draft_id=draft_id,
            )
            assert row is not None
            assert row["status"] == "locked"
            assert row["id"] == draft_id
            assert row["confirmed_at"] is not None
        finally:
            pool = get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE agent.session SET latest_requirement_draft_id = NULL WHERE thread_id = $1", sid,
                )
                await conn.execute(
                    "DELETE FROM agent.requirement_draft WHERE session_id = $1", sid,
                )
                await conn.execute(
                    "DELETE FROM agent.session WHERE thread_id = $1", sid,
                )

    _run(body())


def test_lock_draft_rejects_incomplete() -> None:
    from app.infra.db import requirement_repository
    from app.infra.db.postgres import get_pool
    from app.models.requirement import (
        RequirementCard, RequirementMissingField, RequirementOption,
    )

    sid = f"test-{uuid.uuid4()}"

    async def body():
        card = RequirementCard(
            id="draft-missing",
            status="missing",
            summary="test",
            missing_fields=[
                RequirementMissingField(
                    key="time_range", label="时间",
                    options=[RequirementOption(label="本月", value="本月")],
                ),
            ],
        )
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                draft_id = await requirement_repository.create_draft(
                    conn,
                    session_id=sid, user_id=1, user_query="销售", card=card,
                )
                with pytest.raises(requirement_repository.LockError) as exc:
                    await requirement_repository.lock_draft(
                        conn, draft_id=draft_id, user_id=1,
                    )
                assert "must be 'complete' to lock" in str(exc.value)
        finally:
            pool = get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM agent.requirement_draft WHERE session_id = $1", sid,
                )
                await conn.execute(
                    "DELETE FROM agent.session WHERE thread_id = $1", sid,
                )

    _run(body())


# ---------------------------------------------------------------------------
# Report version repository
# ---------------------------------------------------------------------------


def test_append_report_version_first_run() -> None:
    from app.infra.db import report_version_repository
    from app.infra.db.postgres import get_pool

    sid = f"test-{uuid.uuid4()}"

    async def body():
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                row = await report_version_repository.append_version(
                    conn,
                    session_id=sid, user_id=1, parent_version=None,
                    requirement_draft_id=None, adjustment_text=None,
                    title="v1 报告", status="done",
                    report_payload={"answer": {"text": "ok"}},
                    query_snapshot={"sql": "SELECT 1", "columns": [], "rows": []},
                    trace_id="t-1",
                )
                assert row["version"] == 1
                assert row["parent_version"] is None
        finally:
            pool = get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM agent.report_version WHERE session_id = $1", sid,
                )
                await conn.execute(
                    "DELETE FROM agent.session WHERE thread_id = $1", sid,
                )

    _run(body())


def test_append_report_version_adjust_increments_with_parent() -> None:
    from app.infra.db import report_version_repository
    from app.infra.db.postgres import get_pool

    sid = f"test-{uuid.uuid4()}"

    async def body():
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                v1 = await report_version_repository.append_version(
                    conn,
                    session_id=sid, user_id=1, parent_version=None,
                    requirement_draft_id=None, adjustment_text=None,
                    title="v1", status="done",
                    report_payload={"answer": {"text": "v1"}},
                    query_snapshot=None, trace_id="t-1",
                )
                v2 = await report_version_repository.append_version(
                    conn,
                    session_id=sid, user_id=1,
                    parent_version=v1["version"], requirement_draft_id=None,
                    adjustment_text="增加华南", title="v2", status="done",
                    report_payload={"answer": {"text": "v2"}},
                    query_snapshot=None, trace_id="t-2",
                )
                assert v2["version"] == 2
                assert v2["parent_version"] == 1
        finally:
            pool = get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM agent.report_version WHERE session_id = $1", sid,
                )
                await conn.execute(
                    "DELETE FROM agent.session WHERE thread_id = $1", sid,
                )

    _run(body())


# ---------------------------------------------------------------------------
# Template repository — user isolation
# ---------------------------------------------------------------------------


def test_templates_list_scoped_by_user() -> None:
    from app.infra.db import template_repository
    from app.infra.db.postgres import get_pool

    user_a = 1
    user_b = 999_999
    name_a = f"tpl-{uuid.uuid4()}"

    async def body():
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                await template_repository.create(
                    conn,
                    user_id=user_a, name=name_a, description="",
                    requirement_payload={"k": "v"},
                )
                a_list = await template_repository.list_for_user(conn, user_id=user_a)
                b_list = await template_repository.list_for_user(conn, user_id=user_b)
                names_a = [t["name"] for t in a_list]
                assert name_a in names_a
                assert all(t["user_id"] == user_a for t in a_list)
                assert b_list == []
        finally:
            pool = get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM app.report_template WHERE name = $1", name_a,
                )

    _run(body())


def test_template_delete_404_for_other_user() -> None:
    from app.infra.db import template_repository
    from app.infra.db.postgres import get_pool

    name = f"tpl-{uuid.uuid4()}"

    async def body():
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                row = await template_repository.create(
                    conn,
                    user_id=1, name=name, description="",
                    requirement_payload={"k": "v"},
                )
                deleted = await template_repository.delete(
                    conn, template_id=row["id"], user_id=2,
                )
                assert deleted is False

                deleted = await template_repository.delete(
                    conn, template_id=row["id"], user_id=1,
                )
                assert deleted is True
        finally:
            pool = get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM app.report_template WHERE name = $1", name,
                )

    _run(body())
