from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Optional, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from app.context import format_context_block
from app.llm import _format_tools_for_prompt, call_llm
from app.tools.registry import registry
from app.models.contracts import ErrorDetail, QueryPlan, QueryResult, SchemaContext
from app.utils.text import extract_sql, safe_json_parse
from app.infra.trace.sdk import current_tracer, traced_node
from app.reliability.errors import SQL_ERROR_KINDS, agent_recoverable
from app.tools.sql_tools import validate_sql, execute_sql
from app.state.checkpoint_adapter import migrate_checkpoint
from app.agent.prompts import (
    build_sql_intent_analyze_prompt,
    build_sql_plan_prompt,
    build_sql_generate_prompt,
)

logger = logging.getLogger(__name__)


def _get_max_sql_retries() -> int:
    try:
        return int(os.getenv("MAX_SQL_REPAIR_RETRIES", "2"))
    except (TypeError, ValueError):
        return 2


def _get_max_plan_retries() -> int:
    try:
        return int(os.getenv("MAX_PLAN_RETRIES", "1"))
    except (TypeError, ValueError):
        return 1


@dataclass
class RepairContext:
    """P8 D4: 7 要素上下文回灌。plan §D4 alignment：
    original_requirement / target_metric / prev_sql / error / error_kind /
    validation_result / retry_count / hint。
    schema 仅引用（不在 prompt 重复拼接；_format_repair_ctx 不渲染该字段，F7 拍板）。
    """

    original_requirement: str = ""
    plan: Optional[Any] = None
    target_metric: str = ""
    prev_sql: str = ""
    error: str = ""
    error_kind: str = ""
    validation_result: Optional[dict] = None
    retry_count: Optional[dict] = None
    hint: Optional[str] = None


class EvaluateResult(BaseModel):
    # F10: EvaluateResult.status 只描述"发生了什么"——纯状态枚举。
    # 路由语义（SQL_SYNTAX_ERROR / SCHEMA_ERROR / NEED_CLARIFICATION）由
    # DiagnoseDecision.action + execution_status 承担，不进此处。
    status: Literal["SUCCESS", "FAILED", "VALIDATION_FAILED"] = "SUCCESS"
    kind: Optional[str] = None
    error: Optional[ErrorDetail] = None
    validation_result: Optional[dict] = None


class DiagnoseDecision(BaseModel):
    # F3: 加 "end" 表示成功 / pass-through；"fail" 只表示真实失败决策。
    action: Literal["retry_sql", "replan", "clarify", "fail", "end"]
    reason: str
    error_kind: str
    recoverable: bool
    # F2: retry_target 是 plan 早期伪代码字段，与 action 重复；
    # 当前路由由 action 直接驱动，retry_target 保留仅供 trace 用。
    retry_target: Literal["generate_sql", "plan", "end"] = "end"
    hint: Optional[str] = None
    confidence: float = 0.5


class DiagnosePolicy:
    @staticmethod
    def decide(
        *,
        error_kind: str = "other",
        retry_counters: Optional[dict] = None,
        validation_failed: bool = False,
        raw_empty: bool = False,
    ) -> DiagnoseDecision:
        retry_counters = retry_counters or {}
        sql_retries = retry_counters.get("sql_generation", 0)
        plan_retries = retry_counters.get("plan", 0)
        max_sql = _get_max_sql_retries()
        max_plan = _get_max_plan_retries()
        kind = (error_kind or "other").lower()
        if kind not in SQL_ERROR_KINDS:
            kind = "other"
        # R2: validation_failed / raw_empty 路径直接按 retry budget 走，不再二次
        # normalize kind。修复前先把 kind 限制到 {"syntax","object","other"}，
        # 导致 timeout/connection/permission fail 分支永远进不去；叠加 R1
        # 修好的 _evaluate 优先 validation 路径后，validation_failed 时
        # evaluate_result.kind 已是 "syntax"，timeout 不再泄漏进来。
        # P9：kind 白名单与 fail 判定收编 reliability.errors 单一来源（表驱动，
        # 决策输出不变——test_diagnose_policy_sources.py 钉同源）。
        if raw_empty or validation_failed:
            if sql_retries < max_sql:
                return DiagnoseDecision(action="retry_sql", reason=f"{kind}: retry sql {sql_retries+1}/{max_sql} (validation)", error_kind=kind, recoverable=True, retry_target="generate_sql", confidence=0.7)
            if plan_retries < max_plan:
                return DiagnoseDecision(action="replan", reason=f"{kind}: replan {plan_retries+1}/{max_plan} (validation)", error_kind=kind, recoverable=True, retry_target="plan", confidence=0.6)
            return DiagnoseDecision(action="clarify", reason=f"{kind}: budget exhausted after validation", error_kind=kind, recoverable=False, retry_target="end", confidence=0.5)
        if not agent_recoverable(kind):
            return DiagnoseDecision(action="fail", reason=f"{kind}: non-recoverable", error_kind=kind, recoverable=False, retry_target="end", confidence=0.9)
        if sql_retries < max_sql:
            return DiagnoseDecision(action="retry_sql", reason=f"{kind}: retry sql {sql_retries+1}/{max_sql}", error_kind=kind, recoverable=True, retry_target="generate_sql", confidence=0.7)
        if plan_retries < max_plan:
            return DiagnoseDecision(action="replan", reason=f"{kind}: replan {plan_retries+1}/{max_plan}", error_kind=kind, recoverable=True, retry_target="plan", confidence=0.6)
        return DiagnoseDecision(action="clarify", reason=f"{kind}: budget exhausted", error_kind=kind, recoverable=False, retry_target="end", confidence=0.5)


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


