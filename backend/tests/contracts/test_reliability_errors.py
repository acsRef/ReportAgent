"""P9 reliability/errors.py 契约：ErrorEnvelope + 10 码 + 两张 recoverable 表 + classify 全家。

防漂移钉：user_message / user_code 的 6 组值必须与 P9 收编前 main.py 内联表逐字相等
（前端已消费的稳定契约，见 docs/sse-v2.md 与 confirmStream.test.ts）。
"""
from __future__ import annotations

import httpx
import pytest
from openai import APITimeoutError, AuthenticationError, InternalServerError, RateLimitError

from app.reliability.errors import (
    AGENT_RECOVERABLE_KINDS,
    SQL_ERROR_KINDS,
    USER_RECOVERABLE_KINDS,
    ErrorEnvelope,
    ErrorCode,
    agent_recoverable,
    classify_exception,
    classify_llm_exception,
    classify_mcp_error,
    classify_sql_kind,
    kind_to_error_code,
    user_code,
    user_message,
    user_recoverable,
)
from app.tools.mcp_errors import MCPBoundaryError, MCPErrorCode


def _req() -> httpx.Request:
    return httpx.Request("POST", "https://api.minimax.chat/v1/chat/completions")


def _status_error(cls, code: int):
    req = _req()
    resp = httpx.Response(code, request=req)
    return cls(message="boom", response=resp, body=None)


# --- ErrorCode 10 码最小集（伞形 §七） -------------------------------------


def test_error_code_has_exactly_the_ten_contract_codes():
    assert {c.value for c in ErrorCode} == {
        "LLM_TIMEOUT", "LLM_UNAVAILABLE",
        "MCP_TIMEOUT", "MCP_UNAVAILABLE", "MCP_INVALID_RESPONSE",
        "SQL_SYNTAX_ERROR", "SQL_EXECUTION_ERROR", "SQL_TIMEOUT",
        "REPORT_VALIDATION_ERROR", "INTERNAL_ERROR",
    }


# --- 两张 recoverable 表显式分离 --------------------------------------------


def test_sql_error_kinds_vocabulary():
    assert set(SQL_ERROR_KINDS) == {"syntax", "object", "timeout", "connection", "permission", "other"}


def test_agent_recoverable_table_matches_p8_verdict():
    # P8 DiagnosePolicy 拍板：timeout/connection/permission repair 无意义 → fail
    assert set(AGENT_RECOVERABLE_KINDS) == {"syntax", "object", "other"}
    assert agent_recoverable("syntax") is True
    assert agent_recoverable("object") is True
    assert agent_recoverable("other") is True
    assert agent_recoverable("timeout") is False
    assert agent_recoverable("connection") is False
    assert agent_recoverable("permission") is False
    # 纯集合成员判断，不做隐藏 normalize
    assert agent_recoverable("weird") is False


def test_user_recoverable_table_matches_sse_contract():
    # SSE canRetry 语义（前端 analysisReducer 已钉）：用户重试有意义
    assert set(USER_RECOVERABLE_KINDS) == {"timeout", "connection", "object", "other"}
    assert user_recoverable("timeout") is True
    assert user_recoverable("connection") is True
    assert user_recoverable("object") is True
    assert user_recoverable("other") is True
    assert user_recoverable("syntax") is False
    assert user_recoverable("permission") is False


def test_user_helpers_normalize_unknown_kind_to_other():
    # main.py 现状语义：未知 kind → other 再查表
    assert user_recoverable("weird") is True
    assert user_code("weird") == "QUERY_FAILED"
    assert user_message("weird") == "查询执行失败,请稍后重试或调整需求"


# --- kind → runtime ErrorCode 映射 ------------------------------------------


def test_kind_to_error_code_mapping():
    assert kind_to_error_code("syntax") is ErrorCode.SQL_SYNTAX_ERROR
    assert kind_to_error_code("timeout") is ErrorCode.SQL_TIMEOUT
    assert kind_to_error_code("object") is ErrorCode.SQL_EXECUTION_ERROR
    assert kind_to_error_code("connection") is ErrorCode.SQL_EXECUTION_ERROR
    assert kind_to_error_code("permission") is ErrorCode.SQL_EXECUTION_ERROR
    assert kind_to_error_code("other") is ErrorCode.SQL_EXECUTION_ERROR
    assert kind_to_error_code("weird") is ErrorCode.SQL_EXECUTION_ERROR


