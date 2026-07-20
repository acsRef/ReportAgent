from __future__ import annotations

import datetime
import uuid

from app.infra.db.postgres import get_pool


class SessionManager:
    async def create_session(self, user_id: str = "anonymous") -> str:
        session_id = str(uuid.uuid4())
        now = datetime.datetime.now()
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO agent.session (thread_id, user_id, title, created_at, status)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (thread_id) DO NOTHING""",
                session_id, user_id, "", now, "active",
            )
        return session_id

    async def get_session(self, session_id: str) -> dict | None:
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT thread_id, user_id, title, created_at, status "
                "FROM agent.session WHERE thread_id = $1",
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
                }
        return None


session_manager = SessionManager()