class SQLAgentState(TypedDict, total=False):
    schema_context: Optional[SchemaContext]
    user_query: str
    query_plan: Optional[QueryPlanWithClarify]
    generated_sql: str
    validation_result: dict
    sql_result: str
    execution_status: str
    error: Optional[ErrorDetail]
    retry_counters: dict
    query_result: Optional[QueryResult]
    chosen_tool: Optional[str]
    confirmed_requirement: Optional[str]
    trace_id: str
    conversation_context: Optional[str]
    assembled_context: Optional[str]
    evaluate_result: Optional[dict]
    diagnose_decision: Optional[dict]


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
    user_query = state.get("user_query", "")
    tools_block = _format_tools_for_prompt()
    prompt = build_sql_intent_analyze_prompt(
        user_query=user_query,
        tools_block=tools_block,
    )
    raw = call_llm(prompt, max_tokens=1500)
    parsed = safe_json_parse(raw) if isinstance(raw, str) else raw
    if not isinstance(parsed, dict):
        parsed = {}
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
    state = migrate_checkpoint(dict(state))
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
        confirmed_block = (
            f"\n\n已确认需求（以下字段为权威依据，优先于对自由文本的推断）：\n"
            f"{confirmed_requirement}\n"
        )
    today = date.today().isoformat()
    # F1: _plan 入口保留 retry_counters（不重置）。_diagnose 在 replan 时已
    # 把 plan +=1、清零会让 sql_generation 计数被无端覆盖，单次分析最坏 4 次
    # SQL retry（plan §D3 契约 max_sql=2 形同虚设）。只在 counters 不存在时初始化。
    counters = dict(state.get("retry_counters") or {})
    counters.setdefault("plan", 0)
    counters.setdefault("sql_generation", 0)
    diagnose = state.get("diagnose_decision")
    repair_ctx = None
    if diagnose is not None and isinstance(diagnose, dict) and diagnose.get("action") == "replan":
        raw_dec = diagnose
        repair_ctx = RepairContext(
            original_requirement=state.get("user_query", ""),
            plan=state.get("query_plan"),
            target_metric=getattr(state.get("query_plan"), "target_metric", "") if state.get("query_plan") else "",
            prev_sql=state.get("generated_sql", ""),
            error=state.get("sql_result", "")[:500] if isinstance(state.get("sql_result"), str) else str(state.get("sql_result", ""))[:500],
            error_kind=raw_dec.get("error_kind", "other"),
            validation_result=state.get("validation_result"),
            retry_count=counters,
            hint=raw_dec.get("reason", ""),
        )
    prompt = build_sql_plan_prompt(
        today=today,
        # F9: 一致用 .get()，与 SQLAgentState total=False 契约对齐；空 query
        # 让 LLM 自己提示缺字段，build 端不抛 KeyError。
        user_query=state.get("user_query", ""),
        schema_text=schema_text,
        tool_hint=tool_hint,
        confirmed_block=confirmed_block,
        plan_table_hints=_PLAN_TABLE_HINTS,
        plan_fewshot=_PLAN_FEWSHOT,
        repair_ctx=repair_ctx,
    )
    _ctx_injected = state.get("assembled_context") or state.get("conversation_context")
    if _ctx_injected:
        prompt = f"{format_context_block(_ctx_injected)}\n\n{prompt}"
    raw = call_llm(prompt, max_tokens=1500)
    plan_dict = safe_json_parse(raw) if isinstance(raw, str) else raw
    if not isinstance(plan_dict, dict):
        plan_dict = {}
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
                "retry_counters": counters,
            }
        except Exception as exc:
            logger.warning("plan node: Pydantic validation failed, falling back to clarify: %s", exc)
    else:
        logger.warning("plan node: LLM output not parseable as JSON")
    plan = QueryPlanWithClarify(
        target_metric=state.get("user_query", ""),
        clarify_decision=fallback_decision,
    )
    return {
        "query_plan": plan,
        "retry_counters": counters,
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
    today = date.today().isoformat()
    faq_block = ""
    try:
        faq_tools = registry.get(["search_faq"])
        if faq_tools:
            raw = faq_tools[0].invoke({"query": state.get("user_query", ""), "top_k": 3})
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            faq_rows = parsed.get("matches") or [] if isinstance(parsed, dict) else []
        else:
            faq_rows = []
        if faq_rows:
            def _format_faq_row(r: dict) -> str:
                parts = [f"问题：{r.get('question', '')}"]
                text = r.get("text") or ""
                if text:
                    parts.append(text)
                return "\n\n".join(parts)
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
    repair_ctx: Optional[RepairContext] = None
    prev_validation = state.get("validation_result") or {}
    prev_sql = (state.get("generated_sql") or "").strip()
    prev_sql_result = state.get("sql_result") or ""
    _exec_err = ""
    _error_kind = "other"
    # Review-3: validation failure → syntax，且优先于 sql_result（与
    # _evaluate VALIDATION_FAILED 分类及 R1「validation 优先」一致）。
    # 修复前 _error_kind 只从 sql_result 推导，validation failure 路径
    # （sql_result 空）恒落 "other"，repair prompt「错误分类」与
    # Evaluate/Diagnose 链漂移。
    if prev_validation.get("valid") is False:
        _error_kind = "syntax"
    elif prev_sql_result:
        try:
            _parsed_result = json.loads(prev_sql_result)
            if isinstance(_parsed_result, dict):
                _exec_err = _parsed_result.get("error") or ""
                _error_kind = _parsed_result.get("error_kind") or "other"
        except json.JSONDecodeError:
            _parsed_result = None
    if prev_sql and (prev_validation.get("valid") is False or _exec_err):
        error_to_show = prev_validation.get("error") or _exec_err
        diagnose = state.get("diagnose_decision")
        hint = ""
        if isinstance(diagnose, dict):
            hint = diagnose.get("reason", "") or diagnose.get("hint", "")
        # F7: 删 schema_context_ref（set-but-never-rendered，F7 拍板）+
        # fewshot（faq_block 已由 6 段 task_contract 注入完整版，repair 段不再重复，
        # F8 拍板）。其余 7 要素保留。
        repair_ctx = RepairContext(
            original_requirement=state.get("user_query", ""),
            plan=plan,
            target_metric=plan.target_metric if plan else "",
            prev_sql=prev_sql,
            error=str(error_to_show)[:800],
            error_kind=_error_kind,
            validation_result=prev_validation if prev_validation.get("valid") is False else None,
            retry_count=state.get("retry_counters"),
            hint=hint,
        )
    prompt = build_sql_generate_prompt(
        today=today,
        target_metric=plan.target_metric if plan else "",
        dimensions=list(plan.dimensions) if plan and plan.dimensions else [],
        filters=list(plan.filters) if plan and plan.filters else [],
        aggregation=plan.aggregation if plan and plan.aggregation else "",
        time_range=plan.time_range if plan and plan.time_range else None,
        schema_text=schema_text,
        fk_chain_hints=_FK_CHAIN_HINTS,
        faq_block=faq_block,
        sql_generation_rules=_SQL_GENERATION_RULES,
        repair_ctx=repair_ctx,
    )
    _ctx_injected = state.get("assembled_context") or state.get("conversation_context")
    if _ctx_injected:
        prompt = f"{format_context_block(_ctx_injected)}\n\n{prompt}"
    sql = call_llm([{"role": "user", "content": prompt}], max_tokens=1500)
    sql = extract_sql(sql)
    retry = state.get("retry_counters", {}) or {}
    retry = dict(retry)
    retry["sql_generation"] = retry.get("sql_generation", 0) + 1
    # R3: 清掉上一轮 execution-derived state，避免下一轮 _evaluate 读 stale
    # sql_result / evaluate_result / error 跨 attempt 污染。validation_result
    # 不清——caller 已用它构造 repair_ctx，validate 节点会在下一轮重写。
    return {
        "generated_sql": sql,
        "retry_counters": retry,
        "sql_result": "",
        "evaluate_result": None,
        "error": None,
    }


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
    # R1: validation_failed 必须优先于 sql_result 检查——上一轮 execution
    # 留下的 stale sql_result 不能污染本轮 evaluate 判断。修复前 _evaluate
    # 用 `if not raw:` 间接推断 validation failure，结果上一轮 timeout/
    # connection kind 会被错误消费；现显式先看 validation_failed 直接走
    # VALIDATION_FAILED 路径，不读 sql_result。
    validation = state.get("validation_result") or {}
    if validation.get("valid") is False:
        return {
            "evaluate_result": EvaluateResult(status="VALIDATION_FAILED", kind="syntax", validation_result=validation).model_dump(),
            "execution_status": "SQL_SYNTAX_ERROR",
        }
    raw = state.get("sql_result", "")
    if not raw:
        return {
            "evaluate_result": EvaluateResult(status="VALIDATION_FAILED", kind="other", validation_result=validation).model_dump(),
            "execution_status": "SQL_SYNTAX_ERROR",
        }
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "evaluate_result": EvaluateResult(status="FAILED", kind="other", error=ErrorDetail(code="INVALID_RESULT", message=f"Invalid JSON: {raw[:100]}", kind="other")).model_dump(),
            "execution_status": "FAILED",
            "error": ErrorDetail(code="INVALID_RESULT", message=f"Invalid JSON: {raw[:100]}", kind="other"),
        }
    if "error" in result and result.get("error"):
        kind = result.get("error_kind") or "other"
        error_detail = ErrorDetail(code="EXECUTION_ERROR", message=str(result.get("error", "")), kind=kind)
        if kind in ("timeout", "connection", "permission"):
            message = {
                "timeout": "查询超时，请缩小时间范围或维度后重试",
                "connection": "数据库连接失败，请稍后重试",
                "permission": "权限不足，无法执行该查询",
            }[kind]
            wrapped = ErrorDetail(code="EXECUTION_ERROR", message=f"{message}：{result.get('error', '')}", kind=kind)
            return {
                "evaluate_result": EvaluateResult(status="FAILED", kind=kind, error=wrapped, validation_result=state.get("validation_result")).model_dump(),
                "execution_status": "FAILED",
                "error": wrapped,
            }
        return {
            "evaluate_result": EvaluateResult(status="FAILED", kind=kind, error=error_detail, validation_result=state.get("validation_result")).model_dump(),
            "execution_status": "FAILED",
            "error": error_detail,
        }
    return {
        "evaluate_result": EvaluateResult(status="SUCCESS", kind=None).model_dump(),
        "execution_status": "SUCCESS",
    }


