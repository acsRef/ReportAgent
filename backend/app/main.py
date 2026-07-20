from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.agent.parent_graph import build_parent_graph
from app.db import get_connection, close_connection
from app.infra.db.postgres import init_pool, close_pool
from app.infra.checkpoint.session import session_manager
from app.infra.trace.sdk import get_tracer

load_dotenv()

_agent = None


class ChatRequest(BaseModel):
    user_query: str
    session_id: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent
    get_connection()
    await init_pool()
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


@app.post("/api/v1/chat")
async def chat(request: ChatRequest, req: Request):
    session_id = request.session_id or str(uuid.uuid4())

    existing = await session_manager.get_session(session_id)
    is_new = existing is None

    if is_new:
        await session_manager.create_session(
            user_id=request.session_id or "anonymous"
        )

    config = {
        "configurable": {"thread_id": session_id},
    }

    async def event_generator() -> AsyncGenerator[dict, None]:
        trace_id = str(uuid.uuid4())
        try:
            if is_new:
                initial_state = {
                    "user_query": request.user_query,
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
                }
                async for event in _agent.astream_events(
                    initial_state, config, version="v2"
                ):
                    if await req.is_disconnected():
                        break
                    yield _format_event(event)

                async for s in _agent.astream(initial_state, config):
                    final_state = _extract_final_state(s)
                    if final_state:
                        result = _build_response(final_state)
                        yield {"event": "report", "data": json.dumps(result, ensure_ascii=False)}
                        break
            else:
                async for event in _agent.astream_events(
                    None, config, version="v2"
                ):
                    if await req.is_disconnected():
                        break
                    yield _format_event(event)

                async for s in _agent.astream(None, config):
                    final_state = _extract_final_state(s)
                    if final_state:
                        result = _build_response(final_state)
                        yield {"event": "report", "data": json.dumps(result, ensure_ascii=False)}
                        break

        except Exception as exc:
            yield {"event": "error", "data": str(exc)}
        finally:
            tracer = get_tracer(trace_id)
            await tracer.flush()

        yield {"event": "done", "data": ""}

    return EventSourceResponse(event_generator())


def _format_event(event: dict) -> dict:
    kind = event.get("event")

    if kind == "on_chat_model_stream":
        chunk = event.get("data", {}).get("chunk", {})
        content = chunk.content if hasattr(chunk, "content") else ""
        if content:
            return {"event": "token", "data": content}

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

    elif kind == "on_chain_end":
        node_name = event.get("name", "")
        if node_name == "clarify":
            return {
                "event": "clarify",
                "data": json.dumps({
                    "question": event.get("data", {}).get("output", {}).get("question", ""),
                }, ensure_ascii=False),
            }

    return {"event": "", "data": ""}


def _extract_final_state(stream_output: dict) -> dict | None:
    if "__end__" in stream_output:
        for node_name, node_state in stream_output.items():
            if isinstance(node_state, dict) and "report_spec" in node_state:
                return node_state
    for node_name, node_state in stream_output.items():
        if isinstance(node_state, dict) and "report_spec" in node_state:
            return node_state
    return None


def _build_response(state: dict) -> dict:
    report_spec = state.get("report_spec")
    schema_ctx = state.get("schema_context")
    query_result = state.get("query_result")

    table = None
    if query_result and query_result.get("rows"):
        table = {
            "columns": [{"key": c.get("name", c) if isinstance(c, dict) else c, "title": c.get("name", c) if isinstance(c, dict) else c} for c in query_result.get("columns", [])],
            "rows": query_result.get("rows", []),
        }

    chart = state.get("chart_config") or {}
    insight = state.get("insight_text") or ""

    return {
        "answer": {
            "text": insight or "查询完成",
            "table": table,
            "chart": chart if chart else None,
            "insight": insight or None,
        },
        "trace": [],
    }
