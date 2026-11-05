from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Optional, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from app.context import format_context_block
from app.llm import _format_tools_for_prompt, call_llm
from app.tools.registry import registry
from app.models.contracts import ErrorDetail, QueryPlan, QueryResult, SchemaContext, TableSchema, ColumnSchema
from app.utils.text import extract_sql, safe_json_parse
from app.infra.trace.sdk import current_tracer, traced_node
from app.reliability.errors import SQL_ERROR_KINDS, agent_recoverable
from app.reliability import fault_inject
from app.tools.sql_tools import validate_sql, _execute_validated as execute_sql
from app.tools.data_tools import search_tables, get_table_ddl
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


def _get_max_mcp_retries() -> int:
    """MCP retry 预算（P15 prelude）。与 SQL/plan retry 正交独立计数。

    上限 = settings.MAX_MCP_REPAIR_RETRIES（默认 1；CLAUDE.md §11「Retry 固定预算
    MCP 2」略缩——object_not_found 一次 schema retrieval 通常足够，无需 2 次）。

    counter key "mcp_schema" 与 "sql_generation" / "plan" 三 key 正交。
    """
    try:
        return int(os.getenv("MAX_MCP_REPAIR_RETRIES", "1"))
    except (TypeError, ValueError):
        return 1


def _parse_ddl_columns(ddl: str) -> list[dict]:
    """解析 CREATE TABLE DDL 文本 → [{name, type}]。

    P15 prelude retry_mcp_schema_retrieval fallback 用：get_table_ddl 返回
    "CREATE TABLE fact_orders (...)" 文本，从中提取列名/类型（不为做 AST，
    够 generate_sql 用即可）。
    """
    columns: list[dict] = []
    for line in ddl.splitlines():
        line = line.strip().rstrip(",")
        if not line or line.startswith("CREATE") or line.startswith("(") or line.startswith(")"):
            continue
        if line.startswith(("PRIMARY", "FOREIGN", "UNIQUE", "CONSTRAINT", "INDEX", "CHECK")):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0].islower() and not parts[0].startswith(('"', "'", "--")):
            columns.append({"name": parts[0].strip('"'), "type": " ".join(parts[1:]).strip('"')})
    return columns


