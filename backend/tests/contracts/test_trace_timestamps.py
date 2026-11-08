"""trace 时间戳统一 UTC（plan 2026-11-08-trace-inspector 的连带修复）。

真机现象：`observability.agent_trace` 同一行里 `start_time`（宿主本地时间）与
`created_at`（容器 `NOW()`，UTC）差 8 小时。用 `start_time` 做「最近 N 分钟」过滤
会查不到刚跑的那一轮——排查时会被误判成「trace 没落库」。
"""
from __future__ import annotations

import time
from datetime import datetime

import pytest

from app.infra.trace.sdk import Tracer, _utc_now

pytestmark = pytest.mark.contracts


class TestUtcNow:
    def test_与_utc_当前时间同量级(self) -> None:
        before = datetime.utcnow()
        value = _utc_now()
        after = datetime.utcnow()

        assert before <= value <= after

    def test_不是本地时间(self) -> None:
        """北京时区下两者差 8 小时；这条钉住「没有退回到 datetime.now()」。"""
        delta = abs((_utc_now() - datetime.now()).total_seconds())
        if time.timezone == 0 and time.altzone == 0:
            pytest.skip("本机就是 UTC 时区")
        assert delta > 60, "trace 时间戳又用上了本地时间"


class TestTracerTimes:
    def test_trace_起始时间是_utc(self) -> None:
        tracer = Tracer("t-1", session_id="s", user_query="q")
        delta = abs((tracer._start_time - datetime.utcnow()).total_seconds())

        assert delta < 5

    def test_span_起止时间是_utc(self) -> None:
        tracer = Tracer("t-2")
        with tracer.span("node"):
            pass

        span = tracer._spans[-1]
        now = datetime.utcnow()
        assert abs((span.start_time - now).total_seconds()) < 5
        assert abs((span.end_time - now).total_seconds()) < 5
        assert span.end_time >= span.start_time

    def test_end_把总耗时记成正数(self) -> None:
        tracer = Tracer("t-3")
        tracer.end("SUCCESS")

        assert tracer._trace.total_duration_ms is not None
        assert tracer._trace.total_duration_ms >= 0
