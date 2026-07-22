from __future__ import annotations

import json
import time
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from app.llm import call_llm
from app.models.contracts import ComponentSpec, QueryResult, ReportSpec
from app.tools.sql_tools import chart_advisor, insight_analyst
from app.utils.text import safe_json_parse, strip_markdown_fence
from app.tools.registry import registry
from app.infra.trace.sdk import traced_node


class ReportAgentState(TypedDict):
    query_result: Optional[QueryResult]
    user_query: str
    chart_config: dict
    insight_text: str
    report_spec: Optional[ReportSpec]
    assemble_plan: list[dict]
    assemble_step_idx: int
    assemble_results: list[dict]


@traced_node("report_plan_analysis")
def _plan_analysis(state: ReportAgentState) -> dict:
    qr_raw = state.get("query_result")
    if not qr_raw:
        return {"assemble_plan": [], "assemble_step_idx": 0, "assemble_results": []}

    # Normalize QueryResult (might be Pydantic model or dict from subgraph)
    if isinstance(qr_raw, dict):
        qr = QueryResult(**qr_raw)
    else:
        qr = qr_raw

    if not qr.rows:
        return {"assemble_plan": [], "assemble_step_idx": 0, "assemble_results": []}

    col_names = [c.get("name", "") if isinstance(c, dict) else str(c) for c in qr.columns]

    prompt = f"""你是一个数据分析规划师。根据以下数据特征，制定分析计划。

列: {', '.join(col_names)}
行数: {qr.row_count}

可用分析：
1. chart_advisor(data) — 推荐图表
2. trend_analysis(data) — 趋势分析
3. group_compare(data, group_col, value_col) — 分组对比
4. detect_anomaly(data, value_col) — 异常检测

只输出JSON，禁止解释，禁止markdown，禁止思考过程。
格式：
{{"steps": [{{"tool": "...", "args": {{}}, "description": "..."}}], "reasoning": "..."}}"""

    plan_text = call_llm(prompt, max_tokens=500)

    plan = safe_json_parse(plan_text)
    if isinstance(plan, dict):
        steps = plan.get("steps", [])
    else:
        steps = [{"tool": "chart_advisor", "args": {}, "description": "推荐图表"}]

    return {"assemble_plan": steps, "assemble_step_idx": 0, "assemble_results": []}


@traced_node("report_run_step")
def _run_step(state: ReportAgentState) -> dict:
    plan = state.get("assemble_plan", [])
    idx = state.get("assemble_step_idx", 0)

    if not plan or idx >= len(plan):
        return _build_output(state)

    step = plan[idx]
    qr_raw = state.get("query_result")
    if isinstance(qr_raw, dict):
        qr = QueryResult(**qr_raw)
    else:
        qr = qr_raw

    data_json = json.dumps({
        "columns": qr.columns if qr else [],
        "rows": qr.rows if qr else [],
    }, ensure_ascii=False, default=str)

    def _call_tool(caps: list[str], *args) -> str:
        tools = registry.get(caps)
        if tools:
            return tools[0](*args)
        return f"工具不可用: {caps[0]}"

    tool_name = step.get("tool", "")
    if tool_name == "chart_advisor":
        text = chart_advisor(data_json)
    elif tool_name == "trend_analysis":
        text = _call_tool(["trend_analysis"], data_json)
    elif tool_name == "group_compare":
        text = _call_tool(["group_compare"], data_json,
                          step.get("args", {}).get("group_col", ""),
                          step.get("args", {}).get("value_col", ""))
    elif tool_name == "detect_anomaly":
        text = _call_tool(["detect_anomaly"], data_json,
                          step.get("args", {}).get("value_col", ""))
    else:
        text = chart_advisor(data_json)

    results = list(state.get("assemble_results", []))
    results.append({"step": step.get("description", tool_name), "result": text})

    return {"assemble_step_idx": idx + 1, "assemble_results": results}


@traced_node("report_build_output")
def _build_output(state: ReportAgentState) -> dict:
    results = state.get("assemble_results", [])
    chart_config = {}
    insight_text = ""

    for r in results:
        if "图表" in r["step"] or "chart" in r["step"]:
            try:
                chart_config = json.loads(r["result"])
            except (json.JSONDecodeError, Exception):
                pass
        else:
            insight_text += r["result"] + "\n"

    if not chart_config:
        qr_raw = state.get("query_result")
        if isinstance(qr_raw, dict):
            qr = QueryResult(**qr_raw)
        else:
            qr = qr_raw
        data_json = json.dumps({
            "columns": qr.columns if qr else [],
            "rows": qr.rows if qr else [],
        }, ensure_ascii=False, default=str)
        chart_config = safe_json_parse(chart_advisor(data_json)) or {}

    comps = []
    if chart_config.get("type") and chart_config["type"] != "table":
        comps.append(ComponentSpec(
            id="c1",
            type=chart_config["type"],
            title="数据分析",
            visual_config=chart_config.get("config", {}),
        ))

    spec = ReportSpec(version="1.0", components=comps, insight=insight_text.strip())

    return {"chart_config": chart_config, "insight_text": insight_text.strip(), "report_spec": spec}


def _route_step(state: ReportAgentState) -> str:
    plan = state.get("assemble_plan", [])
    idx = state.get("assemble_step_idx", 0)
    if not plan or idx >= len(plan):
        return "build_output"
    return "run_step"


def build_report_graph():
    workflow = StateGraph(ReportAgentState)

    workflow.add_node("plan_analysis", _plan_analysis)
    workflow.add_node("run_step", _run_step)
    workflow.add_node("build_output", _build_output)

    workflow.set_entry_point("plan_analysis")
    workflow.add_edge("plan_analysis", "run_step")
    workflow.add_conditional_edges("run_step", _route_step)
    workflow.add_edge("build_output", END)

    return workflow.compile()