def _extract_table_names(sql_text: str) -> list[str]:
    """从 SQL 文本提取涉及的 dim_*/fact_* 表名（FROM/JOIN 后跟的标识符）。

    P15 prelude retry_mcp_schema_retrieval 用：给 LLM 的 schema 用 SQL 里
    真实涉及的表（而不是语义检索可能漏的表）。
    """
    names: list[str] = []
    for keyword in ("FROM ", "JOIN "):
        idx = 0
        while True:
            idx = sql_text.upper().find(keyword, idx)
            if idx < 0:
                break
            rest = sql_text[idx + len(keyword):].lstrip()
            token = ""
            for ch in rest:
                if ch.isalnum() or ch == "_":
                    token += ch
                else:
                    break
            if token.startswith(("dim_", "fact_")) and token not in names:
                names.append(token)
            idx += len(keyword)
    return names


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
    # P15 prelude: 加 "retry_mcp_schema_retrieval" 给 object_not_found 路径走 MCP schema。
    action: Literal["retry_sql", "replan", "clarify", "fail", "end",
                    "retry_mcp_schema_retrieval"]
    reason: str
    error_kind: str
    recoverable: bool
    # F2: retry_target 是 plan 早期伪代码字段，与 action 重复；
    # 当前路由由 action 直接驱动，retry_target 保留仅供 trace 用。
    # P15 prelude: 加 "mcp_schema" 标识触发 MCP schema retrieval。
    retry_target: Literal["generate_sql", "plan", "end", "mcp_schema"] = "end"
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
        # P15 prelude: mcp_schema counter 与 sql_generation / plan 三 key 正交。
        mcp_schema_retrievals = retry_counters.get("mcp_schema", 0)
        max_sql = _get_max_sql_retries()
        max_plan = _get_max_plan_retries()
        max_mcp = _get_max_mcp_retries()
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
        #
        # P15 prelude fix（方案 A）：object_not_found / object_ambiguous 必须
        # 优先于 validation_failed 分支——列/表名错误的修复路径是 MCP schema
        # retrieval 或用户消歧，不是盲 retry_sql。此前 validation_failed 分支
        # 在前，object_not_found 永远落 retry_sql（同 SQL 重跑无信息增益）。
        if kind == "object_not_found":
            if mcp_schema_retrievals < max_mcp:
                return DiagnoseDecision(
                    action="retry_mcp_schema_retrieval",
                    reason=f"{kind}: retry MCP schema retrieval {mcp_schema_retrievals+1}/{max_mcp}",
                    error_kind=kind, recoverable=True, retry_target="mcp_schema", confidence=0.8,
                )
            return DiagnoseDecision(
                action="clarify",
                reason=f"{kind}: schema retrieval budget exhausted",
                error_kind=kind, recoverable=False, retry_target="end", confidence=0.7,
            )
        # P15 prelude fix: object_ambiguous（AmbiguousColumn 列名歧义）→ 必须用户消歧。
        if kind == "object_ambiguous":
            return DiagnoseDecision(
                action="clarify",
                reason=f"{kind}: column ambiguous, user disambiguation required",
                error_kind=kind, recoverable=False, retry_target="end", confidence=0.9,
            )
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
    # P15 e2e T4 fault seam：confirmed 图 _confirmed_sql_agent 透传的 e2e 故障覆盖。
    # 必须在此声明为 channel——否则 langgraph 把未声明 key 从输入丢弃，_evaluate 收不到。
    fault_override: Optional[dict]
    # 已确认的时间粒度（day/week/month/quarter/year）——由父图从需求卡透传，
    # _plan 用它确定性改写 dimensions（见 _apply_confirmed_granularity）。
    granularity: Optional[str]
    # 展示粒度：detail = 逐条明细（_plan 清空 dimensions/聚合）。
    detail_level: Optional[str]
    # 最高/最低的那一期（2026-11-04）：{direction: max|min, unit: day|week|month|...,
    # metric: 口径名}。为空 = 普通全区间查询。由父图从需求卡透传。
    top_period: Optional[dict]
    # 指标白名单渲染结果（2026-11-03）：需求卡解析出的指标表达式，只渲染这些。
    # 为空时退回全量注册表（无卡片场景）。
    metric_hints: Optional[str]


_FK_CHAIN_HINTS = """事实表 → 维度表外键链路:
- fact_orders: order_date→dim_date, store_id→dim_store, customer_id→dim_customer, product_id→dim_product, promotion_id→dim_promotion
- fact_payments: payment_date→dim_date, order_id→fact_orders"""

# 业务口径与结构提示（2026-11-02 迁到 app/knowledge/*.yaml）
#
# 历史：2026-10-28 实测「2024年各产品类别的销售额和退货金额对比」时模型凭空写了
# `fact_orders.sales_amount`（该列不存在）→ EXPLAIN 报 object_not_found → 烧掉 MCP
# schema 重检索预算 → FAILED；换一次采样又蒙对了。当时的修法是把固定口径写进
# Python 字符串——换一套 schema 就得改代码，加一个指标也得改代码。
#
# 现在口径/结构都是**数据文件**（`app/knowledge/metrics.yaml` + `schema_hints.yaml`），
# 这三个常量只是它们在 import 时的渲染结果，保持既有调用点与测试不变。
from app.knowledge import registry as knowledge  # noqa: E402  (紧邻使用处，便于阅读)

_FK_CHAIN_HINTS = knowledge.render_fk_chain_hints()
_SCHEMA_TERM_HINTS = knowledge.render_metric_hints()

_GRANULARITY_DIM_LABELS: dict[str, str] = {
    "day": "日期", "week": "周", "month": "月份", "quarter": "季度", "year": "年份",
}
# 计划里可能出现的「宽时间维度」写法——改写时统一替换成具体粒度
_BROAD_TIME_DIMS = frozenset({"时间", "日期", "月份", "周", "季度", "年份", "年", "月", "日"})


