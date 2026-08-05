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
from app.infra.db.requirement_repository import LockError
from app.infra.db.postgres import get_pool
from app.models.requirement import RequirementCard, RequirementMissingField
from app.utils.pii import mask_pii

# A-4/A-5：卡里所有用户可控的文本字段——脱敏与安全闸共用这份清单。
_CARD_TEXT_FIELDS = ("summary", "time_range")
_CARD_LIST_FIELDS = ("scope", "target_metrics", "dimensions", "analysis_methods")


class RequirementLockedError(Exception):
    """The draft is already locked; no further modifications allowed."""


def _mask_value(value: Any) -> Any:
    """对 str / list[str] 形态的字段值做 PII 脱敏，其它形态原样返回。"""
    if isinstance(value, str):
        return mask_pii(value)
    if isinstance(value, list):
        return [mask_pii(v) if isinstance(v, str) else v for v in value]
    return value


def _mask_card_pii(card_dict: dict) -> dict:
    """A-4：卡字段 PII 脱敏——chat 入口只 mask 了 user_query，PATCH 卡字段
    是遗漏的盲区（scope/assumption.text 等会原样注入 SQL 生成 prompt）。

    纯函数：不改入参，返回新 dict。覆盖 summary / time_range / scope /
    target_metrics / dimensions / analysis_methods / assumptions.text /
    missing_fields.selected_value。
    """
    masked = dict(card_dict)
    for key in _CARD_TEXT_FIELDS + _CARD_LIST_FIELDS:
        if key in masked:
            masked[key] = _mask_value(masked[key])
    masked["assumptions"] = [
        ({**a, "text": mask_pii(a.get("text", ""))} if isinstance(a, dict) else a)
        for a in masked.get("assumptions", [])
    ]
    masked["missing_fields"] = [
        ({**mf, "selected_value": _mask_value(mf.get("selected_value"))}
         if isinstance(mf, dict) else mf)
        for mf in masked.get("missing_fields", [])
    ]
    return masked


def card_guard_text(card_dict: dict) -> str:
    """A-5：拼接卡里全部用户可控文本字段，供 SecurityGuard.check。

    与 _mask_card_pii 共用字段清单——确保闸覆盖的面与脱敏面一致。
    """
    parts: list[str] = []
    for key in _CARD_TEXT_FIELDS:
        value = card_dict.get(key)
        if isinstance(value, str):
            parts.append(value)
    for key in _CARD_LIST_FIELDS:
        value = card_dict.get(key)
        if isinstance(value, list):
            parts.extend(v for v in value if isinstance(v, str))
    for a in card_dict.get("assumptions", []):
        if isinstance(a, dict) and isinstance(a.get("text"), str):
            parts.append(a["text"])
    for mf in card_dict.get("missing_fields", []):
        if not isinstance(mf, dict):
            continue
        sel = mf.get("selected_value")
        if isinstance(sel, str):
            parts.append(sel)
        elif isinstance(sel, list):
            parts.extend(v for v in sel if isinstance(v, str))
    return "\n".join(parts)


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
    # A-4：卡字段 PII 脱敏——在任何落库/进 prompt 之前。selected_value 会在
    # Step 1 合入结构化字段，所以必须先脱敏再合并。
    card_dict = _mask_card_pii(incoming.model_dump(mode="json"))

    # Step 1: apply selected_value → structured fields, drop filled ones.
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

    # Step 2: recompute status BEFORE re-validating. The incoming card
    # may carry `status="missing"` from the server-side analysis, even
    # though the user has now filled every missing_field and accepted
    # every assumption. Pydantic's model_validator would reject that
    # combination as "missing requirement must contain missing fields",
    # which used to surface as a silent 422 → confirm-button-does-
    # nothing failure. We normalize status from form state first.
    unresolved = [a for a in card_dict.get("assumptions", []) if a.get("accepted") is None]
    if remaining_missing or unresolved:
        new_status = "missing"
    else:
        new_status = "complete"
    card_dict["status"] = new_status
    # confirmed_at is reserved for `locked`; strip it on incomplete cards.
    if new_status != "locked":
        card_dict["confirmed_at"] = None

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
    """Lock a draft and stamp the session phase. Returns the updated row.

    Recovery: if the draft is already in `locked` state but the session
    has no report_version row, this means a previous confirm run failed
    mid-execution (after lock but before persist). Reset the draft to
    `complete` and try again so the user doesn't have to manually
    re-PATCH to recover.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                row = await requirement_repository.lock_draft(
                    conn, draft_id=draft_id, user_id=user_id,
                )
            except LockError:
                # lock_draft raises LockError when the draft is not in
                # 'complete' state. If it was already locked AND no
                # report_version exists, this is a stale lock from a
                # previous failed run — reset and retry.
                existing = await conn.fetchrow(
                    """SELECT d.status
                          FROM agent.requirement_draft d
                         WHERE d.id = $1 AND d.user_id = $2""",
                    draft_id, user_id,
                )
                if existing and existing["status"] == "locked":
                    has_v = await conn.fetchval(
                        """SELECT 1 FROM agent.report_version
                            WHERE session_id = $1 AND user_id = $2 LIMIT 1""",
                        session_id, user_id,
                    )
                    if not has_v:
                        await conn.execute(
                            """UPDATE agent.requirement_draft
                                  SET status = 'complete',
                                      confirmed_at = NULL,
                                      updated_at = NOW()
                                WHERE id = $1""",
                            draft_id,
                        )
                        row = await requirement_repository.lock_draft(
                            conn, draft_id=draft_id, user_id=user_id,
                        )
                    else:
                        raise
                else:
                    raise
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
