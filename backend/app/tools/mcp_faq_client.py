"""Compatibility shim（P2, docs/plans/2026-08-26-p2-rag-mcp-boundary.md Q6）。

本模块为兼容层：所有运行态（子进程 / session / 单例）由 mcp_client 持有。

依赖方向（单向，P0 钉子）：
    mcp_faq_client.py  →  mcp_client.py
    mcp_client.py  MUST NOT import mcp_faq_client（防循环依赖）

公开符号：
  - get_mcp_faq_client()  → 转发到 get_rag_mcp_client()
  - close_mcp_faq_client() → 转发到 close_rag_mcp_client()（无条件，幂等）
  - MCPFaqClient          = RagMCPClient（类型 alias）
  - MCPFaqClientError     = MCPBoundaryError（Q6：避免 alias 到 Exception 防 catch-all 复活）
"""
from __future__ import annotations

from app.tools.mcp_client import (
    RagMCPClient,
    close_rag_mcp_client,
    get_rag_mcp_client,
)
from app.tools.mcp_errors import MCPBoundaryError

# 兼容 alias：旧 import 路径仍可用
MCPFaqClient = RagMCPClient
MCPFaqClientError = MCPBoundaryError


def get_mcp_faq_client() -> RagMCPClient:
    """兼容别名。内部返回泛化单例。"""
    return get_rag_mcp_client()


def close_mcp_faq_client() -> None:
    """兼容别名。无条件委托 close，幂等。"""
    close_rag_mcp_client()