def _apply_confirmed_granularity(plan: "QueryPlanWithClarify", granularity: Optional[str]) -> "QueryPlanWithClarify":
    """把已确认的时间粒度写进 plan.dimensions（替换宽时间维度）。

    为什么不用 prompt 让 LLM 自己理解：实测「时间粒度 = 月（必须按 dim_date.month 分组）」
    已经写进 plan prompt，模型依旧输出 dimensions=["时间","区域"] 并最终 GROUP BY year。
    粒度是**确定性事实**（用户在需求卡里选过），不该再赌一次采样。
    """
    label = _GRANULARITY_DIM_LABELS.get(granularity or "")
    if not label:
        return plan
    dims = [d for d in (plan.dimensions or []) if str(d) not in _BROAD_TIME_DIMS]
    plan.dimensions = [label, *dims]
    return plan


_PLAN_TABLE_HINTS = knowledge.render_table_hints() + "\n\n" + _FK_CHAIN_HINTS + "\n\n" + _SCHEMA_TERM_HINTS


_PLAN_FEWSHOT = knowledge.render_plan_few_shot()

_SQL_GENERATION_RULES = knowledge.render_sql_generation_rules()


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
        # 指标白名单（2026-11-03）：卡片已解析出指标时只给这些，避免模型把整张
        # 注册表一起 SELECT 出来。
        plan_table_hints=(
            knowledge.render_table_hints() + "\n\n" + _FK_CHAIN_HINTS + "\n\n" + state["metric_hints"]
            if state.get("metric_hints")
            else _PLAN_TABLE_HINTS
        ),
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
            # 粒度确定性改写（2026-10-28）：plan 只输出「时间」这种宽维度，
            # generate 又严格照 plan 分组 → 用户要「每个月」时 SQL 仍是 GROUP BY year。
            # 已经确认的粒度不该靠 LLM 二次理解，这里直接把它落到 dimensions 上。
            plan = _apply_confirmed_granularity(plan, state.get("granularity"))
            # 明细模式（2026-11-03）：用户要逐条数据时，plan 不能带维度/聚合。
            # 实测 prompt 里写了「禁止聚合」，模型仍按趋势规则 GROUP BY month 出 2 行。
            # 已确认的展示粒度是确定性事实，这里直接改写 plan。
            if state.get("detail_level") == "detail":
                plan.dimensions = []
                plan.aggregation = "none"
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
        fk_chain_hints=_FK_CHAIN_HINTS + "\n\n" + (state.get("metric_hints") or _SCHEMA_TERM_HINTS),
        faq_block=faq_block,
        sql_generation_rules=_SQL_GENERATION_RULES,
        repair_ctx=repair_ctx,
    )
    _ctx_injected = state.get("assembled_context") or state.get("conversation_context")
    if _ctx_injected:
        prompt = f"{format_context_block(_ctx_injected)}\n\n{prompt}"
    # 2026-11-03：明细模式必须在**生成阶段**再说一遍。plan 阶段清空 dimensions/聚合
    # 之后，generate 只看得到 target_metric，模型会把它重新 SUM 起来（实测「给我今年
    # 华东的明细」返回 1 行 销售金额）。
    if state.get("detail_level") == "detail":
        prompt += (
            "\n\n【明细模式（优先级最高）】用户要逐条数据："
            "禁止 SUM/COUNT/AVG 等聚合与 GROUP BY；"
            "直接 SELECT 原始列（日期、门店、品类、金额等）；"
            "按时间倒序；末尾加 LIMIT 200。"
        )
    # 2026-11-04（top-period-detail-intent）：最高/最低的那一期必须在生成阶段就拆两步，
    # 否则模型会把「最高的那一周」当成「整段区间的汇总」（实测 v2 = GROUP BY year,region 一行）。
    top_period = state.get("top_period") or None
    if top_period:
        unit = str(top_period.get("unit") or "month")
        unit_label = _GRANULARITY_DIM_LABELS.get(unit, unit)
        word = "最高" if str(top_period.get("direction") or "max") == "max" else "最低"
        if top_period.get("resolved"):
            filters = " AND ".join(top_period.get("filters") or []) or (
                f"{top_period.get('period_column') or unit} = {top_period.get('period_value')}"
            )
            prompt += (
                f"\n\n【选期已算好（确定性）】{top_period.get('metric') or '指标'}{word}的"
                f"{unit_label} = {top_period.get('label')}：把 `WHERE {filters}` 写进查询，"
                f"只取这一期的数据；**禁止**再把全区间汇总一遍，也禁止按期 GROUP BY。"
            )
        else:
            prompt += (
                f"\n\n【必需结构：先选期、再取数】用户要的是{word}的那一{unit_label}："
                f"必须先用 WITH 子句聚合选出那一期（GROUP BY 期 + ORDER BY <指标> DESC LIMIT 1），"
                f"再只取该期的数据；明细模式下外层不许聚合。不许把全区间汇总一遍。"
            )
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


