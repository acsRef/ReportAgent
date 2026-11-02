"""Schema RAG Phase 1：FAQ 知识库检索测试（faq_tools 后端 + MCP server registry）。

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


def test_search_faq_rows_local_seed_ranking():
    """本地 seed 检索：命中 / 排序 / top_k 上限 / 无命中返回 []。"""
    rows = faq_tools._search_faq_rows("退款率", top_k=3)
    assert rows
    top = rows[0]
    assert top["score"] > 0
    assert "退款率" in top["question"]
    # P2 review 修订：local seed 规范化到 {question, text, score}——SQL 文本在
    # text 字段里（_format_faq_text 序列化），不再作为独立字段暴露。
    assert "SELECT" in top["text"]
    assert "退款率" in top["text"]  # note 也在 text 里

    # 排序：最匹配的排第一
    ranked = faq_tools._search_faq_rows("各区域销售额排名", top_k=3)
    assert ranked
    assert ranked[0]["question"] == "各区域销售额排名"
    assert len(ranked) <= 3

    # top_k 上限
    bounded = faq_tools._search_faq_rows("销售 区域 退货 库存 考勤 毛利率", top_k=20)
    assert 0 < len(bounded) <= 20

    # 完全无命中 → []
    assert faq_tools._search_faq_rows("完全无关的乱码词汇xyz", top_k=3) == []


def test_search_faq_rows_missing_file_degrades(monkeypatch):
    faq_tools._FAQ_ENTRIES = None
    monkeypatch.setattr(
        faq_tools,
        "_FAQ_PATH",
        Path(__file__).parent / "does_not_exist_schema_faq.json",
    )
    try:
        assert faq_tools._search_faq_rows("退款率", top_k=3) == []
    finally:
        # 清缓存：否则 _load_faq 会把「缺失 → 空列表」固化，污染后续测试
        faq_tools._FAQ_ENTRIES = None


def test_search_faq_registered_in_tool_registry(monkeypatch):
    """一等注册工具：register_all_tools 后 registry 能取到 search_faq。"""
    from app.tools import register_all_tools
    from app.tools.registry import registry

    register_all_tools()
    tools = registry.get(["search_faq"])
    assert tools and tools[0] is faq_tools.search_faq
    meta = registry.get_metadata("search_faq")
    assert meta is not None and meta.capability == "faq_search"


# --- MCP server 侧活路径 ---


def test_mcp_server_search_faq_returns_seed_sql(monkeypatch):
    """server 侧活路径：registry.search_faq 直跑 seed（返回 sql/note/tables 形态）。

    注：这是 MCP server 端点自己的检索实现，不与 backend faq_tools 对比。
    """
    # MCP package 在仓库根，不在 backend/ 测试路径；临时挂上去验证可调用。
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from mcp_schema_server.registry import registry

    rows = registry.search_faq("退款率", top_k=3)
    assert rows
    assert "SELECT" in rows[0]["sql"]
    # 命中的是退款率 FAQ
    assert "退款率" in rows[0]["question"]


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
      - MCP 成功 → 走统一 client，输出恰为 {question, text, score}
      - MCPBoundaryError(UNAVAILABLE) + fallback → 本地 seed 兜底
      - MCPBoundaryError(UNAVAILABLE) + flag 锁定 → JSON error 含 MCP code
      - MCPBoundaryError(INVALID_RESPONSE) → JSON error（不 retry 不 fallback）
      - 其它 Exception（非 MCPBoundaryError）→ 不再被 catch，向上抛（fail loud）
      - MCP 合法空命中 → matches=[]（空 ≠ 错）
    """

    def test_mcp_path_uses_unified_client_contract(self, monkeypatch):
        """统一 client + 参数形态 + 输出恰为 {question, text, score}。"""
        fake = MagicMock()
        fake.call_tool.return_value = {
            "matches": [
                {
                    "chunk_id": "c-internal",
                    "document_id": "d-internal",
                    "section_path": "internal/path",
                    "title": "区域退款率",
                    "text": "# 退款率\n示例 SQL:\nSELECT rc.region_name...\n要点: 退款率 = 退货金额/销售额",
                    "score": 0.8,
                },
            ],
            "degraded": False,  # mcp_client runtime annotation
            "_note": "internal",
        }
        monkeypatch.setattr(faq_tools, "get_rag_mcp_client", lambda: fake)

        for query, top_k in (("退款率", 3), ("q", 2)):
            fake.call_tool.reset_mock()
            raw = faq_tools.search_faq.invoke({"query": query, "top_k": top_k})
            # 统一 client（非旧 shim 的 search_faq 方法）：工具名 + 参数形态
            fake.call_tool.assert_called_once_with(
                "search_faq", {"query": query, "top_k": top_k}
            )
            parsed = json.loads(raw)
            assert parsed["matches"]
            assert "SELECT" in parsed["matches"][0]["text"]
            assert "退款率" in parsed["matches"][0]["question"]
            # Tool Contract P2：恰为 {question, text, score}；内部字段与顶层
            # annotation（degraded/_note）都不进
            assert set(parsed["matches"][0].keys()) == {"question", "text", "score"}
            assert set(parsed.keys()) == {"matches"}

    def test_fallback_to_local_on_mcp_unavailable(self, monkeypatch):
        """MCP UNAVAILABLE + fallback allowed → 本地 seed（既有契约）。"""
        _mock_mcp(
            monkeypatch,
            side_effect=MCPBoundaryError(MCPErrorCode.MCP_UNAVAILABLE, "down"),
        )
        monkeypatch.setattr(faq_tools, "_fallback_allowed", lambda: True)

        raw = faq_tools.search_faq.invoke({"query": "退款率", "top_k": 3})
        parsed = json.loads(raw)
        # 本地命中：question 含「退款率」，text 字段含 SQL
        assert parsed["matches"]
        assert "退款率" in parsed["matches"][0]["question"]
        # Tool Contract P2：{question, text, score}——SELECT 在 text 里
        assert "SELECT" in parsed["matches"][0]["text"]

    def test_flag_locked_and_invalid_return_error_json(self, monkeypatch):
        """显式 error JSON：含 code、无 MCP_MCP_ 前缀；flag 放行也不 fallback INVALID。"""
        # flag 锁定 → UNAVAILABLE → error（不落本地）
        _mock_mcp(
            monkeypatch,
            side_effect=MCPBoundaryError(MCPErrorCode.MCP_UNAVAILABLE, "down"),
        )
        monkeypatch.setattr(faq_tools, "_fallback_allowed", lambda: False)

        parsed = json.loads(faq_tools.search_faq.invoke({"query": "退款率", "top_k": 3}))
        assert "error" in parsed
        # code.value 已是 "MCP_UNAVAILABLE"；f-string 不应再加 "MCP_" 前缀
        assert "MCP_UNAVAILABLE:" in parsed["error"], (
            f"错误码前缀去重失败（避免 MCP_MCP_UNAVAILABLE）: {parsed!r}"
        )
        assert "MCP_MCP_" not in parsed["error"]
        assert set(parsed.keys()) == {"error"}  # 不伪装成空结果

        # INVALID_RESPONSE → error，即使 fallback 放行也不 fallback
        _mock_mcp(
            monkeypatch,
            side_effect=MCPBoundaryError(MCPErrorCode.MCP_INVALID_RESPONSE, "bad json"),
        )
        monkeypatch.setattr(faq_tools, "_fallback_allowed", lambda: True)

        parsed = json.loads(faq_tools.search_faq.invoke({"query": "退款率", "top_k": 3}))
        assert "error" in parsed
        assert "MCP_INVALID_RESPONSE:" in parsed["error"]
        assert "MCP_MCP_" not in parsed["error"]
        assert set(parsed.keys()) == {"error"}

    def test_catch_tightens_to_mcp_boundary_error_only(self, monkeypatch):
        """收紧：只 catch MCPBoundaryError；其它 Exception 向上抛（fail loud）。"""
        # 非 MCPBoundaryError 异常 → 不应被吞，必须向上抛
        fake = MagicMock()
        fake.call_tool.side_effect = RuntimeError("parse bug not related to MCP boundary")
        monkeypatch.setattr(faq_tools, "get_rag_mcp_client", lambda: fake)

        with pytest.raises(RuntimeError, match="parse bug"):
            faq_tools.search_faq.invoke({"query": "x", "top_k": 3})

    def test_mcp_empty_matches_is_not_error(self, monkeypatch):
        """MCP 合法空命中（matches=[]）→ matches=[]，不算错。"""
        _mock_mcp(monkeypatch, matches=[])
        parsed = json.loads(
            faq_tools.search_faq.invoke({"query": "完全不存在的查询", "top_k": 3})
        )
        assert parsed["matches"] == []
        assert "error" not in parsed


