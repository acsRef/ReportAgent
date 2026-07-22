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

from app.agent.parent_graph import build_parent_graph
from app.db import get_connection, close_connection
from app.infra.db.postgres import init_pool, close_pool
from app.infra.checkpoint.session import session_manager
from app.infra.trace.sdk import get_tracer
from app.infra.auth.repository import ensure_default_user, verify_user
from app.infra.auth.jwt import create_token
from app.infra.auth.deps import get_current_user
from app.infra.conversation.repository import save_message, get_messages, list_sessions
from app.models.contracts import LoginRequest, RegisterRequest

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
    session_id = request.session_id or str(uuid.uuid4())

    existing = await session_manager.get_session(session_id)
    if existing and existing.get("last_checkpoint_at"):
        cp_time = existing["last_checkpoint_at"]
        if isinstance(cp_time, datetime.datetime) and cp_time < _server_start_time:
            existing = None
    is_new = existing is None

    if is_new:
        await session_manager.create_session(
            session_id, user_id=request.session_id or "anonymous"
        )

    await save_message(session_id, user["id"], "user", request.user_query, "text")

    config = {
        "configurable": {"thread_id": session_id},
    }

    async def event_generator() -> AsyncGenerator[dict, None]:
        trace_id = str(uuid.uuid4())
        report_result = None
        error_result = None
        clarify_result = None
        try:
            input_data = {
                "user_query": request.user_query,
                "original_query": request.user_query,
                "current_query": request.user_query,
                "clarification_history": [],
                "session_id": session_id,
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
            } if is_new else None

            async for event in _agent.astream_events(
                input_data, config, version="v2"
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

            if not interrupted:
                snapshot = _agent.get_state(config)
                final_state = snapshot.values if snapshot else None
                if final_state:
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