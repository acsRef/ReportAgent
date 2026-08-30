"""统一错误分类单一来源（P9，伞形 plan §七 / 宪法 §11）。

两套码的关系（有意双轨，勿合并）：
- ``ErrorCode``（10 码最小集）：runtime 内部分类 / trace metadata / persist error_detail。
- ``user_code``（QUERY_* 家族）：SSE 稳定契约，前端 analysisReducer / confirmStream 已消费，
  改值即破约——只许换来源，不许换值。

两张 recoverable 表也有意分离：
- ``AGENT_RECOVERABLE_KINDS``：agent 侧 repair 语义（P8 DiagnosePolicy 三轮 review 拍板，
  timeout/connection/permission 盲修无意义 → fail）。
- ``USER_RECOVERABLE_KINDS``：SSE canRetry 语义（用户缩小范围重试有意义）。
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel

# SQL 6 kind —— app/tools/sql_tools.py:_classify_psycopg2_error 的输出域。
SQL_ERROR_KINDS = ("syntax", "object", "timeout", "connection", "permission", "other")

# Agent 侧（DiagnosePolicy 消费）：syntax/object/other 可 repair。
AGENT_RECOVERABLE_KINDS = ("syntax", "object", "other")

# 用户侧（SSE _build_sse_error 消费）：timeout/connection/object/other 用户重试有意义。
USER_RECOVERABLE_KINDS = ("timeout", "connection", "object", "other")


class ErrorCode(str, Enum):
    """统一错误码表（伞形 §七最小集）。"""

    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    MCP_TIMEOUT = "MCP_TIMEOUT"
    MCP_UNAVAILABLE = "MCP_UNAVAILABLE"
    MCP_INVALID_RESPONSE = "MCP_INVALID_RESPONSE"
    SQL_SYNTAX_ERROR = "SQL_SYNTAX_ERROR"
    SQL_EXECUTION_ERROR = "SQL_EXECUTION_ERROR"
    SQL_TIMEOUT = "SQL_TIMEOUT"
    REPORT_VALIDATION_ERROR = "REPORT_VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorEnvelope(BaseModel):
    """统一错误信封（伞形 §七 / §6.10 示例形状）。

    ``recoverable`` 是 agent/system 侧语义（transient → retry；permanent → 不 retry；
    agent-recoverable → repair）。用户侧可重试请用 ``user_recoverable()``。
    """

    code: str
    kind: str
    recoverable: bool
    failed_action: str
    message: str = ""


def agent_recoverable(kind: str) -> bool:
    """纯集合成员判断，不做隐藏 normalize（调用方先归一 kind）。"""
    return kind in AGENT_RECOVERABLE_KINDS


def normalize_kind(kind: Optional[str]) -> str:
    """未知 / 空 kind 归一为 other（main.py 收编语义）。"""
    return kind if kind in SQL_ERROR_KINDS else "other"


def user_recoverable(kind: Optional[str]) -> bool:
    return normalize_kind(kind) in USER_RECOVERABLE_KINDS


def kind_to_error_code(kind: Optional[str]) -> ErrorCode:
    """SQL kind → runtime 码：syntax/timeout 独立成码，其余归 SQL_EXECUTION_ERROR。"""
    kind = normalize_kind(kind)
    if kind == "syntax":
        return ErrorCode.SQL_SYNTAX_ERROR
    if kind == "timeout":
        return ErrorCode.SQL_TIMEOUT
    return ErrorCode.SQL_EXECUTION_ERROR


def classify_sql_kind(
    kind: Optional[str], message: str = "", failed_action: str = "sql"
) -> ErrorEnvelope:
    kind = normalize_kind(kind)
    return ErrorEnvelope(
        code=kind_to_error_code(kind).value,
        kind=kind,
        recoverable=agent_recoverable(kind),
        failed_action=failed_action,
        message=message,
    )


def classify_llm_exception(exc: BaseException, failed_action: str = "llm") -> ErrorEnvelope:
    """LLM 家族异常 → envelope。openai 惰性 import（与 retry._classify_retryable 同模式）。"""
    from app.reliability.retry import LLMRateLimitExceeded, LLMTimeoutError

    if isinstance(exc, (LLMTimeoutError, LLMRateLimitExceeded)):
        return ErrorEnvelope(code=ErrorCode.LLM_TIMEOUT.value, kind="timeout", recoverable=True, failed_action=failed_action)
    try:
        from openai import (
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )
    except ImportError:  # pragma: no cover - openai 是必装依赖，防御性兜底
        return ErrorEnvelope(code=ErrorCode.INTERNAL_ERROR.value, kind="other", recoverable=False, failed_action=failed_action)
    if isinstance(exc, APITimeoutError):
        return ErrorEnvelope(code=ErrorCode.LLM_TIMEOUT.value, kind="timeout", recoverable=True, failed_action=failed_action)
    if isinstance(exc, (RateLimitError, InternalServerError, APIConnectionError)):
        # 瞬时服务故障重试预算耗尽 → 服务暂不可用；kind 借用 6-kind 词汇表的 connection。
        return ErrorEnvelope(code=ErrorCode.LLM_UNAVAILABLE.value, kind="connection", recoverable=True, failed_action=failed_action)
    # 认证/参数等 Permanent 错 → INTERNAL_ERROR（10 码表无独立 auth 码，fail 不 retry）。
    return ErrorEnvelope(code=ErrorCode.INTERNAL_ERROR.value, kind="other", recoverable=False, failed_action=failed_action)


# MCP 码表以值字符串 keyed——**不 import app.tools.mcp_errors**（P2 边界 freeze：
# boundary 自身只能被 tools/ 包引用，tests/contracts/test_mcp_boundary_freeze.py 钉住）。
# 鸭子类型读 exc.code（MCPBoundaryError.code 为 MCPErrorCode(str, Enum)，.value 即码值）。
_MCP_ENVELOPES: dict[str, ErrorEnvelope] = {
    "MCP_TIMEOUT": ErrorEnvelope(
        code=ErrorCode.MCP_TIMEOUT.value, kind="timeout", recoverable=True, failed_action="mcp"
    ),
    "MCP_UNAVAILABLE": ErrorEnvelope(
        code=ErrorCode.MCP_UNAVAILABLE.value, kind="connection", recoverable=False, failed_action="mcp"
    ),
    "MCP_INVALID_RESPONSE": ErrorEnvelope(
        code=ErrorCode.MCP_INVALID_RESPONSE.value, kind="other", recoverable=False, failed_action="mcp"
    ),
}


def _mcp_code_value(exc: BaseException) -> Optional[str]:
    """鸭子类型提取 MCP 码值；非 MCP 形状（无 .code 或码值不在表内）返回 None。"""
    code = getattr(exc, "code", None)
    value = getattr(code, "value", code)
    return value if isinstance(value, str) and value in _MCP_ENVELOPES else None


def classify_mcp_error(exc: BaseException, failed_action: str = "mcp") -> ErrorEnvelope:
    """MCP 边界错误 → envelope（期望 mcp_errors.MCPBoundaryError 形状，鸭子类型）。

    映射消费 P2 的显式分类，不重写边界语义；非 MCP 形状抛 ValueError。
    """
    value = _mcp_code_value(exc)
    if value is None:
        raise ValueError(f"not an MCP boundary error: {exc!r}")
    env = _MCP_ENVELOPES[value].model_copy()
    env.failed_action = failed_action
    detail = getattr(exc, "detail", "")
    if detail:
        env.message = detail
    return env


def classify_exception(exc: BaseException, failed_action: str = "internal") -> ErrorEnvelope:
    """泛化分类入口：MCP / LLM 家族各归各位，其余 INTERNAL_ERROR 兜底。"""
    if _mcp_code_value(exc) is not None:
        return classify_mcp_error(exc, failed_action=failed_action)
    try:
        from openai import APIError  # noqa: F401 - 仅探测 openai 家族

        llm_family = (APIError,)
    except ImportError:  # pragma: no cover
        llm_family = ()
    from app.reliability.retry import LLMRateLimitExceeded, LLMTimeoutError

    if isinstance(exc, (LLMTimeoutError, LLMRateLimitExceeded)) or (
        llm_family and isinstance(exc, llm_family)
    ):
        return classify_llm_exception(exc, failed_action=failed_action)
    return ErrorEnvelope(
        code=ErrorCode.INTERNAL_ERROR.value, kind="other", recoverable=False, failed_action=failed_action
    )


# --- 用户视图（SSE 稳定契约，值与 P9 收编前 main.py 内联表逐字相等） ----------

_USER_MESSAGES: dict[str, str] = {
    "timeout":    "查询超时,请缩小时间范围或维度后重试",
    "connection": "数据库连接失败,请稍后重试",
    "permission": "权限不足,无法执行该查询",
    "syntax":     "SQL 语法错误,请调整查询条件后重试",
    "object":     "查询引用的表/列不存在,请检查维度后重试",
    "other":      "查询执行失败,请稍后重试或调整需求",
}

_USER_CODES: dict[str, str] = {
    "timeout":    "QUERY_TIMEOUT",
    "connection": "QUERY_CONNECTION",
    "permission": "QUERY_PERMISSION",
    "syntax":     "QUERY_SYNTAX",
    "object":     "QUERY_OBJECT",
    "other":      "QUERY_FAILED",
}


def user_message(kind: Optional[str]) -> str:
    return _USER_MESSAGES[normalize_kind(kind)]


def user_code(kind: Optional[str]) -> str:
    return _USER_CODES[normalize_kind(kind)]
