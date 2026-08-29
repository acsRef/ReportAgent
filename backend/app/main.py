from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).parent.parent.parent / ".env"
load_dotenv(dotenv_path=str(_ENV_PATH))

_llm_key = os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY")
if _llm_key and not os.getenv("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = _llm_key

logger = logging.getLogger(__name__)
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.agent.sql_graph import ChatCard
from app.agent.requirement_analysis_graph import build_requirement_analysis_graph
from app.agent.confirmed_execution_graph import (
    build_confirmed_execution_graph,
    ConfirmedExecutionState,
    RequirementIncompleteError,
    SecurityRejectedError,
    SessionNotFoundError,
)
from app.agent.security_guard import SecurityGuard
from app.api.templates import router as templates_router
from app.api.observability import router as observability_router
from app.utils.pii import mask_pii

# ===========================================================================
# LEGACY BRIDGE BEGIN — mode=legacy 专属引用。禁止在此区块外 import app.legacy.*，
# 禁止向此区块新增条目（Phase 15 整体删除）。见 docs/architecture/* 与
# docs/plans/2026-08-26-p1-architecture-freeze.md 决策 2。
from app.legacy.agents.parent_graph import build_parent_graph
# LEGACY BRIDGE END
# ===========================================================================
from app.infra.db.postgres import init_pool, close_pool, start_pool_monitor, stop_pool_monitor
from app.tools.mcp_faq_client import close_mcp_faq_client
from app.infra.checkpoint.factory import init_checkpointer, close_checkpointer
from app.infra.checkpoint.session import session_manager
from app.infra.execution import registry
from app.infra.trace.sdk import get_tracer
from app.infra.auth.repository import ensure_default_user, verify_user
from app.infra.auth.startup_guard import validate_auth_security_config
from app.infra.auth.jwt import create_token
from app.infra.auth.deps import get_current_user
from app.infra.conversation.repository import save_message, get_messages, list_sessions
from app.models.contracts import ErrorDetail, LoginRequest, RegisterRequest, PatchRequirementRequest
from app.models.requirement import RequirementCard
from app.services import (
    requirement_service,
    report_version_service,
    snapshot_service,
)
from app.infra.db import report_version_repository

VECTOR_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))


# ---------------------------------------------------------------------------
# SSE error-event helper
# ---------------------------------------------------------------------------
#
# All confirmed-execution / adjust failures funnel through this so the
# front-end gets a structured `kind` + the SQL the agent actually tried
# (≤200 chars). Without this the front-end has to guess why a query
# failed, and silent failures (DB unreachable returned as "no data")
# become indistinguishable from legitimate empty results.
_ERROR_FRIENDLY: dict[str, str] = {
    "timeout":    "查询超时,请缩小时间范围或维度后重试",
    "connection": "数据库连接失败,请稍后重试",
    "permission": "权限不足,无法执行该查询",
    "syntax":     "SQL 语法错误,请调整查询条件后重试",
    "object":     "查询引用的表/列不存在,请检查维度后重试",
    "other":      "查询执行失败,请稍后重试或调整需求",
}
_ERROR_CODE: dict[str, str] = {
    "timeout":    "QUERY_TIMEOUT",
    "connection": "QUERY_CONNECTION",
    "permission": "QUERY_PERMISSION",
    "syntax":     "QUERY_SYNTAX",
    "object":     "QUERY_OBJECT",
    "other":      "QUERY_FAILED",
}


def _normalize_sql_snippet(sql: str | None, limit: int = 200) -> str:
    """Flatten newlines + clamp to `limit` characters with an ellipsis.

    SQL in error events is read by the agent (to retry with the same
    params) AND by humans. Keeping it short + single-line avoids breaking
    SSE framing and the front-end toast / card layout.
    """
    if not sql:
        return ""
    snippet = " ".join(sql.strip().split())
    if len(snippet) > limit:
        snippet = snippet[: limit - 1] + "…"
    return snippet


def _build_sse_error(
    err: dict | None,
    sql: str | None,
    failed_action: str,
) -> dict:
    """Compose a structured SSE `error` event from a backend ErrorDetail.

    `err` may be an ErrorDetail dict (with `kind` + `message`) or None;
    when None we default to kind='other' / code='QUERY_FAILED'.

    The SQL snippet is appended to message so the front-end ErrorCard can
    show it in a collapsible section without losing context if the user
    only reads the toast.
    """
    err_dict: dict = err if isinstance(err, dict) else {}
    kind = err_dict.get("kind") or "other"
    if kind not in _ERROR_FRIENDLY:
        kind = "other"
    code = _ERROR_CODE[kind]
    # Always use the friendly mapping — the raw PG message (often in
    # English / opaque) is kept in trace logs but never shown to end
    # users. The point of the helper is to make every kind produce a
    # distinct, actionable sentence in Chinese.
    base_message = _ERROR_FRIENDLY[kind]
    snippet = _normalize_sql_snippet(sql)
    message = base_message if not snippet else f"{base_message}\n尝试的 SQL: {snippet}"
    return {
        "event": "error",
        "data": json.dumps({
            "code": code,
            "message": message,
            "recoverable": kind in ("timeout", "connection", "object", "other"),
            "failed_action": failed_action,
            "kind": kind,
            "sql": snippet,
        }, ensure_ascii=False),
    }


