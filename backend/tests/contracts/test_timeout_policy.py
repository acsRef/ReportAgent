"""Timeout 统一 policy matrix（P15 reliability 收口 ⑦，consolidated）。

把散在各模块的 timeout 钉子收成一张「timeout → classification → retry/no-retry →
terminal」表。四层各自的完整行为由既有模块测试钉；本文件钉**跨层不变量**（单点易漏）：

    层            | 值源                       | classification             | retry?            | terminal
    LLM request   | LAYER_DEFAULTS llm_request | LLM_TIMEOUT/timeout/可恢复  | 2（llm_transient） | 预算耗尽 → LLMTimeoutError → LLM_TIMEOUT
    MCP request   | LAYER_DEFAULTS mcp_request | 仅 TIMEOUT 可恢复          | mcp 1 retry        | 预算耗尽 → 显式 MCP_* envelope
    DB statement  | LAYER_DEFAULTS db_statement| SQL_TIMEOUT/timeout/agent 不可恢复 | 不 repair     | DiagnosePolicy fail；用户侧 QUERY_TIMEOUT 可重试
    background    | LAYER_DEFAULTS background | asyncio.TimeoutError        | 不 retry           | persist FAILED + TASK_TIMEOUT（⑧ live 钉）

**跨层不变量**：timeout ≠ 一律可修复——LLM/MCP timeout 是 transient（agent retry），DB
timeout agent 侧盲修无意义（fail），background timeout 是显式终态。任何把三者打成同一条
retry 路径的改动都会让本 matrix 红。
"""
from __future__ import annotations

import asyncio

import pytest

from app.agent.sql_graph import DiagnosePolicy
from app.reliability.errors import (
    classify_exception,
    classify_llm_exception,
    classify_mcp_error,
    classify_sql_kind,
    user_code,
    user_recoverable,
)
from app.reliability.retry import RETRY_BUDGETS
from app.reliability.timeout import LAYER_DEFAULTS, MAX_TASK_DURATION, run_with_timeout
from app.tools.mcp_errors import MCPBoundaryError, MCPErrorCode

pytestmark = pytest.mark.contracts


# --- LLM request timeout -------------------------------------------------


def test_llm_request_timeout_policy():
    """LLM request timeout → LLM_TIMEOUT / kind timeout / recoverable；固定预算 2。"""
    assert RETRY_BUDGETS["llm_transient"] == 2, "LLM transient 固定预算 2（宪法 §11）"
    # 单次请求超时（openai APITimeoutError 家族）
    from openai import APITimeoutError

    env = classify_llm_exception(APITimeoutError("slow"))
    assert (env.code, env.kind, env.recoverable) == (
        "LLM_TIMEOUT", "timeout", True,
    ), "LLM 单次 timeout → LLM_TIMEOUT / recoverable（transient retry）"
    # 预算耗尽终态：LLMTimeoutError 同归类，不再无限
    from app.reliability.retry import LLMTimeoutError

    env2 = classify_llm_exception(LLMTimeoutError())
    assert env2.code == "LLM_TIMEOUT"


# --- MCP request timeout ------------------------------------------------


def test_mcp_request_timeout_policy():
    """仅 MCP_TIMEOUT recoverable（客户端自动 retry）；UNAVAILABLE/INVALID 不可恢复。"""
    assert RETRY_BUDGETS["mcp"] == 2, "MCP 固定预算 2（1 initial + 1 retry）"
    assert classify_mcp_error(MCPBoundaryError(MCPErrorCode.MCP_TIMEOUT, "s")).recoverable is True
    assert classify_mcp_error(
        MCPBoundaryError(MCPErrorCode.MCP_UNAVAILABLE, "s")).recoverable is False
    assert classify_mcp_error(
        MCPBoundaryError(MCPErrorCode.MCP_INVALID_RESPONSE, "s")).recoverable is False
    # 逃逸到 SSE 出口的分类码是 MCP_*，不并进 QUERY_* 混淆
    assert classify_exception(MCPBoundaryError(MCPErrorCode.MCP_TIMEOUT, "s")).code == "MCP_TIMEOUT"


# --- DB statement timeout（不对称：agent fail、用户可重试） ---------------


def test_db_statement_timeout_policy():
    """DB timeout：agent 侧非 recoverable → DiagnosePolicy fail（盲 retry 无意义）；
    用户侧 QUERY_TIMEOUT recoverable（缩小范围重试有意义）——双轨分离契约。"""
    env = classify_sql_kind("timeout", message="query canceled")
    assert (env.code, env.kind, env.recoverable) == (
        "SQL_TIMEOUT", "timeout", False,
    ), "DB timeout agent 侧不得标 recoverable（否则 DiagnosePolicy 会盲 retry）"
    # DiagnosePolicy：timeout 有 budget 也不 repair → fail
    d = DiagnosePolicy.decide(
        error_kind="timeout", retry_counters={"sql_generation": 0, "plan": 0},
    )
    assert d.action == "fail", f"DB timeout 应 fail（不 retry_sql/不 replan）: {d.action}"
    # 用户侧：QUERY_TIMEOUT + recoverable（SSE canRetry 语义独立）
    assert user_code("timeout") == "QUERY_TIMEOUT"
    assert user_recoverable("timeout") is True


# --- Background task timeout（显式终态，不是 INTERNAL） -------------------


def test_background_timeout_policy():
    """background 超时是独立显式终态：预算默认 600s、asyncio.TimeoutError 不是
    LLM/MCP 家族（classify_exception 会归 INTERNAL——证明 main 的显式 TASK_TIMEOUT
    catch 不可省）。终态语义（persist FAILED + TASK_TIMEOUT SSE + phase≠generating）
    由 ⑧ live case 钉。"""
    assert LAYER_DEFAULTS["background_task"] == 600.0
    assert MAX_TASK_DURATION == LAYER_DEFAULTS["background_task"]
    env = classify_exception(asyncio.TimeoutError())
    assert env.code == "INTERNAL_ERROR", (
        "asyncio.TimeoutError 不在 LLM/MCP 家族；若无显式 catch 会落 INTERNAL——"
        "故 _run_confirmed_graph 必须先于泛化 except 捕获它转 TASK_TIMEOUT"
    )
