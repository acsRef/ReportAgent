"""Schema FAQ MCP client + faq_tools 主从切换测试。

离线——patch 掉 MCP client / 后台协程，不打真实 ragent-py 子进程。
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.tools import faq_tools
from app.tools.mcp_faq_client import MCPFaqClient, MCPFaqClientError, _MCPFaqConfig

pytestmark = pytest.mark.smoke


def _mcp_matches():
    return [{"question": "区域退货率", "text": "# 退货率\n示例 SQL:\nSELECT ...\n要点: 退货率 = 退货金额/销售额",
             "score": 0.8}]


def _local_matches():
    return faq_tools._search_faq_rows("退货率", top_k=3)


# --- faq_tools.search_faq 主从切换 ---


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


# --- MCPFaqClient 同步桥接契约 ---


def test_client_search_faq_bridges_async(monkeypatch):
    config = _MCPFaqConfig()
    config.python = "python"
    config.cwd = "."
    client = MCPFaqClient(config)

    async def _fake_call(self, query, top_k):
        return '{"matches": [{"question": "q", "text": "t", "score": 1.0}]}'

    monkeypatch.setattr(MCPFaqClient, "_call", _fake_call)
    out = client.search_faq("退货率", 3)
    parsed = json.loads(out)
    assert parsed["matches"][0]["question"] == "q"


def test_client_search_faq_timeout_raises(monkeypatch):
    config = _MCPFaqConfig()
    config.python = "python"
    config.cwd = "."
    config.timeout = 0.01
    client = MCPFaqClient(config)

    async def _hanging_call(self, query, top_k):
        import asyncio
        await asyncio.sleep(1)
        return ""

    monkeypatch.setattr(MCPFaqClient, "_call", _hanging_call)
    with pytest.raises(MCPFaqClientError):
        client.search_faq("退货率", 3)


def test_client_unconfigured_raises():
    config = _MCPFaqConfig()
    config.python = ""
    config.cwd = "."
    client = MCPFaqClient(config)
    with pytest.raises(MCPFaqClientError):
        client.search_faq("退货率", 3)


def test_client_close_cleans_thread_and_loop(monkeypatch):
    """close() 幂等、停掉后台循环线程、清空会话状态（防子进程孤儿化）。"""
    from app.tools.mcp_faq_client import MCPFaqClient as _C

    config = _MCPFaqConfig()
    config.python = "python"
    config.cwd = "."
    client = _C(config)

    async def _fake_call(self, query, top_k):
        return '{"matches": []}'

    monkeypatch.setattr(_C, "_call", _fake_call)
    client.search_faq("退货率", 3)  # 触发后台线程 + 循环
    assert client._loop is not None and client._thread is not None

    client.close()
    assert client._loop is None
    assert client._thread is None
    assert client._session is None
    # 幂等：再 close 不抛
    client.close()


def test_close_mcp_faq_client_idempotent(monkeypatch):
    from app.tools import mcp_faq_client as mfc

    monkeypatch.setattr(mfc, "_client", None)
    mfc.close_mcp_faq_client()  # 未初始化时调 close 不抛
    assert mfc._client is None