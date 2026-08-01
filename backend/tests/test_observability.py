"""可观测性查询测试（真 PG）。

覆盖 TraceRepository 的只读查询：trace 写入-查询 round-trip、span/llm_call 关联、
指标聚合结构、按 status 过滤。用独立 trace_id，测后清理，不污染开发库。
"""
from __future__ import annotations

import asyncio
import datetime
import uuid

import pytest

from app.infra.trace.models import LLMCall, Span, Trace
from app.infra.trace.repository import TraceRepository

pytestmark = pytest.mark.persistence


def _run(coro):
    from app.infra.db.postgres import close_pool, init_pool

    async def _body():
        await init_pool()
        try:
            return await coro
        finally:
            await close_pool()

    return asyncio.run(_body())


def _cleanup(trace_id: str):
    import psycopg2
    conn = psycopg2.connect("postgresql://ragent:ragent@localhost:5432/ragent")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM observability.llm_call WHERE span_id IN "
        "(SELECT span_id FROM observability.agent_trace_span WHERE trace_id=%s)",
        (trace_id,),
    )
    cur.execute("DELETE FROM observability.agent_trace_span WHERE trace_id=%s", (trace_id,))
    cur.execute("DELETE FROM observability.agent_trace WHERE trace_id=%s", (trace_id,))
    conn.close()


def test_trace_query_round_trip():
    repo = TraceRepository()
    tid = f"obstest-{uuid.uuid4().hex}"
    span_id = f"span-{uuid.uuid4().hex[:16]}"
    now = datetime.datetime.now()

    async def body():
        await repo.save_trace(Trace(
            trace_id=tid, session_id="s-obs", user_query="观测测试查询",
            status="SUCCESS", start_time=now, end_time=now, total_duration_ms=123,
        ))
        await repo.save_span(Span(
            trace_id=tid, span_id=span_id, span_name="sql_plan", span_type="NODE",
            start_time=now, end_time=now, duration_ms=50, status="SUCCESS",
            input={"q": "x"},
        ))
        await repo.save_llm_call(LLMCall(
            span_id=span_id, model="test-model", prompt_tokens=10,
            completion_tokens=5, latency_ms=200,
        ))
        trace = await repo.get_trace(tid)
        spans = await repo.get_spans(tid)
        calls = await repo.get_llm_calls(tid)
        listed = await repo.list_traces(limit=20, status="SUCCESS")
        return trace, spans, calls, listed

    try:
        trace, spans, calls, listed = _run(body())
        assert trace["trace_id"] == tid
        assert trace["total_duration_ms"] == 123
        assert trace["user_query"] == "观测测试查询"
        assert len(spans) == 1 and spans[0]["span_name"] == "sql_plan"
        assert len(calls) == 1
        assert calls[0]["model"] == "test-model"
        assert calls[0]["prompt_tokens"] == 10  # llm_call 经 span 关联到 trace
        assert any(t["trace_id"] == tid for t in listed)
    finally:
        _cleanup(tid)


def test_metrics_structure_valid():
    repo = TraceRepository()
    metrics = _run(repo.get_metrics())
    assert isinstance(metrics["trace_total"], int)
    assert isinstance(metrics["status_breakdown"], dict)
    assert metrics["success_rate"] is None or 0.0 <= metrics["success_rate"] <= 1.0
    assert metrics["llm_call_total"] >= 0
    assert metrics["llm_tokens_total"] >= 0
    # 平均/P95 耗时：有数据时为数值，无数据时为 None
    for k in ("avg_duration_ms", "p95_duration_ms", "llm_avg_latency_ms"):
        assert metrics[k] is None or isinstance(metrics[k], (int, float))


def test_list_traces_pagination_bounds():
    repo = TraceRepository()
    # limit 被夹在 [1, 200]，offset 不为负
    traces = _run(repo.list_traces(limit=5, offset=0))
    assert isinstance(traces, list)
    assert len(traces) <= 5
