from __future__ import annotations

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

from app.agent.parent_graph import AgentState, build_parent_graph
from app.agent.sql_graph import ChatCard
from app.agent.requirement_analysis_graph import build_requirement_analysis_graph
from app.agent.confirmed_execution_graph import (
    build_confirmed_execution_graph,
    ConfirmedExecutionState,
    RequirementIncompleteError,
    SessionNotFoundError,
)
from app.api.templates import router as templates_router
from app.db import get_connection, close_connection
from app.infra.db.postgres import init_pool, close_pool
from app.infra.checkpoint.session import session_manager
from app.infra.trace.sdk import get_tracer
from app.infra.auth.repository import ensure_default_user, verify_user
from app.infra.auth.jwt import create_token
from app.infra.auth.deps import get_current_user
from app.infra.conversation.repository import save_message, get_messages, list_sessions
from app.models.contracts import LoginRequest, RegisterRequest, PatchRequirementRequest
from app.models.requirement import RequirementCard
from app.services import (
    requirement_service,
    report_version_service,
    snapshot_service,
)
from app.infra.db import report_version_repository

VECTOR_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))


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
    get_connection()
    await init_pool()
    await ensure_default_user()
    await _check_embedding_dimension()
    _agent = build_parent_graph()
    yield
    await close_pool()
    close_connection()


app = FastAPI(
    title="ReportAgent API",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(templates_router)

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
async def get_sessions(user: dict = Depends(get_current_user)):
    sessions = await list_sessions(user["id"])
    return {"sessions": sessions}


@app.get("/api/v1/conversations/{session_id}")
async def get_conversation(session_id: str, user: dict = Depends(get_current_user)):
    messages = await get_messages(session_id, user["id"])
    return {"messages": messages}


@app.post("/api/v1/chat")
async def chat(request: ChatRequest, req: Request, user: dict = Depends(get_current_user)):
    """Conversational Workbench v2 entry.

    mode=new        → requirement-analysis (parse + persist draft, no SQL)
    mode=supplement → requirement-analysis (with prior card context)
    mode=adjust     → confirmed-execution (load latest draft, generate v2/v3)
    mode=legacy     → original 2-stage interrupt + chosen_tool flow
    """
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
    """SSE stream for confirmed-execution (mode=adjust)."""
    trace_id = str(uuid.uuid4())
    graph = build_confirmed_execution_graph()
    config = {"configurable": {"thread_id": session_id}}

    async def event_generator() -> AsyncGenerator[dict, None]:
        try:
            yield {
                "event": "phase",
                "data": json.dumps({"phase": "adjusting"}, ensure_ascii=False),
            }
            initial: ConfirmedExecutionState = {
                "user_query": request.user_query,
                "user_id": user["id"],
                "session_id": session_id,
                "trace_id": trace_id,
                "requirement_card": None,
                "base_report_version": request.base_report_version,
                "adjustment_text": request.user_query,
                "schema_context": None,
                "query_result": None,
                "report_payload": None,
                "execution_status": "RUNNING",
                "error": None,
            }
            result = await graph.ainvoke(initial, config)
            report_payload = result.get("report_payload") or {}
            yield {
                "event": "report",
                "data": json.dumps(report_payload, ensure_ascii=False, default=str),
            }
        except RequirementIncompleteError as exc:
            await session_manager.update_phase(
                session_id, "error", failed_action=request.mode,
            )
            yield {
                "event": "error",
                "data": json.dumps({
                    "code": "REQUIREMENT_INCOMPLETE",
                    "message": str(exc)[:300],
                    "recoverable": True,
                    "failed_action": request.mode,
                }, ensure_ascii=False),
            }
        except SessionNotFoundError as exc:
            yield {
                "event": "error",
                "data": json.dumps({
                    "code": "SESSION_NOT_FOUND",
                    "message": str(exc)[:300],
                    "recoverable": False,
                    "failed_action": request.mode,
                }, ensure_ascii=False),
            }
        except Exception as exc:
            await session_manager.update_phase(
                session_id, "error", failed_action=request.mode,
            )
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
                "data": json.dumps({"final_phase": "report_ready"}, ensure_ascii=False),
            }

    return EventSourceResponse(event_generator())


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
        report_result = None
        error_result = None
        clarify_result = None
        pending_card: ChatCard | None = None
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
                "insight_text": "",
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
    insight = state.get("insight_text") or ""

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
    """Run confirmed-execution on the latest draft. Returns SSE v2 stream."""
    sess = await session_manager.get_session(session_id)
    if sess is None or sess.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND")

    trace_id = str(uuid.uuid4())
    graph = build_confirmed_execution_graph()
    config = {"configurable": {"thread_id": session_id}}

    async def event_generator() -> AsyncGenerator[dict, None]:
        try:
            yield {
                "event": "phase",
                "data": json.dumps({"phase": "generating"}, ensure_ascii=False),
            }
            initial: ConfirmedExecutionState = {
                "user_query": "",
                "user_id": user["id"],
                "session_id": session_id,
                "trace_id": trace_id,
                "requirement_card": None,
                "base_report_version": None,
                "adjustment_text": None,
                "schema_context": None,
                "query_result": None,
                "report_payload": None,
                "execution_status": "RUNNING",
                "error": None,
            }
            result = await graph.ainvoke(initial, config)
            report_payload = result.get("report_payload") or {}
            yield {
                "event": "report",
                "data": json.dumps(report_payload, ensure_ascii=False, default=str),
            }
        except RequirementIncompleteError as exc:
            await session_manager.update_phase(
                session_id, "error", failed_action="confirm",
            )
            yield {
                "event": "error",
                "data": json.dumps({
                    "code": "REQUIREMENT_INCOMPLETE",
                    "message": str(exc)[:300],
                    "recoverable": True,
                    "failed_action": "confirm",
                }, ensure_ascii=False),
            }
        except SessionNotFoundError as exc:
            yield {
                "event": "error",
                "data": json.dumps({
                    "code": "SESSION_NOT_FOUND",
                    "message": str(exc)[:300],
                    "recoverable": False,
                    "failed_action": "confirm",
                }, ensure_ascii=False),
            }
        except Exception as exc:
            await session_manager.update_phase(
                session_id, "error", failed_action="confirm",
            )
            yield {
                "event": "error",
                "data": json.dumps({
                    "code": "INTERNAL",
                    "message": str(exc)[:300],
                    "recoverable": False,
                    "failed_action": "confirm",
                }, ensure_ascii=False),
            }
        finally:
            tracer = get_tracer(trace_id)
            await tracer.flush()
            yield {
                "event": "done",
                "data": json.dumps({"final_phase": "report_ready"}, ensure_ascii=False),
            }

    return EventSourceResponse(event_generator())


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
    """Read a specific report version. No LLM / no graph."""
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
    return {"report": row}