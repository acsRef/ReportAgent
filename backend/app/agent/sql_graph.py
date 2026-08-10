from __future__ import annotations

import json
import logging
from datetime import date
from typing import Literal, Optional, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from app.context import format_context_block
from app.llm import call_llm, _format_tools_for_prompt
from app.tools.faq_tools import search_faq
from app.models.contracts import ErrorDetail, QueryPlan, QueryResult, SchemaContext
from app.utils.text import extract_sql, safe_json_parse, strip_markdown_fence
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
    # C-6: 与所有父图（parent_graph / confirmed_execution_graph）的
    # Optional[ErrorDetail] 对齐。此前是 Optional[str]，边界处靠父图防御性
    # 转换屏蔽——一旦本图真写 str 进 error 就是无声的类型错位。
    error: Optional[ErrorDetail]
    retry_counters: dict
    query_result: Optional[QueryResult]
    chosen_tool: Optional[str]
    # Structured fields the user PATCHed via /requirement. When set,
    # _plan MUST treat these as authoritative and ignore inferences
    # from the free-form `user_query`. Populated by
    # confirmed_execution_graph._confirmed_sql_agent; ignored by the
    # legacy interrupt-based flow.
    confirmed_requirement: Optional[str]
    # 层7/B-3: 声明 trace_id（调用点早已传入），避免 LangGraph 静默丢弃
    # 导致子图 span 落进共享 _local[""] 桶。
    trace_id: str
    # 分层对话上下文（L1/L2/L2.5 渲染后的字符串），由调用方 build_session_context
    # 构建后传入；_plan/_generate_sql 会前置进 prompt，让多轮对话连贯。
    conversation_context: Optional[str]


# 事实表 → 维度表外键链路（单一来源）。v2 修订：抽成独立常量并同时拼进 _plan 与
# _generate_sql——真正写 JOIN 的是 _generate_sql，必须能看到具体外键对照，不能只靠列名猜。
_FK_CHAIN_HINTS = """事实表 → 维度表外键链路:
- fact_sales: date_id→dim_date, region_id→dim_region, product_id→dim_product, customer_id→dim_customer
- fact_returns: return_date_id→dim_date, product_id→dim_product, sale_id→fact_sales
- fact_inventory: date_id→dim_date, product_id→dim_product, warehouse_id→dim_warehouse
- fact_attendance: date_id→dim_date, employee_id→dim_employee"""

_PLAN_TABLE_HINTS = """常用表速查:
- fact_sales(销售事实), fact_returns(退货事实), fact_inventory(库存事实), fact_attendance(考勤事实)
- 维度表: dim_date, dim_region, dim_product, dim_customer, dim_warehouse, dim_employee

""" + _FK_CHAIN_HINTS


_PLAN_FEWSHOT = """[示例1]
用户: "今年华东销售趋势"
输出: {"target_metric":"销售趋势","dimensions":["时间","区域"],"filters":[{"field":"region","operator":"=","value":"华东"},{"field":"year","operator":"=","value":"今年"}],"aggregation":"sum","time_range":"今年","clarify_decision":{"action":"run_direct","missing_dimensions":[],"predicted_table":"fact_sales","confidence":0.9,"reasoning":"时间(今年)、区域(华东)、指标(销售)三维度均明确"}}

[示例2]
用户: "看一下销量"
输出: {"target_metric":"销量","dimensions":["时间"],"filters":[],"aggregation":"sum","time_range":null,"clarify_decision":{"action":"clarify","missing_dimensions":["时间","区域"],"predicted_table":"fact_sales","confidence":0.45,"reasoning":"时间与区域均缺失，无法定位数据范围"}}

[示例3]
用户: "上个月退货最多的是哪个商品"
输出: {"target_metric":"退货数","dimensions":["商品"],"filters":[{"field":"month","operator":"=","value":"上个月"}],"aggregation":"count","time_range":"上个月","clarify_decision":{"action":"run_direct","missing_dimensions":[],"predicted_table":"fact_returns","confidence":0.82,"reasoning":"时间(上个月)、商品、指标(退货)三维度均明确"}}"""


