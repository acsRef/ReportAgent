from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from app.llm import _format_tools_for_prompt, budgets, call_llm
from app.models.contracts import QueryResult
from app.report.preference import (
    apply_chart_preference,
    apply_explicit_chart_request,
    explicit_chart_type,
)
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
    # 2026-11-02：需求卡透传——报告规划需要 analysis_methods / dimensions / summary，
    # 否则「分析趋势和异常」只会产出一张图 + 一张表。
    requirement_card: Optional[dict]


def _user_words(state: ReportAgentState) -> str:
    """用户在这一轮说过的原话（用于显式图表要求等确定性判定）。

    confirm 流里 `user_query` 是空串（安全闸约定），真正的原话在需求卡 `summary`；
    adjust 流还会带 `adjustment_text`。三处拼起来判断，顺序=用户原话优先。

    `requirement_card` 在子图里是 **dict**（父图 `model_dump()` 透传），
    不能按 Pydantic 模型取属性——2026-11-05 真机踩过（`getattr(dict, "summary")` 恒为空）。
    """
    card = state.get("requirement_card") or {}
    if isinstance(card, dict):
        summary = card.get("summary") or ""
    else:  # 兼容传模型的情况（测试/其它调用点）
        summary = getattr(card, "summary", "") or ""
    return " ".join(
        part
        for part in (state.get("user_query") or "", str(summary), state.get("adjustment_text") or "")
        if part
    )


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

    # 2026-11-02：把「用户到底要什么」带进报告规划——此前只有列名+行数，
    # 模型无从知道要趋势/异常，只能出一张图一张表。
    card = state.get("requirement_card") or {}
    prompt = build_report_plan_prompt(
        column_names=col_names,
        row_count=qr.row_count,
        tools_block=_format_tools_for_prompt(),
        requirement_summary=str(card.get("summary") or ""),
        analysis_methods=list(card.get("analysis_methods") or []),
        dimensions=list(card.get("dimensions") or []),
    )

    raw = call_llm(prompt, max_tokens=budgets.max_tokens("report_plan"))
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
    # P16.5 截图验收发现：保留 tool 名——_build_output 按确定性 tool 名分流
    # （description 是 LLM 自由文本，「数据可视化」等描述不含「图表/chart」时
    # chart JSON 会被误拼进 insight——真产品 bug）。
    results.append({"step": step.get("description", tool_name), "tool": tool_name, "result": text})

    return {"assemble_step_idx": idx + 1, "assemble_results": results}


_NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(万|亿|%)?")


def _narrative_intent(card) -> str:
    """从需求卡推出叙述角度（detail > anomaly > trend > compare > overview）。"""
    if not card:
        return "overview"
    get = (lambda k, d=None: card.get(k, d)) if isinstance(card, dict) else (lambda k, d=None: getattr(card, k, d))
    if get("detail_level") == "detail":
        return "detail"
    methods = " ".join(str(m).lower() for m in (get("analysis_methods") or []))
    if "anomaly" in methods:
        return "anomaly"
    if "trend" in methods:
        return "trend"
    if "compare" in methods:
        return "compare"
    return "overview"


def _collect_data_numbers(rows: list[dict], *, sample: int = 200) -> list[float]:
    """收集「结果数据里真实存在的数」，供叙述层做数字锚定。

    两类来源：
    1. **单元格值**（只取前 sample 行，界住内存）；
    2. **度量列的合计 / 均值**（按全量行算）——2026-11-03：此前只收单元格值，
       于是「全年合计 2.63 亿」这种**派生但真实**的结论一律被判成编造、headline
       被打回确定性兜底，长序列（日/周）报告的叙述几乎全被丢掉。
    """
    from decimal import Decimal

    from app.utils.numbers import is_numeric_value, to_decimal
    from app.tools.report_tools import measure_keys

    values: list[float] = []
    if not rows:
        return values

    for row in rows[:sample]:
        for value in row.values():
            if is_numeric_value(value):
                dec = to_decimal(value)
                if dec is not None:
                    values.append(float(dec))

    for key in measure_keys(rows[0]):
        total = Decimal(0)
        count = 0
        for row in rows:
            dec = to_decimal(row.get(key))
            if dec is None:
                continue
            total += dec
            count += 1
        if not count:
            continue
        values.append(float(total))
        values.append(float(total) / count)
    return values


