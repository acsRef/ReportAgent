"""Schema RAG Phase 1：FAQ 知识库检索测试（faq_tools 后端 + MCP registry parity）。

离线，读 backend/scripts/schema_faq.json；不打真实 API、不连 PG。

P2 Task 2 增量：search_faq 走统一 mcp_client.call_tool("search_faq", ...)；
catch 收紧（仅 MCPBoundaryError → fallback 或 error JSON，不再裸 except）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.tools import faq_tools
from app.tools.mcp_errors import MCPBoundaryError, MCPErrorCode

pytestmark = pytest.mark.smoke

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


# --- faq_tools（SQL Agent 使用路径：注册工具 + 纯检索） ---


def test_search_faq_rows_matches_return_rate():
    rows = faq_tools._search_faq_rows("退货率", top_k=3)
    assert rows
    top = rows[0]
    assert top["score"] > 0
    assert "退货率" in top["question"]
    assert "SELECT" in top["sql"]
    assert top["note"]


def test_search_faq_rows_no_match_returns_empty():
    assert faq_tools._search_faq_rows("完全无关的乱码词汇xyz", top_k=3) == []


def test_search_faq_rows_sales_ranking_ranked_first():
    rows = faq_tools._search_faq_rows("各区域销售额排名", top_k=3)
    assert rows
    assert rows[0]["question"] == "各区域销售额排名"
    assert len(rows) <= 3


def test_search_faq_rows_top_k_bounds():
    rows = faq_tools._search_faq_rows("销售 区域 退货 库存 考勤 毛利率", top_k=20)
    assert len(rows) <= 20
    assert len(rows) > 0


def test_search_faq_rows_missing_file_degrades(monkeypatch):
    faq_tools._FAQ_ENTRIES = None
    monkeypatch.setattr(
        faq_tools,
        "_FAQ_PATH",
        Path(__file__).parent / "does_not_exist_schema_faq.json",
    )
    try:
        assert faq_tools._search_faq_rows("退货率", top_k=3) == []
    finally:
        # 清缓存：否则 _load_faq 会把「缺失 → 空列表」固化，污染后续测试
        faq_tools._FAQ_ENTRIES = None


def test_search_faq_tool_invoke_returns_json_matches():
    """@tool 契约：.invoke 返回 JSON 字符串，matches 为命中案例。"""
    raw = faq_tools.search_faq.invoke({"query": "退货率", "top_k": 3})
    assert isinstance(raw, str)
    parsed = json.loads(raw)
    assert isinstance(parsed.get("matches"), list)
    assert parsed["matches"]
    assert "SELECT" in parsed["matches"][0]["sql"]


def test_search_faq_tool_invoke_no_match_empty():
    raw = faq_tools.search_faq.invoke({"query": "完全无关的乱码词汇xyz", "top_k": 3})
    parsed = json.loads(raw)
    assert parsed["matches"] == []


def test_search_faq_registered_in_tool_registry(monkeypatch):
    """一等注册工具：register_all_tools 后 registry 能取到 search_faq。"""
    from app.tools import register_all_tools
    from app.tools.registry import registry

    register_all_tools()
    tools = registry.get(["search_faq"])
    assert tools and tools[0] is faq_tools.search_faq
    meta = registry.get_metadata("search_faq")
    assert meta is not None and meta.capability == "faq_search"


# --- MCP registry parity ---


def test_mcp_registry_search_faq(monkeypatch):
    # MCP package 在仓库根，不在 backend/ 测试路径；临时挂上去验证 parity。
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from mcp_schema_server.registry import registry

    rows = registry.search_faq("退货率", top_k=3)
    assert rows
    assert "SELECT" in rows[0]["sql"]
    # 与后端路径口径一致：命中的是退货率 FAQ
    assert "退货率" in rows[0]["question"]


# ─────────────────────────────────────────────────────────────
# P2 Task 2: MCP-first dispatcher + catch 收紧
# ─────────────────────────────────────────────────────────────


def _mock_mcp(monkeypatch, *, matches=None, side_effect=None):
    """在 faq_tools 模块 patch get_rag_mcp_client。"""
    fake = MagicMock()
    if side_effect is not None:
        fake.call_tool.side_effect = side_effect
    else:
        fake.call_tool.return_value = {"matches": matches or []}
    monkeypatch.setattr(faq_tools, "get_rag_mcp_client", lambda: fake)
    return fake


class TestSearchFaqDispatcher:
    """search_faq 的 MCP-first dispatcher + catch 收紧行为。

    契约（docs/plans/2026-08-26-p2-rag-mcp-boundary.md 决策 1/4 + Q6 决议）：
      - MCP 成功 → 走 MCP 路径
      - MCPBoundaryError(UNAVAILABLE) + fallback → 本地 seed 兜底
      - MCPBoundaryError(UNAVAILABLE) + flag 锁定 → JSON error 含 MCP code
      - MCPBoundaryError(INVALID_RESPONSE) → JSON error（不 retry 不 fallback）
      - 其它 Exception（非 MCPBoundaryError）→ 不再被 catch，向上抛（fail loud）
    """

    def test_mcp_path_used_when_available(self, monkeypatch):
        fake = _mock_mcp(monkeypatch, matches=[
            {
                "chunk_id": "c1",
                "document_id": "d1",
                "title": "区域退货率",
                "text": "# 退货率\n示例 SQL:\nSELECT ...\n要点: 退货率 = 退货金额/销售额",
                "score": 0.8,
            },
        ])
        raw = faq_tools.search_faq.invoke({"query": "退货率", "top_k": 3})
        parsed = json.loads(raw)
        assert parsed["matches"]
        assert parsed["matches"][0]["question"] == "区域退货率"
        # MCP 必被调
        fake.call_tool.assert_called_once_with(
            "search_faq", {"query": "退货率", "top_k": 3}
        )

    def test_fallback_to_local_on_mcp_unavailable(self, monkeypatch):
        """MCP UNAVAILABLE + fallback allowed → 本地 seed（既有契约）。"""
        _mock_mcp(
            monkeypatch,
            side_effect=MCPBoundaryError(MCPErrorCode.MCP_UNAVAILABLE, "down"),
        )
        monkeypatch.setattr(faq_tools, "_fallback_allowed", lambda: True)

        raw = faq_tools.search_faq.invoke({"query": "退货率", "top_k": 3})
        parsed = json.loads(raw)
        # 本地命中：question 含「退货率」，sql 非空
        assert parsed["matches"]
        assert "退货率" in parsed["matches"][0]["question"]
        assert "sql" in parsed["matches"][0]

    def test_flag_locked_returns_error_json(self, monkeypatch):
        """flag 锁定 → MCP UNAVAILABLE → 返回 JSON error，不落本地。"""
        _mock_mcp(
            monkeypatch,
            side_effect=MCPBoundaryError(MCPErrorCode.MCP_UNAVAILABLE, "down"),
        )
        monkeypatch.setattr(faq_tools, "_fallback_allowed", lambda: False)

        raw = faq_tools.search_faq.invoke({"query": "退货率", "top_k": 3})
        parsed = json.loads(raw)
        assert "error" in parsed
        # code.value 已是 "MCP_UNAVAILABLE"；f-string 不应再加 "MCP_" 前缀
        assert "MCP_UNAVAILABLE:" in parsed["error"], (
            f"错误码前缀去重失败（避免 MCP_MCP_UNAVAILABLE）: {parsed!r}"
        )
        assert "MCP_MCP_" not in parsed["error"]

    def test_invalid_response_returns_error_json_without_fallback(self, monkeypatch):
        """MCP INVALID_RESPONSE → 显式 error，不 fallback（即使 fallback 放行）。"""
        _mock_mcp(
            monkeypatch,
            side_effect=MCPBoundaryError(MCPErrorCode.MCP_INVALID_RESPONSE, "bad json"),
        )
        monkeypatch.setattr(faq_tools, "_fallback_allowed", lambda: True)

        raw = faq_tools.search_faq.invoke({"query": "退货率", "top_k": 3})
        parsed = json.loads(raw)
        assert "error" in parsed
        assert "MCP_INVALID_RESPONSE:" in parsed["error"]
        assert "MCP_MCP_" not in parsed["error"]

    def test_catch_tightens_to_mcp_boundary_error_only(self, monkeypatch):
        """收紧：只 catch MCPBoundaryError；其它 Exception 向上抛（fail loud）。"""
        # 非 MCPBoundaryError 异常 → 不应被吞，必须向上抛
        fake = MagicMock()
        fake.call_tool.side_effect = RuntimeError("parse bug not related to MCP boundary")
        monkeypatch.setattr(faq_tools, "get_rag_mcp_client", lambda: fake)

        with pytest.raises(RuntimeError, match="parse bug"):
            faq_tools.search_faq.invoke({"query": "x", "top_k": 3})

    def test_mcp_empty_matches_returns_empty(self, monkeypatch):
        """MCP 合法空命中（matches=[]）→ 返回空 matches（不算错）。"""
        _mock_mcp(monkeypatch, matches=[])
        raw = faq_tools.search_faq.invoke({"query": "完全不存在的查询", "top_k": 3})
        parsed = json.loads(raw)
        assert parsed["matches"] == []

    def test_uses_unified_mcp_client_not_faq_shim(self, monkeypatch):
        """dispatcher 必须用 get_rag_mcp_client（统一单例），不用旧 shim 的 search_faq 方法。"""
        # 旧 shim: get_mcp_faq_client().search_faq(q, top_k) — string 返回
        # 新统一: get_rag_mcp_client().call_tool("search_faq", {...}) — dict 返回
        fake = MagicMock()
        fake.call_tool.return_value = {"matches": []}
        monkeypatch.setattr(faq_tools, "get_rag_mcp_client", lambda: fake)
        # 显式验证 call_tool 被调、参数形态
        raw = faq_tools.search_faq.invoke({"query": "q", "top_k": 2})
        fake.call_tool.assert_called_once_with("search_faq", {"query": "q", "top_k": 2})
        parsed = json.loads(raw)
        assert "matches" in parsed
