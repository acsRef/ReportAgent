from __future__ import annotations

import json
import time
from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.models.contracts import ErrorDetail, SchemaContext, TableSchema, ColumnSchema
from app.tools.data_tools import search_tables, get_table_ddl, list_tables
from app.tools.registry import registry
from app.infra.trace.sdk import traced_node


class DataAgentState(TypedDict):
    user_query: str
    has_data_intent: bool
    discovered_tables: list[dict]
    mcp_tool_calls: list[dict]
    raw_schema: str
    schema_context: SchemaContext
    # 层7/B-3: 必须声明 trace_id，否则 LangGraph 对未声明 key 静默丢弃，
    # traced_node 读到空串 → 所有子图 span 落进共享的 _local[""] 桶，
    # 造成跨请求 trace 污染 + 内存泄露。调用点早已传入 trace_id。
    trace_id: str


@traced_node("data_detect_intent")
def _detect_intent(state: DataAgentState) -> dict:
    """判定是否数据查询（廉价关键词门控）。

    非数据查询返回 has_data_intent=False，让 _search_schema 短路、不调 rag 检索。
    修复：此前 `has_data_intent` 算了但两个分支都返回 []，意图门形同虚设。
    """
    query = state["user_query"]
    keywords_data = ["查询", "统计", "数据", "表", "字段", "销售", "订单", "库存",
                     "退货", "分析", "排名", "趋势", "多少", "哪个", "占比", "利润"]
    q = query.lower()
    has_data_intent = any(k in q for k in keywords_data)
    return {"has_data_intent": has_data_intent, "discovered_tables": []}


@traced_node("data_search_schema")
def _search_schema(state: DataAgentState) -> dict:
    query = state["user_query"]
    # 非数据查询（has_data_intent=False）直接短路，不调 rag 检索、不白烧 token。
    if state.get("has_data_intent", True) and not state.get("discovered_tables"):
        raw = search_tables.invoke({"query": query, "top_k": 3})
        result = json.loads(raw) if isinstance(raw, str) else raw
        return {
            "discovered_tables": result if isinstance(result, list) else [],
            "mcp_tool_calls": [{"tool": "search_tables", "query": query}],
        }
    return {}


@traced_node("data_build_context")
def _build_context(state: DataAgentState) -> dict:
    tables = state.get("discovered_tables", [])
    schema_tables = []

    for t in tables[:5]:
        cols = t.get("columns", [])
        schema_tables.append(TableSchema(
            name=t.get("table_name", ""),
            description=t.get("description", ""),
            columns=[ColumnSchema(name=c["name"], type=c["type"]) for c in cols],
        ))

    ctx = SchemaContext(
        version="1.0",
        source="postgres",
        tables=schema_tables,
        confidence=min(len(tables) * 0.3, 1.0),
        status="SUCCESS" if schema_tables else "FAILED",
        error=None if schema_tables else ErrorDetail(code="NO_TABLES", message="未找到相关数据表"),
    )
    return {"schema_context": ctx}


def build_data_graph():
    workflow = StateGraph(DataAgentState)

    workflow.add_node("detect_intent", _detect_intent)
    workflow.add_node("search_schema", _search_schema)
    workflow.add_node("build_context", _build_context)

    workflow.set_entry_point("detect_intent")
    workflow.add_edge("detect_intent", "search_schema")
    workflow.add_edge("search_schema", "build_context")
    workflow.add_edge("build_context", END)

    return workflow.compile()