# ---------------------------------------------------------------------------
# 后台执行：graph 从 SSE 响应解耦成独立任务（「后台跑完」语义）
# ---------------------------------------------------------------------------
# 客户端断连只会取消 _subscribe_events（SSE 响应任务）；后台任务由
# asyncio.create_task 独立运行、不受影响，继续跑到 persist_report 落库。
# 事件契约与改造前的 SSE event_generator 逐事件一致（report | error → done）。

_DEFAULT_ERROR_EVENTS = [
    {
        "event": "error",
        "data": json.dumps({
            "code": "INTERNAL",
            "message": "后台任务执行失败，请稍后重试",
            "recoverable": False,
            "failed_action": "confirm",
        }, ensure_ascii=False),
    },
    {"event": "done", "data": json.dumps({"final_phase": "error"}, ensure_ascii=False)},
]


def _confirmed_initial(
    session_id: str,
    user_id: int,
    *,
    user_query: str = "",
    base_report_version: int | None = None,
    adjustment_text: str | None = None,
) -> ConfirmedExecutionState:
    """构造 ConfirmedExecutionState，并抢占创建带身份的 tracer。

    必须在 SSE 返回前调用——initial / trace_id 归属于后台任务，不依赖连接存活。
    """
    trace_id = str(uuid.uuid4())
    get_tracer(trace_id, session_id=session_id, user_id=user_id, user_query=user_query)
    return {
        "user_query": user_query,
        "user_id": user_id,
        "session_id": session_id,
        "trace_id": trace_id,
        "requirement_card": None,
        "base_report_version": base_report_version,
        "adjustment_text": adjustment_text,
        "schema_context": None,
        "query_result": None,
        "report_payload": None,
        "execution_status": "RUNNING",
        "error": None,
    }


async def _run_confirmed_graph(
    task: registry.ConfirmedTask,
    graph,
    initial: ConfirmedExecutionState,
    session_id: str,
    failed_action: str,
) -> None:
    """后台执行确认图：跑完 → 事件序列写入 task → 唤醒订阅者。

    session phase 由本任务在结束时写入：成功补 report_ready（现状成功路径
    不更新 current_phase，前端轮询依赖此值），失败写 error + failed_action。
    """
    final_phase = "report_ready"
    events: list[dict] = []
    try:
        config = {"configurable": {"thread_id": session_id}}
        result = await graph.ainvoke(initial, config)
        status = result.get("execution_status", "FAILED")
        if status == "FAILED":
            final_phase = "error"
            await session_manager.update_phase(session_id, "error", failed_action="sql")
            events.append(_build_sse_error(result.get("error"), result.get("sql"), "sql"))
        else:
            report_payload = result.get("report_payload") or {}
            events.append({
                "event": "report",
                "data": json.dumps(report_payload, ensure_ascii=False, default=str),
            })
            await session_manager.update_phase(session_id, "report_ready")
    except SecurityRejectedError as exc:
        final_phase = "error"
        await session_manager.update_phase(session_id, "error", failed_action=failed_action)
        events.append({
            "event": "error",
            "data": json.dumps({
                "code": "SECURITY_REJECTED",
                "message": str(exc)[:300],
                "recoverable": False,
                "failed_action": failed_action,
            }, ensure_ascii=False),
        })
    except RequirementIncompleteError as exc:
        final_phase = "error"
        await session_manager.update_phase(session_id, "error", failed_action=failed_action)
        events.append({
            "event": "error",
            "data": json.dumps({
                "code": "REQUIREMENT_INCOMPLETE",
                "message": str(exc)[:300],
                "recoverable": True,
                "failed_action": failed_action,
            }, ensure_ascii=False),
        })
    except SessionNotFoundError as exc:
        final_phase = "error"
        events.append({
            "event": "error",
            "data": json.dumps({
                "code": "SESSION_NOT_FOUND",
                "message": str(exc)[:300],
                "recoverable": False,
                "failed_action": failed_action,
            }, ensure_ascii=False),
        })
    except Exception as exc:
        final_phase = "error"
        await session_manager.update_phase(session_id, "error", failed_action=failed_action)
        events.append({
            "event": "error",
            "data": json.dumps({
                "code": "INTERNAL",
                "message": str(exc)[:300],
                "recoverable": False,
                "failed_action": failed_action,
            }, ensure_ascii=False),
        })
    finally:
        events.append({
            "event": "done",
            "data": json.dumps({"final_phase": final_phase}, ensure_ascii=False),
        })
        registry.complete(task, events)
        tracer = get_tracer(initial.get("trace_id", ""))
        await tracer.flush()