class TestMcpResponseSchemaValidation:
    """MCP 返回的 items schema 必须满足 {text: str, score: numeric}（review 第 2 轮 P1 修订）。

    不满足 → MCPBoundaryError(INVALID_RESPONSE) → search_faq 走显式 error 路径
    而不是把坏 MCP response 当成空结果静默吞掉（这正是 P2 决策 4 要消灭的
    「protocol error 伪装成 empty result」风险）。
    """

    def test_bad_items_return_error_json(self, monkeypatch):
        """缺 text / 缺 score / matches 非 list → 显式 error（不静默空结果）。"""
        bad_results = (
            {"matches": [{"score": 0.9, "title": "x"}]},  # 缺 text
            {"matches": [{"text": "hello", "title": "x"}]},  # 缺 score
            {"matches": "not a list"},  # matches 形态错
        )
        fake = MagicMock()
        monkeypatch.setattr(faq_tools, "get_rag_mcp_client", lambda: fake)
        # 即使 fallback 放行也不 fallback
        monkeypatch.setattr(faq_tools, "_fallback_allowed", lambda: True)

        for result in bad_results:
            fake.call_tool.return_value = result
            parsed = json.loads(faq_tools.search_faq.invoke({"query": "x", "top_k": 3}))
            assert "error" in parsed, result
            assert "MCP_INVALID_RESPONSE" in parsed["error"], result
            assert "matches" not in parsed, result
