from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.contracts

from app.infra.trace.sdk import Tracer


def _make_tracer() -> Tracer:
    return Tracer(
        trace_id="t-1",
        session_id="s-1",
        user_query="查询 id_card 110101199001011234",
        user_id=1,
    )


@pytest.mark.asyncio
async def test_flush_calls_both_pg_and_langfuse_when_enabled():
    """Langfuse 启用（env 有 key）→ flush 调 PG + Langfuse 两个 sink。"""
    t = _make_tracer()
    pg_mock = AsyncMock()
    langfuse_mock = AsyncMock()

    with patch("app.infra.trace.sdk._flush_pg", pg_mock), \
         patch("app.infra.trace.sdk._flush_langfuse", langfuse_mock), \
         patch("app.infra.trace.sdk.LangfuseConfig") as cfg_mock:
        cfg_mock.return_value = MagicMock(enabled=True, flush_timeout=5.0)
        await t.flush()
        assert pg_mock.called
        assert langfuse_mock.called


@pytest.mark.asyncio
async def test_flush_pg_only_when_langfuse_disabled():
    """Langfuse 禁用（env 缺 key）→ flush 仅调 PG，不调 Langfuse。"""
    t = _make_tracer()
    pg_mock = AsyncMock()
    langfuse_mock = AsyncMock()

    with patch("app.infra.trace.sdk._flush_pg", pg_mock), \
         patch("app.infra.trace.sdk._flush_langfuse", langfuse_mock), \
         patch("app.infra.trace.sdk.LangfuseConfig") as cfg_mock:
        cfg_mock.return_value = MagicMock(enabled=False, flush_timeout=5.0)
        await t.flush()
        assert pg_mock.called
        assert not langfuse_mock.called


@pytest.mark.asyncio
async def test_pg_failure_does_not_block_langfuse():
    """PG sink 失败 → Langfuse bi sink 仍尝试（两者独立 best-effort）。"""
    t = _make_tracer()
    pg_mock = AsyncMock(side_effect=RuntimeError("pg down"))
    langfuse_mock = AsyncMock()

    with patch("app.infra.trace.sdk._flush_pg", pg_mock), \
         patch("app.infra.trace.sdk._flush_langfuse", langfuse_mock), \
         patch("app.infra.trace.sdk.LangfuseConfig") as cfg_mock:
        cfg_mock.return_value = MagicMock(enabled=True, flush_timeout=5.0)
        await t.flush()  # 不抛
        assert pg_mock.called
        assert langfuse_mock.called


def test_tracer_redacts_user_query_at_construction():
    """Tracer.__init__ 时 user_query 已经 PII mask（tracer 与落库 Trace 都是脱敏值）。"""
    t = Tracer(
        trace_id="t-pii",
        user_query="手机 13800138000 的订单",
    )
    assert "13800138000" not in t.user_query
    assert "***" in t.user_query
    assert t._trace.user_query == t.user_query