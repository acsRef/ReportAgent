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

import logging
from typing import Any, Optional, TypedDict

from langgraph.graph import END, StateGraph

from app.infra.checkpoint.factory import get_checkpointer

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
async def _confirmed_data_agent(state: ConfirmedExecutionState, config: Optional[dict] = None) -> dict:
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
async def _confirmed_sql_agent(state: ConfirmedExecutionState, config: Optional[dict] = None) -> dict:
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
    confirmed_requirement = _format_confirmed_requirement(card)

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


def _format_confirmed_requirement(card) -> str | None:
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
    if card.scope:
        parts.append(f"数据范围 = [{_join(card.scope, 20)}]")
    if card.target_metrics:
        parts.append(f"核心指标 = [{_join(card.target_metrics, 10)}]")
    if card.dimensions:
        parts.append(f"分析维度 = [{_join(card.dimensions, 10)}]")
    if card.analysis_methods:
        parts.append(f"分析方法 = [{_join(card.analysis_methods, 10)}]")
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
async def _confirmed_report_agent(state: ConfirmedExecutionState, config: Optional[dict] = None) -> dict:
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
        cols = []
        for c in columns:
            name = c.get("name") if isinstance(c, dict) else str(c)
            cols.append({"key": name, "title": name})
        table = {"columns": cols, "rows": rows}
        insight_text = rs.get("insight") or None
        chart_cfg = rs.get("chart_config") or None
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

    All three end with execution_status='DONE' so main.py can emit a
    `report` SSE event regardless of the empty/err verdict. The actual
    SSE error event is emitted by main.py from the FAIL/EMPTY exit, not
    here — see _route_after_report.
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
            title="报告",
            error_detail=error_detail,
            query_snapshot=query_snapshot,
            trace_id=state.get("trace_id"),
        )
    elif verdict == "EMPTY":
        row = await report_version_service.persist_empty_run(
            session_id=state["session_id"],
            user_id=state["user_id"],
            requirement_draft_id=draft_id,
            title="报告",
            query_snapshot=query_snapshot,
            trace_id=state.get("trace_id"),
        )
    elif base_version is None:
        row = await report_version_service.persist_confirmed_run(
            session_id=state["session_id"],
            user_id=state["user_id"],
            requirement_draft_id=draft_id,
            title="报告",
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
            title="报告（调整）",
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
    return {
        "report_payload": merged,
        "execution_status": "DONE",
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
    workflow.add_edge("load_confirmed_requirement", "sql_gate")
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
