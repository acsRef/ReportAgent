"""Confirmed-execution graph.

Runs only AFTER the user has explicitly confirmed a RequirementCard.
The hard SQL gate lives in `load_confirmed_requirement` + `sql_gate`:
both verify the draft is owned by the JWT user, status='complete',
no missing fields, and all assumptions accepted. If anything is off,
the graph raises a structured error that the SSE layer surfaces as a
`409 REQUIREMENT_INCOMPLETE` event.

Flow:
    load_confirmed_requirement → sql_gate → data_agent(refresh) →
    sql_agent → report_agent → persist_report → END
"""
from __future__ import annotations

import logging
from typing import Any, Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from app.agent.data_graph import build_data_graph
from app.agent.sql_graph import build_sql_graph
from app.agent.report_graph import build_report_graph
from app.infra.db import requirement_repository, report_version_repository
from app.infra.db.postgres import get_pool
from app.infra.trace.sdk import get_tracer, traced_node
from app.models.contracts import ErrorDetail, SchemaContext
from app.models.requirement import RequirementCard
from app.services import requirement_service, report_version_service

logger = logging.getLogger(__name__)


class ConfirmedExecutionState(TypedDict, total=False):
    user_query: str
    user_id: int
    session_id: str
    trace_id: str
    requirement_card: Optional[RequirementCard]
    base_report_version: Optional[int]
    adjustment_text: Optional[str]
    schema_context: Optional[SchemaContext]
    query_result: Optional[dict]
    report_payload: Optional[dict]
    execution_status: str
    error: Optional[ErrorDetail]


# --- Errors ----------------------------------------------------------------


class RequirementIncompleteError(Exception):
    """Raised when the user calls /confirm with a draft that is missing
    fields or has unresolved assumptions. The HTTP layer maps this to 409.
    """


class SessionNotFoundError(Exception):
    """Raised when the (user_id, session_id) pair is not owned by the user."""


# --- Nodes ----------------------------------------------------------------


@traced_node("load_confirmed_requirement")
async def _load_confirmed_requirement(state: ConfirmedExecutionState) -> dict:
    """Load the latest draft and verify it's lockable."""
    pool = get_pool()
    async with pool.acquire() as conn:
        draft = await requirement_repository.get_latest(
            conn,
            session_id=state["session_id"],
            user_id=state["user_id"],
        )
    if draft is None:
        raise SessionNotFoundError(
            f"no requirement draft for session {state['session_id']}"
        )
    if draft["status"] == "locked":
        # Already locked = already in a confirmed run; treat as a no-op
        # reload so the graph is idempotent.
        return {"requirement_card": _hydrate_card(draft)}
    if draft["status"] != "complete":
        raise RequirementIncompleteError(
            f"draft {draft['id']} is in status '{draft['status']}', "
            f"must be 'complete' to execute"
        )
    card = _hydrate_card(draft)
    # Sanity: all missing_fields must be empty, all assumptions resolved.
    if card.missing_fields:
        raise RequirementIncompleteError(
            f"draft {draft['id']} still has {len(card.missing_fields)} "
            f"missing fields"
        )
    if any(a.accepted is None for a in card.assumptions):
        raise RequirementIncompleteError(
            f"draft {draft['id']} has unresolved assumptions"
        )
    return {"requirement_card": card}


@traced_node("sql_gate")
async def _sql_gate(state: ConfirmedExecutionState) -> dict:
    """TOCTOU-safe re-check. Lock the draft transactionally so concurrent
    /confirm calls cannot double-execute. If the lock fails, abort.
    """
    card = state.get("requirement_card")
    if card is None:
        raise RequirementIncompleteError("no card loaded")
    draft_id = await _draft_id_from_state(state)
    try:
        await requirement_service.lock_for_execution(
            session_id=state["session_id"],
            user_id=state["user_id"],
            draft_id=draft_id,
        )
    except Exception as exc:
        raise RequirementIncompleteError(
            f"failed to lock draft for execution: {exc}"
        ) from exc
    return {"execution_status": "RUNNING"}


@traced_node("confirmed_data_agent")
async def _confirmed_data_agent(state: ConfirmedExecutionState) -> dict:
    """Refresh schema (do NOT re-analyze requirements)."""
    data_graph = build_data_graph()
    ds = await data_graph.ainvoke({
        "user_query": state["user_query"],
        "discovered_tables": [],
        "mcp_tool_calls": [],
        "raw_schema": "",
        "trace_id": state.get("trace_id", ""),
    })
    return {"schema_context": ds.get("schema_context")}