def _numbers_valid(text: str, data_values: list[float]) -> bool:
    """叙述里的绝对数字必须能在结果数据里找到（百分比视为派生值，放行）。

    2026-11-03：模型会顺手写「销售额 9040 万」这种看似合理的数字。宁可少说，
    也不能让它编——找不到来源的那条证据直接丢掉。

    同年修订：**年份不算数据**。此前「2024 年销售额合计 …」里的 2024 也要在结果里
    找到，而按月/按周聚合的帧里根本没这一格 → 整条 headline 被误杀、退回确定性兜底
    （长序列报告的 findings 几乎全灭）。日历标签不该进锚定。
    """
    if not text:
        return True
    for match in _NUM_RE.finditer(text):
        # group(2) 是可选单位组——没命中时是 None（不是空串），必须归一化，
        # 否则年份豁免（unit in ("", "年")）永远不生效。
        raw = match.group(1).replace(",", "")
        unit = match.group(2) or ""
        try:
            value = float(raw)
        except ValueError:
            continue
        if unit == "%":
            continue
        if unit in ("", "年") and len(raw) == 4 and 1900 <= value <= 2100:
            continue  # 年份（2024 年 / 2024）：日历标签，不是数据断言
        if unit == "万":
            value *= 1e4
        elif unit == "亿":
            value *= 1e8
        if value < 10:  # 「2 名」「3 个点」这类小数字不校验
            continue
        if not any(abs(d - value) <= max(abs(d) * 0.02, 1.0) for d in data_values):
            return False
    return True


def _fallback_headline(qr) -> str:
    """叙述不可用时的确定性结论：最高/最低 + 合计。

    2026-11-03：度量列必须排除时间类维度。此前取「第一个数值列」，按周聚合时
    那一列是 `周`（int 1..53），结论写成「周合计 1,484.00」——把周序号求了和。
    """
    from app.utils.numbers import is_numeric_value, to_decimal
    from app.tools.report_tools import is_time_like, measure_keys

    rows = list(qr.rows or [])
    if not rows:
        return ""
    first = rows[0]
    # 度量列：排除时间类（周/月份/年份…）。
    measure = measure_keys(first)
    if not measure:
        return f"共 {len(rows)} 行记录"
    value_key = measure[0]
    # 标签列：优先真文本列；全是数值时退回时间维度（「第 7 周最高」比没有标签好）。
    label_key = next((k for k, v in first.items() if not is_numeric_value(v)), None)
    if label_key is None:
        label_key = next(
            (k for k in first if k != value_key and is_time_like(k)), None,
        )
    pairs = [(str(r.get(label_key, "")), to_decimal(r.get(value_key))) for r in rows] \
        if label_key else [("", to_decimal(r.get(value_key))) for r in rows]
    pairs = [(k, v) for k, v in pairs if v is not None]
    if not pairs:
        return f"共 {len(rows)} 行记录"
    pairs.sort(key=lambda kv: kv[1], reverse=True)
    top_label, top_value = pairs[0]
    total = sum(v for _, v in pairs)
    if label_key:
        shown = _format_period_label(label_key, top_label) if is_time_like(label_key) else top_label
        return (
            f"{value_key}合计 {total:,.2f}，其中 {shown} 最高"
            f"（{top_value:,.2f}），共 {len(rows)} 组"
        )
    return f"{value_key}合计 {total:,.2f}，共 {len(rows)} 行"


def _format_period_label(column: str, value: str) -> str:
    """时间维度标签的人话写法：周=41 → 第41周；月份=6 → 6月；年份=2024 → 2024年。

    确定性结论是用户最先读到的一句话，「周=41 最高」读着像变量名。
    """
    name = str(column or "").strip()
    low = name.lower()
    if not value:
        return name
    if "周" in name or "week" in low:
        return f"第{value}周"
    if "月份" in name or name == "月" or "month" in low:
        return f"{value}月"
    if "年份" in name or name == "年" or "year" in low:
        return f"{value}年"
    if "季度" in name or "quarter" in low:
        return f"Q{value}"
    return f"{name}={value}"


