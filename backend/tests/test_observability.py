"""可观测性查询测试（真 PG）。

覆盖 TraceRepository 的只读查询：trace 写入-查询 round-trip、span/llm_call 关联、
指标聚合结构、按 status 过滤。用独立 trace_id，测后清理，不污染开发库。

A-3（docs/plans/2026-08-04-agent-security-hardening.md）：所有查询按 user_id
隔离——他人 trace 不可见、历史 NULL 无主行对所有人不可见。测试用 999001/999002
两个假用户 ID，避开真实用户。
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
    uid = 999001  # A-3：round-trip 带 user_id

    async def body():
        await repo.save_trace(Trace(
            trace_id=tid, session_id="s-obs", user_id=uid, user_query="观测测试查询",
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
        trace = await repo.get_trace(tid, user_id=uid)
        spans = await repo.get_spans(tid)
        calls = await repo.get_llm_calls(tid)
        listed = await repo.list_traces(user_id=uid, limit=20, status="SUCCESS")
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
    metrics = _run(repo.get_metrics(user_id=999001))
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
    traces = _run(repo.list_traces(user_id=999001, limit=5, offset=0))
    assert isinstance(traces, list)
    assert len(traces) <= 5


# ── A-3 用户隔离 ──────────────────────────────────────────────────────

def test_trace_isolation_between_users():
    """他人 trace_id → get_trace 返回 None；list 只见本人数据。"""
    repo = TraceRepository()
    tid = f"obstest-{uuid.uuid4().hex}"
    now = datetime.datetime.now()
    owner, other = 999001, 999002

    async def body():
        await repo.save_trace(Trace(
            trace_id=tid, session_id="s-iso", user_id=owner,
            status="SUCCESS", start_time=now, end_time=now, total_duration_ms=10,
        ))
        own = await repo.get_trace(tid, user_id=owner)
        foreign = await repo.get_trace(tid, user_id=other)
        foreign_list = await repo.list_traces(user_id=other, limit=100)
        return own, foreign, foreign_list

    try:
        own, foreign, foreign_list = _run(body())
        assert own is not None and own["trace_id"] == tid
        assert foreign is None, "他人 trace 不应可读"
        assert all(t["trace_id"] != tid for t in foreign_list)
    finally:
        _cleanup(tid)


def test_legacy_null_user_trace_invisible_to_everyone():
    """历史无主行（user_id IS NULL）对所有人不可见——审计数据，安全优先。"""
    repo = TraceRepository()
    tid = f"obstest-{uuid.uuid4().hex}"
    now = datetime.datetime.now()

    async def body():
        await repo.save_trace(Trace(
            trace_id=tid, session_id="s-null", user_id=None,
            status="SUCCESS", start_time=now, end_time=now, total_duration_ms=10,
        ))
        direct = await repo.get_trace(tid, user_id=1)
        listed = await repo.list_traces(user_id=1, limit=200)
        return direct, listed

    try:
        direct, listed = _run(body())
        assert direct is None, "NULL 归属的历史 trace 不应被任何人读到"
        assert all(t["trace_id"] != tid for t in listed)
    finally:
        _cleanup(tid)
