"""Requirement-analysis graph.

A small LangGraph that runs only schema discovery and requirement
parsing — never SQL or report tools. This is the "do not run anything
against business data until the user confirms" gate.

Flow:
    security_guard → data_agent(schema only) → requirement_parse →
    persist_draft → END

The `data_agent` node here is a *schema-only* wrapper: it deliberately
does not register `validate_sql` / `execute_sql` / report tools. The
SQL gate is enforced by node wiring + by the fact that nothing in this
graph imports the SQL/Report tools.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Optional, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from app.agent.data_graph import build_data_graph
from app.agent.intent import IntentKind, _CHITCHAT_KEYWORDS, classify_intent
from app.infra.checkpoint.factory import get_checkpointer
from app.agent.requirement_parser import parse_requirement
from app.agent.security_guard import SecurityGuard
from app.models.requirement import RequirementAssumption, RequirementCard
from app.infra.db import requirement_repository
from app.infra.db.postgres import get_pool
from app.state.checkpoint_adapter import migrate_checkpoint
from app.infra.trace.sdk import get_tracer, traced_node
from app.models.contracts import ErrorDetail, SchemaContext
from app.models.requirement import RequirementCard

logger = logging.getLogger(__name__)

_TYPICAL_CONTEXT_BUDGET_CHARS = 8000  # 预估 conversation+system+context 块占位（≈2000 tokens），用于 remaining_token_budget 估算


def _progress_detail(node_label: str, text: str) -> None:
    """把「这一步的结论」写进执行过程帧（plan 2026-11-04-trace-visible-reasoning）。

    失败静默：过程帧只是可观测性，不能因为写 detail 出错拖垮需求解析。
    """
    try:
        from app.infra.execution import progress_detail as _pd

        _pd.detail(node_label, text)
    except Exception:  # noqa: BLE001 - 观测量不影响主链
        pass


class RequirementAnalysisState(TypedDict, total=False):
    user_query: str
    user_id: int
    session_id: str
    trace_id: str
    mode: Optional[str]  # P15 e2e T2: new | supplement——supplement 轮需求继承上一轮确认卡
    schema_context: Optional[SchemaContext]
    requirement_card: Optional[RequirementCard]
    draft_id: Optional[int]
    security_score: int
    security_level: str
    security_warning: str
    error: Optional[ErrorDetail]
    intent: Optional[str]  # chitchat | report | interface | dashboard | unknown
    intent_reason: Optional[str]
    casual_reply: Optional[str]
    dict_context: Optional[str]  # 字典检索上下文（外部接口检测 + parse 复用）
    execution_status: str  # SUCCESS | SECURITY_BLOCKED | FAILED
    # 2026-11-05（plan turn-understanding-agent）：Turn Understanding 链路的状态。
    # 必须在 TypedDict 里声明——langgraph 会丢弃未声明 key。
    turn_plan: Optional[Any]
    analysis_task: Optional[Any]
    task_revision: Optional[Any]
    reportability: Optional[Any]
    # 推理模型返回的思维链（只用于观测/前端展示，不参与业务判断）
    turn_reasoning: Optional[str]


# --- Nodes ----------------------------------------------------------------


@traced_node("requirement_security_guard")
def _security_guard(state: RequirementAnalysisState) -> dict:
    state = migrate_checkpoint(dict(state))  # P3 (γ): graph 入口 v1→v2 adapter
    result = SecurityGuard.check(state["user_query"])
    out = {
        "security_score": result.score,
        "security_level": result.level,
        "security_warning": result.reason,
    }
    _progress_detail(
        "安全检查",
        f"{'拦截' if result.blocked else '通过'}（{result.level}，风险分 {result.score}）",
    )
    if result.blocked:
        out["error"] = ErrorDetail(
            code="SECURITY_REJECTED",
            message="请求包含潜在危险指令，无法执行",
        )
        out["execution_status"] = "SECURITY_BLOCKED"
    return out


def _route_security(state: RequirementAnalysisState) -> str:
    return "__end__" if state.get("security_level") == "HIGH" else "classify_intent"


def _route_intent(state: RequirementAnalysisState) -> str:
    intent = state.get("intent") or "report"
    if intent == "chitchat":
        return "casual_reply"
    if intent == "interface":
        return "interface_requirement"
    # report / dashboard / unknown → 走正常需求分析
    return "data_agent"


async def _fetch_dict_context(query: str) -> tuple[bool, str]:
    """字典检索：返回 (dict_hit, dict_context)。

    dict_hit = 有字典命中（数据库表/字段/接口文档）→ 判定为数据库查询（REPORT），
    需求分析处理字段澄清。dict_context 供 parse 复用。

    P2：改走 registry 通道（统一注册面），与 sql_graph 的 search_faq 同模式。
    """
    try:
        from app.tools.registry import registry
        dict_tools = registry.get(["search_interface_dictionary"])
        if not dict_tools:
            return False, ""
        raw = await asyncio.to_thread(
            dict_tools[0].invoke, {"query": query, "top_k": 5},
        )
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        matches = parsed.get("matches") or []
        context = "\n".join(
            f"- {m.get('source', '')}: {(m.get('text') or '')[:300]}" for m in matches
        )
        return bool(matches), context
    except Exception as exc:
        logger.warning("dictionary lookup failed: %s", exc)
        return False, ""


@traced_node("requirement_intent")
async def _classify_intent(state: RequirementAnalysisState) -> dict:
    """工作流式意图分类：闲聊快路径 → 字典检索（外部接口）→ LLM 兜底。"""
    q = state["user_query"]
    # 闲聊快路径：不查字典、不跑 LLM（省 token/延迟）。
    if any(k in q.lower() for k in _CHITCHAT_KEYWORDS):
        _progress_detail("识别意图", "闲聊（关键词命中，不建需求卡）")
        return {"intent": IntentKind.CHITCHAT.value, "intent_reason": "闲聊关键词命中", "dict_context": ""}
    dict_hit, dict_context = await _fetch_dict_context(q)
    res = classify_intent(q, dict_hit=dict_hit)
    _progress_detail(
        "识别意图",
        f"意图={res.kind.value}（{res.reason}）" + ("｜字典命中" if dict_hit else ""),
    )
    return {
        "intent": res.kind.value,
        "intent_reason": res.reason,
        "dict_context": dict_context,
    }


@traced_node("requirement_casual")
def _casual_reply(state: RequirementAnalysisState) -> dict:
    """闲聊：返回友好回复，不建需求卡、不跑需求解析。"""
    return {
        "casual_reply": (
            "你好！我可以帮你分析数据库里的销售、退货、库存、考勤等数据，"
            "或查询外部接口/实时数据源的字段含义。直接告诉我你想看什么就行。"
        ),
        "execution_status": "SUCCESS",
    }


@traced_node("requirement_interface")
def _interface_requirement(state: RequirementAnalysisState) -> dict:
    """外部接口/实时数据：构造接口需求卡（确定性，不经 LLM）。

    卡上 assumption `data_source:stream` 是确认流程的短路标记——确认后不生成 SQL，
    而走外部接口接入说明。
    """
    card = RequirementCard(
        id=str(uuid.uuid4()),
        version=1,
        status="complete",
        summary="此查询涉及外部实时接口/数据源数据，需接入实时数据源，非数据库报表。",
        target_metrics=[],
        scope=[],
        dimensions=[],
        analysis_methods=["实时数据接入"],
        assumptions=[
            RequirementAssumption(
                key="data_source:stream",
                text="数据来自外部实时接口，需接入数据源后取数；非数据库星型模型报表。",
                accepted=None,
            )
        ],
        missing_fields=[],
        confidence=0.8,
    )
    return {"requirement_card": card, "execution_status": "RUNNING"}


@traced_node("requirement_data_agent")
async def _data_agent(state: RequirementAnalysisState) -> dict:
    """Schema-only discovery. The `data_graph` already uses the schema
    tools; we wrap it here for tracing + state propagation.
    """
    data_graph = build_data_graph()
    ds = await data_graph.ainvoke({
        "user_query": state["user_query"],
        "discovered_tables": [],
        "mcp_tool_calls": [],
        "raw_schema": "",
        "trace_id": state.get("trace_id", ""),
    })
    schema = ds.get("schema_context")
    tables = getattr(schema, "tables", None) or []
    if tables:
        _progress_detail(
            "准备分析数据",
            f"检索到 {len(tables)} 张表：" + "、".join(
                str(getattr(t, "name", "") or "") for t in tables[:5]
            ),
        )
    else:
        _progress_detail(
            "准备分析数据",
            "schema 未连通（MCP 不可达或无需检索）→ 用本地 schema 兜底",
        )
    return {
        "schema_context": ds.get("schema_context"),
        "execution_status": "RUNNING",
    }


@traced_node("requirement_parse")
async def _requirement_parse(state: RequirementAnalysisState) -> dict:
    """LLM 解析 + 与受控选项合并。

    转 async 是为了接入分层对话上下文（build_session_context 需读 DB）；
    上下文构建失败时降级为空，绝不阻塞需求解析。需求卡落库仍在 persist_draft。

    字典上下文（dictionary_context）由 `_classify_intent` 程序化检索后存入 state，
    这里直接复用（避免二次检索）；任何失败降级为空串、绝不影响主流程；命中片段以
    "- source: text[:300]" 形式序列化，由 parse_requirement 拼到「数据字典参考」段。
    """
    conversation_context = ""
    assembled_context = ""
    # P4c Task 1: 真正接入 ContextRuntime.build() —— 替代 facade build_session_context；
    # 获取 conversation_context + assembled_context（含 selective recall 的全景 context）。
    # 失败降级为空，绝不阻塞需求解析（与原 try/except 语义一致）。
    try:
        from app.context.runtime import ContextRuntime  # 局部 import 避免 cycle / 测试 patch
        _user_id = state.get("user_id")
        try:
            _uid = int(_user_id) if _user_id not in (None, "") else 0
        except (TypeError, ValueError):
            _uid = 0
        from app.llm.config import LLMConfig

        _cfg = LLMConfig()
        _est_chars = len(state.get("user_query", "")) + _TYPICAL_CONTEXT_BUDGET_CHARS
        _remaining = max(0, _cfg.context_window - _est_chars // 4)
        _bundle = await ContextRuntime().build(
            session_id=state["session_id"],
            user_id=_uid,
            query=state.get("user_query", ""),
            agent="requirement_analyze",
            state_dict=dict(state),
            remaining_token_budget=_remaining,
        )
        conversation_context = _bundle["conversation_context"]
        assembled_context = _bundle["assembled_context"]
    except Exception as exc:
        logger.warning("ContextRuntime.build failed: %s", exc)

    dictionary_context = state.get("dict_context") or ""

    # supplement 轮把上一轮卡作为 prior_card 传给 parse_requirement——presence 覆盖 /
    # absence 继承；读失败降级为 None（不阻塞）。
    #
    # 2026-10-28 修正：此前只在 `status == "complete"` 时才继承，导致真实主线的常见
    # 路径失效——用户第一轮拿到需求卡后**还没点确认**就接着说「再看月度趋势」，
    # 上一轮的「2024年 / 华东」全部丢失，卡片变成空 → 时间范围被解析成"本月"
    # （实测 SQL 过滤到 2026-10，只回 1 行）。用户已经表达过的约束不该因为"没点确认"
    # 而消失，所以只要不是 locked（执行中）就继承。
    prior_card = None
    if state.get("mode") == "supplement":
        try:
            from app.services.requirement_service import get_latest_card

            _uid_raw = state.get("user_id")
            try:
                _uid = int(_uid_raw) if _uid_raw not in (None, "") else 0
            except (TypeError, ValueError):
                _uid = 0
            _prior = await get_latest_card(
                session_id=state["session_id"], user_id=_uid,
            )
            if _prior is not None and _prior.status != "locked":
                prior_card = _prior
        except Exception as exc:
            logger.warning("supplement: prior card load failed: %s", exc)

    card = parse_requirement(
        user_query=state["user_query"],
        schema_context=state.get("schema_context"),
        conversation_context=conversation_context or None,
        assembled_context=assembled_context or None,  # P4c: 含 recall 的全景 context
        dictionary_context=dictionary_context or None,
        prior_card=prior_card,
    )
    # 过程帧内容：这一段解析出的结论（时间/口径/待确认），让「解析需求」不只是四个字
    pending = "、".join(str(getattr(mf, "label", "") or "") for mf in (card.missing_fields or []))
    _progress_detail(
        "解析需求",
        "｜".join(
            part for part in (
                f"时间 {card.time_range}" if card.time_range else "时间未限",
                "口径 " + "、".join(card.target_metrics) if card.target_metrics else "口径待定",
                f"范围 {'、'.join(card.scope)}" if card.scope else "",
                f"待确认 {len(card.missing_fields)} 项（{pending[:40]}）" if card.missing_fields else "无待确认项",
            ) if part
        ),
    )
    return {"requirement_card": card}


@traced_node("requirement_persist_draft")
async def _persist_draft(state: RequirementAnalysisState) -> dict:
    """Write the parsed card to `agent.requirement_draft` in a transaction
    with the session pointer update.
    """
    card = state.get("requirement_card")
    if card is None:
        return {
            "execution_status": "FAILED",
            "error": ErrorDetail(code="NO_CARD", message="解析未生成需求卡"),
        }
    user_id = state.get("user_id", 0) or 0
    session_id = state["session_id"]
    pool = get_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                draft_id = await requirement_repository.create_draft(
                    conn,
                    session_id=session_id,
                    user_id=user_id,
                    user_query=state["user_query"],
                    card=card,
                )
                await conn.execute(
                    """UPDATE agent.session
                           SET latest_requirement_draft_id = $2,
                               current_phase = $3,
                               updated_at = NOW()
                         WHERE thread_id = $1""",
                    session_id,
                    draft_id,
                    "awaiting_missing" if card.missing_fields else "awaiting_confirm",
                )
    except Exception as exc:
        logger.exception("persist_draft failed")
        _progress_detail("保存需求卡", f"保存失败：{str(exc)[:60]}")
        return {
            "execution_status": "FAILED",
            "error": ErrorDetail(code="PERSIST_FAILED", message=str(exc)[:200]),
        }

    # End trace
    trace_id = state.get("trace_id", "")
    if trace_id:
        tracer = get_tracer(trace_id)
        tracer.end("AWAITING_CONFIRM" if not card.missing_fields else "AWAITING_MISSING")

    _progress_detail(
        "保存需求卡",
        f"已保存 #{draft_id} · {'需要补充' if card.missing_fields else '可直接确认'}"
        f"（{len(card.missing_fields)} 项待定）",
    )
    return {
        "draft_id": draft_id,
        "execution_status": "SUCCESS",
    }


@traced_node("requirement_preview")
async def _requirement_preview(state: RequirementAnalysisState) -> dict:
    """口径歧义时，给澄清选项补上**真实数值**（plan 2026-11-04-requirement-slot-contract Phase 2）。

    只在「卡上有 ≥2 个口径候选」时触发：把每个候选的当年总量算出来写进
    `RequirementOption.hint`，用户就能做识别题而不是填空题。

    契约边界（CLAUDE.md §4.1）：SQL 由 `metrics.yaml` 目录拼装（无 LLM、无自由列名），
    只服务澄清、不产出数据表；任何失败一律**静默退回纯文字选项**（不阻塞澄清、不显示假数）。
    """
    card = state.get("requirement_card")
    if card is None:
        return {}
    metric_field = next((mf for mf in card.missing_fields if mf.key == "metric"), None)
    if metric_field is None or len(metric_field.options) < 2:
        # 口径没有歧义（用户点名或已继承）→ 说明为什么没预览，避免面板出现空白步骤
        _progress_detail(
            "口径预览",
            "口径已确定（" + "、".join(card.target_metrics) + "），无需预览"
            if card.target_metrics else "口径没有多个候选，无需预览",
        )
        return {}

    from app.knowledge import registry as knowledge

    names = [
        opt.value.split(":", 1)[1]
        for opt in metric_field.options
        if opt.value.startswith("metric:")
    ]
    metrics = [m for m in (knowledge.resolve_metric(n) for n in names) if m is not None]
    if len(metrics) < 2:
        return {}

    from app.services.metric_preview_service import format_hint, preview_metrics

    result = await preview_metrics(
        metrics, time_range=card.time_range, scope=list(card.scope or []),
    )
    if not result.ok:
        _progress_detail("口径预览", "预览不可用（时间范围/表关系不支持）→ 退回纯文字选项")
        return {}

    by_name = {item.name: item for item in result.items}
    options = []
    for opt in metric_field.options:
        name = opt.value.split(":", 1)[1] if opt.value.startswith("metric:") else ""
        metric = knowledge.resolve_metric(name) if name else None
        item = by_name.get(name)
        hint = opt.hint
        if metric is not None and item is not None:
            hint = format_hint(item, metric, base_note=metric.note or opt.hint or "")
        options.append(opt.model_copy(update={"hint": hint}))
    # 过程帧内容：把几个候选的真实总量写出来（用户就是靠这个做选择）
    preview_parts = [
        f"{item.name} {format_hint(item, knowledge.resolve_metric(item.name), base_note='')}"
        for item in result.items
        if item.ok and knowledge.resolve_metric(item.name) is not None
    ]
    _progress_detail("口径预览", "、".join(preview_parts) or "已算出候选口径")
    new_field = metric_field.model_copy(update={"options": options})
    new_card = card.model_copy(update={
        "missing_fields": [
            new_field if mf.key == "metric" else mf for mf in card.missing_fields
        ],
    })
    return {"requirement_card": new_card}


# --- Graph build ----------------------------------------------------------


# --- Turn Understanding 链路（plan 2026-11-05-turn-understanding-agent） -------


def turn_understanding_enabled() -> bool:
    """`TURN_UNDERSTANDING=1` 时启用「强模型理解 + 确定性归并」链路，默认关（回退即旧路径）。"""
    return (os.getenv("TURN_UNDERSTANDING") or "").strip() in ("1", "true", "on", "yes")


@traced_node("turn_understand")
async def _turn_understand(state: RequirementAnalysisState) -> dict:
    """强模型理解这一轮（一次调用），产出 TurnPlan；失败返回空计划 → 回落旧解析。

    注意必须是 **async 节点**：任务状态存在 `agent.analysis_task_revision`，而 asyncpg
    连接池绑定在主事件循环上——在同步节点里跑 `asyncio.run` 会拿到另一个 loop，
    读写直接失败（2026-11-05 实测：任务 revision 一条都没落库，第二轮继承全丢）。
    """
    from app.agent.turn_understanding import understand_turn
    from app.infra.db.analysis_task_repository import load_latest_task

    prior = None
    try:
        prior = await load_latest_task(state.get("session_id", ""))
    except Exception as exc:  # noqa: BLE001 - 读历史失败不等于理解失败
        logger.warning("load_latest_task failed: %s", exc)

    digest = _schema_digest(state.get("schema_context"))
    plan, reasoning = understand_turn(
        state.get("user_query", ""), task=prior, history=None, schema_digest=digest,
    )
    _progress_detail(
        "理解意图",
        (
            f"对话行为 {plan.conversation_act}｜目标 "
            + "、".join(goal.type for goal in plan.analysis_goals)
            if plan is not None
            else "理解失败，回落通用解析"
        ),
    )
    # 2026-11-05：推理模型的思维链同步给前端（由 `_run_requirement_graph` 在图结束后
    # 作为一条额外的 trace 帧发出：步骤名「理解意图 · 思考」，正文是 CoT 原文）。
    # 只做观测：不参与判定，也不当作「对用户的解释」。
    return {"turn_plan": plan, "turn_reasoning": reasoning[:4000] if reasoning else ""}


@traced_node("task_reduce")
async def _task_reduce(state: RequirementAnalysisState) -> dict:
    """确定性归并任务状态 + 写一条 TaskRevision（审计）。模型不参与。"""
    from app.agent.turn_understanding import reduce_task
    from app.infra.db.analysis_task_repository import load_latest_task, save_revision

    plan = state.get("turn_plan")
    if plan is None:
        return {"analysis_task": None, "task_revision": None}
    prior = None
    try:
        prior = await load_latest_task(state.get("session_id", ""))
    except Exception:  # noqa: BLE001
        prior = None
    task, revision = reduce_task(prior, plan, user_query=state.get("user_query", ""))
    try:
        await save_revision(
            state.get("session_id", ""), state.get("user_id", 0), task, revision,
        )
    except Exception as exc:  # noqa: BLE001 - 审计写失败不阻塞主流程
        logger.warning("persist task revision failed: %s", exc)
    _progress_detail(
        "归并任务",
        f"第 {task.revision} 版｜" + ("、".join(revision.changes) if revision.changes else "无变更"),
    )
    return {"analysis_task": task, "task_revision": revision}


@traced_node("reportability")
def _reportability(state: RequirementAnalysisState) -> dict:
    """确定性判断「能不能出报告」：READY / NEEDS_CLARIFICATION / UNSUPPORTED。"""
    from app.agent.turn_understanding import assess_reportability
    from app.knowledge import registry as knowledge

    task = state.get("analysis_task")
    if task is None:
        return {"reportability": None}
    known = {metric.name for metric in knowledge.all_metrics()}
    verdict = assess_reportability(task, known_metrics=known)
    _progress_detail(
        "可报告性",
        f"{verdict.verdict}"
        + (f"｜待确认 {'、'.join(b.key for b in verdict.blockers)}" if verdict.blockers else ""),
    )
    return {"reportability": verdict}


@traced_node("requirement_project")
def _requirement_project(state: RequirementAnalysisState) -> dict:
    """ActiveAnalysisTask → RequirementCard（唯一执行契约），不再二次调模型。"""
    from app.agent.turn_understanding import project_card

    task = state.get("analysis_task")
    if task is None:
        return {"requirement_card": None}
    card = project_card(
        task,
        reportability=state.get("reportability"),
        summary=state.get("user_query", ""),
    )
    pending = "、".join(mf.label for mf in (card.missing_fields or []))
    _progress_detail(
        "投影需求卡",
        "｜".join(
            part for part in (
                f"时间 {card.time_range}" if card.time_range else "时间未限",
                "口径 " + "、".join(card.target_metrics) if card.target_metrics else "口径待定",
                f"范围 {'、'.join(card.scope)}" if card.scope else "",
                f"待确认 {len(card.missing_fields)} 项（{pending[:40]}）" if card.missing_fields else "无待确认项",
            ) if part
        ),
    )
    return {"requirement_card": card}


def _route_after_understand(state: RequirementAnalysisState) -> str:
    """理解成功 → 归并/裁决/投影；失败 → 回落既有 requirement_parse（不猜）。"""
    return "task_reduce" if state.get("turn_plan") is not None else "requirement_parse"


def _schema_digest(schema_context) -> str:
    """Schema → 给理解器的短摘要（表名 + 字段名），避免把整份 DDL 塞进 prompt。"""
    if schema_context is None:
        return ""
    try:
        tables = getattr(schema_context, "tables", None) or []
        lines: list[str] = []
        for table in tables[:8]:
            name = getattr(table, "name", "") or getattr(table, "table_name", "")
            columns = getattr(table, "columns", None) or []
            column_names = [
                getattr(column, "name", "") or getattr(column, "column_name", "")
                for column in columns[:12]
            ]
            lines.append(f"- {name}: {', '.join(c for c in column_names if c)}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001 - 摘要失败就当没有
        return ""


def build_requirement_analysis_graph():

    """Compile and return the requirement-analysis graph.

    Checkpointer comes from `get_checkpointer()`：开发环境是 MemorySaver
    （便于 notebook 单步），非开发环境是 AsyncPostgresSaver（checkpoint
    落 PG、跨重启持久）。见 app/infra/checkpoint/factory.py。
    """
    workflow = StateGraph(RequirementAnalysisState)

    workflow.add_node("security_guard", _security_guard)
    workflow.add_node("classify_intent", _classify_intent)
    workflow.add_node("casual_reply", _casual_reply)
    workflow.add_node("interface_requirement", _interface_requirement)
    workflow.add_node("data_agent", _data_agent)
    workflow.add_node("requirement_parse", _requirement_parse)
    # 2026-11-05（plan turn-understanding-agent）：强模型理解 + 确定性归并 + 可报告性裁决。
    # 由 `TURN_UNDERSTANDING=1` 启用；关着时图结构与原来完全一致（回退即旧路径）。
    if turn_understanding_enabled():
        workflow.add_node("turn_understand", _turn_understand)
        workflow.add_node("task_reduce", _task_reduce)
        workflow.add_node("reportability", _reportability)
        workflow.add_node("requirement_project", _requirement_project)
    # 2026-11-04：口径歧义时给澄清选项补真实数值（不修改语义、失败即跳过）
    workflow.add_node("requirement_preview", _requirement_preview)
    workflow.add_node("persist_draft", _persist_draft)

    workflow.set_entry_point("security_guard")
    workflow.add_conditional_edges("security_guard", _route_security)
    workflow.add_conditional_edges("classify_intent", _route_intent)
    if turn_understanding_enabled():
        workflow.add_edge("data_agent", "turn_understand")
        workflow.add_conditional_edges("turn_understand", _route_after_understand)
        workflow.add_edge("task_reduce", "reportability")
        workflow.add_edge("reportability", "requirement_project")
        workflow.add_edge("requirement_project", "requirement_preview")
    else:
        workflow.add_edge("data_agent", "requirement_parse")
        workflow.add_edge("requirement_parse", "requirement_preview")
    workflow.add_edge("requirement_preview", "persist_draft")
    workflow.add_edge("interface_requirement", "persist_draft")
    workflow.add_edge("casual_reply", END)
    workflow.add_edge("persist_draft", END)

    return workflow.compile(checkpointer=get_checkpointer())