async def _subscribe_events(
    task: registry.ConfirmedTask,
    phase_label: str,
) -> AsyncGenerator[dict, None]:
    """SSE 订阅后台任务：已完成 → 重放 result；未完成 → phase + 等完成信号。

    客户端断连 → CancelledError 在此自然传播，无需清理：session phase 由
    后台任务在完成时写入，任务不因连接断开而中断。
    """
    if task.finished:
        for evt in task.result or _DEFAULT_ERROR_EVENTS:
            yield evt
        return
    yield {
        "event": "phase",
        "data": json.dumps({"phase": phase_label}, ensure_ascii=False),
    }
    await task.events.get()
    for evt in task.result or _DEFAULT_ERROR_EVENTS:
        yield evt


def _start_confirmed_stream(
    session_id: str,
    user_id: int,
    kind: str,
    graph,
    initial: ConfirmedExecutionState,
    *,
    failed_action: str,
    phase_label: str,
) -> EventSourceResponse:
    """启动后台任务并返回订阅其完成的 SSE 流。重入 → 409 SESSION_BUSY。"""
    try:
        task = registry.start_confirmed_task(
            session_id, user_id, kind,
            lambda t: _run_confirmed_graph(t, graph, initial, session_id, failed_action),
        )
    except registry.BusyError as exc:
        raise HTTPException(status_code=409, detail=f"SESSION_BUSY: {exc}")
    return EventSourceResponse(_subscribe_events(task, phase_label))


async def _check_embedding_dimension():
    """Verify embedding output dimension matches DB vector dimension on startup."""
    from app.embedding.service import get_embedder
    try:
        embedder = get_embedder()
        test_vec = await embedder.embed("test")
        actual_dim = len(test_vec)
        if actual_dim != VECTOR_DIM:
            raise RuntimeError(
                f"Embedding dimension mismatch: DB expects VECTOR({VECTOR_DIM}), "
                f"but embedding model outputs {actual_dim} dimensions. "
                f"Update init_pg.sql or VECTOR_DIM to match."
            )
        logger.info("Embedding dimension verified: %s == %s", actual_dim, VECTOR_DIM)
    except Exception as exc:
        logger.warning("Could not verify embedding dimension: %s", exc)
        logger.warning("Vector search in QueryMemory will use keyword fallback.")

_agent = None
_server_start_time = datetime.datetime.now()

# C-1/B-8: legacy 模式所有请求共享一个模块全局 _agent + MemorySaver，按
# thread_id=session_id 分桶。同一 session_id 的两个并发请求会互相覆盖
# update_state/get_state（chosen_tool、clarification_history 跨流污染）。
# 用按 session 的 asyncio.Lock 把同 session 的 legacy 流串行化：既消除竞态，
# 又保留共享 MemorySaver 的跨请求 checkpoint（clarify interrupt 连续性不破坏）。
# 不同 session 用不同锁，互不阻塞。锁随进程生命周期保留（与 MemorySaver 同寿命）。
# 彻底的生产方案是 PostgresSaver（独立 PR）。
# ===========================================================================
# LEGACY (mode=legacy 专属 helper) — _chat_legacy / _format_event /
# _build_response / _legacy_lock / _VALID_CHOSEN_TOOLS 只服务旧 2-stage
# interrupt 流。禁止新代码引用；Phase 15 整体删除。
# 见 docs/architecture/* 与 docs/plans/2026-08-26-p1-architecture-freeze.md 决策 2。
# ===========================================================================
_legacy_session_locks: dict[str, asyncio.Lock] = {}


def _legacy_lock(session_id: str) -> asyncio.Lock:
    lock = _legacy_session_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _legacy_session_locks[session_id] = lock
    return lock


class ChatRequest(BaseModel):
    user_query: str
    session_id: str | None = None
    chosen_tool: str | None = None
    metadata: dict | None = None
    mode: str = "new"
    base_report_version: int | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    # B-1 安全闸：fail-closed。不安全默认配置（公开 JWT_SECRET / admin123）在非开发
    # 环境必须让进程「显式启动失败」，而不是带着远程认证绕过漏洞继续运行。
    # 必须早于 init_pool / ensure_default_user——让错误在最早阶段暴露。
    validate_auth_security_config()
    await init_pool()
    start_pool_monitor()
    await ensure_default_user()
    await _check_embedding_dimension()
    # Checkpointer 单例（dev=MemorySaver / 非 dev=AsyncPostgresSaver）：必须先于
    # build_parent_graph，让 legacy 图拿到正确的 checkpointer。非开发环境顺带
    # 建 langgraph checkpoint 表。
    await init_checkpointer()
    _agent = build_parent_graph()
    yield
    await close_checkpointer()
    stop_pool_monitor()
    close_mcp_faq_client()
    await close_pool()