@traced_node("sql_diagnose")
def _diagnose(state: SQLAgentState) -> dict:
    execution_status = state.get("execution_status", "")
    if execution_status == "SUCCESS":
        # F3: SUCCESS 是 pass-through，不属于 "fail" 决策；用 action="end" 与
        # "fail" 区分，P14 Evaluation 按 action 切片不会被污染。
        decision = DiagnoseDecision(
            action="end",
            reason="success: no diagnose needed",
            error_kind="",
            recoverable=False,
            retry_target="end",
            confidence=1.0,
        )
        tracer = current_tracer()
        if tracer is not None:
            tracer.add_decision(
                name="sql_diagnose",
                action=decision.action,
                reason=decision.reason,
                error_kind=decision.error_kind,
                retry_counters=state.get("retry_counters", {}),
                execution_status=execution_status,
            )
        return {"diagnose_decision": decision.model_dump(), "execution_status": "SUCCESS"}
    evaluate_result = state.get("evaluate_result") or {}
    kind = evaluate_result.get("kind") or "other"
    raw = state.get("sql_result", "")
    raw_empty = not raw
    validation = state.get("validation_result") or {}
    validation_failed = validation.get("valid") is False
    retry_counters = state.get("retry_counters", {}) or {}
    # F6: 入口一次性解析 raw，下方 clarify / fail 分支共用，不再重复 json.loads。
    parsed_raw: Optional[dict] = None
    if raw:
        try:
            _loaded = json.loads(raw)
            if isinstance(_loaded, dict):
                parsed_raw = _loaded
        except json.JSONDecodeError:
            parsed_raw = None
    # F12: kind 已被 evaluate_result.get("kind") or "other" 兜底，not kind
    # 永远 False；保留 kind == "other" 单分支即可。
    if kind == "other":
        if validation_failed:
            kind = "syntax"
        elif parsed_raw and parsed_raw.get("error_kind"):
            kind = parsed_raw.get("error_kind") or kind
    decision = DiagnosePolicy.decide(
        error_kind=kind,
        retry_counters=retry_counters,
        validation_failed=validation_failed,
        raw_empty=raw_empty,
    )
    tracer = current_tracer()
    if tracer is not None:
        tracer.add_decision(
            name="sql_diagnose",
            action=decision.action,
            reason=decision.reason,
            error_kind=decision.error_kind,
            retry_counters=dict(retry_counters),
            execution_status=execution_status,
        )
    if decision.action == "retry_sql":
        return {"diagnose_decision": decision.model_dump(), "execution_status": "SQL_SYNTAX_ERROR"}
    if decision.action == "replan":
        new_counters = dict(retry_counters)
        new_counters["plan"] = new_counters.get("plan", 0) + 1
        return {
            "diagnose_decision": decision.model_dump(),
            "execution_status": "SCHEMA_ERROR",
            "retry_counters": new_counters,
        }
    if decision.action == "clarify":
        err = state.get("error")
        if not err:
            raw_err = (parsed_raw or {}).get("error", "") if parsed_raw else raw[:200]
            err = ErrorDetail(
                code="EXECUTION_ERROR",
                message=f"SQL执行失败: {raw_err}",
                kind=kind,
            )
        return {
            "diagnose_decision": decision.model_dump(),
            "execution_status": "NEED_CLARIFICATION",
            "error": err,
        }
    # fail
    err = state.get("error")
    if not err:
        if parsed_raw and parsed_raw.get("error"):
            err = ErrorDetail(
                code="EXECUTION_ERROR",
                message=str(parsed_raw.get("error")),
                kind=kind,
            )
        if err is None and validation_failed:
            err = ErrorDetail(
                code="SQL_GENERATION_FAILED",
                message=str(validation.get("error", "验证不通过")),
                kind=kind,
            )
    result: dict[str, Any] = {
        "diagnose_decision": decision.model_dump(),
        "execution_status": "FAILED",
    }
    if err is not None:
        result["error"] = err
    return result


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
    total = result_data.get("row_count")
    if not isinstance(total, int):
        total = len(rows)
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


