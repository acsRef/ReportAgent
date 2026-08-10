"""Schema RAG Phase 1：FAQ 知识库检索测试（faq_tools 后端 + MCP registry parity）。

离线，读 backend/scripts/schema_faq.json；不打真实 API、不连 PG。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.tools import faq_tools

pytestmark = pytest.mark.smoke

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


# --- faq_tools.search_faq（SQL Agent 使用路径） ---


def test_search_faq_matches_return_rate():
    rows = faq_tools.search_faq("退货率", top_k=3)
    assert rows
    top = rows[0]
    assert top["score"] > 0
    assert "退货率" in top["question"]
    assert "SELECT" in top["sql"]
    assert top["note"]


def test_search_faq_no_match_returns_empty():
    assert faq_tools.search_faq("完全无关的乱码词汇xyz", top_k=3) == []


def test_search_faq_sales_ranking_ranked_first():
    rows = faq_tools.search_faq("各区域销售额排名", top_k=3)
    assert rows
    assert rows[0]["question"] == "各区域销售额排名"
    assert len(rows) <= 3


def test_search_faq_top_k_bounds():
    rows = faq_tools.search_faq("销售 区域 退货 库存 考勤 毛利率", top_k=20)
    assert len(rows) <= 20
    assert len(rows) > 0


def test_search_faq_missing_file_degrades(monkeypatch):
    faq_tools._FAQ_ENTRIES = None
    monkeypatch.setattr(
        faq_tools,
        "_FAQ_PATH",
        Path(__file__).parent / "does_not_exist_schema_faq.json",
    )
    assert faq_tools.search_faq("退货率", top_k=3) == []


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