_DETAIL_AGGREGATE_RE = re.compile(r"\b(SUM|AVG|COUNT|MIN|MAX)\s*\(", re.IGNORECASE)
_DETAIL_GROUP_RE = re.compile(r"\bGROUP\s+BY\b", re.IGNORECASE)
_TOP_PERIOD_LIMIT_RE = re.compile(r"\bLIMIT\s+1\b", re.IGNORECASE)
_CTE_START_RE = re.compile(r"^\s*WITH\b", re.IGNORECASE)


def _strip_cte_blocks(sql: str) -> str:
    """去掉 `WITH x AS (...)` 这些 CTE 块，只留**最终查询**。

    为什么需要：最高期的查询必须先在 CTE 里聚合选期（`ORDER BY SUM(...) DESC LIMIT 1`），
    而明细模式又规定「禁止聚合」。两者不冲突——限制的是**最终查询**的形状，
    不是选期用的中间结果。这里用括号配平切掉 CTE，剩下最终 SELECT 供契约检查。
    """
    text = sql or ""
    match = re.match(r"\s*WITH\s+(?:RECURSIVE\s+)?", text, re.IGNORECASE)
    if not match:
        return text
    index = match.end()
    while index < len(text):
        open_index = text.find("(", index)
        if open_index == -1:
            return text
        if not re.search(r"\bAS\b", text[index:open_index], re.IGNORECASE):
            return text
        depth = 0
        cursor = open_index
        while cursor < len(text):
            if text[cursor] == "(":
                depth += 1
            elif text[cursor] == ")":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        if depth != 0:
            return text
        index = cursor + 1
        while index < len(text) and text[index] in " \t\r\n":
            index += 1
        if index < len(text) and text[index] == ",":
            index += 1
            while index < len(text) and text[index] in " \t\r\n":
                index += 1
            continue
        return text[index:]
    return text


def _top_period_contract_violation(sql: str, top_period: dict | None = None) -> str | None:
    """最高/最低期的确定性契约（2026-11-04，plan top-period-detail-intent）。

    实测：需求卡写着「销售额最高的那一周」，SQL 却是 `GROUP BY year, region` 的一行
    汇总——全区间又被汇总了一遍。两种形态分开校验：

    - **系统已算出具体期**（`resolved=True`）：SQL 必须真的带那一期的过滤条件
      （例如 `dim_date.month = 11`），否则就是把全区间算了一遍；
    - 算不出来（退回两段式）：必须有 `LIMIT 1` 的选期结构。

    违规都按 validation 失败交给既有 repair 环，而不是把错的结果当成功返回。
    """
    text = sql or ""
    spec = top_period or {}
    if spec.get("resolved"):
        column = str(spec.get("period_column") or "")
        label = str(spec.get("label") or "")
        # 两个方向一起要（「最多和最小」）：两期的过滤值都必须出现
        values = [str(v) for v in (spec.get("period_values") or []) if str(v)]
        if not values:
            raw = str(spec.get("period_value") or "")
            values = [part.strip() for part in raw.split(",") if part.strip()]
        bare = column.split(".")[-1].lower()
        if not bare or bare not in text.lower():
            return (
                f"缺少「只取 {label or '选定那一期'}」的过滤条件："
                f"必须加上 {column} 的过滤，不许把全区间再汇总一遍"
            )
        missing = [
            value for value in values
            if not re.search(rf"(?<![\d.]){re.escape(value)}(?![\d.])", text)
        ]
        if missing:
            return (
                f"缺少「只取 {label or '选定那一期'}」的过滤条件："
                f"必须包含 {column} = {'、'.join(missing)}，不许把全区间再汇总一遍"
            )
        return None
    if not _TOP_PERIOD_LIMIT_RE.search(text):
        return (
            "缺少「选出最高/最低那一期」的结构：必须先用聚合 + "
            "ORDER BY <指标> DESC LIMIT 1 选出那一期（可放在 WITH 子句里），"
            "再只取该期的数据；不许把全区间再汇总一遍"
        )
    return None


