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
    2. For each `missing_field` that carries a `selected_value`, apply it
       to the card's structured fields and drop it from missing_fields.
    3. Recompute status: if any missing_fields remain OR any assumption
       is still unresolved, status='missing'; else 'complete'.
    4. Write a new version row.
    5. Update `agent.session.latest_requirement_draft_id` + current_phase.
    6. Persist a conversation pointer (`message_type='requirement_patch'`).
    """
    # Step 1: apply selected_value → structured fields, drop filled ones.
    card_dict = incoming.model_dump(mode="json")
    remaining_missing: list[dict] = []
    for mf in card_dict.get("missing_fields", []):
        sel = mf.get("selected_value")
        key = mf.get("key")
        if sel is None or sel == "" or sel == []:
            remaining_missing.append(mf)
            continue
        if key == "time_range":
            card_dict["time_range"] = sel if isinstance(sel, str) else sel[0]
        elif key == "scope":
            card_dict["scope"] = sel if isinstance(sel, list) else [sel]
        elif key == "metric":
            metrics = sel if isinstance(sel, list) else [sel]
            card_dict["target_metrics"] = metrics
        elif key == "granularity":
            # granularity only affects SQL, not card body; record into
            # analysis_methods or dimensions as a hint.
            card_dict["dimensions"] = list(
                set(card_dict.get("dimensions", []) + [f"粒度:{sel}"]),
            )
        elif key == "comparison":
            card_dict["dimensions"] = list(
                set(card_dict.get("dimensions", []) + [f"对比:{sel}"]),
            )
        # else: drop from missing_fields (filled)
    card_dict["missing_fields"] = remaining_missing

    # Step 2: recompute status.
    unresolved = [a for a in card_dict.get("assumptions", []) if a.get("accepted") is None]
    if remaining_missing or unresolved:
        new_status = "missing"
    else:
        new_status = "complete"
    card_dict["status"] = new_status

    # Re-validate (Pydantic will raise on inconsistent states).
    new_card = RequirementCard.model_validate(card_dict)

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

            new_id = await requirement_repository.create_draft(
                conn,
                session_id=session_id,
                user_id=user_id,
                user_query="",
                card=new_card,
            )

            await conn.execute(
                """UPDATE agent.session
                       SET latest_requirement_draft_id = $2,
                           current_phase = $3,
                           updated_at = NOW()
                     WHERE thread_id = $1""",
                session_id,
                new_id,
                "awaiting_missing" if new_card.missing_fields else "awaiting_confirm",
            )

            await conn.execute(
                """INSERT INTO app.conversations
                       (session_id, user_id, role, content, message_type, metadata)
                   VALUES ($1, $2, 'system', NULL, 'requirement_patch',
                           $3::jsonb)""",
                session_id, user_id,
                json.dumps({"draft_id": new_id, "version": new_card.version},
                           ensure_ascii=False),
            )

    return new_card


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
