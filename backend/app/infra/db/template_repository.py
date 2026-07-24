"""Repository for `app.report_template`.

All functions take an `asyncpg.Connection`; callers own the transaction.
"""
from __future__ import annotations

import json
from typing import Any


async def create(
    conn: Any,
    *,
    user_id: int,
    name: str,
    description: str,
    requirement_payload: dict,
) -> dict:
    row = await conn.fetchrow(
        """INSERT INTO app.report_template (user_id, name, description, requirement_payload)
           VALUES ($1, $2, $3, $4)
           RETURNING id, user_id, name, description, requirement_payload, created_at, updated_at""",
        user_id, name, description,
        json.dumps(requirement_payload, ensure_ascii=False, default=str),
    )
    return dict(row)


async def list_for_user(conn: Any, *, user_id: int) -> list[dict]:
    rows = await conn.fetch(
        """SELECT id, user_id, name, description, requirement_payload, created_at, updated_at
           FROM app.report_template
           WHERE user_id = $1
           ORDER BY updated_at DESC""",
        user_id,
    )
    return [dict(r) for r in rows]


async def get(conn: Any, *, template_id: int, user_id: int) -> dict | None:
    row = await conn.fetchrow(
        """SELECT id, user_id, name, description, requirement_payload, created_at, updated_at
           FROM app.report_template
           WHERE id = $1 AND user_id = $2""",
        template_id, user_id,
    )
    return dict(row) if row else None


async def rename(
    conn: Any,
    *,
    template_id: int,
    user_id: int,
    name: str,
    description: str | None = None,
) -> dict | None:
    if description is None:
        row = await conn.fetchrow(
            """UPDATE app.report_template
                   SET name = $3, updated_at = NOW()
                 WHERE id = $1 AND user_id = $2
             RETURNING id, user_id, name, description, requirement_payload, created_at, updated_at""",
            template_id, user_id, name,
        )
    else:
        row = await conn.fetchrow(
            """UPDATE app.report_template
                   SET name = $3, description = $4, updated_at = NOW()
                 WHERE id = $1 AND user_id = $2
             RETURNING id, user_id, name, description, requirement_payload, created_at, updated_at""",
            template_id, user_id, name, description,
        )
    return dict(row) if row else None


async def delete(conn: Any, *, template_id: int, user_id: int) -> bool:
    row = await conn.fetchrow(
        "DELETE FROM app.report_template WHERE id = $1 AND user_id = $2 RETURNING id",
        template_id, user_id,
    )
    return row is not None