def _compose_narrative(state, qr, results: list[dict], fallback: str) -> str:
    """把「数据 + 工具结论」写成人话（2026-11-03）。

    此前核心发现是工具输出拼接（`insight_text += r["result"]`），实测出现
    「整体趋势平稳 发现 16 个异常值: 2024-09-28: 497,034.67; …」这种一坨。
    现在交给叙述模型产出「结论 / 证据 / 可能原因 / 建议」；失败则退回原拼接。
    """
    if not qr or not qr.rows:
        return ""

    from app.agent.prompts.report_prompts import build_report_narrate_prompt

    card = state.get("requirement_card") or {}
    summary = ""
    if card:
        summary = card.get("summary") if isinstance(card, dict) else getattr(card, "summary", "")
    columns = [c.get("name", "") if isinstance(c, dict) else str(c) for c in qr.columns]
    tool_text = "\n".join(
        f"- {r.get('tool')}: {str(r.get('result'))[:800]}"
        for r in results if r.get("tool") != "chart_advisor"
    )
    prompt = build_report_narrate_prompt(
        user_query=str(state.get("user_query") or ""),
        requirement_summary=str(summary or ""),
        columns=columns,
        row_count=qr.row_count,
        sample_rows=list(qr.rows)[:20],
        tool_results=tool_text,
        intent=_narrative_intent(card),
    )
    try:
        raw = call_llm(prompt, max_tokens=budgets.max_tokens("report_narrative"))
        data = safe_json_parse(raw) if isinstance(raw, str) else raw
    except Exception as exc:  # noqa: BLE001 - 叙述失败不能拖垮报告
        logger.warning("report narration failed: %s", exc)
        return ""
    if not isinstance(data, dict):
        return ""

    headline = str(data.get("headline") or "").strip()
    findings = [str(x).strip() for x in (data.get("findings") or []) if str(x).strip()]
    why = str(data.get("why") or "").strip()
    nxt = [str(x).strip() for x in (data.get("next") or []) if str(x).strip()]

    # 数字校验（2026-11-03）：叙述里的绝对数字必须来自结果数据；找不到就丢掉该条，
    # headline 不通过则用确定性结论兜底——宁可少说，也不许编数字。
    data_values = _collect_data_numbers(list(qr.rows)[:200])
    findings = [item for item in findings if _numbers_valid(item, data_values)]
    if headline and not _numbers_valid(headline, data_values):
        logger.warning("narration headline rejected (number not in data): %s", headline[:80])
        headline = ""
    if not headline:
        headline = _fallback_headline(qr)
    if not headline and not findings:
        return fallback

    lines: list[str] = []
    if headline:
        lines.append(f"**结论**：{headline}")
    for item in findings[:3]:
        lines.append(f"- {item}")
    if why:
        lines.append(f"**可能原因**：{why}")
    if nxt:
        lines.append("**建议下一步**：" + "；".join(nxt[:2]))
    text = "\n".join(lines)
    return text or fallback


@traced_node("report_build_output")
def _build_output(state: ReportAgentState) -> dict:
    results = state.get("assemble_results", [])
    chart_config = {}
    insight_text = ""

    for r in results:
        # P16.5：tool 名分流优先（确定性）——description 仅作兜底（「图表」/「chart」）
        if r.get("tool") == "chart_advisor" or "图表" in r.get("step", "") or "chart" in r.get("step", ""):
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

    # 2026-11-03：核心发现不能是工具输出拼接（实测出现「整体趋势平稳 发现 16 个异常值:
    # 2024-09-28: 497,034.67; …」这种一坨）。改为**叙述层**：把结果数据 + 工具结论交给
    # 叙述模型，产出「结论 / 证据 / 可能原因 / 建议」四段人话。
    narrative = _compose_narrative(state, qr, results, insight_text)
    if narrative:
        insight_text = narrative

    # P16 D3：视觉化偏好确定性应用（memory-architecture §二触发 2）——ContextRuntime
    # 召回的 preference 文本 → chart type override；词表外偏好/数据不支持时让位。
    # 2026-11-05（plan live-session-issues D4）：**用户当轮原话**里的显式要求（「画饼图」）
    # 优先级最高——命中它就不再让记忆偏好二次覆盖（显式指令 > 记忆偏好 > advisor 默认）。
    #
    # 注意 confirm 流的 `user_query` 是**空串**（见 confirmed_execution_graph 的安全闸说明），
    # 用户原话在需求卡 summary / 调整文本里——三处都看，否则「画饼图」永远进不了图表决策。
    explicit_query = _user_words(state)
    if explicit_chart_type(explicit_query):
        chart_config = apply_explicit_chart_request(chart_config, explicit_query)
    else:
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

