"""Repository for `agent.requirement_draft`.

Every function takes an `asyncpg.Connection` that the caller is expected to
have acquired from a transaction context. This module never opens a
transaction on its own — services in `app/services/requirement_service.py`
own the transaction boundary.
"""
from __future__ import annotations

import json
from typing import Any

from app.models.requirement import RequirementCard


async def create_draft(
    conn: Any,
    *,
    session_id: str,
    user_id: int,
    user_query: str,
    card: RequirementCard,
) -> int:
    """Insert a new draft row. Returns the assigned `id`.

    `version` is computed as MAX(version)+1 inside the same transaction so
    concurrent writers are serialized by the UNIQUE(session_id, version)
    constraint.
    """
    next_version = await conn.fetchval(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM agent.requirement_draft WHERE session_id = $1",
        session_id,
    )
    row = await conn.fetchrow(
        """INSERT INTO agent.requirement_draft
               (session_id, user_id, version, user_query, status, payload, confirmed_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           RETURNING id""",
        session_id,
        user_id,
        next_version,
        user_query,
        card.status,
        json.dumps(card.model_dump(mode="json"), ensure_ascii=False),
        card.confirmed_at,
    )
    return row["id"]


async def update_draft(
    conn: Any,
    *,
    draft_id: int,
    session_id: str,
    user_id: int,
    card: RequirementCard,
) -> int:
    """Insert a new version of the draft. Returns the new `id`.

    Increments `version` and writes a new row; old versions are kept for
    audit. Use this for PATCH /requirement.
    """
    return await create_draft(
        conn,
        session_id=session_id,
        user_id=user_id,
        user_query="",  # not changed by PATCH
        card=card,
    )


async def lock_draft(
    conn: Any,
    *,
    draft_id: int,
    user_id: int,
) -> dict:
    """Mark a draft as `locked` and stamp `confirmed_at = NOW()`.

    Returns the updated row. Raises `LockError` if the draft is missing,
    owned by another user, or already in a non-lockable state.
    """
    row = await conn.fetchrow(
        """UPDATE agent.requirement_draft
               SET status = 'locked',
                   confirmed_at = NOW(),
                   updated_at = NOW()
             WHERE id = $1 AND user_id = $2 AND status = 'complete'
         RETURNING id, session_id, user_id, version, status, payload, confirmed_at""",
        draft_id, user_id,
    )
    if row is None:
        # Diagnose: not found / wrong user / not 'complete'
        existing = await conn.fetchrow(
            "SELECT id, user_id, status FROM agent.requirement_draft WHERE id = $1",
            draft_id,
        )
        if existing is None:
            raise LockError(f"requirement_draft {draft_id} not found")
        if existing["user_id"] != user_id:
            raise LockError(f"requirement_draft {draft_id} not owned by user {user_id}")
        raise LockError(
            f"requirement_draft {draft_id} is in status '{existing['status']}', "
            f"must be 'complete' to lock"
        )
    return dict(row)


async def get_draft(
    conn: Any,
    *,
    draft_id: int,
    user_id: int,
) -> dict | None:
    row = await conn.fetchrow(
        """SELECT id, session_id, user_id, version, status, payload,
                  confirmed_at, created_at, updated_at
           FROM agent.requirement_draft
           WHERE id = $1 AND user_id = $2""",
        draft_id, user_id,
    )
    return dict(row) if row else None


async def get_latest(
    conn: Any,
    *,
    session_id: str,
    user_id: int,
) -> dict | None:
    row = await conn.fetchrow(
        """SELECT id, session_id, user_id, version, status, payload,
                  confirmed_at, created_at, updated_at
           FROM agent.requirement_draft
           WHERE session_id = $1 AND user_id = $2
           ORDER BY version DESC
           LIMIT 1""",
        session_id, user_id,
    )
    return dict(row) if row else None


async def list_drafts(
    conn: Any,
    *,
    session_id: str,
    user_id: int,
) -> list[dict]:
    rows = await conn.fetch(
        """SELECT id, session_id, user_id, version, status, payload,
                  confirmed_at, created_at, updated_at
           FROM agent.requirement_draft
           WHERE session_id = $1 AND user_id = $2
           ORDER BY version ASC""",
        session_id, user_id,
    )
    return [dict(r) for r in rows]


class LockError(Exception):
    """Raised by `lock_draft` when the draft is missing / not owned / not 'complete'."""
