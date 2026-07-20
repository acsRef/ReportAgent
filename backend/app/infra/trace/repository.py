from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from app.infra.db.postgres import get_pool
from app.infra.trace.models import LLMCall, Span, Trace


class TraceRepository:
    async def save_trace(self, trace: Trace):
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO observability.agent_trace
                   (trace_id, session_id, user_query, status, start_time, end_time, total_duration_ms)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                trace.trace_id, trace.session_id, trace.user_query,
                trace.status, trace.start_time, trace.end_time, trace.total_duration_ms,
            )

    async def update_trace(self, trace_id: str, status: str, end_time: datetime, duration_ms: int):
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE observability.agent_trace
                   SET status=$1, end_time=$2, total_duration_ms=$3
                   WHERE trace_id=$4""",
                status, end_time, duration_ms, trace_id,
            )

    async def save_span(self, span: Span):
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO observability.agent_trace_span
                   (trace_id, parent_span_id, span_id, span_name, span_type,
                    start_time, end_time, duration_ms, status, input, output, error)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)""",
                span.trace_id, span.parent_span_id, span.span_id,
                span.span_name, span.span_type, span.start_time, span.end_time,
                span.duration_ms, span.status,
                json.dumps(span.input, ensure_ascii=False, default=str) if span.input else None,
                json.dumps(span.output, ensure_ascii=False, default=str) if span.output else None,
                span.error,
            )

    async def save_llm_call(self, call: LLMCall):
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO observability.llm_call
                   (span_id, model, prompt_tokens, completion_tokens, latency_ms, cost)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                call.span_id, call.model, call.prompt_tokens,
                call.completion_tokens, call.latency_ms, call.cost,
            )