# SQL 生成专项规则。与 _generate_sql 的 prompt 内联在规则块里；独立成常量
# 便于测试断言 prompt 内容、也避免 f-string 把规则文本越嵌越乱。
_SQL_GENERATION_RULES = """多表 JOIN 规则（必须逐条遵守）:
- 多表关联优先使用 LEFT JOIN，禁止使用 RIGHT JOIN
- FROM 后面的第一张表就是主表，其余表都是通过 JOIN 挂上来的维度表/关联表
- JOIN 关联条件必须写在 ON 子句里，禁止把外键关联条件下沉到 WHERE
- 维度表的过滤条件写在 ON 里（如 LEFT JOIN dim_region ON fact_sales.region_id = dim_region.region_id AND dim_region.tier = '一线'）；主表（FROM 首表）自身的过滤条件写在 WHERE
- 有聚合函数（SUM/AVG/COUNT）时，GROUP BY 必须包含所有未聚合的查询列
- 关联超过 3 张表时，拆成两层子查询：先在各子查询内完成单表/少表聚合，再在外层 JOIN 子查询结果；禁止在同一个 SELECT 里平铺 4 张以上表
- 明细/非聚合查询（无 GROUP BY 且无聚合函数）默认追加 LIMIT 200，防止全表返回
- 所有表名、列名、别名必须严格来自「可用表结构」，禁止臆造列

时间维度规则:
- 时间过滤一律通过 date_id 外键关联 dim_date 表，再对 dim_date.full_date 做区间过滤（注意 dim_date 没有 month/timestamp 列，只有 year / quarter_num / quarter / week_of_year / day_name / full_date）
- 相对时间（今年/上月/近 7 天）与绝对时间（具体日期，如 2024-01-15）必须统一换算为左闭右开区间 [start, end)，例如整月用 full_date >= '2024-01-01' AND full_date < '2024-02-01'
- 一个问题里同时出现相对时间和绝对时间时（如「对比 2024-01 与上月」），分别用两个带别名的子查询各算各的区间，最后 JOIN 拼接结果；禁止在同一个 WHERE 里混写两种时间逻辑

数组类型规则:
- 若目标列是数组类型（ARRAY，如标签字段），必须用 @> ARRAY['标签'] 判断包含关系，禁止用 LIKE '%标签%'（LIKE 对数组列恒为空）
- 当前表结构中暂无数组列，遇到疑似数组字段先按上一条规则核实列类型再写

字面量与转义规则:
- 日期/字符串字面量使用标准 PostgreSQL 单引号：full_date >= '2024-01-01' AND full_date < '2024-02-01'
- 字面量内部若需单引号，用两个单引号转义：'O''Brien'——**禁止使用反斜杠转义**（不要写 'O\'Brien'、不要写 \n \t 等）
- 数字字面量直接写：WHERE id = 42，不要加引号
- 布尔字面量写 TRUE / FALSE，不要加引号
- 输出 SQL 时**纯文本**，不要附加 markdown 代码块、不要附加注释、不要附加解释"""


