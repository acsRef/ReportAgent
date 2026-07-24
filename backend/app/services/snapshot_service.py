"""Service layer for `GET /api/v1/sessions/{sid}`.

Aggregates session + messages + current requirement + latest report +
last_failed_action in a single read.
"""
from __future__ import annotations

import json
from typing import Any

from app.infra.db import requirement_repository, report_version_repository
from app.infra.db.postgres import get_pool
from app.infra.checkpoint.session import session_manager
from app.infra.conversation.repository import get_messages


async def get_session_snapshot(
    *,
    session_id: str,
    user_id: int,
) -> dict | None:
    """Return the full session snapshot, or None if not owned by user_id.

    Shape (matches docs/api-reference.md §2 GET /api/v1/sessions/{sid}):
        {
          session: { session_id, title, phase, msg_count, updated_at,
                     report_versions: [...] },
          messages: [...],
          current_requirement: RequirementCard | null,
          latest_report: { version, title, report } | null,
          last_failed_action: str | null,
        }
    """
    sess = await session_manager.get_session(session_id)
    if sess is None or sess.get("user_id") != user_id:
        return None

    pool = get_pool()
    async with pool.acquire() as conn:
        versions = await report_version_repository.list_versions(
            conn, session_id=session_id, user_id=user_id,
        )
        latest_draft = await requirement_repository.get_latest(
            conn, session_id=session_id, user_id=user_id,
        )
        latest_report = await report_version_repository.latest_version(
            conn, session_id=session_id, user_id=user_id,
        )

    messages = await get_messages(session_id, user_id)

    snapshot = {
        "session": {
            "session_id": session_id,
            "title": sess.get("title") or "",
            "phase": sess.get("current_phase") or "idle",
            "msg_count": len(messages),
            "updated_at": sess.get("created_at"),
            "report_versions": [
                {
                    "version": v["version"],
                    "title": v["title"],
                    "status": v["status"],
                    "created_at": v["created_at"],
                    "favorite": v["favorite"],
                }
                for v in versions
            ],
        },
        "messages": messages,
        "current_requirement": _decode_requirement(latest_draft),
        "latest_report": _decode_latest_report(latest_report),
        "last_failed_action": sess.get("last_failed_action"),
    }
    return snapshot


def _decode_requirement(row: dict | None) -> dict | None:
    if row is None:
        return None
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "user_id": row["user_id"],
        "version": row["version"],
        "status": row["status"],
        "payload": payload,
        "confirmed_at": row["confirmed_at"].isoformat() if row.get("confirmed_at") else None,
    }


def _decode_latest_report(row: dict | None) -> dict | None:
    if row is None:
        return None
    payload = row["report_payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    snapshot = row["query_snapshot"]
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    return {
        "id": row["id"],
        "version": row["version"],
        "parent_version": row["parent_version"],
        "title": row["title"],
        "status": row["status"],
        "report": payload,
        "query_snapshot": snapshot,
        "trace_id": row["trace_id"],
        "favorite": row["favorite"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }
