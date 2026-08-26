"""MCP 边界错误语义（P2, docs/plans/2026-08-26-p2-rag-mcp-boundary.md Q2/Q4）。

五分类中 quality-insufficient 属 P14，不在本枚举。
Retry 预算固定 max_attempts=2（1 initial + 1 retry），仅对 MCP_TIMEOUT 生效。
"""
from __future__ import annotations

from enum import Enum


class MCPErrorCode(str, Enum):
    MCP_TIMEOUT = "MCP_TIMEOUT"
    MCP_UNAVAILABLE = "MCP_UNAVAILABLE"
    MCP_INVALID_RESPONSE = "MCP_INVALID_RESPONSE"


class MCPBoundaryError(RuntimeError):
    """MCP 边界失败——code 显式分类，禁止被吞成空数组。"""

    def __init__(self, code: MCPErrorCode, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}")