def _detail_contract_violation(sql: str, *, allow_cte_aggregate: bool = False) -> str | None:
    """明细模式的确定性契约：不许聚合、不许 GROUP BY。

    2026-11-03 实测（多轮 e2e）：「给我这两个月的明细数据」卡上 `detail_level=detail`、
    plan 阶段维度已清空、生成 prompt 也明写「禁止聚合」，模型仍然写出
    `SUM(order_amount)` 且没有 GROUP BY → 结果是 1 行汇总，用户要的是逐条。
    光写 prompt 治不住采样，所以这里在 validate 之后加一道确定性拦截——
    违规就交给既有 repair 环（带 repair_ctx 重生成），而不是把 1 行当成功返回。

    2026-11-04 追加：还必须带日期/维度列（否则表格只剩一列、图表没法作图）。

    2026-11-04（top-period-detail-intent）：`allow_cte_aggregate=True` 时只检查
    **CTE 之外的最终查询**——「选出最高那一期」本身必须聚合，那不算违反明细契约。
    """
    text = _strip_cte_blocks(sql or "") if allow_cte_aggregate else (sql or "")
    if _DETAIL_AGGREGATE_RE.search(text):
        return (
            "明细模式禁止聚合函数（SUM/AVG/COUNT/MIN/MAX）："
            "请直接 SELECT 原始列（日期、门店、品类、金额等），按时间倒序，末尾加 LIMIT 200"
        )
    if _DETAIL_GROUP_RE.search(text):
        return (
            "明细模式禁止 GROUP BY：请直接 SELECT 原始列，按时间倒序，末尾加 LIMIT 200"
        )
    # 2026-11-04：明细还必须带日期/维度列。实测「帮我分析一下销量」即使给了照抄投影，
    # 模型仍会只 `SELECT quantity` → 表格只有一列、图表画成一排噪声柱。
    if not _has_dimension_column(sql or ""):
        return (
            "明细模式必须包含日期或维度列（如 dim_date.full_date AS 日期、"
            "dim_store.region AS 区域、dim_product.category AS 品类）："
            "只 SELECT 指标列会让表格只剩一列、图表无法作图"
        )
    return None


def _select_list(sql: str) -> str:
    """SQL 的 SELECT 投影列表（跳过 CTE，只看最终查询最外层 SELECT 到 FROM 之间）。

    为什么需要：`_has_dimension_column` 之前扫全文——而 `dim_date.full_date` 出现在
    JOIN 的 ON 子句、`dim_store.region` 出现在 WHERE 里都是常事，于是「只 SELECT 一列」
    的明细也能过闸（实测 2026-11-04：明细表只有一列「销售额」）。
    """
    text = _strip_cte_blocks(sql or "")
    lower = text.lower()
    depth = 0
    from_index = -1
    for index, char in enumerate(lower):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and lower.startswith("from", index):
            before = lower[index - 1] if index > 0 else " "
            after = lower[index + 4] if index + 4 < len(lower) else " "
            if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                from_index = index
                break
    select_index = lower.find("select")
    if select_index < 0:
        return text
    end = from_index if from_index > select_index else len(text)
    return text[select_index + len("select"):end]


def _has_dimension_column(sql: str) -> bool:
    """SELECT 投影里是否有「可读维度列」（日期 / 区域 / 品类 / 品牌…）。

    列名从知识库来（`schema_hints.yaml`），`table.column` 与裸列名都认。
    只看投影，不看 JOIN/WHERE——那些地方出现维度列不代表表格里有这一列。
    """
    text = _select_list(sql).lower()
    if not text.strip():
        return False
    candidates: list[str] = []
    day_column = knowledge.time_bucket_column("day")
    if day_column:
        candidates.append(day_column)
    for item in (knowledge._schema_hints().get("dimension_terms") or []):  # noqa: SLF001
        column = str(item.get("column") or "")
        if column:
            candidates.append(column)
    for column in candidates:
        bare = column.split(".")[-1].lower()
        if column.lower() in text or (bare and bare in text):
            return True
    return False