def _route_after_evaluate(state: SQLAgentState) -> Literal["diagnose"]:
    return "diagnose"


def _route_after_diagnose(state: SQLAgentState) -> Literal["plan", "generate_sql", "build_output", "__end__"]:
    # F2: 按 DiagnoseDecision.action 路由（plan §D1 字面）。execution_status 由
    # _diagnose 同步写入父图契约，路由键以 action 为准——单一事实源。
    decision = state.get("diagnose_decision") or {}
    action = decision.get("action") if isinstance(decision, dict) else None
    if action == "retry_sql":
        return "generate_sql"
    if action == "replan":
        return "plan"
    if action == "end":
        return "build_output"
    if action == "fail":
        return "__end__"
    if action == "clarify":
        return "__end__"
    # 兜底：diagnose_decision 缺失时退化到 execution_status（与 P7 兼容）。
    status = state.get("execution_status", "")
    if status == "SUCCESS":
        return "build_output"
    if status == "SCHEMA_ERROR":
        return "plan"
    if status == "SQL_SYNTAX_ERROR":
        return "generate_sql"
    return "__end__"


def build_sql_graph():
    workflow = StateGraph(SQLAgentState)

    workflow.add_node("plan", _plan)
    workflow.add_node("generate_sql", _generate_sql)
    workflow.add_node("validate", _validate)
    workflow.add_node("execute", _execute)
    workflow.add_node("evaluate", _evaluate)
    workflow.add_node("diagnose", _diagnose)
    workflow.add_node("build_output", _build_output)

    workflow.set_entry_point("plan")

    workflow.add_edge("plan", "generate_sql")
    workflow.add_edge("generate_sql", "validate")
    workflow.add_conditional_edges("validate", _route_after_validate)
    workflow.add_edge("execute", "evaluate")
    workflow.add_conditional_edges("evaluate", _route_after_evaluate)
    workflow.add_conditional_edges("diagnose", _route_after_diagnose)
    workflow.add_edge("build_output", END)

    return workflow.compile()
