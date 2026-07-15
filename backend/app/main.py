from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from app.agent import build_agent
from app.db import get_connection, close_connection
from app.mcp_client import schema_client
from app.schemas import ChatRequest

load_dotenv()

_agent = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent
    get_connection()
    _agent = build_agent()
    try:
        await schema_client.connect()
    except Exception as exc:
        print(f"[startup] MCP Schema Server not available: {exc}")
        print("[startup] Agent will run without MCP tools (fallback mode)")
    yield
    await schema_client.disconnect()
    close_connection()


app = FastAPI(
    title="ReportAgent API",
    version="1.0.0",
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

    config = {
        "configurable": {"thread_id": session_id},
    }

    initial_state = {
        "messages": [{"role": "user", "content": request.user_query}],
        "user_query": request.user_query,
        "session_id": session_id,
        "intent": "",
        "memory_context": "",
        "schema_context": "",
        "generated_sql": "",
        "sql_valid": False,
        "sql_result": "",
        "sql_error": "",
        "retry_count": 0,
        "need_clarification": False,
        "clarification_question": "",
        "clarification_answer": "",
        "chart_config": {},
        "insight_text": "",
        "trace_log": [],
        "assemble_plan": [],
        "assemble_step_idx": 0,
        "assemble_results": [],
        "error": "",
    }

    async def event_generator() -> AsyncGenerator[dict, None]:
        try:
            async for event in _agent.astream_events(initial_state, config, version="v2"):
                if await req.is_disconnected():
                    break

                kind = event.get("event")
                if kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk", {})
                    content = chunk.content if hasattr(chunk, "content") else ""
                    if content:
                        yield {"event": "token", "data": content}

                elif kind == "on_tool_start":
                    tool_name = event.get("name", "")
                    tool_input = event.get("data", {}).get("input", {})
                    yield {
                        "event": "trace",
                        "data": json.dumps({
                            "step": f"工具调用: {tool_name}",
                            "status": "running",
                            "detail": str(tool_input)[:200],
                        }, ensure_ascii=False),
                    }

                elif kind == "on_tool_end":
                    tool_name = event.get("name", "")
                    output = event.get("data", {}).get("output", "")
                    yield {
                        "event": "trace",
                        "data": json.dumps({
                            "step": f"工具完成: {tool_name}",
                            "status": "success",
                            "detail": str(output)[:200],
                        }, ensure_ascii=False),
                    }

        except Exception as exc:
            yield {"event": "error", "data": str(exc)}
            return

        try:
            final_state = None
            async for s in _agent.astream(initial_state, config):
                if "__end__" in s:
                    node_name = list(s.keys())[0]
                    final_state = s[node_name]
                else:
                    for node_name, node_state in s.items():
                        if isinstance(node_state, dict) and "trace_log" in node_state:
                            for trace in node_state.get("trace_log", []):
                                yield {
                                    "event": "trace",
                                    "data": json.dumps(trace, ensure_ascii=False),
                                }

            if final_state is None:
                async for s in _agent.astream(initial_state, config):
                    if isinstance(s, dict):
                        for node_state in s.values():
                            if isinstance(node_state, dict):
                                final_state = node_state

            if final_state:
                result = _build_response(final_state)
                yield {"event": "report", "data": json.dumps(result, ensure_ascii=False)}
            else:
                yield {"event": "error", "data": "Agent did not produce a result"}

        except Exception as exc:
            yield {"event": "error", "data": str(exc)}

        yield {"event": "done", "data": ""}

    return EventSourceResponse(event_generator())


def _build_response(state: dict) -> dict:
    sql_result = state.get("sql_result", "{}")
    data = json.loads(sql_result) if isinstance(sql_result, str) else sql_result

    table = None
    if data.get("columns") and data.get("rows"):
        table = {
            "columns": [{"key": c, "title": c} for c in data["columns"]],
            "rows": data["rows"],
        }

    chart = state.get("chart_config") or {}
    insight = state.get("insight_text", "")

    text = insight or "查询完成"
    return {
        "answer": {
            "text": text,
            "table": table,
            "chart": chart if chart else None,
            "insight": insight or None,
        },
        "trace": [
            {
                "step": t.get("step", ""),
                "status": t.get("status", ""),
                "detail": t.get("detail", ""),
                "duration": t.get("duration", ""),
            }
            for t in state.get("trace_log", [])
        ],
    }
