"""Repository for `agent.report_version`.

All functions take an `asyncpg.Connection`; callers own the transaction
boundary (so they can write report_version + conversation pointer +
session.latest_report_version atomically).
"""
from __future__ import annotations

import json
from typing import Any


class VersionConflictError(Exception):
    """Raised by `append_version` when the UNIQUE(session_id, version) constraint
    is violated despite our MAX+1 attempt. The caller should roll back the
    surrounding transaction.
    """


async def append_version(
    conn: Any,
    *,
    session_id: str,
    user_id: int,
    parent_version: int | None,
    requirement_draft_id: int | None,
    adjustment_text: str | None,
    title: str,
    status: str,
    report_payload: dict,
    query_snapshot: dict | None,
    trace_id: str | None,
) -> dict:
    """Append a new report version. Returns the inserted row.

    `version` is computed as MAX(version)+1 inside this same connection so
    the transaction provides serialization. The UNIQUE(session_id, version)
    constraint is a safety net for any race that survives the transaction
    boundary.
    """
    next_version = await conn.fetchval(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM agent.report_version WHERE session_id = $1",
        session_id,
    )
    try:
        row = await conn.fetchrow(
            """INSERT INTO agent.report_version
                   (session_id, user_id, version, parent_version,
                    requirement_draft_id, adjustment_text, title, status,
                    report_payload, query_snapshot, trace_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
               RETURNING id, session_id, user_id, version, parent_version,
                         requirement_draft_id, adjustment_text, title, status,
                         report_payload, query_snapshot, trace_id, favorite,
                         created_at""",
            session_id,
            user_id,
            next_version,
            parent_version,
            requirement_draft_id,
            adjustment_text,
            title,
            status,
            json.dumps(report_payload, ensure_ascii=False, default=str),
            json.dumps(query_snapshot, ensure_ascii=False, default=str) if query_snapshot else None,
            trace_id,
        )
    except Exception as exc:  # asyncpg.UniqueViolationError; tested in repo tests
        raise VersionConflictError(str(exc)) from exc
    return dict(row)


async def get_version(
    conn: Any,
    *,
    session_id: str,
    user_id: int,
    version: int,
) -> dict | None:
    row = await conn.fetchrow(
        """SELECT id, session_id, user_id, version, parent_version,
                  requirement_draft_id, adjustment_text, title, status,
                  report_payload, query_snapshot, trace_id, favorite,
                  created_at
           FROM agent.report_version
           WHERE session_id = $1 AND user_id = $2 AND version = $3""",
        session_id, user_id, version,
    )
    return dict(row) if row else None


async def list_versions(
    conn: Any,
    *,
    session_id: str,
    user_id: int,
) -> list[dict]:
    rows = await conn.fetch(
        """SELECT version, title, status, favorite, created_at
           FROM agent.report_version
           WHERE session_id = $1 AND user_id = $2
           ORDER BY version ASC""",
        session_id, user_id,
    )
    return [dict(r) for r in rows]


async def latest_version(
    conn: Any,
    *,
    session_id: str,
    user_id: int,
) -> dict | None:
    row = await conn.fetchrow(
        """SELECT id, session_id, user_id, version, parent_version,
                  requirement_draft_id, adjustment_text, title, status,
                  report_payload, query_snapshot, trace_id, favorite,
                  created_at
           FROM agent.report_version
           WHERE session_id = $1 AND user_id = $2
           ORDER BY version DESC
           LIMIT 1""",
        session_id, user_id,
    )
    return dict(row) if row else None


async def set_favorite(
    conn: Any,
    *,
    session_id: str,
    user_id: int,
    version: int,
    favorite: bool,
) -> bool:
    row = await conn.fetchrow(
        """UPDATE agent.report_version
               SET favorite = $4
             WHERE session_id = $1 AND user_id = $2 AND version = $3
         RETURNING id""",
        session_id, user_id, version, favorite,
    )
    return row is not None