app = FastAPI(
    title="ReportAgent API",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(templates_router)
app.include_router(observability_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/v1/auth/login")
async def login(request: LoginRequest):
    user = await verify_user(request.username, request.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_token(user["id"], user["username"])
    return {"access_token": token, "user_id": user["id"], "username": user["username"]}


@app.post("/api/v1/auth/register")
async def register(request: RegisterRequest):
    from app.infra.auth.repository import verify_user, get_user_by_id
    from app.infra.db.postgres import get_pool
    import hashlib
    pool = get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM app.users WHERE username = $1", request.username
        )
        if existing:
            raise HTTPException(status_code=409, detail="Username already exists")
        pw_hash = hashlib.sha256(request.password.encode()).hexdigest()
        row = await conn.fetchrow(
            "INSERT INTO app.users (username, password_hash) VALUES ($1, $2) RETURNING id",
            request.username, pw_hash,
        )
        token = create_token(row["id"], request.username)
        return {"access_token": token, "user_id": row["id"], "username": request.username}


@app.get("/api/v1/sessions")
async def get_sessions(
    user: dict = Depends(get_current_user),
    limit: int = 30,
    offset: int = 0,
):
    """List current user's sessions, paginated.

    Pagination: `limit` caps response size (default 30; UI uses default for
    initial SessionRail load). `offset` is for the "load more" button. The
    UI can use response size < limit as a "no more" sentinel without needing
    a separate count endpoint.
    """
    sessions = await list_sessions(user["id"], limit=limit, offset=offset)
    return {"sessions": sessions}


@app.get("/api/v1/conversations/{session_id}")
async def get_conversation(session_id: str, user: dict = Depends(get_current_user)):
    messages = await get_messages(session_id, user["id"])
    return {"messages": messages}


async def _require_session_owner(session_id: str | None, user_id: int) -> None:
    """A-2：/chat 入口的会话归属校验，堵 resume 他人会话的 IDOR。

    三分支：
    - session_id 为空或会话不存在 → 放行（新会话合法创建）；
    - 会话存在且 user_id 匹配 → 放行；
    - 会话存在但属于他人 → 404 SESSION_NOT_FOUND，与 PATCH/confirm/retry 端点一致。

    在 SSE 流开始前抛出，前端收到标准 HTTP 404（而不是流内 error 事件）。
    """
    if not session_id:
        return
    sess = await session_manager.get_session(session_id)
    if sess is not None and sess.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND")


@app.post("/api/v1/chat")
async def chat(request: ChatRequest, req: Request, user: dict = Depends(get_current_user)):
    """Conversational Workbench v2 entry.

    mode=new        → requirement-analysis (parse + persist draft, no SQL)
    mode=supplement → requirement-analysis (with prior card context)
    mode=adjust     → confirmed-execution (load latest draft, generate v2/v3)
    mode=legacy     → original 2-stage interrupt + chosen_tool flow
    """
    # v2 修订：PII 脱敏——入口统一 mask，使手机号/邮箱/身份证不进 prompt/trace/
    # conversations/report_version（对所有 mode 生效，含 legacy）。
    request.user_query = mask_pii(request.user_query or "")

    # A-2：v2 与 legacy 共用本入口，mode 分发前统一校验会话归属，
    # 防止 resume 他人 legacy checkpoint / 写他人会话（IDOR）。
    await _require_session_owner(request.session_id, user["id"])

    # Legacy flow keeps the old behaviour for backward compatibility
    if request.mode == "legacy":
        return await _chat_legacy(request, req, user)

    session_id = request.session_id or str(uuid.uuid4())

    existing = await session_manager.get_session(session_id)
    if existing and existing.get("last_checkpoint_at"):
        cp_time = existing["last_checkpoint_at"]
        if isinstance(cp_time, datetime.datetime) and cp_time < _server_start_time:
            existing = None
    is_new = existing is None

    if is_new:
        await session_manager.create_session(session_id, user_id=user["id"])

    await save_message(session_id, user["id"], "user", request.user_query, "text")

    if request.mode in ("new", "supplement"):
        return await _chat_requirement_analysis(
            request, req, user, session_id=session_id,
        )
    # mode=adjust (and any future 'execute' alias) → confirmed-execution
    return await _chat_confirmed_execution(
        request, req, user, session_id=session_id,
    )


async def _chat_requirement_analysis(
    request: ChatRequest,
    req: Request,
    user: dict,
    *,
    session_id: str,
):
    """SSE stream for requirement-analysis mode."""
    trace_id = str(uuid.uuid4())
    # A-3：trace 起点抢占创建带身份的 tracer，避免 traced_node 先建出无主 tracer。
    get_tracer(trace_id, session_id=session_id, user_id=user["id"],
               user_query=request.user_query)
    graph = build_requirement_analysis_graph()
    config = {"configurable": {"thread_id": session_id}}

    async def event_generator() -> AsyncGenerator[dict, None]:
        try:
            initial: dict = {
                "user_query": request.user_query,
                "user_id": user["id"],
                "session_id": session_id,
                "trace_id": trace_id,
                "schema_context": None,
                "requirement_card": None,
                "draft_id": None,
                "security_score": 0,
                "security_level": "LOW",
                "security_warning": "",
                "error": None,
                "execution_status": "RUNNING",
            }
            yield {
                "event": "phase",
                "data": json.dumps({"phase": "parsing"}, ensure_ascii=False),
            }
            result = await graph.ainvoke(initial, config)

            if result.get("security_level") == "HIGH":
                yield {
                    "event": "error",
                    "data": json.dumps({
                        "code": "SECURITY_REJECTED",
                        "message": "请求被安全规则拦截",
                        "recoverable": False,
                        "failed_action": request.mode,
                    }, ensure_ascii=False),
                }
                return

            # 闲聊意图：直接返回文本回复，不建需求卡、不进确认流程。
            if result.get("intent") == "chitchat":
                casual = result.get("casual_reply") or "你好！有什么可以帮你的？"
                yield {
                    "event": "report",
                    "data": json.dumps({"answer": {"text": casual}}, ensure_ascii=False),
                }
                return

            card = result.get("requirement_card")
            phase = (
                "awaiting_missing"
                if (card and card.missing_fields)
                else "awaiting_confirm"
            )
            yield {
                "event": "phase",
                "data": json.dumps({"phase": phase}, ensure_ascii=False),
            }
            if card is not None:
                yield {
                    "event": "requirement",
                    "data": json.dumps(
                        card.model_dump(mode="json"),
                        ensure_ascii=False,
                    ),
                }
        except asyncio.CancelledError:
            # 断连：ClientDisconnected 取消响应任务。需求分析不产生落库副作用，
            # 无需清理，直接透传完成取消（不产生半截 done 事件）。
            raise
        except Exception as exc:
            yield {
                "event": "error",
                "data": json.dumps({
                    "code": "INTERNAL",
                    "message": str(exc)[:300],
                    "recoverable": False,
                    "failed_action": request.mode,
                }, ensure_ascii=False),
            }
        finally:
            tracer = get_tracer(trace_id)
            await tracer.flush()
            yield {
                "event": "done",
                "data": json.dumps({"final_phase": phase if "phase" in dir() else "error"}, ensure_ascii=False),
            }

    return EventSourceResponse(event_generator())


async def _chat_confirmed_execution(
    request: ChatRequest,
    req: Request,
    user: dict,
    *,
    session_id: str,
):
    """SSE stream for confirmed-execution (mode=adjust).

    后台化：graph 执行由 ExecutionRegistry 的独立任务完成，客户端断连只断
    本流的前端渲染，任务继续跑到 persist_report 落库（「后台跑完」语义）。
    """
    graph = build_confirmed_execution_graph()
    initial = _confirmed_initial(
        session_id, user["id"],
        user_query=request.user_query,
        base_report_version=request.base_report_version,
        adjustment_text=request.user_query,
    )
    return _start_confirmed_stream(
        session_id, user["id"], "adjust", graph, initial,
        failed_action="adjust", phase_label="adjusting",
    )


# A-6：chosen_tool 后端白名单——与 IntentOption.tool 的 5 个 Literal 值一致。
# chosen_tool 从 body 直取，此前无校验（当前仅作 _plan 的 prompt hint）。
_VALID_CHOSEN_TOOLS = frozenset({
    "group_compare", "trend_analysis", "detect_anomaly",
    "chart_advisor", "insight_analyst",
})


async def _chat_legacy(
    request: ChatRequest,
    req: Request,
    user: dict,
):
    """Original 2-stage interrupt + chosen_tool flow. Kept on
    /api/v1/chat?mode=legacy for backward compatibility. Phase 8 of the
    workbench plan decides whether to retire this; for now it is
    unchanged from the prior implementation.
    """
    global _agent
    if _agent is None:
        _agent = build_parent_graph()
    session_id = request.session_id or str(uuid.uuid4())

    existing = await session_manager.get_session(session_id)
    if existing and existing.get("last_checkpoint_at"):
        cp_time = existing["last_checkpoint_at"]
        if isinstance(cp_time, datetime.datetime) and cp_time < _server_start_time:
            existing = None
    is_new = existing is None

    if is_new:
        await session_manager.create_session(session_id, user_id=user["id"])

    await save_message(session_id, user["id"], "user", request.user_query, "text")

    config = {
        "configurable": {"thread_id": session_id},
    }

    async def event_generator() -> AsyncGenerator[dict, None]:
        trace_id = str(uuid.uuid4())
        # A-3：legacy trace 起点同样 priming 身份。
        get_tracer(trace_id, session_id=session_id, user_id=user["id"],
                   user_query=request.user_query)
        report_result = None
        error_result = None
        clarify_result = None
        pending_card: ChatCard | None = None
        # C-1: 串行化同 session 的 legacy 流，消除共享 MemorySaver 的跨请求污染。
        await _legacy_lock(session_id).acquire()
        try:
            input_data = {
                "user_query": request.user_query,
                "original_query": request.user_query,
                "current_query": request.user_query,
                "clarification_history": [],
                "session_id": session_id,
                "user_id": user["id"],
                "intent": "",
                "memory_context": "",
                "schema_context": None,
                "query_plan": None,
                "query_result": None,
                "report_spec": None,
                "chart_config": {},
                "insight": "",
                "execution_status": "RUNNING",
                "error": None,
                "trace_id": trace_id,
                "active_sub_agent": "",
                "clarification_context": {},
                "retry_count": 0,
                "security_score": 0,
                "security_level": "LOW",
                "security_warning": "",
                "pending_card": None,
                "cards": [],
            } if is_new else None

            # A-6：非法 chosen_tool 记 warning 并置 None（回退 stage-1 intent
            # card）。防御纵深，不 4xx——不改变前端契约。
            if request.chosen_tool and request.chosen_tool not in _VALID_CHOSEN_TOOLS:
                logger.warning(
                    "unknown chosen_tool=%r rejected; falling back to intent card",
                    request.chosen_tool,
                )
                request.chosen_tool = None

            # If the user already chose an intent tool (round-2 of the
            # intent_card flow), inject it into the LangGraph state before
            # resuming. `_run_sql_agent` reads `chosen_tool` from state.
            # We inject for BOTH new and existing sessions — `update_state`
            # is idempotent (last write wins on subsequent astream_events
            # input).
            if request.chosen_tool:
                try:
                    _agent.update_state(config, {"chosen_tool": request.chosen_tool})
                    snapshot = _agent.get_state(config)
                    cur = (snapshot.values or {}).get("chosen_tool") if snapshot else None
                    logger.info("chosen_tool=%r injected; state.chosen_tool=%r", request.chosen_tool, cur)
                except Exception as e:
                    logger.warning("update_state(chosen_tool) failed: %s", e)

            async for event in _agent.astream_events(
                input_data if is_new else None,
                config, version="v2"
            ):
                if await req.is_disconnected():
                    break
                ev = _format_event(event)
                if ev:
                    yield ev

            interrupted = False
            try:
                snapshot = _agent.get_state(config)
                if snapshot and snapshot.next:
                    for task in snapshot.tasks:
                        val = getattr(task, 'interrupt', None)
                        if val is not None:
                            q = val.get("question", "") if isinstance(val, dict) else str(val)
                            clarify_result = q
                            yield {"event": "clarify", "data": json.dumps({"question": q}, ensure_ascii=False)}
                            interrupted = True
                            break
            except Exception:
                logger.warning("Could not check graph state for interrupts")

            # Read final state to: (a) emit any pending chat card produced by
            # the pre-SQL clarification feature, and (b) build the final report.
            snapshot = _agent.get_state(config)
            final_state = snapshot.values if snapshot else None
            if final_state:
                raw_card = final_state.get("pending_card")
                if raw_card is not None:
                    card_json = raw_card.model_dump() if hasattr(raw_card, "model_dump") else dict(raw_card)
                    yield {
                        "event": "card",
                        "data": json.dumps(card_json, ensure_ascii=False),
                    }
                    pending_card = raw_card

            if not interrupted:
                if final_state:
                    # Stage-1 intent card pauses here — do NOT emit report.
                    if final_state.get("execution_status") == "INTENT_AWAIT":
                        logger.info("intent_card emitted; awaiting user choice")
                        await session_manager.update_checkpoint_time(session_id)
                    elif final_state.get("execution_status") == "FAILED":
                        # P-6: legacy 失败不能再走 _build_response 发「查询完成」假报告。
                        # 发结构化 error 事件，前端据此显示错误而非空表格。
                        err = final_state.get("error")
                        if isinstance(err, ErrorDetail):
                            msg = err.message
                        elif isinstance(err, dict):
                            msg = err.get("message", "")
                        else:
                            msg = str(err) if err else "查询执行失败"
                        error_result = msg
                        yield {
                            "event": "error",
                            "data": json.dumps({
                                "code": "QUERY_FAILED",
                                "message": (msg or "查询执行失败")[:300],
                                "recoverable": False,
                                "failed_action": "legacy",
                            }, ensure_ascii=False),
                        }
                        await session_manager.update_checkpoint_time(session_id)
                    else:
                        result = _build_response(final_state)
                        report_result = result
                        yield {"event": "report", "data": json.dumps(result, ensure_ascii=False)}
                        await session_manager.update_checkpoint_time(session_id)

        except Exception as exc:
            error_result = str(exc)
            yield {"event": "error", "data": str(exc)}
        finally:
            tracer = get_tracer(trace_id)
            await tracer.flush()
            if clarify_result:
                await save_message(session_id, user["id"], "assistant", clarify_result, "clarify")
            elif report_result:
                await save_message(session_id, user["id"], "assistant", json.dumps(report_result, ensure_ascii=False), "report")
            elif error_result:
                await save_message(session_id, user["id"], "assistant", error_result, "error")
            _legacy_lock(session_id).release()

        yield {"event": "done", "data": ""}

    return EventSourceResponse(event_generator())


def _format_event(event: dict) -> dict:
    kind = event.get("event")
    metadata = event.get("metadata", {}) or {}
    node = metadata.get("langgraph_node", "")

    if kind == "on_chat_model_stream":
        # All LLM calls in this architecture are internal (planning, SQL
        # generation, clarification). The final output is always sent as a
        # structured event (report, clarify, error). Streaming LLM reasoning
        # as tokens only confuses the user — suppress all token events.
        node = metadata.get("langgraph_node", "")
        if node in ("report_agent", "report_plan_analysis", "report_run_step", "report_build_output"):
            chunk = event.get("data", {}).get("chunk", {})
            content = ""
            if hasattr(chunk, "content"):
                content = chunk.content
            elif isinstance(chunk, dict):
                content = chunk.get("content", "")
            elif isinstance(chunk, str):
                content = chunk
            if content:
                return {
                    "event": "token",
                    "data": json.dumps({"text": content}, ensure_ascii=False),
                }
        return None

    elif kind == "on_tool_start":
        return {
            "event": "trace",
            "data": json.dumps({
                "step": f"工具调用: {event.get('name', '')}",
                "status": "running",
                "detail": str(event.get("data", {}).get("input", ""))[:200],
            }, ensure_ascii=False),
        }

    elif kind == "on_tool_end":
        return {
            "event": "trace",
            "data": json.dumps({
                "step": f"工具完成: {event.get('name', '')}",
                "status": "success",
                "detail": str(event.get("data", {}).get("output", ""))[:200],
            }, ensure_ascii=False),
        }

    elif kind == "on_chain_start":
        node_name = event.get("name", "") or node
        # Emit a lightweight `thinking` snapshot right before the SQL
        # `plan` node LLM call. This is the entire purpose of the new
        # event: give the user a "the agent is reasoning" hint during
        # the pre-SQL planning step that decides whether to clarify.
        if node_name in ("plan", "sql_plan"):
            return {
                "event": "thinking",
                "data": json.dumps({
                    "phase": "planning",
                    "text": "正在规划查询...",
                }, ensure_ascii=False),
            }
        if node_name and node_name not in ("LangGraph", "LangGraphRunnableSequence", "LangGraphRunnableGraph", "RunnableSequence", "RunnableParallel", "RunnableLambda", "RunnablePassthrough"):
            return {
                "event": "trace",
                "data": json.dumps({
                    "step": f"节点开始: {node_name}",
                    "status": "running",
                    "detail": "",
                }, ensure_ascii=False),
            }

    elif kind == "on_chain_end":
        node_name = event.get("name", "") or node
        if node_name and node_name not in ("LangGraph", "LangGraphRunnableSequence", "LangGraphRunnableGraph", "RunnableSequence", "RunnableParallel", "RunnableLambda", "RunnablePassthrough"):
            return {
                "event": "trace",
                "data": json.dumps({
                    "step": f"节点完成: {node_name}",
                    "status": "success",
                    "detail": "",
                }, ensure_ascii=False),
            }

    return None




def _build_response(state: dict) -> dict:
    report_spec = state.get("report_spec")
    schema_ctx = state.get("schema_context")
    query_result = state.get("query_result")

    table = None
    if query_result and query_result.rows:
        cols = []
        for c in query_result.columns:
            name = c["name"] if isinstance(c, dict) else c
            cols.append({"key": name, "title": name})
        table = {"columns": cols, "rows": query_result.rows}

    chart = state.get("chart_config") or {}
    insight = state.get("insight") or ""

    return {
        "answer": {
            "text": "查询完成",
            "table": table,
            "chart": chart if chart else None,
            "insight": insight or None,
        },
        "trace": [],
    }


# ============================================================================
# Phase 4 endpoints (Conversational Workbench v2)
# ============================================================================


@app.patch("/api/v1/sessions/{session_id}/requirement")
async def patch_requirement(
    session_id: str,
    payload: "PatchRequirementRequest",
    user: dict = Depends(get_current_user),
):
    """Server-side recompute + persist a PATCH from the frontend.

    Body: { "requirement": RequirementCard }
    Returns: { "requirement": RequirementCard }
    Errors: 422 INVALID_REQUIREMENT, 409 REQUIREMENT_LOCKED, 404 SESSION_NOT_FOUND.
    """
    sess = await session_manager.get_session(session_id)
    if sess is None or sess.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND")

    # A-5：卡字段过 SecurityGuard——PATCH 字段是 new/supplement/legacy 入口闸
    # 从未覆盖的盲区，注入在这里提前拦截（confirm 流的 SQL 层另有 A-1 兜底）。
    guard_result = SecurityGuard.check(requirement_service.card_guard_text(
        payload.requirement.model_dump(mode="json"),
    ))
    if guard_result.blocked:
        raise HTTPException(
            status_code=422,
            detail=f"SECURITY_REJECTED: {guard_result.reason}",
        )

    try:
        saved = await requirement_service.patch_requirement(
            session_id=session_id,
            user_id=user["id"],
            incoming=payload.requirement,
        )
    except requirement_service.RequirementLockedError as exc:
        raise HTTPException(status_code=409, detail=f"REQUIREMENT_LOCKED: {exc}")
    return {"requirement": saved.model_dump(mode="json")}


@app.post("/api/v1/sessions/{session_id}/confirm")
async def confirm_session(
    session_id: str,
    req: Request,
    user: dict = Depends(get_current_user),
):
    """Run confirmed-execution on the latest draft. Returns SSE v2 stream.

    后台化：graph 执行转为后台任务（见 _start_confirmed_stream），连接断开
    不中断执行；同 session 已有未完成任务时返回 409 SESSION_BUSY。
    """
    sess = await session_manager.get_session(session_id)
    if sess is None or sess.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND")

    graph = build_confirmed_execution_graph()
    initial = _confirmed_initial(session_id, user["id"], user_query="")
    return _start_confirmed_stream(
        session_id, user["id"], "confirm", graph, initial,
        failed_action="confirm", phase_label="generating",
    )


@app.post("/api/v1/sessions/{session_id}/retry")
async def retry_session(
    session_id: str,
    req: Request,
    user: dict = Depends(get_current_user),
):
    """Resume the failed action recorded in `agent.session.last_failed_action`.

    For now this delegates to /chat (mode=adjust if the failure was on
    an adjustment, or a fresh requirement-analysis otherwise). The full
    implementation is Phase 8's responsibility.
    """
    sess = await session_manager.get_session(session_id)
    if sess is None or sess.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND")
    # Clear the failure marker and route to confirm as a safe default.
    await session_manager.update_phase(session_id, "parsing")
    return await confirm_session(session_id, req, user)


@app.get("/api/v1/sessions/{session_id}")
async def get_session(
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """Full session snapshot: session + messages + current_requirement +
    latest_report + last_failed_action."""
    snap = await snapshot_service.get_session_snapshot(
        session_id=session_id, user_id=user["id"],
    )
    if snap is None:
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND")
    return snap


@app.get("/api/v1/sessions/{session_id}/reports/{version}")
async def get_report_version(
    session_id: str,
    version: int,
    user: dict = Depends(get_current_user),
):
    """Read a specific report version. No LLM / no graph.

    Surfaces `execution_status` from the persisted payload so the
    front-end can distinguish SUCCESS / EMPTY / FAILED when reviewing
    historical versions (e.g. the user switches to v3 in the right
    rail after a v4 success).
    """
    from app.infra.db.postgres import get_pool
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await report_version_repository.get_version(
            conn,
            session_id=session_id,
            user_id=user["id"],
            version=version,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="VERSION_NOT_FOUND")
    # Normalize JSONB → python objects for JSON response
    import json as _json
    if isinstance(row.get("report_payload"), str):
        row["report_payload"] = _json.loads(row["report_payload"])
    if isinstance(row.get("query_snapshot"), str):
        row["query_snapshot"] = _json.loads(row["query_snapshot"])
    if row.get("created_at"):
        row["created_at"] = row["created_at"].isoformat()
    payload = row.get("report_payload") or {}
    row["execution_status"] = payload.get("execution_status")
    return {"report": row}


@app.get("/api/v1/sessions/{session_id}/reports/{version}/export.xlsx")
async def export_report_xlsx(
    session_id: str,
    version: int,
    user: dict = Depends(get_current_user),
):
    """Download the report's underlying query result as an xlsx workbook.

    Reads the full `query_snapshot` (not the runtime-capped 5000 rows), so
    the export is the dataset the user actually ran, not a UI preview.
    Workbook layout: one sheet named "data", row 1 = column headers from
    `query_snapshot.columns[*].name`, rows 2..N = the saved rows. Numeric
    cells stay numeric so xlsx clients can sort/filter.
    """
    import io
    from fastapi.responses import StreamingResponse

    from app.infra.db.postgres import get_pool
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await report_version_repository.get_version(
            conn,
            session_id=session_id,
            user_id=user["id"],
            version=version,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="VERSION_NOT_FOUND")
    snapshot = row.get("query_snapshot")
    if isinstance(snapshot, str):
        import json as _json
        snapshot = _json.loads(snapshot)
    if not snapshot:
        raise HTTPException(status_code=404, detail="NO_QUERY_SNAPSHOT")
    columns = snapshot.get("columns") or []
    rows = snapshot.get("rows") or []
    # columns entries are {"name": "...", "type": "..."}; fall back to row keys.
    if columns and isinstance(columns[0], dict):
        headers = [c.get("name", "") for c in columns]
    else:
        headers = list(columns)
    if not headers and rows:
        headers = list(rows[0].keys())

    # B-4: openpyxl 的 Workbook 构建 + save 是 CPU 密集同步操作。直接在 async
    # 端点里跑会阻塞整个 event loop——一次大导出期间 /chat、/confirm 全部停摆。
    # 丢进线程池跑，释放 event loop。
    xlsx_bytes = await asyncio.to_thread(_build_xlsx_bytes, headers, rows)
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="report-{session_id[:8]}-v{version}.xlsx"'
            )
        },
    )


def _build_xlsx_bytes(headers: list, rows: list) -> bytes:
    """B-4: 同步构建 xlsx 并返回字节。仅供 `asyncio.to_thread` 调用。"""
    import io
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h) for h in headers])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()