@traced_node("intent_analyze")
def _intent_analyze(state: SQLAgentState) -> dict:
    """Stage 1: pure LLM intent analysis.

    Reads the user's query, looks at the available tool list, and emits a
    `chat_card` of type `intent_card` with 3-4 candidate analyses. Does NOT
    touch SQL or any data tool — this is the cheap pre-flight step.
    """
    user_query = state.get("user_query", "")

    tools_block = _format_tools_for_prompt()
    prompt = f"""你是 ReportAgent 意图分析器。用户的问题要匹配下面的分析工具。
请选最合适的 3-4 个工具推荐给用户，输出 JSON。

用户问题: {user_query}

可用工具:
{tools_block}

── 工具选择指南 ──

【chart_advisor vs insight_analyst】
  chart_advisor → 数据已有，想要一个图来展示（自动判断用饼图还是柱状图）
  insight_analyst → 数据已有，想要数值摘要（合计、平均、最大、最小）
  区别：需要的是"图"还是"数"。两者不互斥，但一次只推荐一个。

【group_compare vs trend_analysis】
  group_compare → 对比不同组的数值高低（哪个区域最高、哪个产品最畅销）
  trend_analysis → 观察单一维度的变化方向（这个月比上个月涨了还是跌了）
  区别：横向对比 vs 纵向趋势。想比高低用 group_compare，想看走势用 trend_analysis。

【detect_anomaly】
  只想看"哪里不正常"（异常高或异常低）时用。数据量小于 3 行时不可用。

── 输出规则 ──

仅输出 JSON，禁止 markdown，禁止解释：
{{
  "options": [
    {{
      "label": "📊 各区域销售对比",
      "description": "按区域汇总销售额并排名",
      "tool": "group_compare",
      "params_preview": {{"group_col": "region_name", "value_col": "total_amount"}}
    }}
  ],
  "needs_options_group": true/false,
  "confidence": 0.85,
  "reasoning": "用户想看不同区域的销售横向对比"
}}

格式约束:
- options 数量: 3-4 个
- 每个 option.tool 必须是上面 5 个之一
- confidence: 0.7-0.95，越匹配越高
- needs_options_group: 用户没指定时间/区域范围等细节时 true，完全明确时 false"""

    raw = call_llm(prompt, max_tokens=1500)
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
        # 权威来源：用户已在需求卡上填好并 PATCH 确认。自由文本
        # user_query 仅作参考，以下结构化字段才是生成 SQL 的依据。
        # 措辞保持克制，避免辅助指令干扰 LLM 的 JSON 输出。
        confirmed_block = (
            f"\n\n已确认需求（以下字段为权威依据，优先于对自由文本的推断）：\n"
            f"{confirmed_requirement}\n"
        )

    # v2 修订：注入当前日期——_plan 判断「今年/上月」是否可推断需要知道今天几号。
    today = date.today().isoformat()

    prompt = f"""你是一个SQL规划器。任务：根据用户问题、可用表结构，一次性产出查询计划与澄清决策。

当前日期: {today}

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

    # 分层对话上下文前置（多轮连贯）：有才加，不打扰单轮场景。
    conversation_context = state.get("conversation_context")
    if conversation_context:
        prompt = f"{format_context_block(conversation_context)}\n\n{prompt}"

    plan_text = call_llm(prompt, max_tokens=1500)

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

    # v2 修订：注入当前日期（相对时间「今年/上月/近 N 天」换算成 [start,end) 的基准）
    # + 外键链路（写 JOIN 的节点必须看到具体外键对照，不能只靠列名猜）。
    today = date.today().isoformat()

    # Schema RAG Phase 1：把与当前问题最相关的历史案例（FAQ）注入 prompt，
    # 给 LLM 提供业务口径与 SQL 模板参考。走「注册工具」通道（同 search_interface_dictionary
    # 的字典检索先例）：调 search_faq 工具、解析 JSON、注入。任何失败降级为空块，不影响主流程。
    faq_block = ""
    try:
        raw = search_faq.invoke({"query": state.get("user_query", ""), "top_k": 3})
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        faq_rows = parsed.get("matches") or [] if isinstance(parsed, dict) else []
        if faq_rows:
            def _format_faq_row(r: dict) -> str:
                parts = [f"问题：{r.get('question', '')}"]
                sql = r.get("sql") or ""
                text = r.get("text") or ""
                note = r.get("note") or ""
                if sql:
                    parts.append(f"示例 SQL：\n{sql}")
                elif text:  # MCP chunk 形态：问题+SQL+要点都在 text 里
                    parts.append(f"示例内容：\n{text}")
                if note:
                    parts.append(f"要点：{note}")
                return "\n".join(parts)

            faq_lines = "\n\n".join(
                f"【参考案例 {i}】{_format_faq_row(r)}"
                for i, r in enumerate(faq_rows, 1)
            )
            faq_block = (
                "\n\n以下历史案例与示例 SQL 仅作参考——表名/字段名必须以上面"
                "「可用表结构」里的真实名称为准，若与案例冲突以可用表结构为准：\n"
                f"{faq_lines}\n"
            )
    except Exception as exc:
        logger.warning("search_faq failed, generating SQL without FAQ context: %s", exc)

    prompt = f"""你是一个SQL生成专家。根据查询计划生成SQL语句。

当前日期: {today}

查询计划:
- 目标指标: {plan.target_metric if plan else ''}
- 维度: {plan.dimensions if plan else []}
- 过滤条件: {plan.filters if plan else []}
- 聚合方式: {plan.aggregation if plan else ''}
- 时间范围: {plan.time_range if plan else '未指定'}

可用表结构:
{schema_text}

{_FK_CHAIN_HINTS}

{faq_block}
规则:
- 数据库是 PostgreSQL，使用标准 PostgreSQL 兼容的 SQL 语法（不用 DuckDB 专属语法）
- 不要使用 EXTRACT() 类的 DuckDB 函数做日期处理
- 只生成 SELECT 语句，WHERE 条件必须完整
- 表名和列名必须严格使用上面列出的名称（注意 dim_date 没有 month 列，只有 year / quarter_num / quarter / week_of_year / day_name / full_date）
- JOIN 条件使用外键关联（如 fact_sales.region_id = dim_region.region_id）
- 使用中文别名（例如「销售额」「年份」）
- 只输出纯 SQL，禁止解释，禁止 markdown 代码块，禁止反斜杠转义
- 字面量规则（重要）— 见 _SQL_GENERATION_RULES 末尾「字面量与转义规则」段

