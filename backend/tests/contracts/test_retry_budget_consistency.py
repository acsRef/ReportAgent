"""P9 RetryPolicy 固定预算一致性（宪法 §11 / 伞形 §194：SQL repair 2 / MCP 2 / LLM transient 2）。

钉三处实现与 reliability/retry.RETRY_BUDGETS 同值——P9 不改 sql_graph / mcp_client 实现，只钉契约一致。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.agent.sql_graph import _get_max_plan_retries, _get_max_sql_retries
from app.llm.config import LLMConfig
from app.reliability.retry import RETRY_BUDGETS, get_budget
from app.tools.mcp_client import RagMCPClient, _MCPConfig
from app.tools.mcp_errors import MCPBoundaryError, MCPErrorCode


def test_retry_budgets_contract_values(monkeypatch):
    monkeypatch.delenv("MAX_SQL_REPAIR_RETRIES", raising=False)
    monkeypatch.delenv("LLM_MAX_RETRIES", raising=False)
    assert get_budget("sql_repair") == 2
    assert get_budget("mcp") == 2
    assert get_budget("llm_transient") == 2
    assert set(RETRY_BUDGETS) == {"sql_repair", "mcp", "llm_transient"}


def test_unknown_budget_name_raises():
    with pytest.raises(KeyError):
        get_budget("adaptive_turbo")


def test_sql_graph_budget_matches_contract(monkeypatch):
    monkeypatch.delenv("MAX_SQL_REPAIR_RETRIES", raising=False)
    assert _get_max_sql_retries() == get_budget("sql_repair")
    # env 真生效（P8 既有语义保留）
    monkeypatch.setenv("MAX_SQL_REPAIR_RETRIES", "5")
    assert _get_max_sql_retries() == 5


def test_plan_budget_unchanged():
    # P8 显式命名：MAX_PLAN_RETRIES=1 非 P9 范围，钉住防漂移
    assert _get_max_plan_retries() == 1


def test_llm_config_budget_matches_contract(monkeypatch):
    monkeypatch.delenv("LLM_MAX_RETRIES", raising=False)
    assert LLMConfig().max_retries == get_budget("llm_transient")
    monkeypatch.setenv("LLM_MAX_RETRIES", "7")
    assert LLMConfig().max_retries == 7


def test_mcp_retry_budget_matches_contract(monkeypatch):
    client = RagMCPClient(_MCPConfig())
    client._loop = MagicMock()
    client._reset = lambda: None
    calls = {"n": 0}

    def fake_do_call(name, args):
        calls["n"] += 1
        raise MCPBoundaryError(MCPErrorCode.MCP_TIMEOUT, f"t{calls['n']}")

    monkeypatch.setattr(client, "_do_call", fake_do_call)
    with pytest.raises(MCPBoundaryError):
        client._call_with_retry("search_dictionary", {"query": "x"})
    assert calls["n"] == get_budget("mcp")
