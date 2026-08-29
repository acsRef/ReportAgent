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
import uuid
from typing import Any, Optional, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from app.agent.data_graph import build_data_graph
from app.agent.intent import IntentKind, _CHITCHAT_KEYWORDS, classify_intent
from app.infra.checkpoint.factory import get_checkpointer
from app.agent.requirement_parser import parse_requirement
from app.agent.security_guard import SecurityGuard
from app.models.requirement import RequirementAssumption, RequirementCard
from app.infra.db import requirement_repository
from app.infra.db.postgres import get_pool
from app.state.checkpoint_adapter import migrate_checkpoint
from app.infra.trace.sdk import get_tracer, traced_node
from app.models.contracts import ErrorDetail, SchemaContext
from app.models.requirement import RequirementCard

logger = logging.getLogger(__name__)

_TYPICAL_CONTEXT_BUDGET_CHARS = 8000  # 预估 conversation+system+context 块占位（≈2000 tokens），用于 remaining_token_budget 估算


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
    intent: Optional[str]  # chitchat | report | interface | dashboard | unknown
    intent_reason: Optional[str]
    casual_reply: Optional[str]
    dict_context: Optional[str]  # 字典检索上下文（外部接口检测 + parse 复用）
    execution_status: str  # SUCCESS | SECURITY_BLOCKED | FAILED


# --- Nodes ----------------------------------------------------------------


@traced_node("requirement_security_guard")
def _security_guard(state: RequirementAnalysisState) -> dict:
    state = migrate_checkpoint(dict(state))  # P3 (γ): graph 入口 v1→v2 adapter
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
    return "__end__" if state.get("security_level") == "HIGH" else "classify_intent"


def _route_intent(state: RequirementAnalysisState) -> str:
    intent = state.get("intent") or "report"
    if intent == "chitchat":
        return "casual_reply"
    if intent == "interface":
        return "interface_requirement"
    # report / dashboard / unknown → 走正常需求分析
    return "data_agent"


async def _fetch_dict_context(query: str) -> tuple[bool, str]:
    """字典检索：返回 (dict_hit, dict_context)。

    dict_hit = 有字典命中（数据库表/字段/接口文档）→ 判定为数据库查询（REPORT），
    需求分析处理字段澄清。dict_context 供 parse 复用。

    P2：改走 registry 通道（统一注册面），与 sql_graph 的 search_faq 同模式。
    """
    try:
        from app.tools.registry import registry
        dict_tools = registry.get(["search_interface_dictionary"])
        if not dict_tools:
            return False, ""
        raw = await asyncio.to_thread(
            dict_tools[0].invoke, {"query": query, "top_k": 5},
        )
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        matches = parsed.get("matches") or []
        context = "\n".join(
            f"- {m.get('source', '')}: {(m.get('text') or '')[:300]}" for m in matches
        )
        return bool(matches), context
    except Exception as exc:
        logger.warning("dictionary lookup failed: %s", exc)
        return False, ""


@traced_node("requirement_intent")
async def _classify_intent(state: RequirementAnalysisState) -> dict:
    """工作流式意图分类：闲聊快路径 → 字典检索（外部接口）→ LLM 兜底。"""
    q = state["user_query"]
    # 闲聊快路径：不查字典、不跑 LLM（省 token/延迟）。
    if any(k in q.lower() for k in _CHITCHAT_KEYWORDS):
        return {"intent": IntentKind.CHITCHAT.value, "intent_reason": "闲聊关键词命中", "dict_context": ""}
    dict_hit, dict_context = await _fetch_dict_context(q)
    res = classify_intent(q, dict_hit=dict_hit)
    return {
        "intent": res.kind.value,
        "intent_reason": res.reason,
        "dict_context": dict_context,
    }


@traced_node("requirement_casual")
def _casual_reply(state: RequirementAnalysisState) -> dict:
    """闲聊：返回友好回复，不建需求卡、不跑需求解析。"""
    return {
        "casual_reply": (
            "你好！我可以帮你分析数据库里的销售、退货、库存、考勤等数据，"
            "或查询外部接口/实时数据源的字段含义。直接告诉我你想看什么就行。"
        ),
        "execution_status": "SUCCESS",
    }


