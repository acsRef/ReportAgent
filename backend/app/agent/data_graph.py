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
    discovered_tables: list[dict]
    mcp_tool_calls: list[dict]
    raw_schema: str
    schema_context: SchemaContext


@traced_node("data_detect_intent")
def _detect_intent(state: DataAgentState) -> dict:
    query = state["user_query"]
    keywords_data = ["查询", "统计", "数据", "表", "字段", "销售", "订单", "库存", "退货"]
    q = query.lower()
    has_data_intent = any(k in q for k in keywords_data)
    return {"discovered_tables": [] if not has_data_intent else []}


@traced_node("data_search_schema")
def _search_schema(state: DataAgentState) -> dict:
    query = state["user_query"]
    if not state.get("discovered_tables"):
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
        source="duckdb",
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