from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal, Optional, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from app.agent.data_graph import build_data_graph
from app.agent.sql_graph import ChatCard, build_sql_graph
from app.agent.report_graph import build_report_graph
from app.infra.checkpoint.factory import get_checkpointer
from app.infra.memory.memory_manager import MemoryManager
from app.infra.trace.sdk import get_tracer, traced_node
from app.llm import call_llm
from app.models.contracts import (
    ClarificationRequest,
    ErrorDetail,
    QueryPlan,
    QueryResult,
    ReportSpec,
    SchemaContext,
)
from app.models.requirement import RequirementCard
from app.agent.security_guard import SecurityGuard
from app.tools.registry import registry
from app.tools.__init__ import register_all_tools

logger = logging.getLogger(__name__)

# C-2: clarify 跨多轮会让 clarification_history 与 current_query 线性膨胀
# （每轮把上一轮答案追加进 current_query），最终挤爆 LLM 上下文窗口。
# 滚动窗口：history 只留最近 N 轮，augmented query 套硬上限。
CLARIFY_MAX_TURNS = 5
CLARIFY_QUERY_MAX_CHARS = 2000
_CLARIFY_ANSWER_MAX_CHARS = 200


class AgentState(TypedDict):
    user_query: str
    original_query: str
    current_query: str
    clarification_history: list
    session_id: str
    user_id: int  # JWT user id; injected from /api/v1/chat deps in main.py
    intent: str
    memory_context: str

    schema_context: Optional[SchemaContext]
    query_plan: Optional[QueryPlan]
    query_result: Optional[QueryResult]
    report_spec: Optional[ReportSpec]
    chart_config: dict
    insight_text: str

    execution_status: str
    error: Optional[ErrorDetail]
    trace_id: str
    active_sub_agent: str
    clarification_context: dict
    retry_count: int

    security_score: int
    security_level: str
    security_warning: str

    # Chat card fields (new) — populated by the sql_agent node when
    # `query_plan.clarify_decision.action == "clarify"`. The SSE stream
    # handler in main.py reads `pending_card` after astream_events ends
    # and emits a single `event: card` frame, then clears the field.
    pending_card: Optional[ChatCard]
    cards: list[ChatCard]  # cumulative history within a turn

    # Round-2 of the intent_card flow: when the user clicks an intent option,
    # the frontend re-issues the request with `chosen_tool` set in the body.
    # main.py writes it into state via `_agent.update_state`. When set, the
    # `_run_sql_agent` node skips the stage-1 intent analyzer and goes
    # straight into planning + SQL generation.
    chosen_tool: Optional[str] = None
    intent_card: Optional[ChatCard] = None
    intent_needs_options_group: bool = False
    intent_confidence: float = 0.0
    intent_reasoning: str = ""

    # Phase 1 / Phase 3 wiring: requirement-analysis graph populates this
    # from the parsed RequirementCard and downstream nodes (e.g.
    # confirmed-execution) read it. Legacy flow leaves it None.
    requirement_card: Optional[RequirementCard]


# --- Security Guard ---

@traced_node("security_guard")
def _security_guard(state: AgentState) -> dict:
    result = SecurityGuard.check(state.get("current_query", state["user_query"]))
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
        trace_id = state.get("trace_id")
        if trace_id:
            tracer = get_tracer(trace_id)
            tracer.end("REJECTED")
    return out


def _route_security(state: AgentState) -> Literal["__end__", "classify"]:
    if state.get("security_level") == "HIGH":
        return "__end__"
    return "classify"


# --- Intent Classification ---

_INTENT_KEYWORDS_REPORT = [
    "销售", "排名", "趋势", "增长", "利润", "退货",
    "多少", "统计", "哪个", "分析", "最高", "最低",
    "占比", "比较", "去年", "本月", "上月", "季度",
    "查询", "数据", "报表", "显示", "展示", "查看", "列表", "明细", "汇总", "报告", "情况",
]

