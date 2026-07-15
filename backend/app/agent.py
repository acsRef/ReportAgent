from __future__ import annotations

import json
from typing import Literal

from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.state import AgentState
from app.nodes import (
    classify_intent,
    gen_sql_llm,
    validate_sql_step,
    execute_sql_step,
    self_correct,
    clarify,
    assemble_planner,
    assemble_executor,
)
from app.tools import run_sql, validate_sql
from app.clarify_tool import ask_clarification_tool
from app.mcp_client import schema_client


def build_agent() -> StateGraph:
    workflow = StateGraph(AgentState)

    mcp_tools = [
        schema_client.search_tables_wrapper,
        schema_client.get_table_ddl_wrapper,
        schema_client.list_tables_wrapper,
    ]
    all_tools = mcp_tools + [ask_clarification_tool]
    tool_node = ToolNode(all_tools)

    workflow.add_node("classify", classify_intent)
    workflow.add_node("gen_sql_llm", gen_sql_llm)
    workflow.add_node("mcp_tools", tool_node)
    workflow.add_node("validate", validate_sql_step)
    workflow.add_node("execute", execute_sql_step)
    workflow.add_node("correct", self_correct)
    workflow.add_node("clarify", clarify)
    workflow.add_node("assemble_plan", assemble_planner)
    workflow.add_node("assemble_exec", assemble_executor)

    workflow.set_entry_point("classify")

    workflow.add_conditional_edges(
        "classify",
        lambda state: state["intent"],
        {"报表": "gen_sql_llm", "闲聊": END, "看板": END},
    )

    workflow.add_conditional_edges(
        "gen_sql_llm",
        tools_condition,
        {"tools": "mcp_tools", END: "validate"},
    )
    workflow.add_edge("mcp_tools", "gen_sql_llm")

    workflow.add_conditional_edges(
        "validate",
        lambda state: "execute" if state["sql_valid"] else "correct",
    )
    workflow.add_conditional_edges(
        "correct",
        lambda state: "clarify" if state.get("retry_count", 0) >= 3 else "validate",
    )

    workflow.add_conditional_edges(
        "execute",
        lambda state: _after_execute(state),
    )

    workflow.add_edge("assemble_plan", "assemble_exec")
    workflow.add_conditional_edges(
        "assemble_exec",
        lambda state: (
            "assemble_exec"
            if state.get("assemble_step_idx", 0) < len(state.get("assemble_plan", []))
            else END
        ),
    )

    workflow.add_edge("clarify", END)

    return workflow.compile()


def _after_execute(state: AgentState) -> Literal["assemble_plan", "correct", "clarify"]:
    data = json.loads(state.get("sql_result", "{}"))
    if "error" in data:
        if state.get("retry_count", 0) >= 3:
            return "clarify"
        state["sql_error"] = data["error"]
        return "correct"
    return "assemble_plan"
