from __future__ import annotations

import json

from app.infra.db.postgres import get_pool
from app.utils.timefmt import utc_iso


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
                "created_at": utc_iso(r["created_at"]),
            }
            for r in rows
        ]


async def list_sessions(user_id: int, limit: int = 30, offset: int = 0) -> list[dict]:
    """List sessions for a user. Joins `app.conversations` (for msg_count +
    first/last message) with `agent.session` (for title / phase /
    current_phase / updated_at / report_versions).

    Pagination (2026-08-09 Plan B): LIMIT + OFFSET for SessionRail render.
    Without it, accumulated test sessions slow SessionRail fetch past the
    30s Playwright wait and the chat input never becomes visible.
    Frontend uses a single default page (limit=30) plus a "Load more" button
    that increments offset. Total count is returned in headers by
    get_sessions() so the UI can decide whether to show the button.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT
                 s.thread_id        AS session_id,
                 s.title            AS title,
                 s.current_phase    AS phase,
                 COALESCE(conv.msg_count, 0)     AS msg_count,
                 COALESCE(conv.last_msg, s.created_at) AS updated_at,
                 conv.first_message_text          AS first_message,
                 conv.last_message_text           AS last_message
               FROM agent.session s
               LEFT JOIN (
                 SELECT session_id,
                        COUNT(*) AS msg_count,
                        MAX(created_at) AS last_msg,
                        (array_agg(content ORDER BY created_at ASC))[1] AS first_message_text,
                        (array_agg(content ORDER BY created_at DESC))[1] AS last_message_text
                 FROM app.conversations
                 WHERE user_id = $1
                 GROUP BY session_id
               ) conv ON conv.session_id = s.thread_id
               WHERE s.user_id = $1
               ORDER BY updated_at DESC
               LIMIT $2 OFFSET $3""",
            user_id, limit, offset,
        )
        # 2026-11-03：report_versions 此前恒为 []（注释写着「lazily populated」，
        # 但从来没有代码去填），前端拿到的会话摘要里版本列表永远是空的。
        # 这里用**一次**批量查询补齐，避免 N+1。
        versions_by_session: dict[str, list[dict]] = {}
        session_ids = [r["session_id"] for r in rows]
        if session_ids:
            version_rows = await conn.fetch(
                """SELECT session_id, version, title, status, favorite, created_at
                     FROM agent.report_version
                    WHERE user_id = $1 AND session_id = ANY($2::text[])
                    ORDER BY version ASC""",
                user_id, session_ids,
            )
            for v in version_rows:
                versions_by_session.setdefault(v["session_id"], []).append({
                    "version": v["version"],
                    "title": v["title"] or "",
                    "status": v["status"],
                    "favorite": bool(v["favorite"]),
                    "created_at": utc_iso(v["created_at"]) or "",
                })
        return [
            {
                "session_id": r["session_id"],
                "title": r["title"] or "",
                "phase": r["phase"] or "idle",
                "msg_count": r["msg_count"],
                "updated_at": utc_iso(r["updated_at"]) or "",
                "first_message": r["first_message"] or "",
                "last_message": r["last_message"] or "",
                "report_versions": versions_by_session.get(r["session_id"], []),
            }
            for r in rows
        ]