_INTENT_KEYWORDS_DASHBOARD = [
    "看板", "驾驶舱", "大屏", "dashboard", "概览",
]

_INTENT_KEYWORDS_CHITCHAT = [
    "你好", "hi", "hello", "你是谁", "你能做什么",
]


@traced_node("classify")
async def _classify_intent(state: AgentState) -> dict:
    q = state.get("current_query", state["user_query"]).lower()
    if any(k in q for k in _INTENT_KEYWORDS_CHITCHAT):
        intent = "闲聊"
    elif any(k in q for k in _INTENT_KEYWORDS_DASHBOARD):
        intent = "看板"
    elif any(k in q for k in _INTENT_KEYWORDS_REPORT):
        intent = "报表"
    else:
        intent = "报表"

    # Recall memories for context
    user_id = state.get("user_id", 0) or 0
    try:
        mm = MemoryManager()
        memory_context = await mm.recall(query=q, user_id=user_id, top_k_queries=2, top_k_preferences=3)
    except Exception as exc:  # Detail D: 记忆召回失败降级为空，但要留痕
        logger.warning("memory recall failed: %s", exc)
        memory_context = ""

    return {
        "intent": intent,
        "active_sub_agent": intent,
        "memory_context": memory_context,
    }


def _route_intent(state: AgentState) -> Literal["data_agent", "dashboard_agent", "clarify", "__end__"]:
    intent = state.get("intent", "")
    if intent == "闲聊":
        tracer = get_tracer(state.get("trace_id", ""))
        tracer.end("SUCCESS")
        return "__end__"
    elif intent == "看板":
        return "dashboard_agent"
    return "data_agent"


# --- Data Agent Node ---

@traced_node("data_agent")
async def _run_data_agent(state: AgentState) -> dict:
    data_graph = build_data_graph()
    ds = await data_graph.ainvoke({
        "user_query": state.get("current_query", state["user_query"]),
        "discovered_tables": [],
        "mcp_tool_calls": [],
        "raw_schema": "",
        "trace_id": state.get("trace_id", ""),
    })
    return {
        "schema_context": ds.get("schema_context"),
        "active_sub_agent": "sql",
    }


# --- SQL Agent Node ---