@traced_node("requirement_interface")
def _interface_requirement(state: RequirementAnalysisState) -> dict:
    """外部接口/实时数据：构造接口需求卡（确定性，不经 LLM）。

    卡上 assumption `data_source:stream` 是确认流程的短路标记——确认后不生成 SQL，
    而走外部接口接入说明。
    """
    card = RequirementCard(
        id=str(uuid.uuid4()),
        version=1,
        status="complete",
        summary="此查询涉及外部实时接口/数据源数据，需接入实时数据源，非数据库报表。",
        target_metrics=[],
        scope=[],
        dimensions=[],
        analysis_methods=["实时数据接入"],
        assumptions=[
            RequirementAssumption(
                key="data_source:stream",
                text="数据来自外部实时接口，需接入数据源后取数；非数据库星型模型报表。",
                accepted=None,
            )
        ],
        missing_fields=[],
        confidence=0.8,
    )
    return {"requirement_card": card, "execution_status": "RUNNING"}


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

    字典上下文（dictionary_context）由 `_classify_intent` 程序化检索后存入 state，
    这里直接复用（避免二次检索）；任何失败降级为空串、绝不影响主流程；命中片段以
    "- source: text[:300]" 形式序列化，由 parse_requirement 拼到「数据字典参考」段。
    """
    conversation_context = ""
    assembled_context = ""
    # P4c Task 1: 真正接入 ContextRuntime.build() —— 替代 facade build_session_context；
    # 获取 conversation_context + assembled_context（含 selective recall 的全景 context）。
    # 失败降级为空，绝不阻塞需求解析（与原 try/except 语义一致）。
    try:
        from app.context.runtime import ContextRuntime  # 局部 import 避免 cycle / 测试 patch
        _user_id = state.get("user_id")
        try:
            _uid = int(_user_id) if _user_id not in (None, "") else 0
        except (TypeError, ValueError):
            _uid = 0
        from app.llm.config import LLMConfig

        _cfg = LLMConfig()
        _est_chars = len(state.get("user_query", "")) + _TYPICAL_CONTEXT_BUDGET_CHARS
        _remaining = max(0, _cfg.context_window - _est_chars // 4)
        _bundle = await ContextRuntime().build(
            session_id=state["session_id"],
            user_id=_uid,
            query=state.get("user_query", ""),
            agent="requirement_analyze",
            state_dict=dict(state),
            remaining_token_budget=_remaining,
        )
        conversation_context = _bundle["conversation_context"]
        assembled_context = _bundle["assembled_context"]
    except Exception as exc:
        logger.warning("ContextRuntime.build failed: %s", exc)

    dictionary_context = state.get("dict_context") or ""

    card = parse_requirement(
        user_query=state["user_query"],
        schema_context=state.get("schema_context"),
        conversation_context=conversation_context or None,
        assembled_context=assembled_context or None,  # P4c: 含 recall 的全景 context
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
    workflow.add_node("classify_intent", _classify_intent)
    workflow.add_node("casual_reply", _casual_reply)
    workflow.add_node("interface_requirement", _interface_requirement)
    workflow.add_node("data_agent", _data_agent)
    workflow.add_node("requirement_parse", _requirement_parse)
    workflow.add_node("persist_draft", _persist_draft)

    workflow.set_entry_point("security_guard")
    workflow.add_conditional_edges("security_guard", _route_security)
    workflow.add_conditional_edges("classify_intent", _route_intent)
    workflow.add_edge("data_agent", "requirement_parse")
    workflow.add_edge("requirement_parse", "persist_draft")
    workflow.add_edge("interface_requirement", "persist_draft")
    workflow.add_edge("casual_reply", END)
    workflow.add_edge("persist_draft", END)

    return workflow.compile(checkpointer=get_checkpointer())