def test_classify_sql_kind_six_kinds():
    env = classify_sql_kind("timeout", "statement timeout")
    assert isinstance(env, ErrorEnvelope)
    assert env.code == "SQL_TIMEOUT"
    assert env.kind == "timeout"
    assert env.recoverable is False  # agent 侧语义：timeout 不 repair
    assert env.failed_action == "sql"
    assert env.message == "statement timeout"

    assert classify_sql_kind("syntax").code == "SQL_SYNTAX_ERROR"
    assert classify_sql_kind("syntax").recoverable is True
    for kind in ("object", "connection", "permission", "other"):
        assert classify_sql_kind(kind).code == "SQL_EXECUTION_ERROR"
    # 未知 kind 归一 other
    weird = classify_sql_kind("weird")
    assert weird.code == "SQL_EXECUTION_ERROR"
    assert weird.kind == "other"


# --- LLM 异常分类 ------------------------------------------------------------


def test_classify_llm_exception_timeout_family():
    env = classify_llm_exception(APITimeoutError(request=_req()))
    assert env.code == "LLM_TIMEOUT"
    assert env.kind == "timeout"
    assert env.recoverable is True  # 伞形 §6.10 示例：LLM_TIMEOUT recoverable=true
    assert env.failed_action == "llm"


def test_classify_llm_exception_budget_exhausted_is_timeout():
    from app.llm_resilience import LLMTimeoutError, LLMRateLimitExceeded

    for exc in (LLMTimeoutError("budget"), LLMRateLimitExceeded("wait")):
        env = classify_llm_exception(exc)
        assert env.code == "LLM_TIMEOUT"
        assert env.recoverable is True


def test_classify_llm_exception_transient_service_errors():
    for exc in (
        _status_error(RateLimitError, 429),
        _status_error(InternalServerError, 500),
    ):
        env = classify_llm_exception(exc)
        assert env.code == "LLM_UNAVAILABLE"
        assert env.kind == "connection"
        assert env.recoverable is True


def test_classify_llm_exception_permanent_falls_to_internal():
    env = classify_llm_exception(_status_error(AuthenticationError, 401))
    assert env.code == "INTERNAL_ERROR"
    assert env.recoverable is False


# --- MCP 边界错误映射（消费 P2 MCPErrorCode，不重写边界） ---------------------


def test_classify_mcp_error_three_codes():
    env = classify_mcp_error(MCPBoundaryError(MCPErrorCode.MCP_TIMEOUT, "slow"))
    assert env.code == "MCP_TIMEOUT"
    assert env.kind == "timeout"
    assert env.recoverable is True  # 伞形 §190：MCP timeout → retry

    env = classify_mcp_error(MCPBoundaryError(MCPErrorCode.MCP_UNAVAILABLE, "down"))
    assert env.code == "MCP_UNAVAILABLE"
    assert env.recoverable is False

    env = classify_mcp_error(MCPBoundaryError(MCPErrorCode.MCP_INVALID_RESPONSE, "junk"))
    assert env.code == "MCP_INVALID_RESPONSE"
    assert env.recoverable is False
    assert all(e.failed_action == "mcp" for e in (env,))


def test_classify_mcp_error_rejects_non_mcp_shape():
    # 鸭子类型契约：无 .code 或码值不在 MCP 三码内 → ValueError（不静默兜底）
    with pytest.raises(ValueError):
        classify_mcp_error(ValueError("plain"))


# --- 泛化分类入口（dispatcher） ----------------------------------------------


def test_classify_exception_dispatches_llm_and_mcp():
    assert classify_exception(APITimeoutError(request=_req())).code == "LLM_TIMEOUT"
    mcp_env = classify_exception(MCPBoundaryError(MCPErrorCode.MCP_TIMEOUT, "t"))
    assert mcp_env.code == "MCP_TIMEOUT"


def test_classify_exception_generic_falls_to_internal():
    env = classify_exception(ValueError("boom"), failed_action="confirm")
    assert env.code == "INTERNAL_ERROR"
    assert env.kind == "other"
    assert env.recoverable is False
    assert env.failed_action == "confirm"


# --- 用户视图表防漂移钉（与 P9 收编前 main.py 逐字相等） ----------------------


def test_user_message_exact_pinned_values():
    assert user_message("timeout") == "查询超时,请缩小时间范围或维度后重试"
    assert user_message("connection") == "数据库连接失败,请稍后重试"
    assert user_message("permission") == "权限不足,无法执行该查询"
    assert user_message("syntax") == "SQL 语法错误,请调整查询条件后重试"
    assert user_message("object") == "查询引用的表/列不存在,请检查维度后重试"
    assert user_message("other") == "查询执行失败,请稍后重试或调整需求"


def test_user_code_exact_pinned_values():
    assert user_code("timeout") == "QUERY_TIMEOUT"
    assert user_code("connection") == "QUERY_CONNECTION"
    assert user_code("permission") == "QUERY_PERMISSION"
    assert user_code("syntax") == "QUERY_SYNTAX"
    assert user_code("object") == "QUERY_OBJECT"
    assert user_code("other") == "QUERY_FAILED"
