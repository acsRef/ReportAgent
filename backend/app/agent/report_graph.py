from __future__ import annotations

import json
import logging
import time
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from app.llm import _format_tools_for_prompt, call_llm
from app.models.contracts import QueryResult
from app.report.preference import apply_chart_preference
from app.report.spec import ComponentSpec, DataBinding, ReportSpec, TableSpec
from app.tools.sql_tools import chart_advisor, insight_analyst
from app.utils.text import safe_json_parse, strip_markdown_fence
from app.tools.registry import registry
from app.infra.trace.sdk import traced_node
from app.state.checkpoint_adapter import migrate_checkpoint
from app.agent.prompts import build_report_plan_prompt

logger = logging.getLogger(__name__)


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
    # P16 D1：Report 偏好接线（memory-architecture §三 Report = Semantic ✅ Preference）——
    # 父图透传会话归属，_plan_analysis 经 ContextRuntime 召回 preference 类记忆，
    # 走确定性通道（apply_chart_preference）影响 chart_config，不注入 prompt。
    session_id: Optional[str]
    user_id: Optional[int]
    report_preferences: list[str]


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
async def _plan_analysis(state: ReportAgentState) -> dict:
    state = migrate_checkpoint(dict(state))  # P3 (γ): graph 入口 v1→v2 adapter

    # P16 D2：Report 偏好接入（按 memory-architecture 契约补全——§二触发 2「报告生成时
    # 召回图表偏好」+ §三 Report = Semantic ✅ Preference）。decision 层 REPORT 档已保证
    # semantic=True / query=False / top_k_preferences=3，这里只取 preference 类条目；
    # 视觉化偏好 → 确定性 apply_chart_preference（不注入 prompt，LLM 输出面不扩大）。
    # 失败降级为空，绝不阻塞报告生成（与 SQL 链接入语义一致）。
    report_preferences: list[str] = []
    query = state.get("user_query") or ""
    session_id = state.get("session_id")
    if session_id and query:
        try:
            from app.context.runtime import ContextRuntime  # 局部 import 避免 cycle / 测试 patch

            _uid_raw = state.get("user_id")
            try:
                _uid = int(_uid_raw) if _uid_raw not in (None, "") else 0
            except (TypeError, ValueError):
                _uid = 0
            _bundle = await ContextRuntime().build(
                session_id=session_id,
                user_id=_uid,
                query=query,
                agent="report_plan_analysis",  # resolver report_* → REPORT 档
                state_dict=dict(state),
            )
            report_preferences = [
                it["raw_text"] for it in _bundle["recall_items"]
                if it.get("source") == "memory_preference" or it.get("kind") == "preference"
            ]
        except Exception as exc:
            logger.warning("ContextRuntime.build (report) failed: %s", exc)

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

    return {
        "assemble_plan": steps, "assemble_step_idx": 0, "assemble_results": [],
        "report_preferences": report_preferences,
    }


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

    qr = _validate_qr(state.get("query_result"))
    if not chart_config:
        data_json = json.dumps({
            "columns": qr.columns if qr else [],
            "rows": qr.rows if qr else [],
        }, ensure_ascii=False, default=str)
        chart_config = safe_json_parse(chart_advisor(data_json)) or {}

    # P16 D3：视觉化偏好确定性应用（memory-architecture §二触发 2）——ContextRuntime
    # 召回的 preference 文本 → chart type override；词表外偏好/数据不支持时让位。
    chart_config = apply_chart_preference(chart_config, state.get("report_preferences") or [])

    # P10：v2 spec 带 provenance——chart 行/字段锚定 QueryResult（validator 钉），
    # table 列直通 QueryResult 列名，kpi 不生产（无业务诉求，schema+校验机制就位）。
    config = chart_config.get("config", {}) or {}
    dims = config.get("dimensions") or {}
    fields = [v for v in dims.values() if isinstance(v, str)]
    comps = []
    if chart_config.get("type") and chart_config["type"] != "table":
        # P10 provenance 闭合：声明 fields 必须 ⊇ 实际行携带字段（validator 钉）。
        # chart_advisor 的 dimensions 常只给 x/y，行里却带完整列（区域/年份/销售额）——
        # 声明以行字段并集为准，避免「行携带未声明字段」violations。
        rows = config.get("data", []) or []
        declared = list(fields)
        for row in rows:
            for k in row.keys():
                if k not in declared:
                    declared.append(k)
        comps.append(ComponentSpec(
            id="c1",
            type=chart_config["type"],
            title="数据分析",
            data_binding=DataBinding(fields=declared, rows=rows),
            visual_config=config,
        ))

    table = None
    if qr and qr.rows:
        col_names = [c.get("name", "") if isinstance(c, dict) else str(c) for c in qr.columns]
        table = TableSpec(columns=col_names)

    spec = ReportSpec(
        version="1.0", components=comps, insight=insight_text.strip(), table=table,
    )

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