@traced_node("sql_validate")
def _validate(state: SQLAgentState) -> dict:
    sql = state.get("generated_sql", "")
    if not sql:
        return {"validation_result": {"valid": False, "error": "无SQL语句"},
                "execution_status": "SQL_SYNTAX_ERROR"}
    validation = json.loads(validate_sql(sql))
    logger.info("validate_sql result for sql[:60]=%s -> %s", (sql or "")[:60], validation)
    # 明细契约（2026-11-03）：SQL 本身合法但形状违反需求 → 同样按 validation 失败处理，
    # 由 DiagnosePolicy → repair 重生成。放在 safety/EXPLAIN 之后，不绕过任何安全闸。
    if validation.get("valid"):
        is_detail = state.get("detail_level") == "detail"
        top_period = state.get("top_period")
        # 2026-11-04：最高期查询要先在 CTE 里聚合选期，明细契约只看最终查询。
        if is_detail:
            violation = _detail_contract_violation(
                sql, allow_cte_aggregate=bool(top_period),
            )
            if violation:
                logger.warning("detail contract violated, routing to repair: %s", violation)
                validation = {"valid": False, "error": violation, "error_kind": "syntax"}
        if validation.get("valid") and top_period:
            violation = _top_period_contract_violation(sql, top_period)
            if violation:
                logger.warning("top-period contract violated, routing to repair: %s", violation)
                validation = {"valid": False, "error": violation, "error_kind": "syntax"}
    return {"validation_result": validation}


@traced_node("sql_execute")
def _execute(state: SQLAgentState) -> dict:
    """执行节点。走 `_execute_validated`（Final Hardening ⑦）：图内 EXPLAIN gate
    已在 `_validate` 节点完成，此处不重复 EXPLAIN；公共 execute_sql（工具直调）
    才是内建 EXPLAIN 的自证路径。"""
    sql = state.get("generated_sql", "")
    if not sql:
        return {"sql_result": json.dumps({"error": "无SQL语句"})}
    result = execute_sql(sql)
    return {"sql_result": result}


