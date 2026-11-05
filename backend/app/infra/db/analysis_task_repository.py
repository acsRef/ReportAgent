"""Task Revision 持久化：每轮「任务理解 → 状态归并」的审计记录（append-only）。

plan 2026-11-05-turn-understanding-agent。为什么单独建表而不是塞进 requirement_draft：
卡片是**执行契约**（会被 PATCH、被投影），审计是**事实日志**——两者生命周期不同。
有了它才能回答「这份报告为什么长这样」：第几轮、改了哪些字段、依据的原话证据是什么。

表在首次使用时按需创建（`CREATE TABLE IF NOT EXISTS`），避免要求所有人重跑
`init_pg.sql`；DDL 同时补进 `backend/scripts/init_pg.sql` 的注释区，新环境直接建好。
"""
from __future__ import annotations

import asyncio
import json
import logging

from app.infra.db.postgres import get_pool

logger = logging.getLogger(__name__)

_DDL = """
CREATE SCHEMA IF NOT EXISTS agent;
CREATE TABLE IF NOT EXISTS agent.analysis_task_revision (
    id           BIGSERIAL PRIMARY KEY,
    session_id   VARCHAR(64) NOT NULL,
    user_id      INTEGER,
    revision     INTEGER NOT NULL,
    act          VARCHAR(32) NOT NULL,
    task         JSONB NOT NULL,
    changes      JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence     JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at   TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_analysis_task_revision_session
    ON agent.analysis_task_revision (session_id, revision DESC);
"""

_ensured = False


async def ensure_table() -> None:
    global _ensured
    if _ensured:
        return
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(_DDL)
    _ensured = True


async def save_revision(
    session_id: str, user_id: int, task, revision,
) -> None:
    """写一条审计记录（append-only；失败只告警，不影响主流程）。"""
    await ensure_table()
    pool = get_pool()
    payload = task.model_dump(mode="json") if hasattr(task, "model_dump") else dict(task)
    changes = {
        key: list(value) if isinstance(value, tuple) else value
        for key, value in (getattr(revision, "changes", {}) or {}).items()
    }
    evidence = list(getattr(revision, "evidence", []) or [])
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO agent.analysis_task_revision
                   (session_id, user_id, revision, act, task, changes, evidence)
               VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb)""",
            session_id, int(user_id or 0), int(getattr(task, "revision", 0)),
            str(getattr(revision, "conversation_act", "new_task")),
            json.dumps(payload, ensure_ascii=False),
            json.dumps(changes, ensure_ascii=False, default=str),
            json.dumps(evidence, ensure_ascii=False),
        )


async def load_latest_task(session_id: str):
    """读最近一轮的任务状态（不存在返回 None）。"""
    from app.models.turn_plan import ActiveAnalysisTask

    await ensure_table()
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT task FROM agent.analysis_task_revision "
            "WHERE session_id = $1 ORDER BY revision DESC LIMIT 1",
            session_id,
        )
    if row is None:
        return None
    payload = row["task"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    try:
        return ActiveAnalysisTask.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - 旧数据不合法就当没有历史
        logger.warning("load_latest_task invalid payload: %s", exc)
        return None


async def list_revisions(session_id: str, limit: int = 20) -> list[dict]:
    """任务变更日志（前端/调试用）。"""
    await ensure_table()
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT revision, act, changes, evidence, created_at "
            "FROM agent.analysis_task_revision WHERE session_id = $1 "
            "ORDER BY revision DESC LIMIT $2",
            session_id, limit,
        )
    return [
        {
            "revision": row["revision"],
            "act": row["act"],
            "changes": row["changes"],
            "evidence": row["evidence"],
            "created_at": row["created_at"].isoformat(),
        }
        for row in rows
    ]


def _run_sync(coro):
    """在同步节点里跑协程：已有事件循环时用独立线程（graph 节点是 sync）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import threading

    box: dict = {}

    def _worker() -> None:
        box["value"] = asyncio.run(coro)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join()
    return box.get("value")


def save_task_blocking(session_id: str, user_id: int, task, revision) -> None:
    _run_sync(save_revision(session_id, user_id, task, revision))


def load_task_blocking(session_id: str):
    return _run_sync(load_latest_task(session_id))
