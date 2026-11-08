"""Confirmed-execution graph.

Runs only AFTER the user has explicitly confirmed a RequirementCard.
The hard SQL gate lives in `load_confirmed_requirement` + `sql_gate`:
both verify the draft is owned by the JWT user, status='complete',
no missing fields, and all assumptions accepted. If anything is off,
the graph raises a structured error that the SSE layer surfaces as a
`409 REQUIREMENT_INCOMPLETE` event.

Flow:
    load_confirmed_requirement → sql_gate → data_agent(refresh) →
    sql_agent → report_agent → persist_report → END
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional, TypedDict

from langgraph.graph import END, StateGraph
from langchain_core.runnables import RunnableConfig

from app.infra.checkpoint.factory import get_checkpointer
from app.knowledge import registry as knowledge

from app.agent.data_graph import build_data_graph
from app.agent.sql_graph import build_sql_graph
from app.agent.report_graph import build_report_graph
from app.agent.security_guard import SecurityGuard
from app.infra.db import requirement_repository, report_version_repository
from app.infra.db.postgres import get_pool
from app.infra.trace.sdk import current_tracer, get_tracer, traced_node
from app.models.contracts import ErrorDetail, SchemaContext
from app.models.requirement import RequirementCard
from app.reliability.errors import ErrorCode
from app.report.validator import validate_report_spec
from app.services import requirement_service, report_version_service
from app.state.checkpoint_adapter import migrate_checkpoint

logger = logging.getLogger(__name__)

_TYPICAL_CONTEXT_BUDGET_CHARS = 8000  # 预估 conversation+system+context 块占位（≈2000 tokens），用于 remaining_token_budget 估算


def _callbacks_only(config: Optional[dict]) -> Optional[dict]:
    """P11：向子图 ainvoke 只透传 callbacks——thread_id 等 configurable 不进
    无 checkpointer 子图（避免 checkpoint 命名空间污染），progress handler 仍
    能在嵌套 sql/data/report 子图内收到节点生命周期事件。"""
    cbs = (config or {}).get("callbacks")
    return {"callbacks": cbs} if cbs else None


class ConfirmedExecutionState(TypedDict, total=False):
    user_query: str
    user_id: int
    session_id: str
    trace_id: str
    requirement_card: Optional[RequirementCard]
    # P-4: 加载阶段确定的 draft 主键。下游 gate/persist 只读它，不再中途重查
    # 最新 draft，避免用户在窗口内 PATCH 时锁定的卡与实际执行的卡错位。
    draft_id: Optional[int]
    base_report_version: Optional[int]
    adjustment_text: Optional[str]
    schema_context: Optional[SchemaContext]
    query_result: Optional[dict]
    report_payload: Optional[dict]
    execution_status: str
    error: Optional[ErrorDetail]
    # Last SQL the sub-graph generated (filled by _confirmed_sql_agent).
    # Used by the SSE error helper so the user sees the query that
    # actually ran when execution fails.
    sql: Optional[str]
    # 2026-11-08（plan top-period-annotate-and-timeout-copy）：选期/标注规格。
    # `_confirmed_sql_agent` 算出后写回 state，报告层用它决定「哪些列不许当常量列删掉」。
    # 没有这个字段时 LangGraph 会把这个键丢掉（TypedDict 未声明 → 无 channel），
    # 报告层永远读到 None，于是「是否最高期」被当成无信息列删掉。
    top_period: Optional[dict]
    # P15 e2e T4 fault seam: main 从 X-E2E-Fault header 解析（REPORTAGENT_E2E=1 双 gate），
    # 经此透传到 sql_graph._evaluate（确定性 fail/repair e2e 用；生产恒 None）。
    fault_override: Optional[dict]
    # B7 幂等：`session_id:draft_id` 执行幂等键（trace/审计可见）。
    idempotency_key: Optional[str]
    # 非 None 表示本轮是「重放已完成的版本」，图直接结束、不再执行 SQL。
    replayed_version: Optional[int]


# --- Errors ----------------------------------------------------------------


class RequirementIncompleteError(Exception):
    """Raised when the user calls /confirm with a draft that is missing
    fields or has unresolved assumptions. The HTTP layer maps this to 409.
    """


class SessionNotFoundError(Exception):
    """Raised when the (user_id, session_id) pair is not owned by the user."""


class SecurityRejectedError(Exception):
    """Raised when the incoming user_query（adjust 模式即调整文本）被
    SecurityGuard 判为高风险（prompt 注入等）。HTTP 层映射为 SECURITY_REJECTED。"""


# --- Nodes ----------------------------------------------------------------


@traced_node("confirmed_security_guard")
async def _security_guard(state: ConfirmedExecutionState) -> dict:
    """入口安全闸：对 user_query 过 SecurityGuard，拦 prompt 注入。

    v2 修订：此前 confirmed/adjust 流没有任何安全闸（仅 new/supplement/legacy 过闸），
    调整文本里的注入不被检查。mode=adjust 时 user_query 即调整文本；mode=confirm 时
    user_query 为空串，SecurityGuard 不会误拦。命中高风险 → 抛 SecurityRejectedError。
    """
    state = migrate_checkpoint(dict(state))  # P3 (γ): graph 入口 v1→v2 adapter
    result = SecurityGuard.check(state.get("user_query", "") or "")
    if result.blocked:
        raise SecurityRejectedError(result.reason or "检测到高风险输入")
    return {}


@traced_node("load_confirmed_requirement")
async def _load_confirmed_requirement(state: ConfirmedExecutionState) -> dict:
    """Load the latest draft and verify it's lockable."""
    pool = get_pool()
    async with pool.acquire() as conn:
        draft = await requirement_repository.get_latest(
            conn,
            session_id=state["session_id"],
            user_id=state["user_id"],
        )
    if draft is None:
        raise SessionNotFoundError(
            f"no requirement draft for session {state['session_id']}"
        )
    if draft["status"] == "locked":
        # Already locked = already in a confirmed run; treat as a no-op
        # reload so the graph is idempotent.
        return {"requirement_card": _hydrate_card(draft), "draft_id": draft["id"]}
    if draft["status"] != "complete":
        raise RequirementIncompleteError(
            f"draft {draft['id']} is in status '{draft['status']}', "
            f"must be 'complete' to execute"
        )
    card = _hydrate_card(draft)
    # Sanity: all missing_fields must be empty, all assumptions resolved.
    if card.missing_fields:
        raise RequirementIncompleteError(
            f"draft {draft['id']} still has {len(card.missing_fields)} "
            f"missing fields"
        )
    if any(a.accepted is None for a in card.assumptions):
        raise RequirementIncompleteError(
            f"draft {draft['id']} has unresolved assumptions"
        )
    return {"requirement_card": card, "draft_id": draft["id"]}