@traced_node("sql_agent")
async def _run_sql_agent(state: AgentState) -> dict:
    sql_graph = build_sql_graph()
    schema = state.get("schema_context")
    from app.models.contracts import SchemaContext as SchemaCtx
    schema_dict = schema.model_dump() if schema else None
    if schema_dict is not None:
        schema_input = SchemaCtx(**schema_dict)
    else:
        schema_input = None
    parent_retries = state.get("retry_count", 0)

    # Stage-1 intent-card short-circuit:
    # If this is the first pass on this user_query (no `chosen_tool` chosen
    # yet), run the lightweight intent analyzer and emit `event: card {type:
    # intent_card}`. Stop the SQL subgraph entirely. The frontend will receive
    # the card, prompt the user, and re-issue the request with
    # `chosen_tool` set — at which point we skip intent and go straight to
    # plan → generate_sql.
    chosen_tool = state.get("chosen_tool") or state.get("metadata", {}).get("chosen_tool")
    logger.info(
        "sql_agent: chosen_tool=%r (state.chosen_tool=%r, state.metadata=%r)",
        chosen_tool, state.get("chosen_tool"), state.get("metadata"),
    )
    if not chosen_tool:
        from app.agent.sql_graph import _intent_analyze as _run_intent
        intent_state = {
            "user_query": state.get("current_query", state["user_query"]),
            "intent_card": None,
            "intent_needs_options_group": False,
            "intent_confidence": 0.0,
            "intent_reasoning": "",
            "execution_status": "",
        }
        # 真 P0（bug-review #8）：_intent_analyze 是 sync 函数（内含 1-5s 的
        # call_llm HTTP），从 async 节点直接调用会阻塞整个 event loop——LangGraph
        # 只对「注册为节点」的 sync 函数自动套线程池，这里是节点内部直调，没有包装。
        # 显式丢进线程池，释放 event loop。
        intent_result = await asyncio.to_thread(_run_intent, intent_state)
        # Mark trace + bubble the intent_card up so main.py can emit it.
        return {
            "query_plan": None,
            "query_result": None,
            "execution_status": "INTENT_AWAIT",
            "error": None,
            "pending_card": ChatCard(**intent_result["intent_card"]) if intent_result.get("intent_card") else None,
            "cards": [ChatCard(**intent_result["intent_card"])] if intent_result.get("intent_card") else [],
        }

    ss = await sql_graph.ainvoke({
        "schema_context": schema_input,
        "user_query": state.get("current_query", state["user_query"]),
        "query_plan": None,
        "generated_sql": "",
        "validation_result": {},
        "sql_result": "",
        "execution_status": "",
        "error": None,
        "retry_counters": {"plan": 0, "sql_generation": parent_retries},
        "trace_id": state.get("trace_id", ""),
        "chosen_tool": chosen_tool,
    })
    error_raw = ss.get("error")
    if error_raw and isinstance(error_raw, str):
        error_obj = ErrorDetail(code="SQL_AGENT_ERROR", message=error_raw)
    elif isinstance(error_raw, ErrorDetail):
        error_obj = error_raw
    else:
        error_obj = None

    # Save successful query to memory
    status = ss.get("execution_status", "")
    qr = ss.get("query_result")
    if status == "SUCCESS" and qr and qr.sql:
        try:
            mm = MemoryManager()
            await mm.remember_query(
                question=state.get("original_query", state["user_query"]),
                sql=qr.sql,
                schema=schema.model_dump() if schema else None,
                target_metric=ss["query_plan"].target_metric if ss.get("query_plan") else "",
            )
        except Exception as exc:  # Detail D
            logger.warning("remember_query failed: %s", exc)

    # Pre-SQL clarification: if the plan node decided we need to clarify,
    # build a card payload and route via execution_status rather than forcing
    # the parent graph to inspect query_plan everywhere. The card is stored
    # in state so the SSE layer can emit `event: card` after the node returns.
    qp = ss.get("query_plan")
    decision: dict[str, Any] = {}
    if qp is not None:
        cd = getattr(qp, "clarify_decision", None)
        if cd is not None:
            if isinstance(cd, dict):
                decision = cd
            else:
                decision = cd.model_dump() if hasattr(cd, "model_dump") else {}

    should_clarify = decision.get("action") == "clarify"
    pending_card: Optional[ChatCard] = None
    new_status = status
    if should_clarify:
        pending_card = _build_options_group_card(
            user_query=state.get("current_query", state["user_query"]),
            decision=decision,
        )
        # Route through the existing _route_evaluate → clarify branch by
        # surfacing the same NEED_CLARIFICATION status. This keeps all
        # downstream consumers (evaluate, retry logic) unchanged.
        new_status = "NEED_CLARIFICATION"
        error_obj = error_obj or ErrorDetail(
            code="NEED_CLARIFICATION",
            message="需要补充信息以完成查询",
        )
        logger.info(
            "sql_agent: pre-SQL clarification triggered, confidence=%.2f, missing=%s",
            decision.get("confidence", 0.0),
            decision.get("missing_dimensions", []),
        )

    cards_history = list(state.get("cards") or [])
    if pending_card is not None:
        cards_history.append(pending_card)

    return {
        "query_plan": ss.get("query_plan"),
        "query_result": qr,
        "execution_status": new_status,
        "error": error_obj,
        "retry_count": state.get("retry_count", 0) + 1,
        "pending_card": pending_card,
        "cards": cards_history,
    }


