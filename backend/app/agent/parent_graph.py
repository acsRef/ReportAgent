from __future__ import annotations

from typing import Literal, Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from app.agent.data_graph import build_data_graph
from app.agent.sql_graph import build_sql_graph
from app.agent.report_graph import build_report_graph
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
from app.agent.security_guard import SecurityGuard
from app.tools.registry import registry
from app.tools.__init__ import register_all_tools


class AgentState(TypedDict):
    user_query: str
    original_query: str
    current_query: str
    clarification_history: list
    session_id: str
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
    "查询", "数据", "报表",
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
    session_id = state.get("session_id", "")
    try:
        mm = MemoryManager()
        memory_context = await mm.recall(query=q, user_id=session_id, top_k_queries=2, top_k_preferences=3)
    except Exception:
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
        return "clarify"
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
    ss = sql_graph.invoke({
        "schema_context": schema_input,
        "user_query": state.get("current_query", state["user_query"]),
        "query_plan": None,
        "generated_sql": "",
        "validation_result": {},
        "sql_result": "",
        "execution_status": "",
        "error": None,
        "retry_counters": {"plan": 0, "sql_generation": 0},
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
                target_metric=ss.get("query_plan", {}).target_metric if ss.get("query_plan") else "",
            )
        except Exception:
            pass

    return {
        "query_plan": ss.get("query_plan"),
        "query_result": qr,
        "execution_status": status,
        "error": error_obj,
        "retry_count": state.get("retry_count", 0) + 1,
    }


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
    else:
        if state.get("retry_count", 0) < 3:
            return "sql_agent"
        tracer = get_tracer(state.get("trace_id", ""))
        tracer.end("FAILED")
        return "clarify"


# --- Report Agent Node ---

@traced_node("report_agent")
async def _run_report_agent(state: AgentState) -> dict:
    report_graph = build_report_graph()
    qr = state.get("query_result")
    rs = report_graph.invoke({
        "query_result": qr.model_dump() if qr else None,
        "user_query": state.get("current_query", state["user_query"]),
        "chart_config": {},
        "insight_text": "",
        "report_spec": None,
        "assemble_plan": [],
        "assemble_step_idx": 0,
        "assemble_results": [],
    })

    insight = rs.get("insight_text", "")

    # Save insight to semantic memory
    if insight:
        try:
            mm = MemoryManager()
            await mm.remember_preference(
                user_id=state.get("session_id", "anonymous"),
                content=insight,
                source="report_agent",
                memory_type="insight",
                importance=0.3,
            )
        except Exception:
            pass

    # End trace
    tracer = get_tracer(state["trace_id"])
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
    clarification_entry = {"question": question, "answer": answer}
    history = list(state.get("clarification_history", []))
    history.append(clarification_entry)

    # Build augmented query: original + clarification context
    current_q = state.get("current_query", state["user_query"])
    augmented = f"{current_q}\n\n补充信息: {answer}"

    return {
        "current_query": augmented,
        "clarification_history": history,
        "clarification_context": {"question": question, "answer": answer},
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

    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)
