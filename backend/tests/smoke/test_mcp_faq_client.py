"""Schema FAQ MCP client + faq_tools 主从切换测试（P2, smoke）。

P2 架构变化后：
  - mcp_faq_client 变为薄 shim，所有状态由 mcp_client 持有（Q6）
  - 旧 MCPFaqClient._call 同步桥接契约已被 RagMCPClient.call_tool 替代
  - 4 个旧 bridge 契约测试已删除（其覆盖范围迁移至 contracts/test_mcp_client.py）
  - 当前 faq_tools 的 catch-all + local-seed fallback 行为属 Task 2 重写范围
    （Q6 决议：仅 catch MCPBoundaryError + flag-gated）；本文件 3 个主从切换测试
    在 Task 2 之前继续覆盖当前行为，Task 2 实施时同步更新。

离线——patch 掉 mcp_client / 后台协程，不打真实 ragent-py 子进程。
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.tools import faq_tools
from app.tools.mcp_client import get_rag_mcp_client
from app.tools.mcp_faq_client import (
    MCPFaqClient,
    MCPFaqClientError,
    close_mcp_faq_client,
    get_mcp_faq_client,
)

pytestmark = pytest.mark.smoke


def _mcp_matches():
    return [{"question": "区域退货率", "text": "# 退货率\n示例 SQL:\nSELECT ...\n要点: 退货率 = 退货金额/销售额",
             "score": 0.8}]


def _local_matches():
    return faq_tools._search_faq_rows("退货率", top_k=3)


# --- faq_tools.search_faq 主从切换（当前 catch-all 行为；Task 2 重写）---


def test_mcp_primary_used_when_available(monkeypatch):
    fake = type("Fake", (), {"search_faq": lambda self, q, top_k=3: json.dumps(
        {"matches": _mcp_matches()}, ensure_ascii=False)})()
    monkeypatch.setattr(faq_tools, "get_mcp_faq_client", lambda: fake)
    raw = faq_tools.search_faq.invoke({"query": "退货率", "top_k": 3})
    parsed = json.loads(raw)
    assert parsed["matches"] and parsed["matches"][0]["question"] == "区域退货率"
    # 走 MCP，不落本地（本地命中是「各区域退货率」开头，MCP 是「区域退货率」）
    assert parsed["matches"][0]["question"].startswith("区域退货率")


def test_mcp_error_falls_back_to_local(monkeypatch):
    def _boom(query, top_k):
        raise MCPFaqClientError("MCP FAQ 检索失败")

    fake = type("Fake", (), {"search_faq": _boom})()
    monkeypatch.setattr(faq_tools, "get_mcp_faq_client", lambda: fake)
    raw = faq_tools.search_faq.invoke({"query": "退货率", "top_k": 3})
    parsed = json.loads(raw)
    assert parsed["matches"], "MCP 失败应降级本地并返回主题内容"
    # 本地命中含 sql/note 字段
    assert "sql" in parsed["matches"][0]


def test_mcp_not_configured_falls_back_to_local(monkeypatch):
    def _unconfigured(query, top_k):
        raise MCPFaqClientError("MCP FAQ 服务未配置")

    fake = type("Fake", (), {"search_faq": _unconfigured})()
    monkeypatch.setattr(faq_tools, "get_mcp_faq_client", lambda: fake)
    raw = faq_tools.search_faq.invoke({"query": "毛利率", "top_k": 3})
    parsed = json.loads(raw)
    assert parsed["matches"]
    assert "毛利率" in parsed["matches"][0]["question"]


def test_mcp_empty_with_local_available_no_crash(monkeypatch):
    fake = type("Fake", (), {"search_faq": lambda self, q, top_k=3: '{"matches": []}'})()
    monkeypatch.setattr(faq_tools, "get_mcp_faq_client", lambda: fake)
    raw = faq_tools.search_faq.invoke({"query": "退货率", "top_k": 3})
    parsed = json.loads(raw)
    # MCP 空 → 落到本地 seed（SQL 生成不崩）
    assert parsed["matches"]


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
