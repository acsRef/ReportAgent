from __future__ import annotations

import datetime
import uuid

from app.infra.db.postgres import get_pool


class SessionManager:
    async def create_session(self, session_id: str, user_id: int) -> str:
        """Insert a session row. If a row with this thread_id already exists,
        refresh `last_checkpoint_at` but keep the original `user_id` (a session
        cannot be reassigned to a different user).
        """
        now = datetime.datetime.now()
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO agent.session (thread_id, user_id, title, created_at, status, last_checkpoint_at)
                   VALUES ($1, $2, $3, $4, $5, $6)
                   ON CONFLICT (thread_id) DO UPDATE SET last_checkpoint_at = $6""",
                session_id, user_id, "", now, "active", now,
            )
        return session_id

    async def update_checkpoint_time(self, session_id: str) -> None:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE agent.session SET last_checkpoint_at = NOW() WHERE thread_id = $1",
                session_id,
            )

    async def update_phase(
        self, session_id: str, phase: str, failed_action: str | None = None
    ) -> None:
        """Set the current phase (and optionally last_failed_action).

        When `failed_action` is None, the column is cleared — used on retry
        success / new phase transition out of error.
        """
        pool = get_pool()
        async with pool.acquire() as conn:
            if failed_action is None:
                await conn.execute(
                    "UPDATE agent.session SET current_phase = $2, last_failed_action = NULL "
                    "WHERE thread_id = $1",
                    session_id, phase,
                )
            else:
                await conn.execute(
                    "UPDATE agent.session SET current_phase = $2, last_failed_action = $3 "
                    "WHERE thread_id = $1",
                    session_id, phase, failed_action,
                )

    async def update_latest_requirement(
        self, session_id: str, draft_id: int | None
    ) -> None:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE agent.session SET latest_requirement_draft_id = $2 "
                "WHERE thread_id = $1",
                session_id, draft_id,
            )

    async def update_latest_report_version(
        self, session_id: str, version: int | None
    ) -> None:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE agent.session SET latest_report_version = $2 "
                "WHERE thread_id = $1",
                session_id, version,
            )

    async def get_session(self, session_id: str) -> dict | None:
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT thread_id, user_id, title, created_at, status, last_checkpoint_at,
                          current_phase, last_failed_action,
                          latest_requirement_draft_id, latest_report_version
                   FROM agent.session WHERE thread_id = $1""",
                session_id,
            )
            if row:
                return {
                    "session_id": session_id,
                    "thread_id": row["thread_id"],
                    "user_id": row["user_id"],
                    "title": row["title"],
                    "created_at": row["created_at"],
                    "status": row["status"],
                    "last_checkpoint_at": row.get("last_checkpoint_at"),
                    "current_phase": row.get("current_phase"),
                    "last_failed_action": row.get("last_failed_action"),
                    "latest_requirement_draft_id": row.get("latest_requirement_draft_id"),
                    "latest_report_version": row.get("latest_report_version"),
                }
        return None

    # --- 分层对话上下文 digest 状态（L2 摘要 / L2.5 归档） ---------------------
    # 见 docs/plans/2026-08-01-memory-mechanism.md。digest 为覆盖重写的叙事摘要，
    # digest_msg_count 记已压缩到的消息数，digest_version 为重写计数（每 N 次归档 L2.5）。

    _CONTEXT_FIELDS = ("digest", "digest_msg_count", "digest_version", "mid_digest")

    async def get_context_state(self, session_id: str) -> dict:
        """读取 digest 状态；无 session 返回零值态（便于首次构建上下文）。"""
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT digest, digest_msg_count, digest_version, mid_digest "
                "FROM agent.session WHERE thread_id = $1",
                session_id,
            )
        if not row:
            return {"digest": None, "digest_msg_count": 0, "digest_version": 0, "mid_digest": None}
        return {
            "digest": row["digest"],
            "digest_msg_count": row["digest_msg_count"] or 0,
            "digest_version": row["digest_version"] or 0,
            "mid_digest": row["mid_digest"],
        }

    async def save_context_state(self, session_id: str, updates: dict) -> None:
        """回写 digest 状态——只接受白名单内的 4 列，杜绝任意键注入。"""
        fields = {k: updates[k] for k in self._CONTEXT_FIELDS if k in updates}
        if not fields:
            return
        set_clause = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(fields))
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE agent.session SET {set_clause} WHERE thread_id = $1",
                session_id, *fields.values(),
            )


session_manager = SessionManager()
