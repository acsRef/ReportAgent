"""B7 幂等闸 · 真 PG 契约：done 重放、error 可重试。

这一条钉的是**仓储判定口径**（`find_done_by_draft` 只认 status='done'）：
- 有 FAILED 版本时**不得**重放（否则一次失败把 draft 永久锁死，用户无法重试）；
- 有 SUCCESS / EMPTY 版本时**必须**重放（否则重复执行、重复落库）。

沿用本目录约定：sync 测试 + 自建 event loop + 独立 asyncpg pool。
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


def test_error_version_allows_retry_but_done_version_replays() -> None:
    from app.agent import confirmed_execution_graph as ceg
    from app.infra.db import report_version_repository as rvr
    from app.infra.db import requirement_repository
    from app.infra.db.postgres import get_pool
    from app.models.requirement import RequirementCard

    sid = f"idem-{uuid.uuid4()}"
    uname = f"idem_{uuid.uuid4().hex[:12]}"

    async def body():
        pool = get_pool()
        async with pool.acquire() as conn:
            user_id = await conn.fetchval(
                "INSERT INTO app.users (username, password_hash) VALUES ($1, 'x') RETURNING id",
                uname,
            )
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    draft_id = await requirement_repository.create_draft(
                        conn, session_id=sid, user_id=user_id, user_query="q",
                        card=RequirementCard(id=uuid.uuid4().hex, status="complete", summary="s"),
                    )
            state = {
                "user_id": user_id,
                "session_id": sid,
                "trace_id": "t",
                "draft_id": draft_id,
                "base_report_version": None,
            }

            # ① FAILED 版本存在 → 允许重试（不得重放）
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await rvr.append_version(
                        conn, session_id=sid, user_id=user_id, parent_version=None,
                        requirement_draft_id=draft_id, adjustment_text=None, title="报告",
                        status="error",
                        report_payload={"answer": {"text": "查询执行失败"},
                                        "execution_status": "FAILED"},
                        query_snapshot=None, trace_id=None,
                    )
            out = await ceg._idempotency_guard(dict(state))
            assert out.get("replayed_version") is None, "FAILED 必须允许重试"

            # ② 补一条 done（EMPTY）版本 → 必须重放它
            async with pool.acquire() as conn:
                async with conn.transaction():
                    row = await rvr.append_version(
                        conn, session_id=sid, user_id=user_id, parent_version=None,
                        requirement_draft_id=draft_id, adjustment_text=None, title="报告",
                        status="done",
                        report_payload={"answer": {"text": "未匹配"}, "execution_status": "EMPTY"},
                        query_snapshot=None, trace_id=None,
                    )
            out2 = await ceg._idempotency_guard(dict(state))
            assert out2["replayed_version"] == row["version"]
            assert out2["execution_status"] == "EMPTY"
            assert out2["report_payload"]["version"] == row["version"]

            # ③ 断言没有新增行：重放不落库
            async with pool.acquire() as conn:
                n = await conn.fetchval(
                    "SELECT count(*) FROM agent.report_version WHERE session_id = $1", sid
                )
            assert n == 2, f"重放不得新增版本行，实际 {n}"
        finally:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM agent.report_version WHERE session_id = $1", sid)
                await conn.execute("DELETE FROM agent.requirement_draft WHERE session_id = $1", sid)
                await conn.execute("DELETE FROM app.users WHERE id = $1", user_id)

    _run(body())
