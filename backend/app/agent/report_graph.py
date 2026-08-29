from __future__ import annotations

import json
import time
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from app.llm import _format_tools_for_prompt, call_llm
from app.models.contracts import ComponentSpec, QueryResult, ReportSpec
from app.tools.sql_tools import chart_advisor, insight_analyst
from app.utils.text import safe_json_parse, strip_markdown_fence
from app.tools.registry import registry
from app.infra.trace.sdk import traced_node
from app.state.checkpoint_adapter import migrate_checkpoint
from app.agent.prompts import build_report_plan_prompt


class ReportAgentState(TypedDict):
    query_result: Optional[QueryResult]
    user_query: str
    chart_config: dict
    # P3 §2.4 deterministic rename：v1 insight_text → v2 insight（plan §F10 收口；
    # checkpoint_adapter / blocks 的 rename map 已就位，本 TypedDict 跟进使用 v2 名）
    insight: str
    report_spec: Optional[ReportSpec]
    assemble_plan: list[dict]
    assemble_step_idx: int
    assemble_results: list[dict]
    # 层7/B-3: 声明 trace_id（调用点早已传入），避免子图 span 落进共享桶。
    trace_id: str


def _validate_qr(qr_raw) -> Optional[QueryResult]:
    """C-9: 子图边界统一收敛 QueryResult 的解析。

    父图传进来的可能是 Pydantic 模型，也可能是 `model_dump()` 后的 dict。
    之前三处节点各自 `QueryResult(**qr_raw)` 防御，形态一变就静默失效；
    统一走 model_validate（比 `**dict` 更宽容，允许字段类型强转）。
    """
    if qr_raw is None:
        return None
    if isinstance(qr_raw, QueryResult):
        return qr_raw
    return QueryResult.model_validate(qr_raw)


@traced_node("report_plan_analysis")
def _plan_analysis(state: ReportAgentState) -> dict:
    state = migrate_checkpoint(dict(state))  # P3 (γ): graph 入口 v1→v2 adapter
    qr_raw = state.get("query_result")
    if not qr_raw:
        return {"assemble_plan": [], "assemble_step_idx": 0, "assemble_results": []}

    qr = _validate_qr(qr_raw)

    if not qr.rows:
        return {"assemble_plan": [], "assemble_step_idx": 0, "assemble_results": []}

    col_names = [c.get("name", "") if isinstance(c, dict) else str(c) for c in qr.columns]

    prompt = build_report_plan_prompt(
        column_names=col_names,
        row_count=qr.row_count,
        tools_block=_format_tools_for_prompt(),
    )

    raw = call_llm(prompt, max_tokens=1500)
    plan = safe_json_parse(raw) if isinstance(raw, str) else raw
    if not isinstance(plan, dict):
        plan = {}
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
    qr = _validate_qr(state.get("query_result"))

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
    elif tool_name == "insight_analyst":
        # 菜单与分发必须一致：模型能选的工具必须能执行
        text = insight_analyst(data_json)
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
        qr = _validate_qr(state.get("query_result"))
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

    return {"chart_config": chart_config, "insight": insight_text.strip(), "report_spec": spec}


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