@traced_node("confirmed_idempotency_guard")
async def _idempotency_guard(state: ConfirmedExecutionState) -> dict:
    """B7 幂等闸：同一 draft 已执行完成过 → 重放该版本，不再跑 SQL。

    背景：`_release_draft_lock` 会把 draft 从 locked 放回 complete，于是用户
    重复点「确认」、客户端超时重发、或走 `/retry` 都会**再次通过 gate 并重新
    执行一遍 SQL**，产出一条内容相同的新版本——这就是「重复执行」。

    判定键 = `session_id:draft_id`。draft 一旦被 PATCH 就换新 id，所以需求
    改动不会被误判成重放；只认 `status='done'`（SUCCESS / EMPTY），FAILED
    落库为 `status='error'`，必须允许重试。

    `base_report_version` 非 None（mode=adjust）时每次都该产出新版本，不参与幂等。
    """
    if state.get("base_report_version") is not None:
        return {}

    draft_id = await _draft_id_from_state(state)
    session_id = state["session_id"]
    key = f"{session_id}:{draft_id}"
    pool = get_pool()
    async with pool.acquire() as conn:
        existing = await report_version_repository.find_done_by_draft(
            conn,
            session_id=session_id,
            user_id=state["user_id"],
            requirement_draft_id=draft_id,
        )
    if existing is None:
        return {"idempotency_key": key}

    payload: Any = existing["report_payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    payload = dict(payload or {})
    verdict = str(payload.get("execution_status") or "SUCCESS")

    tracer = current_tracer()
    if tracer is not None:
        tracer.add_decision(
            name="idempotency_guard",
            action="replay",
            idempotency_key=key,
            replayed_version=existing["version"],
            execution_status=verdict,
        )
    logger.info(
        "idempotency replay: session=%s draft=%s version=%s",
        session_id, draft_id, existing["version"],
    )
    return {
        "idempotency_key": key,
        "replayed_version": existing["version"],
        "report_payload": {
            **payload,
            "version": existing["version"],
            "parent_version": existing.get("parent_version"),
            "title": existing.get("title") or "报告",
        },
        "execution_status": verdict,
    }


def _route_after_idempotency(state: ConfirmedExecutionState) -> str:
    """已重放 → 直接 END（不执行 SQL、不再落库）；否则走正常执行链。"""
    return "replay" if state.get("replayed_version") is not None else "execute"


@traced_node("sql_gate")
async def _sql_gate(state: ConfirmedExecutionState) -> dict:
    """TOCTOU-safe re-check. Lock the draft transactionally so concurrent
    /confirm calls cannot double-execute. If the lock fails, abort.
    """
    card = state.get("requirement_card")
    if card is None:
        raise RequirementIncompleteError("no card loaded")
    draft_id = await _draft_id_from_state(state)
    try:
        await requirement_service.lock_for_execution(
            session_id=state["session_id"],
            user_id=state["user_id"],
            draft_id=draft_id,
        )
    except Exception as exc:
        raise RequirementIncompleteError(
            f"failed to lock draft for execution: {exc}"
        ) from exc
    return {"execution_status": "RUNNING"}


@traced_node("confirmed_data_agent")
async def _confirmed_data_agent(state: ConfirmedExecutionState, config: Optional[RunnableConfig] = None) -> dict:
    """Refresh schema (do NOT re-analyze requirements)."""
    data_graph = build_data_graph()
    ds = await data_graph.ainvoke({
        "user_query": state["user_query"],
        "discovered_tables": [],
        "mcp_tool_calls": [],
        "raw_schema": "",
        "trace_id": state.get("trace_id", ""),
    }, _callbacks_only(config))
    return {"schema_context": ds.get("schema_context")}


@traced_node("confirmed_sql_agent")
async def _confirmed_sql_agent(state: ConfirmedExecutionState, config: Optional[RunnableConfig] = None) -> dict:
    """Run the SQL subgraph. Note: we deliberately reuse `build_sql_graph`
    WITHOUT its `_intent_analyze` entry node — the requirement is already
    confirmed, so we just plan → generate → execute.

    The structured fields from the PATCHed RequirementCard are serialized
    into `confirmed_requirement` and passed to `_plan` so the LLM does
    not re-infer from the (potentially vague) `user_query`.
    """
    sql_graph = build_sql_graph()
    schema = state.get("schema_context")
    from app.models.contracts import SchemaContext as SchemaCtx
    schema_dict = schema.model_dump() if schema else None
    schema_input = SchemaCtx(**schema_dict) if schema_dict else None

    # Build the authoritative requirement string the LLM will use.
    card = state.get("requirement_card")
    # 2026-11-04（plan top-period-detail-intent）：最高/最低的那一期由**系统算**，
    # 不让模型写两段式 SQL（实测会写成 `SELECT ...; SELECT ...` 两条语句直接语法错）。
    # 算出来就变成确定性 WHERE；算不出来 `_top_period_hint` 退回 CTE 两段式指令。
    top_period_state: dict | None = None
    top_resolved = None
    _top = getattr(card, "top_period", None) if card is not None else None
    if _top is not None:
        metric_name = str(getattr(_top, "metric", "") or "") or next(
            iter(getattr(card, "target_metrics", None) or []), "",
        )
        metric_def = None
        if metric_name:
            _resolved, _unresolved = knowledge.resolve_metrics([str(metric_name)])
            metric_def = _resolved[0] if _resolved else None
        try:
            from app.services import metric_preview_service

            top_resolved = await metric_preview_service.resolve_top_period(
                metric_def,
                unit=str(getattr(_top, "unit", "") or "month"),
                direction=str(getattr(_top, "direction", "") or "max"),
                time_range=getattr(card, "time_range", None),
                scope=list(getattr(card, "scope", None) or []),
            )
        except Exception as exc:  # noqa: BLE001 - 选期失败退两段式，不阻塞执行
            logger.warning("resolve_top_period failed: %s", exc)
        if top_resolved is not None and top_resolved.ok:
            logger.info(
                "top period resolved: %s (%s)", top_resolved.label, top_resolved.filters,
            )
        top_period_state = {
            "direction": str(getattr(_top, "direction", "") or "max"),
            "unit": str(getattr(_top, "unit", "") or "month"),
            "metric": metric_name or None,
            # 2026-11-08（plan top-period-annotate-and-timeout-copy）：
            # select = 只取极值那几期；annotate = 全区间 + 标出哪几期是极值期
            "purpose": str(getattr(_top, "purpose", "") or "select"),
            "resolved": bool(top_resolved is not None and top_resolved.ok),
            "label": getattr(top_resolved, "label", "") if top_resolved else "",
            "period_column": getattr(top_resolved, "period_column", "") if top_resolved else "",
            "period_value": getattr(top_resolved, "period_value", "") if top_resolved else "",
            # 「最多和最小」：两期各自的过滤值（契约要逐条校验都在 SQL 里）
            "period_values": [
                str(entry.period) for entry in (getattr(top_resolved, "entries", ()) or ())
            ] if top_resolved else [],
            "filters": list(getattr(top_resolved, "filters", ()) or ()) if top_resolved else [],
        }
    confirmed_requirement = _format_confirmed_requirement(card, top_resolved=top_resolved)

    # confirm 流（POST /confirm）的 user_query 为空时，用确认卡合成
    # 一个非空查询，否则 _plan 拿到空串、产不出任何信号。
    user_query = state["user_query"]
    if not user_query.strip() and confirmed_requirement:
        user_query = f"生成报告：{confirmed_requirement}"

    # P4c Task 1: 真正接入 ContextRuntime.build() —— 替代 facade build_session_context；
    # 把 conversation_context + assembled_context 一同注入 SQL subgraph
    # state（_plan / _generate_sql 读 assembled_context 优先）。
    # 失败降级为空，绝不阻塞执行链（与原 try/except 语义一致）。
    conversation_context = ""
    assembled_context = ""
    try:
        from app.context.runtime import ContextRuntime  # 局部 import 避免 cycle / 测试 patch
        _uid_raw = state.get("user_id")
        try:
            _uid = int(_uid_raw) if _uid_raw not in (None, "") else 0
        except (TypeError, ValueError):
            _uid = 0
        from app.llm.config import LLMConfig

        _cfg = LLMConfig()
        _est_chars = len(user_query) + _TYPICAL_CONTEXT_BUDGET_CHARS
        _remaining = max(0, _cfg.context_window - _est_chars // 4)
        _bundle = await ContextRuntime().build(
            session_id=state["session_id"],
            user_id=_uid,
            query=user_query,
            agent="confirmed_execution_sql_agent",  # P4c: 符合 ContextPolicyResolver prefix 规则
            state_dict=dict(state),
            remaining_token_budget=_remaining,
        )
        conversation_context = _bundle["conversation_context"]
        assembled_context = _bundle["assembled_context"]
    except Exception as exc:
        logger.warning("ContextRuntime.build failed: %s", exc)

    ss = await sql_graph.ainvoke({
        "schema_context": schema_input,
        "user_query": user_query,
        "query_plan": None,
        "generated_sql": "",
        "validation_result": {},
        "sql_result": "",
        "execution_status": "",
        "error": None,
        "retry_counters": {"plan": 0, "sql_generation": 0},
        "trace_id": state.get("trace_id", ""),
        "chosen_tool": None,  # legacy field; ignored in new flow
        "confirmed_requirement": confirmed_requirement,
        "conversation_context": conversation_context or None,
        "assembled_context": assembled_context or None,  # P4c: 含 recall 的全景 context
        "granularity": getattr(card, "granularity", None),  # 粒度确定性透传（_plan 改写 dimensions）
        "detail_level": getattr(card, "detail_level", None),  # 明细模式：_plan 清空维度/聚合
        # 2026-11-04（top-period-detail-intent）：最高/最低的一期 → sql_graph 需要
        # 在生成阶段再强调、在校验阶段检查「真的只取那一期」。
        "top_period": top_period_state,
        # 2026-11-08（plan analysis-quality-cleanup D1）：对比基线透传给生成阶段。
        # 子图没有需求卡，靠 plan.dimensions 猜不可靠——「销售额同比怎么样」实测
        # 只出了 12 个月的量，同比一个数都没有。
        "comparison_baseline": _comparison_baseline_from_card(card),
        # 指标白名单（2026-11-03）：卡片解析出的指标才给模型，避免把整张注册表塞进去
        "metric_hints": _metric_hints_for(card),
        "fault_override": state.get("fault_override"),  # P15 e2e T4: e2e fault seam 透传
    }, _callbacks_only(config))
    qr = ss.get("query_result")
    # Passthrough of error + last-tried SQL so the parent graph can emit
    # a structured SSE error event and persist a status='error' version
    # row. Both stay None on the success path.
    sub_error = ss.get("error")
    return {
        "query_result": qr.model_dump() if qr else None,
        "execution_status": ss.get("execution_status", "FAILED"),
        "error": sub_error,
        "sql": ss.get("generated_sql") or "",
    }


# C-3: prompt 里的需求文本必须有界。RequirementCard 各字段来自 LLM + 用户 PATCH，
# 长度不受信任——assumption.text 单条可达 300 字，100 条假设就能拼出 30K prompt。
# 每个字段套单项上限 + 列表长度上限，假设区块再套整段上限。
_MAX_FIELD_VALUE_CHARS = 300
_MAX_LIST_ITEM_CHARS = 80
_MAX_ASSUMPTION_TEXT = 200
_MAX_ASSUMPTION_TOTAL = 2000

# 粒度英文值 → prompt 里的中文说法（LLM 对「时间粒度 = 月」比 "month" 更稳）
_GRANULARITY_LABELS: dict[str, str] = {
    "day": "日", "week": "周", "month": "月", "quarter": "季度", "year": "年",
}

# 粒度 → dim_date 的实际分组列。写进 prompt 时带上列名，否则模型仍会按 year 分组
# （2026-10-28 实测：卡里明明是 month，SQL 却是 GROUP BY dim_date.year → 12 行变 1 行）。
_GRANULARITY_COLUMNS: dict[str, str] = {
    "day": "dim_date.full_date",
    "week": "dim_date.week_of_year",
    "month": "dim_date.month",
    "quarter": "dim_date.quarter",
    "year": "dim_date.year",
}


_PERIOD_RE = re.compile(
    r"((?:19|20)\d{2})\s*年(?:\s*(\d{1,2})\s*月(?:\s*[-~至]\s*(\d{1,2})\s*月)?)?"
)


def _period_filter_hint(time_range: str | None) -> str | None:
    """把「2024年7月」「2024年7月-8月」翻译成确定性筛选条件。

    2026-11-03 实测两处：①模型把单月写成 `dim_date.month = '2024-07'`（month 是 int）
    → `invalid input syntax for type integer`；②月份区间被写成 `month = 7` 却又配
    `order_date < '2024-09-01'`，两个条件互相矛盾（8 月被过滤掉）。时间列类型与
    区间写法属于元数据事实，不该让模型每次猜。
    """
    match = _PERIOD_RE.search(str(time_range or ""))
    if not match:
        return None
    year, month, month_end = match.group(1), match.group(2), match.group(3)
    year_col = knowledge.time_bucket_column("year") or "dim_date.year"
    month_col = knowledge.time_bucket_column("month") or "dim_date.month"
    if month and month_end:
        start, end = sorted((int(month), int(month_end)))
        return (
            f"筛选条件（确定性）：{year_col} = {year} AND "
            f"{month_col} BETWEEN {start} AND {end}"
            f"（{month_col} 是整数月份；禁止写成日期字符串）"
        )
    if month:
        return (
            f"筛选条件（确定性）：{year_col} = {year} AND {month_col} = {int(month)}"
            f"（{month_col} 是整数月份；禁止写成 '{year}-{month}' 这类日期字符串）"
        )
    return f"筛选条件（确定性）：{year_col} = {year}"


def _metric_hints_for(card) -> str | None:
    """按需求卡渲染指标白名单；解析不到则返回 None（退回全量）。

    两级解析（2026-11-03）：
    1. `target_metrics` 直接命中注册表（销售额 / 订单数 …）；
    2. 命不中时，从**用户已接受的假设**文本里找（卡片常写成 `sales_trend` 这种
       语义名，但假设里会写「→ 已选：支付金额」）。
    """
    if card is None:
        return None
    resolved, _unresolved = knowledge.resolve_metrics(
        list(getattr(card, "target_metrics", None) or []),
    )
    if not resolved:
        accepted_text = " ".join(
            str(getattr(a, "text", "") or "")
            for a in (getattr(card, "assumptions", None) or [])
            if getattr(a, "accepted", None) is True
        )
        resolved = knowledge.metrics_mentioned_in(accepted_text)
    if not resolved:
        return None
    return knowledge.render_metric_hints([m.name for m in resolved]) or None


# 明细投影只认「SUM(单列)」这种能安全脱掉聚合的口径（COUNT/比率/FILTER 没有单行原值）
_SIMPLE_SUM_RE = re.compile(r"^SUM\(\s*([A-Za-z_][\w.]*)\s*\)$")


def _detail_projection_hint(card) -> str | None:
    """明细模式的**确定性投影**：日期 + 维度列 + 原值指标列（P1-②）。

    实测病灶：「帮我分析一下销量」→ 明细 SQL 只 `SELECT quantity`，表格只有一列
    「销量」，图表把销量同时当 x/y 画出一排无意义柱子。光在 prompt 里写「要原始列」
    模型照样只选指标列，所以这里给一份照抄清单。

    列与别名全部来自知识库（`schema_hints.yaml` / `metrics.yaml`），不硬编码表名：
    - 日期：`time_bucket_column("day")`（缺省 `dim_date.full_date`）；
    - 维度：卡片声明的维度映射成物理列；卡片没声明就取知识库里**不同维度表**的前两个；
    - 指标：只接 `SUM(<列>)` 这类有「原值」语义的口径（COUNT/比率/FILTER 口径在明细里
      没有单行原值，跳过——宁缺勿造列）。
    """
    from app.knowledge import registry as knowledge

    projections: list[str] = []

    day_column = knowledge.time_bucket_column("day")
    if day_column:
        projections.append(f"{day_column} AS 日期")

    dimension_columns: list[str] = []

    def _push_dimension(column: str) -> None:
        if column and column not in dimension_columns:
            dimension_columns.append(column)

    for dim in (getattr(card, "dimensions", None) or []):
        _push_dimension(knowledge.dimension_column(str(dim)) or "")

    if not dimension_columns:
        # 卡片没说维度 → 取知识库里**不同维度表**的前两个（区域 + 品类），保证明细可读
        seen_tables: set[str] = set()
        for item in (knowledge._schema_hints().get("dimension_terms") or []):  # noqa: SLF001
            column = str(item.get("column") or "")
            table = column.split(".")[0] if "." in column else ""
            if not column or table in seen_tables:
                continue
            seen_tables.add(table)
            _push_dimension(column)
            if len(dimension_columns) >= 2:
                break

    for column in dimension_columns:
        label = knowledge.label_for_dimension(column) or column
        projections.append(f"{column} AS {label}")

    resolved, _unresolved = knowledge.resolve_metrics(
        list(getattr(card, "target_metrics", None) or [])
    )
    for metric in resolved:
        # 明细要「单行原值」——只有 SUM(单列) 能安全脱掉聚合；COUNT/比率/FILTER 口径跳过
        match = _SIMPLE_SUM_RE.match(metric.expr.strip())
        if match is None:
            continue
        projections.append(f"{match.group(1)} AS {metric.name}")

    if len(projections) <= 1:
        return None
    lines = "\n".join(f"  {p}," for p in projections).rstrip(",")
    return (
        "明细投影（逐字照抄这些列与别名，禁止只 SELECT 指标列）：\n"
        f"{lines}\n"
        "  —— 日期列 + 至少一个维度列必须在 SELECT 里，否则前端表格只剩一列、图表无法作图"
    )


def _top_period_hint(card, top_resolved=None) -> str | None:
    """「最高/最低的那一期」的确定性指令（2026-11-04，plan top-period-detail-intent）。

    实测病灶：「那最多那一周为什么销售额这么多」→ SQL 变成 `GROUP BY year, region`
    的一行汇总，「最高那周」完全没出现；「2024年里销售额最高的那个月的明细」同理。

    两条路径：
    1) **已算出具体期**（`resolve_top_period` 成功）：直接把期写成确定性过滤条件
       （`dim_date.year = 2024 AND dim_date.month = 11`）——模型只需要「照这个 WHERE 取数」，
       成功率最高；
    2) 算不出来：退回两段式指令（CTE + ORDER BY ... LIMIT 1），由 sql_graph 的契约兜底。
    """
    from app.knowledge import registry as knowledge

    top = getattr(card, "top_period", None)
    if top is None:
        return None

    unit = str(getattr(top, "unit", "") or "month")
    direction = str(getattr(top, "direction", "") or "max")
    word = "最高" if direction == "max" else "最低"
    unit_label = _GRANULARITY_LABELS.get(unit, unit)
    is_detail = getattr(card, "detail_level", None) == "detail"
    final_shape = (
        "按明细投影返回该期的**逐条明细**（不再聚合、不加 GROUP BY），按时间倒序、LIMIT 200"
        if is_detail
        else "只返回该期的汇总/对比结果，不要再按期分组"
    )

    # 2026-11-08（plan top-period-annotate-and-timeout-copy A1）：「标注」是另一回事——
    # 保留全区间，只在结果里多加一列指出哪几期是极值期。绝不能走下面的「只取那一期」路径。
    if str(getattr(top, "purpose", "") or "select") == "annotate":
        return _top_period_annotate_hint(
            card, top_resolved, unit=unit, unit_label=unit_label,
            word=word, is_detail=is_detail,
        )

    # 「最多和最小」两个方向一起要（2026-11-05）：输出**两行**，每期一行
    if direction == "both" and top_resolved is not None and getattr(top_resolved, "ok", False):
        entries = list(getattr(top_resolved, "entries", ()) or ())
        if len(entries) == 2:
            period_column = knowledge.time_bucket_column(unit) or unit
            labels = "、".join(f"{e.label}（{e.direction}）" for e in entries)
            values = ", ".join(e.period for e in entries)
            metric_name = str(getattr(top, "metric", "") or "")
            return (
                f"【选期结果（确定性，系统已算出）】{metric_name}的两个极端{unit_label}：{labels}\n"
                f"筛选条件（确定性，必须逐字使用）：{period_column} IN ({values})"
                f"（若跨年，年份条件取各自那一年）\n"
                f"—— 结果必须**恰好两行**：每个极端{unit_label}一行（列：{unit_label} + {metric_name}），"
                f"按 {period_column} 分组；\n"
                f"—— **不要**只给一行，也**不要**给全部{unit_label}。"
            )

    if top_resolved is not None and getattr(top_resolved, "ok", False):
        label = getattr(top_resolved, "label", "") or f"{word}的{unit_label}"
        filters = list(getattr(top_resolved, "filters", ()) or ())
        metric_name = str(getattr(top, "metric", "") or "")
        return (
            f"【选期结果（确定性，系统已算出）】{metric_name}{word}的{unit_label} = {label}\n"
            f"筛选条件（确定性，必须逐字使用）：{' AND '.join(filters)}\n"
            f"—— 这就是「{word}的那一{unit_label}」；{final_shape}。\n"
            f"—— **禁止**再把全区间汇总一遍，也**禁止**按期 GROUP BY。"
        )

    period_column = knowledge.time_bucket_column(unit) or _GRANULARITY_COLUMNS.get(unit)
    if not period_column:
        return None

    metric_name = str(getattr(top, "metric", "") or "") or (
        (getattr(card, "target_metrics", None) or [None])[0]
    )
    metric_expr = None
    if metric_name:
        resolved, _unresolved = knowledge.resolve_metrics([str(metric_name)])
        if resolved:
            metric_expr = resolved[0].expr
    if not metric_expr:
        metric_expr = "SUM(<指标列>)"

    order = "DESC" if direction == "max" else "ASC"
    return (
        f"【必需结构：{word}的{unit_label}（确定性，必须照此拆成两步）】\n"
        f"1) 用 CTE 选出{word}的那一期：\n"
        f"   WITH top_period AS (\n"
        f"     SELECT {period_column} AS 期\n"
        f"     FROM ... <与最终查询相同的 WHERE 条件>\n"
        f"     GROUP BY {period_column}\n"
        f"     ORDER BY {metric_expr} {order}\n"
        f"     LIMIT 1\n"
        f"   )\n"
        f"2) {final_shape}\n"
        f"   —— 必须 JOIN/过滤 top_period（例如 `{period_column} = (SELECT 期 FROM top_period)`）；\n"
        f"   —— 少这一步就是把全区间又汇总了一遍，用户要的「{word}的那一{unit_label}」根本没出现。"
    )


def _top_period_annotate_hint(
    card, top_resolved, *, unit: str, unit_label: str, word: str, is_detail: bool,
) -> str:
    """标注模式的确定性指令：「全区间 + 一列标记」，不是「只看那一期」。

    用户说「最高的那两个月，标注一下」——只取那两期的话「标注」这个词就没意义了。
    所以这里反过来：**禁止**用极值条件过滤数据，只许用它生成标记列。
    """
    from app.knowledge import registry as knowledge

    flag = f"是否{word}期"
    period_column = knowledge.time_bucket_column(unit) or unit
    shape = (
        "逐条明细（不聚合、不 GROUP BY），按时间倒序、LIMIT 200"
        if is_detail
        else f"按{unit_label}分组的一行汇总（每个{unit_label}一行）"
    )

    if top_resolved is not None and getattr(top_resolved, "ok", False):
        label = getattr(top_resolved, "label", "") or f"{word}的{unit_label}"
        filters = " AND ".join(list(getattr(top_resolved, "filters", ()) or ()))
        values = [
            str(entry.period) for entry in (getattr(top_resolved, "entries", ()) or ())
        ] or [str(getattr(top_resolved, "period_value", "") or "")]
        condition = filters or f"{period_column} IN ({', '.join(v for v in values if v)})"
        return (
            f"【标注模式（确定性，系统已算出极值期）】{label}\n"
            f"标记条件：{condition}\n"
            f"—— **不要**用它过滤数据：必须返回**全区间**的{shape}，不许只返回该期；\n"
            f"—— 必须新增一列标记（列名固定）：\n"
            f"   CASE WHEN {condition} THEN '是' ELSE '否' END AS \"{flag}\"\n"
            f"—— 列顺序：业务列在前，\"{flag}\" 放最后一列；"
            f"也就是「全量数据 + 一眼看出哪几{unit_label}是{word}期」。\n"
            f"—— 排序：仍按**时间倒序**，**不要**按标记列排序"
            f"（否则标为「是」的行会挤满前 N 行，用户反而看不到其余区间）。"
        )

    order = "DESC" if word == "最高" else "ASC"
    return (
        f"【标注模式（必须照此拆成两步）】{word}的{unit_label}由系统算不出来，"
        f"请先用 CTE 选出它，再**回到全区间**打标：\n"
        f"1) WITH top_period AS (\n"
        f"     SELECT {period_column} AS 期 FROM ... GROUP BY {period_column}\n"
        f"     ORDER BY SUM(<指标列>) {order} LIMIT 1\n"
        f"   )\n"
        f"2) 主查询**不 JOIN 过滤**，只做标记：返回全区间的{shape}，并加一列\n"
        f"   CASE WHEN {period_column} IN (SELECT 期 FROM top_period) THEN '是' ELSE '否' END"
        f" AS \"{flag}\"\n"
        f"   —— 少这一列就是把「标注」做成了没标注；用 top_period 过滤数据则是另一件事"
        f"（用户要的是全量里标出来）。"
    )


def _comparison_baseline_from_card(card) -> str | None:
    """需求卡 dimensions 里的对比标签 → 给 SQL 生成阶段的一句话（没有则 None）。

    标签由需求层确定性产出（`requirement_parser._ensure_comparison_slot`），
    这里只做翻译，不做判断。
    """
    dims = getattr(card, "dimensions", None) if card is not None else None
    for dim in (dims or []):
        text = str(dim)
        if "同比" in text:
            return "同比（今年与去年同期同月份比）"
        if "环比" in text:
            return "环比（本期与上一期比）"
        if "指定基线" in text:
            return "与指定基线对比"
    return None


def _format_confirmed_requirement(card, *, top_resolved=None) -> str | None:
    """把 PATCH 确认后的 RequirementCard 序列化成 SQL plan prompt
    消费的结构化字符串（中文字段标签）。无卡时返回 None，plan
    退回从自由文本 user_query 推断。
    """
    if card is None:
        return None

    def _join(items: list[str], limit: int) -> str:
        return ", ".join(str(s)[:_MAX_LIST_ITEM_CHARS] for s in items[:limit])

    parts: list[str] = []
    if card.time_range:
        parts.append(f"时间范围 = {str(card.time_range)[:_MAX_FIELD_VALUE_CHARS]}")
    if card.granularity:
        # 2026-10-28：粒度此前只存在于 dimensions 的「粒度:month」标签里，用户明说
        # 「每个月」时卡片没有字段承载 → SQL 只按年分组（实测 1 行，应为 12 行）。
        # 光写「时间粒度 = 月」模型仍会按 year 分组，所以直接把分组列写死。
        label = _GRANULARITY_LABELS.get(card.granularity, card.granularity)
        column = _GRANULARITY_COLUMNS.get(card.granularity)
        suffix = f"（时间维度必须按 {column} 分组，不要用 year）" if column else ""
        parts.append(f"时间粒度 = {label}{suffix}")
    hint = _period_filter_hint(card.time_range)
    if hint:
        parts.append(hint)
    # 2026-11-04：先选期、再取该期的数据/明细。这条要在明细块之前给出，否则明细块
    # 的「禁止聚合」会把选期所需的 CTE 聚合也一起禁掉（见 sql_graph 的 CTE 例外）。
    top_hint = _top_period_hint(card, top_resolved)
    if top_hint:
        parts.append(top_hint)
    if card.scope:
        # 2026-11-02 实测：「分析2024年华东销售趋势」的 SQL 出现 WHERE region='华东'
        # 之外还 GROUP BY region → 每行多一个恒为「华东」的列、图表退化成一根柱子。
        # 范围是过滤条件，不是分析维度，必须说死。
        parts.append(
            f"数据范围 = [{_join(card.scope, 20)}]"
            "（只作 WHERE 过滤条件，**禁止**把范围里的实体放进 GROUP BY 或 SELECT）"
        )
    if card.target_metrics:
        parts.append(f"核心指标 = [{_join(card.target_metrics, 10)}]")
        # 指标口径从注册表解析（数据驱动），命中则把 SQL 表达式写死给模型照抄，
        # 不命中则显式告知「未注册」——不让模型自由发明列名。
        resolved, unresolved = knowledge.resolve_metrics(list(card.target_metrics))
        if resolved:
            lines = "\n".join(f"  - {m.name} = {m.expr}" for m in resolved)
            parts.append(
                "指标表达式（必须逐字使用；**只输出这些指标列**，不要额外新增指标）:\n" + lines
            )
            joins = [j for m in resolved for j in m.joins]
            if joins:
                parts.append("指标依赖的 JOIN（必须一起带上）:\n" + "\n".join(f"  {j}" for j in joins))
        if unresolved:
            parts.append(f"未注册指标（禁止臆造列名）: [{_join(unresolved, 10)}]")
    is_detail = getattr(card, "detail_level", None) == "detail"
    if card.dimensions and not is_detail:
        dims = [str(d) for d in card.dimensions[:10]]
        methods_norm = [str(m).lower() for m in (card.analysis_methods or [])]
        if any("trend" in m for m in methods_norm):
            # 确定性收窄：趋势只看时间。实测光在 prompt 里写规则，模型仍会照抄
            # 「分析维度 = [时间, 区域]」并 GROUP BY region（多出一列常量）。
            time_terms = set(knowledge.time_dimension_terms())
            time_dims = [d for d in dims if d in time_terms]
            dims = time_dims or dims[:1]
        parts.append(f"分析维度 = [{', '.join(dims)}]")
    if card.analysis_methods and not is_detail:
        methods = [str(m) for m in card.analysis_methods]
        parts.append(f"分析方法 = [{_join(methods, 10)}]")
        if any("trend" in m.lower() or "趋势" in m for m in methods):
            parts.append(
                "趋势分析：只按时间维度分组，不要再加入其它维度；"
                "已被数据范围限定的维度不得进入 GROUP BY。"
            )
    if is_detail:
        # 2026-11-03：「给我这两个月的明细数据」此前没有任何字段承载 → 仍然
        # GROUP BY month 只出 2 行。明细模式必须显式禁止聚合，且**优先级最高**
        # （上面若还有趋势/分组要求，会与它打架，所以这里明确覆盖）。
        parts.append(
            "明细模式（优先级最高，覆盖上面的聚合/分组要求）：\n"
            "- 禁止 SUM/COUNT/AVG 与 GROUP BY，直接 SELECT 原始列\n"
            "- 金额取原值（不要 ROUND 到万级），并带上可读的日期/门店/品类列\n"
            "- 按时间倒序，LIMIT 200"
        )
        # 2026-11-04（P1-② 明细类列太薄）：只写「要原始列」模型仍然只 SELECT 指标列
        # ——实测「帮我分析一下销量」的明细表只有一列「销量」，图表把销量同时当
        # x/y 画出一排无意义柱子。这里给**确定性投影**（日期 + 维度 + 指标），
        # 模型照抄即可；列名与别名全部来自知识库（换 schema 只改 YAML）。
        projection = _detail_projection_hint(card)
        if projection:
            parts.append(projection)
    if card.assumptions:
        accepted = [a for a in card.assumptions if a.accepted is True]
        if accepted:
            joined = "; ".join((a.text or "")[:_MAX_ASSUMPTION_TEXT] for a in accepted)
            if len(joined) > _MAX_ASSUMPTION_TOTAL:
                joined = joined[:_MAX_ASSUMPTION_TOTAL] + "..."
            parts.append("用户已接受的假设 = [" + joined + "]")
    if not parts:
        return None
    return "\n".join(parts)


@traced_node("confirmed_report_agent")
async def _confirmed_report_agent(state: ConfirmedExecutionState, config: Optional[RunnableConfig] = None) -> dict:
    """Build the report payload from the query result.

    Three-state verdict drives `execution_status`:

    - SUCCESS: query_result.error is None AND rows present
    - EMPTY:   query_result.error is None AND rows absent (legitimate
      zero-match — the SQL ran fine, just didn't match anything)
    - FAILED:  query_result.error is not None (timeout / connection /
      permission / syntax / object / other)

    The old single-state `if rows: SUCCESS else FAILED` collapsed EMPTY
    and FAILED, which made legitimate empty results look like query
    failures. We split them here so the SSE + persistence layer can
    tell the two cases apart and the front-end can render distinct UX.
    """
    report_graph = build_report_graph()
    card = state.get("requirement_card")
    rs = await report_graph.ainvoke({
        "query_result": state.get("query_result"),
        "user_query": state["user_query"],
        "chart_config": {},
        "insight": "",
        "report_spec": None,
        "assemble_plan": [],
        "assemble_step_idx": 0,
        "assemble_results": [],
        "trace_id": state.get("trace_id", ""),
        # P16 D1：Report 偏好接线——父 state 透传会话归属，report 链经 ContextRuntime
        # 召回 preference（memory-architecture §三 Report = Semantic ✅ Preference）。
        "session_id": state.get("session_id"),
        "user_id": state.get("user_id"),
        # 2026-11-02：需求卡透传（报告规划要 analysis_methods/dimensions/summary）
        "requirement_card": (
            card.model_dump() if hasattr(card, "model_dump") else card
        ),
    }, _callbacks_only(config))

    qr = state.get("query_result")
    err: ErrorDetail | None = None
    rows: list = []
    columns: list = []
    if qr and isinstance(qr, dict):
        rows = qr.get("rows") or []
        columns = qr.get("columns") or []
        err_field = qr.get("error")
        if isinstance(err_field, dict):
            err = ErrorDetail.model_validate(err_field)

    # 续命：SQL 子图在 clarify / fail 分支是直接 END 的，不会走 build_output，
    # 于是 query_result=None —— 此前这里只从 qr 取 error，**上游的 error_kind 被丢掉**，
    # 用户看到的是笼统的「查询执行失败,请稍后重试」，而不是「表/字段不存在」。
    # 现在 qr 缺失时回落到父 state 的 error（子图 _confirmed_sql_agent 已透传）。
    if err is None and qr is None:
        upstream = state.get("error")
        if isinstance(upstream, ErrorDetail):
            err = upstream
        elif isinstance(upstream, dict):
            try:
                err = ErrorDetail.model_validate(upstream)
            except Exception:  # noqa: BLE001 - 形状不符就保持 None（不阻塞报告壳）
                err = None

    # Three-state verdict:
    # - FAILED  : err is set OR query_result never produced (sub-graph
    #            crashed / never executed). Both mean "the user can't
    #            trust the report content".
    # - EMPTY   : SQL ran cleanly but matched 0 rows (legitimate
    #            no-match — distinct from FAILED).
    # - SUCCESS : rows present.
    if err is not None or qr is None:
        status = "FAILED"
        table = None
        insight_text = None
        chart_cfg = None
    elif not rows:
        status = "EMPTY"
        table = None
        insight_text = "未找到匹配数据。你可以尝试放宽筛选条件，比如扩大时间范围或调整关键词。"
        chart_cfg = None
    else:
        status = "SUCCESS"
        # 2026-11-04：列名中文化 + 去掉整列取值完全相同的重复列（模型偶尔把同一表达式
        # 输出两次且第二次没有中文别名 → 界面上冒出 `week_of_year`/`category` 这种列名）。
        # key 保持原样（前端按 key 取数），只换 title；图表行键同步改名，轴名才不会露英文。
        from app.report.labels import build_display_table, localize_chart

        qr_columns = [
            c.get("name") if isinstance(c, dict) else str(c) for c in columns
        ]
        table, label_map = build_display_table(qr_columns, rows)
        # 2026-11-03：去掉「全表恒定」的列（scope 过滤后的 地区=华东 这类）。
        # 这类列不携带信息，却占掉表格一列、还会把图表维度带偏。
        # 2026-11-08：标注模式的标记列例外——它是用户点名要的，恒定也不能删。
        table["columns"] = _drop_constant_columns(
            table["columns"], rows,
            keep=_annotation_columns(state, table["columns"]),
        )
        insight_text = rs.get("insight") or None
        chart_cfg = localize_chart(rs.get("chart_config") or None, label_map)
        # P10 三层 Validator：校验 ReportSpec → QueryResult 映射（结构/数值/禁止
        # 自由生成）。violations → FAILED（宪法 §10 永不伪造成功），走既有 FAILED
        # 全链：persist_error_run + SSE error（用户码 QUERY_FAILED 兜底，前端零改动）。
        spec = rs.get("report_spec")
        if spec is not None:
            vres = validate_report_spec(spec, qr)
            if not vres.ok:
                summary = "; ".join(
                    f"[{v.layer}] {v.block}: {v.detail}" for v in vres.violations[:5]
                )
                logger.warning(
                    "report spec validation failed (%d violations): %s",
                    len(vres.violations), summary,
                )
                status = "FAILED"
                err = ErrorDetail(
                    code=ErrorCode.REPORT_VALIDATION_ERROR.value,
                    message=f"报告数据校验失败: {summary}",
                    kind="other",
                )
                table = None
                insight_text = None
                chart_cfg = None
                # P8 D5 语义复用：决策进 trace（P13 Langfuse 落库）
                tracer = current_tracer()
                if tracer is not None:
                    tracer.add_decision(
                        name="report_validate",
                        action="fail",
                        reason=summary,
                        error_kind="other",
                        violation_count=len(vres.violations),
                    )

    payload = {
        "answer": {
            "text": "查询完成",
            "table": table,
            "chart": chart_cfg,
            "insight": insight_text,
        },
        "trace": [],
        "execution_status": status,
    }
    if err is not None:
        payload["error"] = err.model_dump()

    return {
        "report_payload": payload,
        "execution_status": status,
        "error": err,
    }


_MAX_TITLE_CHARS = 24

# 标注列的固定形状（「是否最高期」/「是否最低期」），用于在 state 透传断掉时兜底识别。
_FLAG_COLUMN_RE = re.compile(r"^是否.*期$")


def _annotation_columns(
    state: ConfirmedExecutionState, columns: list[dict] | None = None,
) -> set[str]:
    """标注模式下必须保留的列名（空集 = 没有标注需求）。

    两条来源取并集：①state 里的选期规格（`_confirmed_sql_agent` 算出）；②列名形状
    `是否…期`。只靠 ① 不够——state 透传一旦断掉，标注列就被当常量删了（2026-11-08 踩过）。
    """
    keep: set[str] = {str(c["key"]) for c in (columns or []) if _FLAG_COLUMN_RE.match(str(c["key"]))}
    top = state.get("top_period") or {}
    if str(top.get("purpose") or "select") != "annotate":
        return keep
    word = "最高" if str(top.get("direction") or "max") == "max" else "最低"
    keep.add(f"是否{word}期")
    return keep


def _drop_constant_columns(
    columns: list[dict], rows: list[dict], keep: set[str] | None = None,
) -> list[dict]:
    """隐藏「所有行都相同」的列（2026-11-03）。

    实测：「分析2024年华东销售趋势」的表格里带着一列 `地区=华东`——因为 scope
    已经把它过滤成常量。它不提供信息，却占掉一列宽度，也会让图表误以为有分类维度。
    仅在还有其它列可显示时才隐藏（单列结果不能删空）。

    2026-11-08（plan top-period-annotate-and-timeout-copy）：`keep` 里的列**永不删**。
    真机踩到：用户要「全量 + 标注哪两个月最高」，而明细按时间倒序只取最近 200 条，
    这 200 条里一条「是」都没有 → 整个「是否最高期」列被当成常量删掉，
    用户在界面上**完全看不到**自己要的标注。标注列恒定也是信息（「这批都不是」），
    何况它是用户点名要的语义，不能交给「有没有信息量」的启发式决定。
    """
    keep = keep or set()
    if len(rows) <= 1 or len(columns) <= 1:
        return columns
    kept = [
        col for col in columns
        if col["key"] in keep or len({str(row.get(col["key"])) for row in rows}) > 1
    ]
    return kept or columns


def _report_title(state: ConfirmedExecutionState) -> str:
    """报告标题取自需求卡摘要（2026-11-03）。

    此前所有版本都叫「报告」/「报告（调整）」，左栏十条会话十个「报告」，
    用户翻历史完全分不清是哪次分析。标题是给**人看的索引**，应该来自需求本身。
    """
    card = state.get("requirement_card")
    summary = ""
    if card is not None:
        summary = getattr(card, "summary", None) or (
            card.get("summary") if isinstance(card, dict) else None
        ) or ""
    summary = " ".join(str(summary).split())
    if not summary:
        return "报告"
    return summary[:_MAX_TITLE_CHARS] + ("…" if len(summary) > _MAX_TITLE_CHARS else "")


@traced_node("persist_report")
async def _persist_report(state: ConfirmedExecutionState) -> dict:
    """Append a row to `agent.report_version`.

    Branches on `execution_status` from the report agent:

    - SUCCESS: existing path (persist_confirmed_run / persist_adjust_run).
    - EMPTY:   persist_empty_run — same as SUCCESS but payload carries
      execution_status=EMPTY and the no-data band tells the front-end
      to render "未找到匹配记录" instead of a fake table.
    - FAILED:  persist_error_run — still inserts a row so version
      history shows the failed attempt; status='error'.

    P11 Review-1 P1-1：返回值**保留** verdict（SUCCESS / EMPTY / FAILED）—
    main.py 读 result.execution_status 决定 SSE 出口（FAILED→error，
    其余→report）；之前在 return 里覆盖成 "DONE" 把 FAILED 抹成 report，
    用户收到假成功报告 + version，未走 ErrorCard。verdict 由
    _confirmed_report_agent 写入 state，这里只 merge report_payload
    + 持久化副作用，不动 verdict。
    """
    if state.get("report_payload") is None:
        return {"execution_status": "FAILED"}
    draft_id = await _draft_id_from_state(state)
    base_version = state.get("base_report_version")
    verdict = state.get("execution_status") or "SUCCESS"

    qr = state.get("query_result") or {}
    query_snapshot = _build_query_snapshot(qr, verdict, state.get("error"))

    if verdict == "FAILED":
        err = state.get("error")
        error_detail = (
            err.model_dump() if hasattr(err, "model_dump") else
            (err if isinstance(err, dict) else {"code": "QUERY_FAILED", "message": "", "kind": "other"})
        )
        row = await report_version_service.persist_error_run(
            session_id=state["session_id"],
            user_id=state["user_id"],
            requirement_draft_id=draft_id,
            title=_report_title(state),
            error_detail=error_detail,
            query_snapshot=query_snapshot,
            trace_id=state.get("trace_id"),
        )
    elif verdict == "EMPTY":
        row = await report_version_service.persist_empty_run(
            session_id=state["session_id"],
            user_id=state["user_id"],
            requirement_draft_id=draft_id,
            title=_report_title(state),
            query_snapshot=query_snapshot,
            trace_id=state.get("trace_id"),
        )
    elif base_version is None:
        row = await report_version_service.persist_confirmed_run(
            session_id=state["session_id"],
            user_id=state["user_id"],
            requirement_draft_id=draft_id,
            title=_report_title(state),
            report_payload=state["report_payload"],
            query_snapshot=query_snapshot,
            trace_id=state.get("trace_id"),
        )
    else:
        row = await report_version_service.persist_adjust_run(
            session_id=state["session_id"],
            user_id=state["user_id"],
            base_report_version=base_version,
            requirement_draft_id=draft_id,
            adjustment_text=state.get("adjustment_text") or "",
            title=_report_title(state),
            report_payload=state["report_payload"],
            query_snapshot=query_snapshot,
            trace_id=state.get("trace_id"),
        )

    trace_id = state.get("trace_id", "")
    if trace_id:
        tracer = get_tracer(trace_id)
        tracer.end("DONE")

    # 执行结束（三态都落库后）释放 draft 锁：否则 draft 永久 locked，
    # 重新生成 / adjust / PATCH 全被拒。并发保护仍由 `lock_draft` 原语 +
    # ExecutionRegistry 409 承担；中途失败（未走到本节点）场景由
    # lock_for_execution 的恢复逻辑兜底。
    await _release_draft_lock(state)

    merged = {
        **state["report_payload"],
        "version": row["version"],
        "parent_version": row.get("parent_version"),
        "title": row.get("title") or "报告",
    }
    # P11 Review-1 P1-1：verdict 由 _confirmed_report_agent 写入 state；本节点只 merge
    # report_payload 副作用，不覆写 execution_status——main.py 据此决定 error/report SSE。
    return {
        "report_payload": merged,
    }


def _build_query_snapshot(
    qr: dict,
    verdict: str,
    err: ErrorDetail | None,
) -> dict | None:
    """Compose the JSONB query_snapshot we persist on agent.report_version.

    - SUCCESS / EMPTY → sql + columns + rows + row_count + truncated.
    - FAILED          → sql + error_kind + error message; no rows
      (we never had any) and row_count=0 / truncated=False.
    """
    if not qr:
        return None
    base = {
        "sql": qr.get("sql") if isinstance(qr, dict) else None,
    }
    if verdict == "FAILED":
        kind = None
        message = ""
        if err is not None:
            kind = err.kind
            message = err.message
        elif isinstance(qr.get("error"), dict):
            inner = qr["error"]
            kind = inner.get("kind")
            message = inner.get("message", "")
        base.update({
            "row_count": 0,
            "truncated": False,
            "error_kind": kind,
            "error": message,
            "columns": [],
            "rows": [],
        })
        return base
    base.update({
        "columns": qr.get("columns") if isinstance(qr, dict) else None,
        "rows": qr.get("rows") if isinstance(qr, dict) else None,
        "row_count": qr.get("row_count") if isinstance(qr, dict) else None,
        "truncated": bool(qr.get("truncated")) if isinstance(qr, dict) else False,
    })
    return base


# --- Helpers --------------------------------------------------------------


def _hydrate_card(draft_row: dict) -> RequirementCard:
    import json
    payload = draft_row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return RequirementCard.model_validate(payload)


async def _release_draft_lock(state: ConfirmedExecutionState) -> None:
    """把本次执行的 draft 从 `locked` 释放回 `complete`（幂等）。"""
    draft_id = await _draft_id_from_state(state)
    if not draft_id:
        return
    pool = get_pool()
    async with pool.acquire() as conn:
        await requirement_repository.release_lock(
            conn, draft_id=draft_id, user_id=state.get("user_id", 0),
        )


async def _draft_id_from_state(state: ConfirmedExecutionState) -> int:
    """P-4: 返回加载阶段已确定并写入 state 的 draft_id。

    旧实现在执行中途「重新查最新 draft」：若用户在 load 与 gate/persist 之间
    PATCH 了一次需求，就会锁住新 draft、却拿旧卡执行——锁定的卡与实际执行的卡
    错位。load 阶段把 draft_id 写进 state 后，这里只读 state，保证整条链路
    （gate 锁定 / persist 落库）用的是同一份 draft。缺失时回退 0，让
    lock_for_execution 以「draft 不存在」优雅失败。
    """
    return state.get("draft_id") or 0


# --- Graph build ----------------------------------------------------------


def _is_external_interface(card) -> bool:
    """需求卡是否标记为外部实时接口（data_source:stream assumption）。"""
    if card is None:
        return False
    return any(getattr(a, "key", "") == "data_source:stream" for a in (card.assumptions or []))


def _route_after_gate(state: ConfirmedExecutionState) -> str:
    """外部接口需求 → 不生成 SQL，直接出接口说明；否则正常 SQL 流程。"""
    return "interface_response" if _is_external_interface(state.get("requirement_card")) else "data_agent"


@traced_node("interface_response")
def _interface_response(state: ConfirmedExecutionState) -> dict:
    """外部实时接口需求：确认后不生成 SQL，返回接口接入说明文本。"""
    card = state.get("requirement_card")
    source = ""
    if card:
        for a in (card.assumptions or []):
            if a.key == "data_source:stream":
                source = a.text
                break
    payload = {
        "answer": {
            "text": source or "此查询涉及外部实时接口/数据源，需接入实时数据源后取数，非数据库报表。",
            "table": None,
            "chart": None,
            "insight": None,
        },
        "trace": [],
        "execution_status": "SUCCESS",
    }
    return {"report_payload": payload, "execution_status": "SUCCESS"}


def build_confirmed_execution_graph():
    workflow = StateGraph(ConfirmedExecutionState)

    workflow.add_node("security_guard", _security_guard)
    workflow.add_node("load_confirmed_requirement", _load_confirmed_requirement)
    workflow.add_node("idempotency_guard", _idempotency_guard)
    workflow.add_node("sql_gate", _sql_gate)
    workflow.add_node("data_agent", _confirmed_data_agent)
    workflow.add_node("sql_agent", _confirmed_sql_agent)
    workflow.add_node("report_agent", _confirmed_report_agent)
    workflow.add_node("persist_report", _persist_report)

    # v2 修订：入口先过安全闸，拦 prompt 注入后再加载需求。
    workflow.set_entry_point("security_guard")
    workflow.add_edge("security_guard", "load_confirmed_requirement")
    workflow.add_node("interface_response", _interface_response)
    workflow.add_conditional_edges("sql_gate", _route_after_gate,
                                   {"interface_response": "interface_response", "data_agent": "data_agent"})
    workflow.add_edge("data_agent", "sql_agent")
    workflow.add_edge("interface_response", "persist_report")
    # B7：load 之后先过幂等闸——同一 draft 已执行完成过就直接重放该版本并结束。
    workflow.add_edge("load_confirmed_requirement", "idempotency_guard")
    workflow.add_conditional_edges(
        "idempotency_guard",
        _route_after_idempotency,
        {"replay": END, "execute": "sql_gate"},
    )
    workflow.add_edge("sql_agent", "report_agent")

    def _route_after_report(state: ConfirmedExecutionState) -> str:
        # All three verdicts (SUCCESS / EMPTY / FAILED) now persist so
        # the version history shows the full timeline. The
        # execution_status flows to main.py unchanged, where:
        #   FAILED → emit SSE error event AND emit a `report` event with
        #            status='error' payload (persisted above)
        #   EMPTY  → emit SSE `report` event with execution_status=EMPTY
        #   SUCCESS → emit SSE `report` event with full payload
        return "persist_report"

    workflow.add_conditional_edges(
        "report_agent",
        _route_after_report,
        {"persist_report": "persist_report"},
    )
    workflow.add_edge("persist_report", END)

    return workflow.compile(checkpointer=get_checkpointer())