{_SQL_GENERATION_RULES}"""

    # Retry feedback loop: when regenerating after a failure, feed the failed
    # SQL and its error back to the LLM. Without this the retry loop is blind —
    # the same prompt reproduces the same invalid SQL (e.g. a hallucinated
    # column) until retries are exhausted.
    #
    # Two failure sources must BOTH be checked: a validation failure
    # (validation_result.valid is False) AND an execution failure (sql_result
    # carries an "error"). Bug: previously only validation was checked, so when
    # validate passed but execute failed (e.g. column does not exist at runtime)
    # the error was silently dropped and the retry regenerated identical SQL.
    prev_validation = state.get("validation_result") or {}
    prev_sql = (state.get("generated_sql") or "").strip()
    prev_sql_result = state.get("sql_result") or ""
    _exec_err = ""
    if prev_sql_result:
        try:
            _parsed_result = json.loads(prev_sql_result)
            if isinstance(_parsed_result, dict):
                _exec_err = _parsed_result.get("error") or ""
        except json.JSONDecodeError:
            _parsed_result = None
    if prev_sql and (prev_validation.get("valid") is False or _exec_err):
        error_to_show = prev_validation.get("error") or _exec_err
        prompt += f"""

【上一次生成失败，必须修正】
上一次的 SQL：
{prev_sql}
错误：{error_to_show}
请针对该错误修正 SQL：只使用上面「可用表结构」中真实存在的表名和列名，不要臆造列。"""

    # 分层对话上下文前置（与 _plan 一致）。
    conversation_context = state.get("conversation_context")
    if conversation_context:
        prompt = f"{format_context_block(conversation_context)}\n\n{prompt}"

    sql = call_llm([{"role": "user", "content": prompt}], max_tokens=1500)

    sql = extract_sql(sql)

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
                    "error": ErrorDetail(code="SQL_GENERATION_FAILED",
                                         message="SQL生成失败: 验证不通过", kind="other")}
    result = json.loads(raw)
    if "error" in result:
        retry = state.get("retry_counters", {})
        sql_retries = retry.get("sql_generation", 0)
        plan_retries = retry.get("plan", 0)
        kind = result.get("error_kind") or "other"

        # timeout / connection / permission：盲重试没意义，直接失败让用户调整。
        if kind in ("timeout", "connection", "permission"):
            message = {
                "timeout": "查询超时，请缩小时间范围或维度后重试",
                "connection": "数据库连接失败，请稍后重试",
                "permission": "权限不足，无法执行该查询",
            }[kind]
            return {
                "execution_status": "FAILED",
                "error": ErrorDetail(code="EXECUTION_ERROR",
                                     message=f"{message}：{result.get('error', '')}",
                                     kind=kind),
            }

        # syntax / object / other：维持原重试逻辑
        if sql_retries < 3:
            return {"execution_status": "SQL_SYNTAX_ERROR"}
        elif plan_retries < 1:
            retry["plan"] = plan_retries + 1
            return {"execution_status": "SCHEMA_ERROR", "retry_counters": retry}
        else:
            return {"execution_status": "NEED_CLARIFICATION",
                    "error": ErrorDetail(code="EXECUTION_ERROR",
                                         message=f"SQL执行失败: {result['error']}",
                                         kind=kind)}

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
    rows = result_data.get("rows", [])
    # 保留 execute_sql 用 CTE count(*) 算出的「真实总行数」。截断场景下
    # len(rows) 只是 MAX_RESULT_ROWS 上限，会误导 _plan_analysis 的规模估算，
    # 也让 query_snapshot.row_count 入库失真。
    total = result_data.get("row_count")
    if not isinstance(total, int):
        total = len(rows)
    # 三态：FAILED（有 error）/ EMPTY（无 error 但零行，合法结论）/ SUCCESS。
    # EMPTY 之前永远是死代码——父图被迫从 rows 反推 verdict 绕过去，下游任何
    # 依赖 query_result.status == 'EMPTY' 的判定都会无声失效。
    if has_error:
        status = "FAILED"
    elif not rows:
        status = "EMPTY"
    else:
        status = "SUCCESS"
    error_kind = result_data.get("error_kind") if has_error else None
    qr = QueryResult(
        sql=state.get("generated_sql", ""),
        columns=columns,
        rows=rows,
        row_count=total,
        status=status,
        truncated=bool(result_data.get("truncated", False)),
        error_kind=error_kind,
        error=ErrorDetail(
            code="EXECUTION_ERROR",
            message=str(result_data["error"]),
            kind=error_kind,
        ) if has_error else None,
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
