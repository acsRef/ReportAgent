from __future__ import annotations

import json

from app.infra.db.postgres import get_pool


async def save_message(
    session_id: str,
    user_id: int,
    role: str,
    content: str,
    message_type: str = "text",
    metadata: dict | None = None,
):
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO app.conversations (session_id, user_id, role, content, message_type, metadata)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            session_id, user_id, role, content, message_type,
            json.dumps(metadata) if metadata else None,
        )


async def get_messages(session_id: str, user_id: int) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, role, content, message_type, metadata, created_at "
            "FROM app.conversations "
            "WHERE session_id = $1 AND user_id = $2 "
            "ORDER BY created_at",
            session_id, user_id,
        )
        return [
            {
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "message_type": r["message_type"],
                "metadata": r["metadata"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in rows
        ]


async def list_sessions(user_id: int) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT session_id, COUNT(*) as msg_count,
                      MIN(created_at) as first_msg,
                      MAX(created_at) as last_msg
               FROM app.conversations
               WHERE user_id = $1
               GROUP BY session_id
               ORDER BY last_msg DESC""",
            user_id,
        )
        return [
            {
                "session_id": r["session_id"],
                "msg_count": r["msg_count"],
                "first_message": r["first_msg"].isoformat(),
                "last_message": r["last_msg"].isoformat(),
            }
            for r in rows
        ]