@traced_node("sql_evaluate")
def _evaluate(state: SQLAgentState) -> dict:
    # P15 e2e T4 fault seam（fail-closed，仅 REPORTAGENT_E2E=1 时可能激活）：真实判定前
    # 按 kind_override 注入——object_not_found 走 VALIDATION_FAILED → DiagnosePolicy →
    # retry_mcp_schema_retrieval（确定性测 repair 恢复）；permission 走 execution FAILED →
    # DiagnosePolicy not agent_recoverable → fail（确定性测永久 fault 不伪造成功）。
    _fk = fault_inject.kind_override(state)
    if _fk is not None:
        if _fk == "object_not_found":
            return {
                "evaluate_result": EvaluateResult(
                    status="VALIDATION_FAILED", kind="object_not_found",
                    validation_result={
                        "valid": False, "error_kind": "object_not_found",
                        "error": "E2E fault injection (object_not_found)",
                    },
                ).model_dump(),
                "execution_status": "SQL_SYNTAX_ERROR",
            }
        # 注入失败必须与真实失败**同形**：真实路径（下方 error 分支）除了
        # evaluate_result 还会把 `error` 写进 state；否则 `_diagnose` 的 fail
        # 分支拿不到 err → persist_error_run 只能落 `kind="other"` 兜底，SSE
        # 也退化成通用 QUERY_FAILED。此前 permission 注入就丢在这一跳。
        _injected_err = ErrorDetail(
            code="EXECUTION_ERROR",
            message=(
                "权限不足，无法执行该查询"
                if _fk == "permission"
                else f"查询执行失败（{_fk}）"
            ),
            kind=_fk,
        )
        return {
            "evaluate_result": EvaluateResult(
                status="FAILED", kind=_fk, error=_injected_err,
            ).model_dump(),
            "execution_status": "FAILED",
            "error": _injected_err,
        }
    # R1: validation_failed 必须优先于 sql_result 检查——上一轮 execution
    # 留下的 stale sql_result 不能污染本轮 evaluate 判断。修复前 _evaluate
    # 用 `if not raw:` 间接推断 validation failure，结果上一轮 timeout/
    # connection kind 会被错误消费；现显式先看 validation_failed 直接走
    # VALIDATION_FAILED 路径，不读 sql_result。
    validation = state.get("validation_result") or {}
    if validation.get("valid") is False:
        # P15 prelude fix: validate 失败的 kind 不能硬编码 syntax——validate_sql 返回
        # 的 error_kind（object_not_found / object_ambiguous）是真实的列/表名错误，
        # 必须透传给 DiagnosePolicy 走 retry_mcp_schema_retrieval / clarify 路径。
        # 此前丢成 "syntax" → DiagnosePolicy 永远走 retry_sql（同 SQL 反复重跑无信息增益）。
        vkind = validation.get("error_kind") or "syntax"
        return {
            "evaluate_result": EvaluateResult(status="VALIDATION_FAILED", kind=vkind, validation_result=validation).model_dump(),
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
    if decision.action == "retry_mcp_schema_retrieval":
        # P15 prelude fix 第二层：caller 接线。DiagnosePolicy 决策表升级后
        # 必须有 caller 真调 search_schema + 替换 schema_context + 路由回
        # generate_sql，否则新 action 等于死代码。
        #
        # 设计：从 SQL 文本提取涉及的表名（FROM/JOIN），逐个 get_table_ddl
        # 拿准确表结构拼 SchemaContext（比语义检索可靠——错误类型可能是
        # JOIN 键类型错 / 列不存在，只要把 SQL 涉及表的结构给全，LLM 就能修）。
        # counter 递增 mcp_schema。MCP 失败时仍递增 counter 让 graph 重跑
        # generate_sql（generate_sql 自己会失败回 DiagnosePolicy，budget 收敛）。
        sql_text = state.get("generated_sql", "") or ""
        table_names = _extract_table_names(sql_text)
        user_query = state.get("user_query", "") or ""
        discovered: list[dict] = []
        logger.info(
            "retry_mcp_schema_retrieval triggered: kind=%s tables_in_sql=%r counters=%s",
            kind, table_names, retry_counters,
        )
        for tname in table_names:
            try:
                ddl = get_table_ddl.invoke({"table_name": tname})
                if isinstance(ddl, str) and ddl and "not found" not in ddl.lower():
                    discovered.append({
                        "table_name": tname,
                        "description": "",
                        "columns": _parse_ddl_columns(ddl),
                    })
            except Exception as exc:
                logger.warning(
                    "retry_mcp_schema_retrieval: get_table_ddl(%s) failed: %s", tname, exc,
                )
        # 表名提取不全时兜底语义检索（user_query 剥「生成报告：」指令前缀）
        if not discovered:
            try:
                search_query = user_query.split("生成报告", 1)[-1].split("：")[-1][:100] or user_query[:100]
                raw = search_tables.invoke({"query": search_query, "top_k": 3})
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(parsed, list):
                    discovered = parsed
                logger.info("retry_mcp_schema_retrieval: fallback search_tables got %d tables", len(discovered))
            except Exception as exc:
                logger.warning(
                    "retry_mcp_schema_retrieval: fallback search_tables failed: %s", exc,
                )

        schema_tables = [
            TableSchema(
                name=t.get("table_name", ""),
                description=t.get("description", ""),
                columns=[
                    ColumnSchema(name=c["name"], type=c["type"])
                    for c in (t.get("columns") or [])
                    if isinstance(c, dict)
                ],
            )
            for t in discovered[:5]
        ]
        new_schema = SchemaContext(
            version="1.0",
            source="mcp_search_schema",  # 标记：MCP 重新搜出来的 schema
            tables=schema_tables,
            confidence=min(len(schema_tables) * 0.3, 1.0),
            status="SUCCESS" if schema_tables else "FAILED",
            error=(
                None if schema_tables
                else ErrorDetail(code="NO_TABLES", message="MCP search_schema 无结果")
            ),
        )

        new_counters = dict(retry_counters)
        new_counters["mcp_schema"] = new_counters.get("mcp_schema", 0) + 1

        return {
            "diagnose_decision": decision.model_dump(),
            "schema_context": new_schema,
            "retry_counters": new_counters,
            # execution_status=SCHEMA_ERROR 与 replan 同语义（让 graph 知道当前不是 SUCCESS）
            "execution_status": "SCHEMA_ERROR",
        }
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
    # P15 prelude caller 接线：retry_mcp_schema_retrieval 把 schema_context 替换后
    # 路由回 generate_sql 重跑一次（不再走 plan，避免与 replan 语义混淆）。
    if action == "retry_mcp_schema_retrieval":
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
