"""Schema FAQ MCP client + faq_tools 主从切换测试（P2, smoke）。

P2 Task 2 重写后：
  - faq_tools.search_faq 走统一 mcp_client.call_tool("search_faq", ...)
  - catch 收紧：仅 MCPBoundaryError → fallback/error；其它异常向上抛
  - shim (mcp_faq_client) 仅保留兼容别名，行为由 mcp_client 决定

本文件覆盖主从切换行为契约（Task 2 后的 dispatcher 行为）。
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.tools import faq_tools
from app.tools.mcp_client import get_rag_mcp_client
from app.tools.mcp_errors import MCPBoundaryError, MCPErrorCode
from app.tools.mcp_faq_client import (
    MCPFaqClient,
    MCPFaqClientError,
    close_mcp_faq_client,
    get_mcp_faq_client,
)

pytestmark = pytest.mark.smoke


def _mcp_matches():
    return [
        {
            "chunk_id": "c1",
            "document_id": "d1",
            "title": "区域退货率",
            "text": "# 退货率\n示例 SQL:\nSELECT ...\n要点: 退货率 = 退货金额/销售额",
            "score": 0.8,
        }
    ]


def _local_matches():
    return faq_tools._search_faq_rows("退货率", top_k=3)


# --- faq_tools.search_faq 主从切换（Task 2 后：统一 client.call_tool）---


def test_mcp_primary_used_when_available(monkeypatch):
    """MCP 成功 → 走 MCP 路径，不落本地（本地命中以「各区域退货率」开头，MCP 是「区域退货率」）。"""
    fake = MagicMock()
    fake.call_tool.return_value = {"matches": _mcp_matches()}
    monkeypatch.setattr(faq_tools, "get_rag_mcp_client", lambda: fake)

    raw = faq_tools.search_faq.invoke({"query": "退货率", "top_k": 3})
    parsed = json.loads(raw)
    assert parsed["matches"] and parsed["matches"][0]["question"] == "区域退货率"
    fake.call_tool.assert_called_once_with(
        "search_faq", {"query": "退货率", "top_k": 3}
    )


def test_mcp_error_falls_back_to_local(monkeypatch):
    """MCP UNAVAILABLE + fallback allowed → 本地 seed 兜底。"""
    fake = MagicMock()
    fake.call_tool.side_effect = MCPBoundaryError(
        MCPErrorCode.MCP_UNAVAILABLE, "MCP FAQ 检索失败"
    )
    monkeypatch.setattr(faq_tools, "get_rag_mcp_client", lambda: fake)
    monkeypatch.setattr(faq_tools, "_fallback_allowed", lambda: True)

    raw = faq_tools.search_faq.invoke({"query": "退货率", "top_k": 3})
    parsed = json.loads(raw)
    assert parsed["matches"], "MCP 失败应降级本地并返回主题内容"
    # 本地命中含 sql/note 字段
    assert "sql" in parsed["matches"][0]


def test_mcp_not_configured_falls_back_to_local(monkeypatch):
    """MCP UNAVAILABLE（未配置）+ fallback allowed → 本地 seed。"""
    fake = MagicMock()
    fake.call_tool.side_effect = MCPBoundaryError(
        MCPErrorCode.MCP_UNAVAILABLE, "MCP FAQ 服务未配置"
    )
    monkeypatch.setattr(faq_tools, "get_rag_mcp_client", lambda: fake)
    monkeypatch.setattr(faq_tools, "_fallback_allowed", lambda: True)

    raw = faq_tools.search_faq.invoke({"query": "毛利率", "top_k": 3})
    parsed = json.loads(raw)
    assert parsed["matches"]
    assert "毛利率" in parsed["matches"][0]["question"]


def test_mcp_empty_returns_empty_legitimate_result(monkeypatch):
    """MCP 合法空命中（matches=[]）→ search_faq 返回空 matches（不算错，决策 4）。

    P2 契约变更：旧行为是「MCP 空 + fallback → 落本地 seed」；新行为是 EMPTY_RESULT
    与 unavailable 严格区分（伞形 §八 + plan 决策 4）——空命中是合法返回，不触发 fallback。
    """
    fake = MagicMock()
    fake.call_tool.return_value = {"matches": []}
    monkeypatch.setattr(faq_tools, "get_rag_mcp_client", lambda: fake)
    monkeypatch.setattr(faq_tools, "_fallback_allowed", lambda: True)

    raw = faq_tools.search_faq.invoke({"query": "退货率", "top_k": 3})
    parsed = json.loads(raw)
    assert parsed["matches"] == [], "MCP 空命中是合法结果，不应触发本地 seed fallback"


def test_mcp_unavailable_flag_locked_returns_error_json(monkeypatch):
    """flag 锁定 → MCP UNAVAILABLE → JSON error，不落本地。"""
    fake = MagicMock()
    fake.call_tool.side_effect = MCPBoundaryError(
        MCPErrorCode.MCP_UNAVAILABLE, "down"
    )
    monkeypatch.setattr(faq_tools, "get_rag_mcp_client", lambda: fake)
    monkeypatch.setattr(faq_tools, "_fallback_allowed", lambda: False)

    raw = faq_tools.search_faq.invoke({"query": "退货率", "top_k": 3})
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "MCP_UNAVAILABLE" in parsed["error"]


# --- P2 shim 契约（替代旧 bridge 契约测试）---


def test_shim_get_mcp_faq_client_returns_rag_mcp_client():
    """shim 必须返回 RagMCPClient 同一实例（Q6 单例 owner 唯一）。"""
    from app.tools.mcp_client import RagMCPClient

    # 显式重置单例以确保本测试独立
    import app.tools.mcp_client as mod
    mod._client = None
    try:
        a = get_mcp_faq_client()
        b = get_rag_mcp_client()
        assert a is b
        assert isinstance(a, RagMCPClient)
    finally:
        close_mcp_faq_client()


def test_shim_mcp_faq_client_error_aliases_to_mcp_boundary_error():
    """MCPFaqClientError 是 MCPBoundaryError 子类，保留旧单参构造。"""
    from app.tools.mcp_errors import MCPBoundaryError, MCPErrorCode

    assert issubclass(MCPFaqClientError, MCPBoundaryError)

    # 旧方式：单参数 detail（默认 code = MCP_UNAVAILABLE）
    err = MCPFaqClientError("MCP FAQ 检索失败")
    assert isinstance(err, MCPBoundaryError)
    assert err.code is MCPErrorCode.MCP_UNAVAILABLE
    assert err.detail == "MCP FAQ 检索失败"

    # 新方式：显式 code
    err2 = MCPFaqClientError("other detail", MCPErrorCode.MCP_TIMEOUT)
    assert err2.code is MCPErrorCode.MCP_TIMEOUT
    assert err2.detail == "other detail"