def _build_options_group_card(user_query: str, decision: dict) -> ChatCard:
    """Construct an `options_group` chat card from a clarification decision.

    The card groups options by the missing dimensions identified by the
    `plan` node. This is the only card type emitted in the prototype
    release; `preview_card` support is reserved for follow-up work.
    """
    missing = decision.get("missing_dimensions") or []
    predicted_table = decision.get("predicted_table")
    reasoning = decision.get("reasoning") or ""

    time_opts = [
        {"label": "本月", "value": {"time_range": "本月"}},
        {"label": "上月", "value": {"time_range": "上月"}},
        {"label": "本季度", "value": {"time_range": "本季度"}},
        {"label": "今年", "value": {"time_range": "今年"}},
    ]
    region_opts = [
        {"label": "华东", "value": {"region": "华东"}},
        {"label": "华北", "value": {"region": "华北"}},
        {"label": "华南", "value": {"region": "华南"}},
        {"label": "全部区域", "value": {"region": "ALL"}},
    ]
    metric_opts = [
        {"label": "销售额", "value": {"metric": "销售额"}},
        {"label": "销售量", "value": {"metric": "销售量"}},
        {"label": "订单数", "value": {"metric": "订单数"}},
        {"label": "毛利率", "value": {"metric": "毛利率"}},
    ]

    dim_to_opts = {"time": time_opts, "region": region_opts, "metric": metric_opts,
                   "时间": time_opts, "区域": region_opts, "指标": metric_opts,
                   "time_range": time_opts, "metric_type": metric_opts}

    # 当 LLM 决策 clarify 但没明确缺什么,默认推 3 个维度兜底
    if not missing:
        missing = ["time", "region", "metric"]

    groups = []
    for d in missing:
        opts = dim_to_opts.get(d)
        if opts:
            groups.append({
                "dimension": d,
                "options": opts,
            })

    # 兜底 2:即便 LLM 报了维度名,结果 groups 仍为空,强制给全 3 组
    if not groups:
        for k in ("time", "region", "metric"):
            groups.append({"dimension": k, "options": dim_to_opts[k]})

    return ChatCard(
        type="options_group",
        version=1,
        payload={
            "title": "请补充以下信息以继续查询",
            "subtitle": user_query,
            "groups": groups,
            "predicted_table": predicted_table,
            "reasoning": reasoning,
            "actions": [
                {"label": "确认", "kind": "primary"},
                {"label": "修改", "kind": "secondary"},
            ],
        },
    )


# --- Evaluate Node ---

def _evaluate(state: AgentState) -> dict:
    return {"active_sub_agent": "evaluate"}


def _route_evaluate(state: AgentState) -> Literal["report_agent", "clarify", "__end__"]:
    status = state.get("execution_status", "SUCCESS")
    if status == "SUCCESS":
        return "report_agent"
    elif status == "NEED_CLARIFICATION":
        tracer = get_tracer(state.get("trace_id", ""))
        tracer.end("NEED_CLARIFICATION")
        return "clarify"
    elif status == "INTENT_AWAIT":
        # Stage-1 intent card emitted; pause graph and wait for user choice.
        tracer = get_tracer(state.get("trace_id", ""))
        tracer.end("INTENT_AWAIT")
        return "__end__"
    else:
        if state.get("retry_count", 0) <= 3:
            return "sql_agent"
        tracer = get_tracer(state.get("trace_id", ""))
        tracer.end("FAILED")
        return "clarify"


# --- Report Agent Node ---

