"""Service layer for `agent.report_version` writes.

Always inside one transaction:
- INSERT report_version
- INSERT app.conversations pointer row
- UPDATE agent.session.latest_report_version + current_phase

Any failure rolls everything back, so we never leave orphan versions.
"""
from __future__ import annotations

import json
from typing import Any

from app.infra.db import report_version_repository
from app.infra.db.postgres import get_pool
from app.report.versioning import resolve_report_status


class VersionConflictError(Exception):
    """Two concurrent writers raced past MAX+1 and the unique constraint fired."""


async def persist_confirmed_run(
    *,
    session_id: str,
    user_id: int,
    requirement_draft_id: int,
    title: str,
    report_payload: dict,
    query_snapshot: dict | None,
    trace_id: str | None,
) -> dict:
    """First successful execution → report_version 1, parent_version NULL.

    The 'confirmed' semantics here means "from a confirmed (locked) draft",
    not the report_status column.
    """
    return await _persist(
        session_id=session_id,
        user_id=user_id,
        parent_version=None,
        requirement_draft_id=requirement_draft_id,
        adjustment_text=None,
        title=title,
        report_status=resolve_report_status("SUCCESS"),
        report_payload=report_payload,
        query_snapshot=query_snapshot,
        trace_id=trace_id,
    )


async def persist_adjust_run(
    *,
    session_id: str,
    user_id: int,
    base_report_version: int,
    requirement_draft_id: int | None,
    adjustment_text: str,
    title: str,
    report_payload: dict,
    query_snapshot: dict | None,
    trace_id: str | None,
) -> dict:
    """Adjustment execution → next version with parent_version set."""
    return await _persist(
        session_id=session_id,
        user_id=user_id,
        parent_version=base_report_version,
        requirement_draft_id=requirement_draft_id,
        adjustment_text=adjustment_text,
        title=title,
        report_status=resolve_report_status("SUCCESS"),
        report_payload=report_payload,
        query_snapshot=query_snapshot,
        trace_id=trace_id,
    )


async def persist_empty_run(
    *,
    session_id: str,
    user_id: int,
    requirement_draft_id: int,
    title: str,
    query_snapshot: dict | None,
    trace_id: str | None,
) -> dict:
    """SQL ran successfully but returned 0 rows.

    Still a "done" version (execution succeeded) — payload carries
    execution_status=EMPTY so the front-end knows to render the no-data
    band instead of pretending the report has content.
    """
    payload = {
        "answer": {
            "text": "查询执行成功,但未匹配到数据",
            "table": None,
            "chart": None,
            "insight": None,
        },
        "trace": [],
        "execution_status": "EMPTY",
    }
    return await _persist(
        session_id=session_id,
        user_id=user_id,
        parent_version=None,
        requirement_draft_id=requirement_draft_id,
        adjustment_text=None,
        title=title,
        report_status=resolve_report_status("EMPTY"),
        report_payload=payload,
        query_snapshot=query_snapshot,
        trace_id=trace_id,
    )


async def persist_error_run(
    *,
    session_id: str,
    user_id: int,
    # P9 背景任务超时路径拿不到 draft（graph 已被 cancel），允许 None。
    requirement_draft_id: int | None,
    title: str,
    error_detail: dict,
    query_snapshot: dict | None,
    trace_id: str | None,
) -> dict:
    """SQL execution failed (timeout / connection / permission / etc.).

    Persists status='error' so version history shows the failed attempt.
    SSE still emits the error event to the active stream; this version
    exists for later inspection (e.g. user switches to v3 from the rail
    after a successful v4).
    """
    payload = {
        "answer": {
            "text": "查询执行失败",
            "table": None,
            "chart": None,
            "insight": None,
        },
        "trace": [],
        "execution_status": "FAILED",
        "error": {
            "code": str(error_detail.get("code", "QUERY_FAILED")),
            "message": str(error_detail.get("message", ""))[:300],
            "kind": error_detail.get("kind") or "other",
        },
    }
    return await _persist(
        session_id=session_id,
        user_id=user_id,
        parent_version=None,
        requirement_draft_id=requirement_draft_id,
        adjustment_text=None,
        title=title,
        report_status=resolve_report_status("FAILED"),
        report_payload=payload,
        query_snapshot=query_snapshot,
        trace_id=trace_id,
    )


async def _persist(
    *,
    session_id: str,
    user_id: int,
    parent_version: int | None,
    requirement_draft_id: int | None,
    adjustment_text: str | None,
    title: str,
    report_status: str,
    report_payload: dict,
    query_snapshot: dict | None,
    trace_id: str | None,
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                row = await report_version_repository.append_version(
                    conn,
                    session_id=session_id,
                    user_id=user_id,
                    parent_version=parent_version,
                    requirement_draft_id=requirement_draft_id,
                    adjustment_text=adjustment_text,
                    title=title,
                    status=report_status,
                    report_payload=report_payload,
                    query_snapshot=query_snapshot,
                    trace_id=trace_id,
                )
            except report_version_repository.VersionConflictError as exc:
                raise VersionConflictError(str(exc)) from exc

            new_version = row["version"]

            # Conversation pointer row
            await conn.execute(
                """INSERT INTO app.conversations
                       (session_id, user_id, role, content, message_type, metadata)
                   VALUES ($1, $2, 'assistant', NULL, 'report_version',
                           $3::jsonb)""",
                session_id, user_id,
                json.dumps(
                    {
                        "version": new_version,
                        "parent_version": parent_version,
                        "title": title,
                        "trace_id": trace_id,
                    },
                    ensure_ascii=False,
                ),
            )

            # Session pointer + phase
            await conn.execute(
                """UPDATE agent.session
                       SET latest_report_version = $2,
                           current_phase = 'report_ready',
                           last_failed_action = NULL,
                           updated_at = NOW()
                     WHERE thread_id = $1""",
                session_id, new_version,
            )

    return row
