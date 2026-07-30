"""B-7 并发竞态测试：MAX(version)+1 必须在并发下分配出连续不撞的版本号。

修复前：READ COMMITTED 下两个并发事务都读到同一 MAX → 后提交者撞
UNIQUE(session_id, version) → VersionConflictError → 调用方 500。
修复后：append_version / create_draft 先取事务级咨询锁
pg_advisory_xact_lock(ns, hashtext(session_id))，同 session 的版本号分配串行化。

实现沿用本目录约定：sync 测试 + 自建 event loop + 独立 asyncpg pool。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

pytestmark = pytest.mark.persistence

N = 8  # < asyncpg pool max_size(10)，避免连接等待与锁等待交织


def _run(coro):
    from app.infra.db.postgres import init_pool, close_pool

    async def _body():
        await init_pool()
        try:
            return await coro
        finally:
            await close_pool()

    return asyncio.run(_body())


def test_concurrent_append_version_assigns_contiguous_versions():
    from app.infra.db import report_version_repository
    from app.infra.db.postgres import get_pool

    sid = f"conc-rv-{uuid.uuid4()}"
    uname = f"conc_rv_{uuid.uuid4().hex[:12]}"

    async def body():
        pool = get_pool()
        async with pool.acquire() as conn:
            user_id = await conn.fetchval(
                "INSERT INTO app.users (username, password_hash) VALUES ($1, 'x') RETURNING id",
                uname,
            )
        try:
            async def one():
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        return await report_version_repository.append_version(
                            conn,
                            session_id=sid,
                            user_id=user_id,
                            parent_version=None,
                            requirement_draft_id=None,
                            adjustment_text=None,
                            title="t",
                            status="done",
                            report_payload={"answer": {}},
                            query_snapshot=None,
                            trace_id=None,
                        )

            rows = await asyncio.gather(*[one() for _ in range(N)])
            versions = sorted(r["version"] for r in rows)
            assert versions == list(range(1, N + 1))
        finally:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM agent.report_version WHERE session_id = $1", sid)
                await conn.execute("DELETE FROM app.users WHERE id = $1", user_id)

    _run(body())


def test_concurrent_create_draft_assigns_contiguous_versions():
    from app.infra.db import requirement_repository
    from app.infra.db.postgres import get_pool
    from app.models.requirement import RequirementCard

    sid = f"conc-draft-{uuid.uuid4()}"
    uname = f"conc_dr_{uuid.uuid4().hex[:12]}"

    async def body():
        pool = get_pool()
        async with pool.acquire() as conn:
            user_id = await conn.fetchval(
                "INSERT INTO app.users (username, password_hash) VALUES ($1, 'x') RETURNING id",
                uname,
            )
        try:
            async def one():
                card = RequirementCard(id=uuid.uuid4().hex, status="complete", summary="s")
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        return await requirement_repository.create_draft(
                            conn, session_id=sid, user_id=user_id,
                            user_query="q", card=card,
                        )

            await asyncio.gather(*[one() for _ in range(N)])
            async with pool.acquire() as conn:
                versions = await conn.fetch(
                    "SELECT version FROM agent.requirement_draft WHERE session_id = $1 ORDER BY version",
                    sid,
                )
            assert [r["version"] for r in versions] == list(range(1, N + 1))
        finally:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM agent.requirement_draft WHERE session_id = $1", sid)
                await conn.execute("DELETE FROM app.users WHERE id = $1", user_id)

    _run(body())
