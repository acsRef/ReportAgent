"""PG 连接池扩容 + Trace flush 超时保护测试。

池监控用 fake pool 离线测；flush 超时/失败用 monkeypatch 假 repo 测，不打真实 PG。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.infra.db import postgres
from app.infra.trace import sdk as trace_sdk
from app.infra.trace.repository import TraceRepository

pytestmark = pytest.mark.smoke


# --- 连接池监控 ---


class _FakePool:
    """暴露 asyncpg.Pool 的 get_size/get_idle_size/get_max_size 假实现。"""

    def __init__(self, size: int, idle: int, max_size: int):
        self._size, self._idle, self._max = size, idle, max_size

    def get_size(self) -> int:
        return self._size

    def get_idle_size(self) -> int:
        return self._idle

    def get_max_size(self) -> int:
        return self._max


def test_log_pool_status_info_when_normal(caplog):
    with caplog.at_level("INFO", logger="app.infra.db.postgres"):
        postgres._log_pool_status(_FakePool(5, 3, 20))
    assert any("pg pool status: size=5 idle=3 max=20" in r.message for r in caplog.records)


def test_log_pool_status_warns_when_exhausted(caplog):
    with caplog.at_level("WARNING", logger="app.infra.db.postgres"):
        postgres._log_pool_status(_FakePool(20, 0, 20))
    assert any("pg pool exhausted" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_start_stop_pool_monitor():
    postgres.stop_pool_monitor()
    postgres.start_pool_monitor(interval=0.01)
    task = postgres._pool_monitor_task
    assert task is not None and not task.done()
    # 幂等：重复 start 不新建任务
    postgres.start_pool_monitor(interval=0.01)
    assert postgres._pool_monitor_task is task
    postgres.stop_pool_monitor()
    assert postgres._pool_monitor_task is None
    # 让被取消的任务真正结束，避免 pending task 告警
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_pool_monitor_loop_skips_when_no_pool():
    # _pool 为 None 时循环应安全跳过，不抛异常（首轮 sleep 前退出即可验证幂等性）
    postgres._pool = None
    task = asyncio.create_task(postgres._pool_monitor_loop(0.01))
    await asyncio.sleep(0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# 真正连池的断言需要 DATABASE_URL，交给 conftest 的 pg_pool fixture（无则 skip）。


def test_pool_max_size_from_env(monkeypatch):
    monkeypatch.setenv("PG_POOL_MAX_SIZE", "20")
    import importlib

    importlib.reload(postgres)
    assert postgres._POOL_MAX_SIZE == 20


# --- Trace flush 超时保护 ---


async def test_flush_timeout_does_not_raise(monkeypatch):
    monkeypatch.setattr(trace_sdk, "_FLUSH_TIMEOUT", 0.01)
    tracer = trace_sdk.Tracer("t-timeout")
    trace_sdk._local["t-timeout"] = tracer

    async def _hang(self):
        await asyncio.sleep(1)

    with patch.object(TraceRepository, "save_trace", _hang):
        await tracer.flush()  # 不应抛异常

    assert "t-timeout" not in trace_sdk._local


async def test_flush_save_error_does_not_raise(monkeypatch):
    tracer = trace_sdk.Tracer("t-error")
    trace_sdk._local["t-error"] = tracer

    async def _boom(self):
        raise RuntimeError("db down")

    with patch.object(TraceRepository, "save_trace", _boom):
        await tracer.flush()  # 单点失败被吞，不重抛

    assert "t-error" not in trace_sdk._local