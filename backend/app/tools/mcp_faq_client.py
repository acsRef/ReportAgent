"""Compatibility shim（P2, docs/plans/2026-08-26-p2-rag-mcp-boundary.md Q6）。

本模块为兼容层：所有运行态（子进程 / session / 单例）由 mcp_client 持有。

依赖方向（单向，P0 钉子）：
    mcp_faq_client.py  →  mcp_client.py
    mcp_client.py  MUST NOT import mcp_faq_client（防循环依赖）

公开符号：
  - get_mcp_faq_client()  → 转发到 get_rag_mcp_client()
  - close_mcp_faq_client() → 转发到 close_rag_mcp_client()（无条件，幂等）
  - MCPFaqClient          = RagMCPClient（类型 alias）
  - MCPFaqClientError     = MCPBoundaryError 子类（review 修订）：
                            保留旧 `raise MCPFaqClientError("msg")` 单参构造，
                            detail 默认为 MCP_UNAVAILABLE。
                            Task 2 之后 faq_tools 改为只 catch MCPBoundaryError；
                            本类仅作旧 import / 旧测试兼容层。
"""
from __future__ import annotations

from app.tools.mcp_client import (
    RagMCPClient,
    close_rag_mcp_client,
    get_rag_mcp_client,
)
from app.tools.mcp_errors import MCPBoundaryError, MCPErrorCode

# 兼容 alias：旧 import 路径仍可用
MCPFaqClient = RagMCPClient


class MCPFaqClientError(MCPBoundaryError):
    """兼容异常类（review 修订）。

    旧 MCPFaqClientError 构造为 `MCPFaqClientError("msg")` 单参数；
    新 MCPBoundaryError 是 `MCPBoundaryError(code, detail)` 双参数。
    通过子类 + 关键字默认值保留旧构造方式，同时仍是 MCPBoundaryError 实例
    （可被 except MCPBoundaryError 捕获）。
    """

    def __init__(
        self,
        detail: str,
        code: MCPErrorCode = MCPErrorCode.MCP_UNAVAILABLE,
    ):
        super().__init__(code, detail)


def get_mcp_faq_client() -> RagMCPClient:
    """兼容别名。内部返回泛化单例。"""
    return get_rag_mcp_client()


def close_mcp_faq_client() -> None:
    """兼容别名。无条件委托 close，幂等。"""
    close_rag_mcp_client()