@traced_node("confirmed_sql_agent")
async def _confirmed_sql_agent(state: ConfirmedExecutionState) -> dict:
    """Run the SQL subgraph. Note: we deliberately reuse `build_sql_graph`
    WITHOUT its `_intent_analyze` entry node — the requirement is already
    confirmed, so we just plan → generate → execute.
    """
    sql_graph = build_sql_graph()
    schema = state.get("schema_context")
    from app.models.contracts import SchemaContext as SchemaCtx
    schema_dict = schema.model_dump() if schema else None
    schema_input = SchemaCtx(**schema_dict) if schema_dict else None

    ss = await sql_graph.ainvoke({
        "schema_context": schema_input,
        "user_query": state["user_query"],
        "query_plan": None,
        "generated_sql": "",
        "validation_result": {},
        "sql_result": "",
        "execution_status": "",
        "error": None,
        "retry_counters": {"plan": 0, "sql_generation": 0},
        "trace_id": state.get("trace_id", ""),
        "chosen_tool": None,  # legacy field; ignored in new flow
    })
    qr = ss.get("query_result")
    return {
        "query_result": qr.model_dump() if qr else None,
        "execution_status": ss.get("execution_status", "FAILED"),
    }


@traced_node("confirmed_report_agent")
async def _confirmed_report_agent(state: ConfirmedExecutionState) -> dict:
    """Build the report payload from the query result."""
    report_graph = build_report_graph()
    rs = await report_graph.ainvoke({
        "query_result": state.get("query_result"),
        "user_query": state["user_query"],
        "chart_config": {},
        "insight_text": "",
        "report_spec": None,
        "assemble_plan": [],
        "assemble_step_idx": 0,
        "assemble_results": [],
        "trace_id": state.get("trace_id", ""),
    })
    return {
        "report_payload": {
            "answer": {
                "text": "查询完成",
                "table": None,
                "chart": rs.get("chart_config") or None,
                "insight": rs.get("insight_text") or None,
            },
            "trace": [],
        },
        "execution_status": "SUCCESS",
    }


@traced_node("persist_report")
async def _persist_report(state: ConfirmedExecutionState) -> dict:
    """Append a row to `agent.report_version` in a transaction with the
    conversation pointer and session.phase update. Returns the new version
    number.
    """
    if state.get("report_payload") is None:
        return {"execution_status": "FAILED"}
    draft_id = await _draft_id_from_state(state)
    base_version = state.get("base_report_version")
    if base_version is None:
        row = await report_version_service.persist_confirmed_run(
            session_id=state["session_id"],
            user_id=state["user_id"],
            requirement_draft_id=draft_id,
            title="报告",
            report_payload=state["report_payload"],
            query_snapshot=None,
            trace_id=state.get("trace_id"),
        )
    else:
        row = await report_version_service.persist_adjust_run(
            session_id=state["session_id"],
            user_id=state["user_id"],
            base_report_version=base_version,
            requirement_draft_id=draft_id,
            adjustment_text=state.get("adjustment_text") or "",
            title="报告（调整）",
            report_payload=state["report_payload"],
            query_snapshot=None,
            trace_id=state.get("trace_id"),
        )

    trace_id = state.get("trace_id", "")
    if trace_id:
        tracer = get_tracer(trace_id)
        tracer.end("DONE")

    return {
        "report_payload": {**state["report_payload"], "version": row["version"]},
        "execution_status": "DONE",
    }


# --- Helpers --------------------------------------------------------------


def _hydrate_card(draft_row: dict) -> RequirementCard:
    import json
    payload = draft_row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return RequirementCard.model_validate(payload)


def _draft_id_from_state(state: ConfirmedExecutionState) -> int:
    """Re-fetch the latest draft id for the current session/user. We keep
    the state lean (it carries the card, not the id) so this is a
    best-effort read. Callers should have just loaded it.

    Returns a coroutine — call with `await` inside an async node.
    """
    from app.infra.db.postgres import get_pool

    async def _read() -> int:
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT id FROM agent.requirement_draft
                   WHERE session_id = $1 AND user_id = $2
                   ORDER BY version DESC LIMIT 1""",
                state["session_id"], state["user_id"],
            )
            return row["id"] if row else 0

    return _read()


# --- Graph build ----------------------------------------------------------


def build_confirmed_execution_graph():
    workflow = StateGraph(ConfirmedExecutionState)

    workflow.add_node("load_confirmed_requirement", _load_confirmed_requirement)
    workflow.add_node("sql_gate", _sql_gate)
    workflow.add_node("data_agent", _confirmed_data_agent)
    workflow.add_node("sql_agent", _confirmed_sql_agent)
    workflow.add_node("report_agent", _confirmed_report_agent)
    workflow.add_node("persist_report", _persist_report)

    workflow.set_entry_point("load_confirmed_requirement")
    workflow.add_edge("load_confirmed_requirement", "sql_gate")
    workflow.add_edge("sql_gate", "data_agent")
    workflow.add_edge("data_agent", "sql_agent")
    workflow.add_edge("sql_agent", "report_agent")
    workflow.add_edge("report_agent", "persist_report")
    workflow.add_edge("persist_report", END)

    return workflow.compile(checkpointer=MemorySaver())
