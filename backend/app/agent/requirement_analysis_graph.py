"""Requirement-analysis graph.

A small LangGraph that runs only schema discovery and requirement
parsing — never SQL or report tools. This is the "do not run anything
against business data until the user confirms" gate.

Flow:
    security_guard → data_agent(schema only) → requirement_parse →
    persist_draft → END

The `data_agent` node here is a *schema-only* wrapper: it deliberately
does not register `validate_sql` / `execute_sql` / report tools. The
SQL gate is enforced by node wiring + by the fact that nothing in this
graph imports the SQL/Report tools.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from app.agent.data_graph import build_data_graph
from app.infra.checkpoint.factory import get_checkpointer
from app.agent.requirement_parser import parse_requirement
from app.agent.security_guard import SecurityGuard
from app.context import build_session_context
from app.infra.db import requirement_repository
from app.infra.db.postgres import get_pool
from app.infra.trace.sdk import get_tracer, traced_node
from app.models.contracts import ErrorDetail, SchemaContext
from app.models.requirement import RequirementCard

logger = logging.getLogger(__name__)


class RequirementAnalysisState(TypedDict, total=False):
    user_query: str
    user_id: int
    session_id: str
    trace_id: str
    schema_context: Optional[SchemaContext]
    requirement_card: Optional[RequirementCard]
    draft_id: Optional[int]
    security_score: int
    security_level: str
    security_warning: str
    error: Optional[ErrorDetail]
    execution_status: str  # SUCCESS | SECURITY_BLOCKED | FAILED


# --- Nodes ----------------------------------------------------------------


@traced_node("requirement_security_guard")
def _security_guard(state: RequirementAnalysisState) -> dict:
    result = SecurityGuard.check(state["user_query"])
    out = {
        "security_score": result.score,
        "security_level": result.level,
        "security_warning": result.reason,
    }
    if result.blocked:
        out["error"] = ErrorDetail(
            code="SECURITY_REJECTED",
            message="请求包含潜在危险指令，无法执行",
        )
        out["execution_status"] = "SECURITY_BLOCKED"
    return out


def _route_security(state: RequirementAnalysisState) -> str:
    return "__end__" if state.get("security_level") == "HIGH" else "data_agent"


@traced_node("requirement_data_agent")
async def _data_agent(state: RequirementAnalysisState) -> dict:
    """Schema-only discovery. The `data_graph` already uses the schema
    tools; we wrap it here for tracing + state propagation.
    """
    data_graph = build_data_graph()
    ds = await data_graph.ainvoke({
        "user_query": state["user_query"],
        "discovered_tables": [],
        "mcp_tool_calls": [],
        "raw_schema": "",
        "trace_id": state.get("trace_id", ""),
    })
    return {
        "schema_context": ds.get("schema_context"),
        "execution_status": "RUNNING",
    }


@traced_node("requirement_parse")
async def _requirement_parse(state: RequirementAnalysisState) -> dict:
    """LLM 解析 + 与受控选项合并。

    转 async 是为了接入分层对话上下文（build_session_context 需读 DB）；
    上下文构建失败时降级为空，绝不阻塞需求解析。需求卡落库仍在 persist_draft。

    字典上下文（dictionary_context）走程序化检索——同 conversation_context 一样
    单次拉取、失败降级空串、绝不影响主流程；命中片段以 "- source: text[:300]"
    形式序列化，由 parse_requirement 拼到 prompt 的「数据字典参考」段。
    """
    conversation_context = ""
    try:
        conversation_context = await build_session_context(
            state["session_id"], state.get("user_id", 0) or 0,
        )
    except Exception as exc:
        logger.warning("build_session_context failed: %s", exc)

    # 数据字典程序化检索：命中则注入释义上下文；任何失败降级为空（同 conversation_context 策略）
    dictionary_context = ""
    try:
        from app.tools.interface_dict_tools import search_interface_dictionary
        # 同步阻塞 httpx（timeout 30s）→ 丢线程池，别占事件循环（同本文件其他 sync 调用风格）
        raw = await asyncio.to_thread(
            search_interface_dictionary.invoke, {"query": state["user_query"], "top_k": 5},
        )
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        matches = parsed.get("matches") or []
        if matches:
            dictionary_context = "\n".join(
                f"- {m.get('source', '')}: {(m.get('text') or '')[:300]}" for m in matches
            )
    except Exception as exc:
        logger.warning("dictionary lookup in requirement analysis failed: %s", exc)

    card = parse_requirement(
        user_query=state["user_query"],
        schema_context=state.get("schema_context"),
        conversation_context=conversation_context or None,
        dictionary_context=dictionary_context or None,
    )
    return {"requirement_card": card}


@traced_node("requirement_persist_draft")
async def _persist_draft(state: RequirementAnalysisState) -> dict:
    """Write the parsed card to `agent.requirement_draft` in a transaction
    with the session pointer update.
    """
    card = state.get("requirement_card")
    if card is None:
        return {
            "execution_status": "FAILED",
            "error": ErrorDetail(code="NO_CARD", message="解析未生成需求卡"),
        }
    user_id = state.get("user_id", 0) or 0
    session_id = state["session_id"]
    pool = get_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                draft_id = await requirement_repository.create_draft(
                    conn,
                    session_id=session_id,
                    user_id=user_id,
                    user_query=state["user_query"],
                    card=card,
                )
                await conn.execute(
                    """UPDATE agent.session
                           SET latest_requirement_draft_id = $2,
                               current_phase = $3,
                               updated_at = NOW()
                         WHERE thread_id = $1""",
                    session_id,
                    draft_id,
                    "awaiting_missing" if card.missing_fields else "awaiting_confirm",
                )
    except Exception as exc:
        logger.exception("persist_draft failed")
        return {
            "execution_status": "FAILED",
            "error": ErrorDetail(code="PERSIST_FAILED", message=str(exc)[:200]),
        }

    # End trace
    trace_id = state.get("trace_id", "")
    if trace_id:
        tracer = get_tracer(trace_id)
        tracer.end("AWAITING_CONFIRM" if not card.missing_fields else "AWAITING_MISSING")

    return {
        "draft_id": draft_id,
        "execution_status": "SUCCESS",
    }


# --- Graph build ----------------------------------------------------------


def build_requirement_analysis_graph():
    """Compile and return the requirement-analysis graph.

    Checkpointer comes from `get_checkpointer()`：开发环境是 MemorySaver
    （便于 notebook 单步），非开发环境是 AsyncPostgresSaver（checkpoint
    落 PG、跨重启持久）。见 app/infra/checkpoint/factory.py。
    """
    workflow = StateGraph(RequirementAnalysisState)

    workflow.add_node("security_guard", _security_guard)
    workflow.add_node("data_agent", _data_agent)
    workflow.add_node("requirement_parse", _requirement_parse)
    workflow.add_node("persist_draft", _persist_draft)

    workflow.set_entry_point("security_guard")
    workflow.add_conditional_edges("security_guard", _route_security)
    workflow.add_edge("data_agent", "requirement_parse")
    workflow.add_edge("requirement_parse", "persist_draft")
    workflow.add_edge("persist_draft", END)

    return workflow.compile(checkpointer=get_checkpointer())
