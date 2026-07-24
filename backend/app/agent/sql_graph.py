from __future__ import annotations

import json
import logging
from typing import Literal, Optional, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from app.llm import call_llm, _format_tools_for_prompt
from app.models.contracts import ErrorDetail, QueryPlan, QueryResult, SchemaContext
from app.utils.text import safe_json_parse, strip_markdown_fence
from app.infra.trace.sdk import traced_node
from app.tools.sql_tools import validate_sql, execute_sql

logger = logging.getLogger(__name__)


class ClarifyDecision(BaseModel):
    """Pre-SQL clarification decision emitted by the `plan` node.

    The plan node returns BOTH the query plan and a clarification decision in
    a single LLM call. When `action == "clarify"`, the parent graph routes to
    the existing `clarify` node instead of generating SQL.
    """

    action: Literal["clarify", "run_direct"] = "run_direct"
    missing_dimensions: list[str] = []
    predicted_table: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: str = ""


class QueryPlanWithClarify(QueryPlan):
    """QueryPlan extended with the merged `clarify_decision` payload."""

    clarify_decision: ClarifyDecision = Field(default_factory=ClarifyDecision)


class IntentOption(BaseModel):
    """A single intent suggestion in the stage-1 intent_card."""
    label: str
    description: str
    tool: Literal["group_compare", "trend_analysis", "detect_anomaly",
                  "chart_advisor", "insight_analyst"]
    params_preview: dict = {}


class IntentCardPayload(BaseModel):
    title: str = "我能这样帮你分析 — 选一个继续"
    options: list[IntentOption]


class ChatCard(BaseModel):
    """Lightweight chat card emitted via SSE `event: card`.

    Supports `intent_card` (stage-1), `options_group` (stage-2),
    and `preview_card` (stage-3 result preview) in this release.
    """

    type: Literal["intent_card", "options_group", "preview_card"]
    version: int = 1
    payload: dict = {}


class SQLAgentState(TypedDict):
    schema_context: Optional[SchemaContext]
    user_query: str
    query_plan: Optional[QueryPlanWithClarify]
    generated_sql: str
    validation_result: dict
    sql_result: str
    execution_status: str
    error: Optional[str]
    retry_counters: dict
    query_result: Optional[QueryResult]
    chosen_tool: Optional[str]
    # Structured fields the user PATCHed via /requirement. When set,
    # _plan MUST treat these as authoritative and ignore inferences
    # from the free-form `user_query`. Populated by
    # confirmed_execution_graph._confirmed_sql_agent; ignored by the
    # legacy interrupt-based flow.
    confirmed_requirement: Optional[str]


_PLAN_TABLE_HINTS = """常用表速查:
- fact_sales(销售事实), fact_returns(退货事实), fact_inventory(库存事实), fact_attendance(考勤事实)
- 维度表: dim_date, dim_region, dim_product, dim_customer, dim_warehouse, dim_employee"""


_PLAN_FEWSHOT = """[示例1]
用户: "今年华东销售趋势"
输出: {"target_metric":"销售趋势","dimensions":["时间","区域"],"filters":[{"field":"region","operator":"=","value":"华东"},{"field":"year","operator":"=","value":"今年"}],"aggregation":"sum","time_range":"今年","clarify_decision":{"action":"run_direct","missing_dimensions":[],"predicted_table":"fact_sales","confidence":0.9,"reasoning":"时间(今年)、区域(华东)、指标(销售)三维度均明确"}}

[示例2]
用户: "看一下销量"
输出: {"target_metric":"销量","dimensions":["时间"],"filters":[],"aggregation":"sum","time_range":null,"clarify_decision":{"action":"clarify","missing_dimensions":["时间","区域"],"predicted_table":"fact_sales","confidence":0.45,"reasoning":"时间与区域均缺失，无法定位数据范围"}}

[示例3]
用户: "上个月退货最多的是哪个商品"
输出: {"target_metric":"退货数","dimensions":["商品"],"filters":[{"field":"month","operator":"=","value":"上个月"}],"aggregation":"count","time_range":"上个月","clarify_decision":{"action":"run_direct","missing_dimensions":[],"predicted_table":"fact_returns","confidence":0.82,"reasoning":"时间(上个月)、商品、指标(退货)三维度均明确"}}"""


