"""Service layer for RequirementCard lifecycle.

Service functions own the transaction boundary. They open an asyncpg
transaction via the shared pool, perform multiple repository operations,
and commit atomically. Errors raise exceptions that propagate as
HTTPException in the API layer.
"""
from __future__ import annotations

import json
from typing import Any

from app.infra.db import requirement_repository
from app.infra.db.postgres import get_pool
from app.models.requirement import RequirementCard, RequirementMissingField


class RequirementLockedError(Exception):
    """The draft is already locked; no further modifications allowed."""


async def patch_requirement(
    *,
    session_id: str,
    user_id: int,
    incoming: RequirementCard,
) -> RequirementCard:
    """Validate + persist a PATCH from the frontend.

    Server-side rules:
    1. If the latest draft is `locked`, reject with RequirementLockedError.
    2. Recompute status: if `missing_fields` is non-empty, status='missing';
       if all assumptions resolved and missing_fields empty, status='complete'.
    3. Write a new version row.
    4. Update `agent.session.latest_requirement_draft_id`.
    5. Persist a conversation pointer (`message_type='requirement_patch'`).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            latest = await requirement_repository.get_latest(
                conn, session_id=session_id, user_id=user_id,
            )
            if latest and latest["status"] == "locked":
                raise RequirementLockedError(
                    f"requirement for session {session_id} is locked"
                )

            # Recompute status from the incoming card's structure.
            new_status = (
                "missing" if incoming.missing_fields else "complete"
            )
            # Defensive: if the client claims 'locked' via PATCH, demote it.
            incoming = incoming.model_copy(update={"status": new_status})
            # Pydantic will now re-validate (complete requires resolved
            # assumptions); if that fails, the ValidationError propagates
            # and the transaction is rolled back.

            new_id = await requirement_repository.create_draft(
                conn,
                session_id=session_id,
                user_id=user_id,
                user_query="",  # PATCH does not change the original query
                card=incoming,
            )

            await conn.execute(
                """UPDATE agent.session
                       SET latest_requirement_draft_id = $2,
                           current_phase = $3,
                           updated_at = NOW()
                     WHERE thread_id = $1""",
                session_id,
                new_id,
                "awaiting_missing" if incoming.missing_fields else "awaiting_confirm",
            )

            # Conversation pointer row (lightweight; full card is in JSONB)
            await conn.execute(
                """INSERT INTO app.conversations
                       (session_id, user_id, role, content, message_type, metadata)
                   VALUES ($1, $2, 'system', NULL, 'requirement_patch',
                           $3::jsonb)""",
                session_id, user_id,
                json.dumps({"draft_id": new_id, "version": incoming.version},
                           ensure_ascii=False),
            )

    return incoming


async def lock_for_execution(
    *,
    session_id: str,
    user_id: int,
    draft_id: int,
) -> dict:
    """Lock a draft and stamp the session phase. Returns the updated row."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await requirement_repository.lock_draft(
                conn, draft_id=draft_id, user_id=user_id,
            )
            await conn.execute(
                """UPDATE agent.session
                       SET latest_requirement_draft_id = $2,
                           current_phase = 'generating',
                           last_failed_action = NULL,
                           updated_at = NOW()
                     WHERE thread_id = $1""",
                session_id, draft_id,
            )
    return row


async def get_latest_card(
    *,
    session_id: str,
    user_id: int,
) -> RequirementCard | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await requirement_repository.get_latest(
            conn, session_id=session_id, user_id=user_id,
        )
    if row is None:
        return None
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return RequirementCard.model_validate(payload)
