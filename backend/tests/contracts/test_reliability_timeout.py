"""P9 reliability/timeout.py 契约：分层超时单一来源 + run_with_timeout。

LAYER_DEFAULTS 与各层既有实现同值（P9 不改 llm/mcp/db 实现，只收编数值）；
真新行为只有 background_task（MAX_TASK_DURATION，伞形 §200）。
"""
from __future__ import annotations

import asyncio

import pytest

from app.reliability.timeout import LAYER_DEFAULTS, MAX_TASK_DURATION, run_with_timeout


def test_layer_defaults_match_existing_implementations(monkeypatch):
    monkeypatch.delenv("LLM_TIMEOUT", raising=False)
    monkeypatch.delenv("LLM_MAX_TOTAL_TIME", raising=False)
    monkeypatch.delenv("RAGENT_MCP_TIMEOUT", raising=False)
    monkeypatch.delenv("MAX_TASK_DURATION", raising=False)

    from app.llm.config import LLMConfig
    from app.tools.mcp_client import _MCPConfig
    from app.tools.sql_tools import CONNECT_TIMEOUT_S, STATEMENT_TIMEOUT_MS

    assert LAYER_DEFAULTS["llm_request"] == LLMConfig().timeout
    assert LAYER_DEFAULTS["llm_total_budget"] == 90.0
    assert LAYER_DEFAULTS["mcp_request"] == _MCPConfig().timeout
    assert LAYER_DEFAULTS["db_connect"] == CONNECT_TIMEOUT_S
    assert LAYER_DEFAULTS["db_statement_ms"] == STATEMENT_TIMEOUT_MS
    assert LAYER_DEFAULTS["background_task"] == 600.0


def test_max_task_duration_env_override(monkeypatch):
    monkeypatch.setenv("MAX_TASK_DURATION", "120")
    import importlib
    import app.reliability.timeout as timeout_module

    reloaded = importlib.reload(timeout_module)
    try:
        assert reloaded.MAX_TASK_DURATION == 120.0
        assert reloaded.LAYER_DEFAULTS["background_task"] == 120.0
    finally:
        monkeypatch.delenv("MAX_TASK_DURATION", raising=False)
        importlib.reload(timeout_module)
    # 重载后全局引用恢复默认——测试内 from-import 的 MAX_TASK_DURATION 不受影响
    assert MAX_TASK_DURATION == 600.0


async def test_run_with_timeout_passes_through_result():
    async def ok():
        return 42

    assert await run_with_timeout(ok(), 1.0) == 42


async def test_run_with_timeout_raises_on_deadline():
    async def hanging():
        await asyncio.sleep(999)

    with pytest.raises(asyncio.TimeoutError):
        await run_with_timeout(hanging(), 0.01)