@traced_node("report_agent")
async def _run_report_agent(state: AgentState) -> dict:
    report_graph = build_report_graph()
    qr = state.get("query_result")
    rs = await report_graph.ainvoke({
        "query_result": qr.model_dump() if qr else None,
        "user_query": state.get("current_query", state["user_query"]),
        "chart_config": {},
        "insight_text": "",
        "report_spec": None,
        "assemble_plan": [],
        "assemble_step_idx": 0,
        "assemble_results": [],
        "trace_id": state.get("trace_id", ""),
    })

    insight = rs.get("insight_text", "")

    # Save insight to user memory
    if insight:
        try:
            mm = MemoryManager()
            await mm.remember_preference(
                user_id=state.get("user_id", 0) or 0,
                content=insight,
                source="report_agent",
                memory_type="insight",
                importance=0.3,
            )
        except Exception as exc:  # Detail D
            logger.warning("remember_preference failed: %s", exc)

    # End trace
    trace_id = state.get("trace_id")
    if trace_id:
        tracer = get_tracer(trace_id)
        tracer.end("DONE")

    return {
        "chart_config": rs.get("chart_config"),
        "insight_text": insight,
        "report_spec": rs.get("report_spec"),
        "execution_status": "DONE",
    }


# --- Clarify Node ---

@traced_node("clarify")
def _clarify(state: AgentState) -> dict:
    error_info = state.get("error")
    current_q = state.get("current_query", state["user_query"])

    prompt = f"""用户的问题是: "{current_q}"

经过分析，这个问题缺少关键信息无法完成查询。
{'错误信息: ' + (error_info.message if isinstance(error_info, ErrorDetail) else error_info.get('message', '') if error_info else '')}

请生成一个简短的追问（一句话），引导用户补充：
1. 具体的时间范围
2. 具体的区域或维度
3. 明确的需求指标

只返回追问问题本身："""

    question = call_llm(prompt)

    user_response = interrupt({
        "type": "clarify",
        "question": question,
    })

    answer = str(user_response)
    truncated_answer = answer[:_CLARIFY_ANSWER_MAX_CHARS]

    # C-2: 滚动窗口——只保留最近 CLARIFY_MAX_TURNS 轮，避免 history 无界增长。
    history = list(state.get("clarification_history", []))
    history = history[-(CLARIFY_MAX_TURNS - 1):]
    history.append({"question": question, "answer": truncated_answer})

    # Build augmented query: original + clarification context. 套硬上限，
    # 超出时保留尾部（最近的补充信息），防止 current_query 随轮次线性膨胀。
    current_q = state.get("current_query", state["user_query"])
    augmented = f"{current_q}\n\n补充信息: {truncated_answer}".strip()
    if len(augmented) > CLARIFY_QUERY_MAX_CHARS:
        augmented = augmented[-CLARIFY_QUERY_MAX_CHARS:]

    return {
        "current_query": augmented,
        "clarification_history": history,
        "clarification_context": {"question": question, "answer": truncated_answer},
        "execution_status": "RETRY",
        "retry_count": 0,
        "active_sub_agent": "sql",
    }


def _dashboard_placeholder(state: AgentState) -> dict:
    tracer = get_tracer(state.get("trace_id", ""))
    tracer.end("DONE")
    return {"report_spec": ReportSpec(version="1.0", insight="Dashboard mode not yet implemented")}


# --- Parent Graph Build ---

def build_parent_graph():
    register_all_tools()

    workflow = StateGraph(AgentState)

    workflow.add_node("security_guard", _security_guard)
    workflow.add_node("classify", _classify_intent)
    workflow.add_node("data_agent", _run_data_agent)
    workflow.add_node("sql_agent", _run_sql_agent)
    workflow.add_node("evaluate", _evaluate)
    workflow.add_node("report_agent", _run_report_agent)
    workflow.add_node("clarify", _clarify)
    workflow.add_node("dashboard_agent", _dashboard_placeholder)

    workflow.set_entry_point("security_guard")

    workflow.add_conditional_edges("security_guard", _route_security)
    workflow.add_conditional_edges("classify", _route_intent)
    workflow.add_edge("data_agent", "sql_agent")
    workflow.add_edge("sql_agent", "evaluate")
    workflow.add_conditional_edges("evaluate", _route_evaluate)
    workflow.add_edge("report_agent", END)
    workflow.add_edge("dashboard_agent", END)
    workflow.add_edge("clarify", "data_agent")

    return workflow.compile(checkpointer=get_checkpointer())