@traced_node("intent_analyze")
def _intent_analyze(state: SQLAgentState) -> dict:
    """Stage 1: pure LLM intent analysis.

    Reads the user's query, looks at the available tool list, and emits a
    `chat_card` of type `intent_card` with 3-4 candidate analyses. Does NOT
    touch SQL or any data tool — this is the cheap pre-flight step.
    """
    user_query = state.get("user_query", "")

    tools_block = _format_tools_for_prompt()
    prompt = f"""你是 ReportAgent 意图分析器。用户给了一个中文业务问题,
请基于下面的可用工具列表,推荐 3-4 个最匹配用户意图的分析能力。

用户问题: {user_query}

可用工具:
{tools_block}

输出 JSON(禁止解释,禁止 markdown,禁止换行):
{{
  "options": [
    {{
      "label": "📊 各区域销售对比",
      "description": "类似 SQL: SELECT region_name, SUM(total_amount) FROM ...",
      "tool": "group_compare",
      "params_preview": {{"group_col": "region_name", "value_col": "total_amount"}}
    }}
  ],
  "needs_options_group": true,
  "missing_dimensions": [],
  "confidence": 0.85,
  "reasoning": "用户想看不同区域的销售横向对比,典型 group_compare 场景"
}}

规则:
- options 数量必须 3 <= n <= 4
- 每个 option.tool 必须是上面列出的 5 个之一
- label 用一句话讲「做什么」(如「各区域销售对比」「月度趋势」「Top 排名」)
- description 用一句话讲「类似 SQL 会怎么写」
- params_preview 给出一个可能落地的 SQL 草图参数,key 与该 tool 的预期 input 对齐
- confidence 取值 0.7-0.95
- needs_options_group: 当 query 仍有数据维度(时间范围/区域范围/Top N 等)未明确时填 true;完全清晰可直跑时填 false
"""

    raw = call_llm(prompt, max_tokens=600)
    parsed = safe_json_parse(raw)

    fallback_options = [
        IntentOption(label="📊 各区域对比汇总", description="按区域/产品维度对比汇总指标",
                     tool="group_compare"),
        IntentOption(label="📈 月度趋势分析", description="12 个月的趋势折线/同比",
                     tool="trend_analysis"),
        IntentOption(label="🏆 Top 排名", description="Top N 排名 + 占比",
                     tool="trend_analysis"),
    ]

    options: list[IntentOption] = []
    needs_options_group = False
    confidence = 0.5
    reasoning = ""

    if isinstance(parsed, dict) and isinstance(parsed.get("options"), list):
        for opt in parsed["options"][:4]:
            if not isinstance(opt, dict):
                continue
            tool_name = opt.get("tool", "")
            if tool_name not in ("group_compare", "trend_analysis",
                                 "detect_anomaly", "chart_advisor", "insight_analyst"):
                continue
            options.append(IntentOption(
                label=str(opt.get("label", ""))[:60],
                description=str(opt.get("description", ""))[:200],
                tool=tool_name,
                params_preview=opt.get("params_preview", {}) if isinstance(opt.get("params_preview"), dict) else {},
            ))

        try:
            confidence = float(parsed.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        needs_options_group = bool(parsed.get("needs_options_group", False))
        reasoning = str(parsed.get("reasoning", ""))[:300]

    # Fallback if LLM failed
    if len(options) < 3:
        options = fallback_options[:max(3, len(fallback_options))]
        needs_options_group = True
        confidence = max(0.0, confidence) or 0.5
        reasoning = reasoning or "LLM 输出不完整,使用默认候选"

    payload = IntentCardPayload(
        title="我能这样帮你分析 — 选一个继续",
        options=options,
    )
    intent_card = ChatCard(
        type="intent_card",
        version=1,
        payload=payload.model_dump(),
    )

    return {
        "intent_card": intent_card.model_dump(),
        "intent_needs_options_group": needs_options_group,
        "intent_confidence": confidence,
        "intent_reasoning": reasoning,
        "execution_status": "INTENT_AWAIT",
    }


@traced_node("sql_plan")
def _plan(state: SQLAgentState) -> dict:
    schema = state.get("schema_context")
    schema_text = "无可用表结构" if not schema else "\n".join(
        f"- {t.name}: {t.description}" for t in schema.tables
    )

    chosen_tool = state.get("chosen_tool")
    tool_hint = ""
    if chosen_tool:
        tool_hint = (
            f"\n\n用户已选定分析工具: {chosen_tool}\n"
            f"请围绕这个工具方向写 SQL,例如 group_compare 优先 GROUP BY,trend_analysis 优先按时间排序。\n"
        )

    confirmed_requirement = state.get("confirmed_requirement")
    confirmed_block = ""
    if confirmed_requirement:
        # Authoritative — the user already filled and PATCHed these via
        # the requirement card. The free-form user_query is for flavor
        # only; the structured fields below are the source of truth.
        # Keep the wording mild so the LLM's JSON output isn't
        # disrupted by the auxiliary instruction.
        confirmed_block = (
            f"\n\nConfirmed requirement (use these as authoritative):\n"
            f"{confirmed_requirement}\n"
        )

    prompt = f"""你是一个SQL规划器。任务：根据用户问题、可用表结构，一次性产出查询计划与澄清决策。

用户问题: {state["user_query"]}
{tool_hint}
{confirmed_block}
可用表结构:
{schema_text}

{_PLAN_TABLE_HINTS}

决策策略(必须逐项执行，不要遗漏):
- 第一步:列出用户问题里关于 time / region / metric 三维度的明确程度
- 第二步:严格按下列规则判断 action:
  · 三维度全明确 → action="run_direct"，confidence ≥ 0.85，missing_dimensions: []
  · 1个维度缺但可推断(例:"今年"→当前年、"上月"→上月) → action="run_direct"，confidence ≈ 0.75，missing_dimensions 列出唯一不可推断的维度名
  · 2个及以上维度缺、无法安全推断 → action="clarify"，confidence ≤ 0.60，missing_dimensions 列出所有缺维度的名字(从 time/region/metric 中选)

必须字段:missing_dimensions 必须是 ["time"] / ["region"] / ["metric"] / 任意组合 / [] 之一,绝不能为空字符串或乱写。
当 action="clarify" 时,missing_dimensions 至少包含 1 个元素。

只输出JSON，禁止解释，禁止markdown，禁止思考过程。
输出格式:
{{
  "target_metric": "目标指标",
  "dimensions": ["维度1", "维度2"],
  "filters": [{{"field": "字段", "operator": "=", "value": "值"}}],
  "aggregation": "sum/count/avg",
  "time_range": "时间范围或null",
  "clarify_decision": {{
    "action": "clarify" | "run_direct",
    "missing_dimensions": ["time"|"region"|"metric"],
    "predicted_table": "fact_sales"|null,
    "confidence": 0.85,
    "reasoning": "简短理由"
  }}
}}

{_PLAN_FEWSHOT}"""

    plan_text = call_llm(prompt, max_tokens=600)

    plan_dict = safe_json_parse(plan_text)
    fallback_decision = ClarifyDecision(
        action="clarify",
        missing_dimensions=["time", "region", "metric"],
        confidence=0.3,
        reasoning="LLM 输出无法解析,默认走澄清路径",
    )
    if plan_dict and isinstance(plan_dict, dict):
        try:
            plan = QueryPlanWithClarify(**plan_dict)
            return {
                "query_plan": plan,
                "retry_counters": {"plan": 0, "sql_generation": 0},
            }
        except Exception as exc:
            logger.warning("plan node: Pydantic validation failed, falling back to clarify: %s", exc)
    else:
        logger.warning("plan node: LLM output not parseable as JSON")

    plan = QueryPlanWithClarify(
        target_metric=state["user_query"],
        clarify_decision=fallback_decision,
    )
    return {
        "query_plan": plan,
        "retry_counters": {"plan": 0, "sql_generation": 0},
    }


@traced_node("sql_generate")
def _generate_sql(state: SQLAgentState) -> dict:
    plan = state.get("query_plan")
    schema = state.get("schema_context")

    schema_text = "无可用表结构" if not schema else "\n".join(
        f"表 {t.name} ({t.description}):\n"
        + "\n".join(f"  {c.name} ({c.type})" for c in t.columns)
        for t in schema.tables
    )

    prompt = f"""你是一个SQL生成专家。根据查询计划生成SQL语句。

查询计划:
- 目标指标: {plan.target_metric if plan else ''}
- 维度: {plan.dimensions if plan else []}
- 过滤条件: {plan.filters if plan else []}
- 聚合方式: {plan.aggregation if plan else ''}
- 时间范围: {plan.time_range if plan else '未指定'}

可用表结构:
{schema_text}

规则:
- 数据库是 PostgreSQL，使用标准 PostgreSQL 兼容的 SQL 语法（不用 DuckDB 专属语法）
- 不要使用 EXTRACT() 类的 DuckDB 函数做日期处理
- 只生成 SELECT 语句，WHERE 条件必须完整
- 表名和列名必须严格使用上面列出的名称（注意 dim_date 没有 month 列，只有 year / quarter_num / quarter / week_of_year / day_name）
- JOIN 条件使用外键关联（如 fact_sales.region_id = dim_region.region_id）
- 使用中文别名（例如「销售额」「年份」）
- 只输出纯 SQL，禁止解释，禁止 markdown 代码块"""

    sql = call_llm([{"role": "user", "content": prompt}], max_tokens=800)

    sql = strip_markdown_fence(sql)
    sql = sql.strip()

    retry = state.get("retry_counters", {})
    retry["sql_generation"] = retry.get("sql_generation", 0) + 1
    return {"generated_sql": sql, "retry_counters": retry}


@traced_node("sql_validate")
def _validate(state: SQLAgentState) -> dict:
    sql = state.get("generated_sql", "")
    if not sql:
        return {"validation_result": {"valid": False, "error": "无SQL语句"},
                "execution_status": "SQL_SYNTAX_ERROR"}

    validation = json.loads(validate_sql(sql))
    logger.info("validate_sql result for sql[:60]=%s -> %s", (sql or "")[:60], validation)
    return {"validation_result": validation}


@traced_node("sql_execute")
def _execute(state: SQLAgentState) -> dict:
    sql = state.get("generated_sql", "")
    if not sql:
        return {"sql_result": json.dumps({"error": "无SQL语句"})}

    result = execute_sql(sql)
    return {"sql_result": result}


@traced_node("sql_evaluate")
def _evaluate(state: SQLAgentState) -> dict:
    raw = state.get("sql_result", "")
    if not raw:
        # SQL was never executed (validation failure or empty)
        sql_retries = state.get("retry_counters", {}).get("sql_generation", 0)
        if sql_retries < 3:
            return {"execution_status": "SQL_SYNTAX_ERROR"}
        else:
            return {"execution_status": "NEED_CLARIFICATION",
                    "error": "SQL生成失败: 验证不通过"}
    result = json.loads(raw)
    if "error" in result:
        retry = state.get("retry_counters", {})
        sql_retries = retry.get("sql_generation", 0)
        plan_retries = retry.get("plan", 0)

        if sql_retries < 3:
            return {"execution_status": "SQL_SYNTAX_ERROR"}
        elif plan_retries < 1:
            retry["plan"] = plan_retries + 1
            return {"execution_status": "SCHEMA_ERROR", "retry_counters": retry}
        else:
            return {"execution_status": "NEED_CLARIFICATION",
                    "error": f"SQL执行失败: {result['error']}"}

    return {"execution_status": "SUCCESS"}


@traced_node("sql_build_output")
def _build_output(state: SQLAgentState) -> dict:
    raw = state.get("sql_result", "")
    if not raw:
        return {"query_result": QueryResult(sql="", status="EMPTY")}
    try:
        result_data = json.loads(raw)
    except json.JSONDecodeError:
        return {"query_result": QueryResult(sql="", status="FAILED",
            error=ErrorDetail(code="INVALID_RESULT", message=f"Invalid JSON: {raw[:100]}"))}
    has_error = "error" in result_data and result_data["error"]
    columns_raw = result_data.get("columns", [])
    columns = [c if isinstance(c, dict) else {"name": c, "type": ""} for c in columns_raw]
    qr = QueryResult(
        sql=state.get("generated_sql", ""),
        columns=columns,
        rows=result_data.get("rows", []),
        row_count=len(result_data.get("rows", [])),
        status="FAILED" if has_error else "SUCCESS",
        error=ErrorDetail(code="EXECUTION_ERROR", message=str(result_data["error"])) if has_error else None,
    )
    return {"query_result": qr}


def _route_after_validate(state: SQLAgentState) -> Literal["execute", "evaluate"]:
    v = state.get("validation_result", {})
    if v.get("valid"):
        return "execute"
    return "evaluate"


def _route_after_evaluate(state: SQLAgentState) -> Literal["plan", "generate_sql", "build_output", "__end__"]:
    status = state.get("execution_status", "")
    if status == "SUCCESS":
        return "build_output"
    elif status == "SCHEMA_ERROR":
        return "plan"
    elif status == "SQL_SYNTAX_ERROR":
        return "generate_sql"
    return "__end__"


def build_sql_graph():
    workflow = StateGraph(SQLAgentState)

    workflow.add_node("plan", _plan)
    workflow.add_node("generate_sql", _generate_sql)
    workflow.add_node("validate", _validate)
    workflow.add_node("execute", _execute)
    workflow.add_node("evaluate", _evaluate)
    workflow.add_node("build_output", _build_output)

    workflow.set_entry_point("plan")

    workflow.add_edge("plan", "generate_sql")
    workflow.add_edge("generate_sql", "validate")
    workflow.add_conditional_edges("validate", _route_after_validate)
    workflow.add_edge("execute", "evaluate")
    workflow.add_conditional_edges("evaluate", _route_after_evaluate)
    workflow.add_edge("build_output", END)

    return workflow.compile()
