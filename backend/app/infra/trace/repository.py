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
                   (trace_id, session_id, user_id, user_query, status, start_time, end_time, total_duration_ms)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                trace.trace_id, trace.session_id, trace.user_id, trace.user_query,
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

    # --- 只读查询（可观测性运维闭环，见 docs/plans/2026-08-01-observability-ops.md）---

    @staticmethod
    def _trace_row(r) -> dict:
        return {
            "trace_id": r["trace_id"],
            "session_id": r["session_id"],
            "user_query": r["user_query"],
            "status": r["status"],
            "start_time": r["start_time"].isoformat() if r["start_time"] else None,
            "end_time": r["end_time"].isoformat() if r.get("end_time") else None,
            "total_duration_ms": r["total_duration_ms"],
        }

    async def list_traces(
        self, *, user_id: int, limit: int = 50, offset: int = 0,
        status: Optional[str] = None,
    ) -> list[dict]:
        """A-3：一律按 user_id 过滤——他人 trace 不可见，
        历史无主行（user_id IS NULL）对所有人不可见。"""
        pool = get_pool()
        async with pool.acquire() as conn:
            if status:
                rows = await conn.fetch(
                    """SELECT trace_id, session_id, user_query, status,
                              start_time, end_time, total_duration_ms
                       FROM observability.agent_trace
                       WHERE user_id = $1 AND status = $2
                       ORDER BY start_time DESC NULLS LAST
                       LIMIT $3 OFFSET $4""",
                    user_id, status, limit, offset,
                )
            else:
                rows = await conn.fetch(
                    """SELECT trace_id, session_id, user_query, status,
                              start_time, end_time, total_duration_ms
                       FROM observability.agent_trace
                       WHERE user_id = $1
                       ORDER BY start_time DESC NULLS LAST
                       LIMIT $2 OFFSET $3""",
                    user_id, limit, offset,
                )
            return [self._trace_row(r) for r in rows]

    async def get_trace(self, trace_id: str, *, user_id: int) -> Optional[dict]:
        """A-3：归属校验内置于查询——他人 trace_id 返回 None（API 层转 404）。"""
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT trace_id, session_id, user_query, status,
                          start_time, end_time, total_duration_ms
                   FROM observability.agent_trace
                   WHERE trace_id = $1 AND user_id = $2""",
                trace_id, user_id,
            )
            return self._trace_row(row) if row else None

    async def get_spans(self, trace_id: str) -> list[dict]:
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT span_id, parent_span_id, span_name, span_type,
                          start_time, duration_ms, status, error
                   FROM observability.agent_trace_span
                   WHERE trace_id = $1
                   ORDER BY start_time ASC NULLS LAST""",
                trace_id,
            )
            return [
                {
                    "span_id": r["span_id"],
                    "parent_span_id": r["parent_span_id"],
                    "span_name": r["span_name"],
                    "span_type": r["span_type"],
                    "start_time": r["start_time"].isoformat() if r["start_time"] else None,
                    "duration_ms": r["duration_ms"],
                    "status": r["status"],
                    "error": r["error"],
                }
                for r in rows
            ]

    async def get_llm_calls(self, trace_id: str) -> list[dict]:
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT lc.model, lc.prompt_tokens, lc.completion_tokens,
                          lc.latency_ms, lc.cost
                   FROM observability.llm_call lc
                   JOIN observability.agent_trace_span s ON s.span_id = lc.span_id
                   WHERE s.trace_id = $1
                   ORDER BY lc.id ASC""",
                trace_id,
            )
            return [
                {
                    "model": r["model"],
                    "prompt_tokens": r["prompt_tokens"] or 0,
                    "completion_tokens": r["completion_tokens"] or 0,
                    "latency_ms": r["latency_ms"] or 0,
                    "cost": float(r["cost"]) if r["cost"] is not None else None,
                }
                for r in rows
            ]

    async def get_metrics(self, *, user_id: int) -> dict:
        """聚合运维指标：trace 总量/状态分布/成功率/耗时均值与 P95/LLM 用量。

        A-3：全部按 user_id 过滤；llm_call 本身无 user_id 列，经
        span JOIN trace 间接归属。
        """
        pool = get_pool()
        async with pool.acquire() as conn:
            trace_stats = await conn.fetchrow(
                """SELECT count(*) AS total,
                          avg(total_duration_ms) AS avg_duration,
                          percentile_cont(0.95) WITHIN GROUP
                            (ORDER BY total_duration_ms) AS p95_duration
                   FROM observability.agent_trace
                   WHERE user_id = $1""",
                user_id,
            )
            status_rows = await conn.fetch(
                """SELECT status, count(*) AS n FROM observability.agent_trace
                   WHERE user_id = $1 GROUP BY status""",
                user_id,
            )
            llm_stats = await conn.fetchrow(
                """SELECT count(*) AS total,
                          sum(COALESCE(lc.prompt_tokens, 0) + COALESCE(lc.completion_tokens, 0)) AS tokens,
                          avg(lc.latency_ms) AS avg_latency
                   FROM observability.llm_call lc
                   JOIN observability.agent_trace_span s ON s.span_id = lc.span_id
                   JOIN observability.agent_trace t ON t.trace_id = s.trace_id
                   WHERE t.user_id = $1""",
                user_id,
            )
        total = trace_stats["total"] or 0
        status_breakdown = {r["status"]: r["n"] for r in status_rows}
        # trace 的 status 取值为 DONE/SUCCESS（完成）、AWAITING_*/RUNNING（中途等待/进行）、
        # FAILED/REJECTED（失败）。完成口径取 DONE + SUCCESS（兼容两种命名）。
        success = sum(n for s, n in status_breakdown.items() if s in ("DONE", "SUCCESS"))
        return {
            "trace_total": total,
            "status_breakdown": status_breakdown,
            "success_rate": (success / total) if total else None,
            "avg_duration_ms": (
                round(float(trace_stats["avg_duration"]), 1)
                if trace_stats["avg_duration"] is not None else None
            ),
            "p95_duration_ms": (
                round(float(trace_stats["p95_duration"]), 1)
                if trace_stats["p95_duration"] is not None else None
            ),
            "llm_call_total": llm_stats["total"] or 0,
            "llm_tokens_total": int(llm_stats["tokens"] or 0),
            "llm_avg_latency_ms": (
                round(float(llm_stats["avg_latency"]), 1)
                if llm_stats["avg_latency"] is not None else None
            ),
        }
