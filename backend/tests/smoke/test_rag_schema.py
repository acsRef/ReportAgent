"""Schema 从 ragent-py 字典 KB 来：文档解析 + 检索工具测试。

离线——mock ragent-py HTTP 面 / mock MCP client，不打真实服务。

P2 Task 2 增量：MCP-first dispatcher（search_tables_from_rag /
get_table_ddl_from_rag）；list_tables_from_rag 维持 HTTP 直连（MCP 无 list 工具）。
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.tools import rag_schema
from app.tools.mcp_errors import MCPBoundaryError, MCPErrorCode

pytestmark = pytest.mark.smoke

_FACT_SALES_DOC = """\
【表 `public.fact_sales` / 字段】
# 表 `public.fact_sales`
销售记录事实表,每条记录代表一笔销售
## 字段
字段 sale_id 类型 integer 含义 销售记录主键 枚举/FK
字段 date_id 类型 integer 含义 销售日期(关联 dim_date.date_id) 枚举/FK
字段 channel 类型 character varying(10) 含义 销售渠道 枚举/FK
字段 total_amount 类型 numeric(12,2) 含义 销售金额 枚举/FK
"""


# ── Mock helpers ──


def _mock_mcp(monkeypatch, *, matches=None, side_effect=None):
    """在 rag_schema 上 patch get_rag_mcp_client。

    matches: 模拟 MCP 成功返回的 items 列表（默认 None → {"matches": []}）
    side_effect: 模拟 MCP 调用抛错（优先级高于 matches）
    """
    fake = MagicMock()
    if side_effect is not None:
        fake.call_tool.side_effect = side_effect
    else:
        fake.call_tool.return_value = {"matches": matches or []}
    monkeypatch.setattr(rag_schema, "get_rag_mcp_client", lambda: fake)
    return fake


# --- 文档解析 ---


def test_parse_table_doc_real_format_and_garbage():
    parsed = rag_schema._parse_table_doc(_FACT_SALES_DOC)
    assert parsed is not None
    assert parsed["table_name"] == "fact_sales"
    assert "销售记录事实表" in parsed["description"]
    assert len(parsed["columns"]) == 4
    assert parsed["columns"][0] == {"name": "sale_id", "type": "integer"}
    # 带空格类型 character varying(10) 也要完整取到
    assert parsed["columns"][2] == {"name": "channel", "type": "character varying(10)"}
    assert parsed["columns"][3] == {"name": "total_amount", "type": "numeric(12,2)"}

    # 垃圾文本 / 空串 → None（不是空 dict，也不是编造的表）
    assert rag_schema._parse_table_doc("随便一段文本 没有表结构") is None
    assert rag_schema._parse_table_doc("") is None


# --- 检索工具 happy path（mock MCP 成功路径）---


def test_search_tables_mcp_happy_path(monkeypatch):
    """MCP happy path：命中 chunk → 结构化 schema + DDL；data_tools 委托同一路径。

    只断言工具名与 query（不断言内部 top_k 放大系数——那是实现细节）。
    """
    fake = _mock_mcp(monkeypatch, matches=[
        {"text": _FACT_SALES_DOC, "title": "dict-table_public_fact_sales.md", "score": 0.9},
        {"text": "无关内容", "score": 0.1},
    ])
    rows = rag_schema.search_tables_from_rag("销售额", top_k=3)
    assert len(rows) == 1  # 无关 chunk 被解析器跳过
    assert rows[0]["table_name"] == "fact_sales"
    assert rows[0]["columns"][0]["name"] == "sale_id"
    assert "CREATE TABLE fact_sales" in rows[0]["ddl"]

    name, args = fake.call_tool.call_args[0]
    assert name == "search_dictionary"
    assert args["query"] == "销售额"

    # data_tools 的 @tool 委托同一检索路径
    from app.tools.data_tools import search_tables

    parsed = json.loads(search_tables.invoke({"query": "销售额", "top_k": 3}))
    assert parsed and parsed[0]["table_name"] == "fact_sales"


def test_get_table_ddl_mcp_happy_path(monkeypatch):
    fake = _mock_mcp(monkeypatch, matches=[
        {"text": _FACT_SALES_DOC, "score": 0.9},
    ])
    ddl = rag_schema.get_table_ddl_from_rag("fact_sales")
    assert ddl is not None
    assert "sale_id integer" in ddl
    assert "total_amount numeric(12,2)" in ddl

    name, args = fake.call_tool.call_args[0]
    assert name == "search_dictionary"
    assert args["query"] == "fact_sales"


def test_get_table_ddl_not_found_returns_none(monkeypatch):
    """检索命中但不含目标表 → None（绝不编造 DDL）；data_tools 层显式 not found。"""
    _mock_mcp(monkeypatch, matches=[
        {"text": _FACT_SALES_DOC, "score": 0.9},
    ])
    assert rag_schema.get_table_ddl_from_rag("fact_does_not_exist") is None

    from app.tools.data_tools import get_table_ddl

    _mock_mcp(monkeypatch, matches=[])
    assert get_table_ddl.invoke({"table_name": "fact_x"}) == "Table 'fact_x' not found"


def test_list_tables_filters_and_degrades(monkeypatch):
    """list_tables 无 MCP 等价工具，维持 HTTP 直连路径；失败降级 []（不阻塞主流程）。"""
    docs = [
        {"filename": "dict-table_public_fact_sales.md", "title": "销售事实表", "chunk_count": 1},
        {"filename": "dict-table_public_dim_region.md", "title": "区域维度表", "chunk_count": 1},
        {"filename": "dict-table_public_users.md", "title": "用户表", "chunk_count": 1},  # 系统表应被过滤
        {"filename": "dict-api_orders.md", "title": "接口文档", "chunk_count": 3},  # 非表文档应被过滤
    ]
    monkeypatch.setattr(rag_schema, "_list_dict_docs", lambda: docs)
    tables = rag_schema.list_tables_from_rag()
    names = [t["table_name"] for t in tables]
    assert names == ["fact_sales", "dim_region"]
    assert tables[0]["column_count"] == 1

    # 文档列表不可达 → graceful []（不向上抛错）
    monkeypatch.setattr(
        rag_schema, "_list_dict_docs", lambda: (_ for _ in ()).throw(RuntimeError("down"))
    )
    assert rag_schema.list_tables_from_rag() == []


def test_system_tables_filtered_out_of_schema(monkeypatch):
    """字典 KB 混有 ragent-py 系统表：users / documents 不得进 schema 输出。"""
    assert rag_schema._is_analytical_table("fact_sales") is True
    assert rag_schema._is_analytical_table("dim_region") is True
    assert rag_schema._is_analytical_table("users") is False
    assert rag_schema._is_analytical_table("documents") is False

    system_doc = """# 表 `public.users`
用户系统表
## 字段
字段 id 类型 integer 含义 主键 枚举/FK
"""
    _mock_mcp(monkeypatch, matches=[
        {"text": _FACT_SALES_DOC, "score": 0.9},
        {"text": system_doc, "score": 0.8},
    ])
    rows = rag_schema.search_tables_from_rag("用户", top_k=5)
    assert all(rag_schema._is_analytical_table(r["table_name"]) for r in rows)
    assert "users" not in [r["table_name"] for r in rows]


# ─────────────────────────────────────────────────────────────
# P2 Task 2: MCP-first dispatcher 行为契约
# ─────────────────────────────────────────────────────────────


class TestRetrieveDictDispatcher:
    """_retrieve_dict dispatcher 行为（search_tables / get_table_ddl 内部都走它）。

    契约（docs/plans/2026-08-26-p2-rag-mcp-boundary.md 决策 1/4）：
      - MCP UNAVAILABLE + _fallback_allowed() → HTTP fallback
      - flag 锁定 → 上抛，调用方 graceful 降级
      - INVALID_RESPONSE → 上抛，即使 fallback 放行也不 fallback
    """

    def test_mcp_unavailable_falls_back_to_http(self, monkeypatch):
        """MCP UNAVAILABLE + fallback 放行 → HTTP 直连，三个入口都按真实行为产出。"""
        _mock_mcp(
            monkeypatch,
            side_effect=MCPBoundaryError(MCPErrorCode.MCP_UNAVAILABLE, "down"),
        )
        monkeypatch.setattr(rag_schema, "_fallback_allowed", lambda: True)
        http_calls = []

        def _fake_http(query, top_k):
            http_calls.append((query, top_k))
            return [{"text": _FACT_SALES_DOC, "score": 0.9}]

        monkeypatch.setattr(rag_schema, "_retrieve_dict_http", _fake_http)

        # dispatcher 层：HTTP 结果原样返回
        assert rag_schema._retrieve_dict("q", top_k=3) == [
            {"text": _FACT_SALES_DOC, "score": 0.9}
        ]
        # search_tables：fallback 结果仍解析成结构化 schema
        rows = rag_schema.search_tables_from_rag("销售额", top_k=3)
        assert len(rows) == 1
        assert rows[0]["table_name"] == "fact_sales"
        # get_table_ddl：fallback 结果仍产出 DDL
        assert rag_schema.get_table_ddl_from_rag("fact_sales") is not None
        assert http_calls, "HTTP fallback should have been invoked"

    def test_fallback_gates_block_http(self, monkeypatch):
        """双 gate：flag 锁定 / INVALID_RESPONSE 都不得走 HTTP fallback。"""
        http_called = []

        def _boom_http(query, top_k):
            http_called.append(1)
            return []

        monkeypatch.setattr(rag_schema, "_retrieve_dict_http", _boom_http)

        # gate 1：UNAVAILABLE + flag 锁定 → 返回空，不调 HTTP
        _mock_mcp(
            monkeypatch,
            side_effect=MCPBoundaryError(MCPErrorCode.MCP_UNAVAILABLE, "down"),
        )
        monkeypatch.setattr(rag_schema, "_fallback_allowed", lambda: False)
        assert rag_schema.search_tables_from_rag("销售额") == []
        assert rag_schema.get_table_ddl_from_rag("fact_sales") is None

        # gate 2：INVALID_RESPONSE（协议错）即使 fallback 放行也不 fallback
        _mock_mcp(
            monkeypatch,
            side_effect=MCPBoundaryError(MCPErrorCode.MCP_INVALID_RESPONSE, "bad json"),
        )
        monkeypatch.setattr(rag_schema, "_fallback_allowed", lambda: True)
        assert rag_schema.search_tables_from_rag("销售额") == []
        assert rag_schema.get_table_ddl_from_rag("fact_sales") is None

        assert not http_called, "gate 命中时不应走 HTTP fallback"

    def test_dispatcher_raises_and_callers_degrade(self, monkeypatch):
        """dispatcher 上抛错误的分类；调用方各自 graceful 降级（[] / None）。"""
        # flag 锁定 → UNAVAILABLE 上抛，让调用方按各自契约处理
        _mock_mcp(
            monkeypatch,
            side_effect=MCPBoundaryError(MCPErrorCode.MCP_UNAVAILABLE, "down"),
        )
        monkeypatch.setattr(rag_schema, "_fallback_allowed", lambda: False)
        with pytest.raises(MCPBoundaryError) as ei:
            rag_schema._retrieve_dict("q", top_k=3)
        assert ei.value.code is MCPErrorCode.MCP_UNAVAILABLE

        # INVALID_RESPONSE → 必须上抛，不 fallback
        _mock_mcp(
            monkeypatch,
            side_effect=MCPBoundaryError(MCPErrorCode.MCP_INVALID_RESPONSE, "bad"),
        )
        monkeypatch.setattr(rag_schema, "_fallback_allowed", lambda: True)
        monkeypatch.setattr(
            rag_schema, "_retrieve_dict_http",
            lambda q, top_k: [{"text": "should not be used"}],
        )
        with pytest.raises(MCPBoundaryError) as ei:
            rag_schema._retrieve_dict("q", top_k=3)
        assert ei.value.code is MCPErrorCode.MCP_INVALID_RESPONSE

        # 调用方：不把「不可达 / 协议错」当成「检索到东西」
        assert rag_schema.search_tables_from_rag("销售额") == []
        assert rag_schema.get_table_ddl_from_rag("fact_sales") is None


class TestMcpResponseSchemaValidation:
    """MCP response schema validation（review 第 2 轮 P1 修订）。

    search_tables_from_rag / get_table_ddl_from_rag 必须拒绝坏 MCP response
    而不是把空 text 当成「没找到表」。
    """

    def test_bad_items_degrade_to_empty(self, monkeypatch):
        """坏 items（缺 text / 缺 score / matches 非 list）→ INVALID → 调用方 graceful 空。"""
        bad_payloads = (
            [{"score": 0.9, "title": "x"}],  # 缺 text
            [{"text": "# 表 `public.fact_sales`\nsale", "title": "x"}],  # 缺 score
            "not a list",  # matches 形态错
        )
        for matches in bad_payloads:
            _mock_mcp(monkeypatch, matches=matches)
            assert rag_schema.search_tables_from_rag("x") == []
            assert rag_schema.get_table_ddl_from_rag("fact_sales") is None

    def test_empty_matches_is_not_an_error(self, monkeypatch):
        """合法空命中（matches=[]）≠ 错：返回 []，不算失败。"""
        fake = _mock_mcp(monkeypatch, matches=[])
        assert rag_schema.search_tables_from_rag("完全不存在的概念xyz", top_k=3) == []
        assert fake.call_tool.called

    def test_internal_fields_not_leaked(self, monkeypatch):
        """MCP 内部字段（chunk_id/document_id/section_path）不进 schema 输出。"""
        _mock_mcp(monkeypatch, matches=[
            {
                "text": _FACT_SALES_DOC,
                "title": "dict-table_public_fact_sales.md",
                "score": 0.9,
                "chunk_id": "c-internal",
                "document_id": "d-internal",
                "section_path": "internal/path",
            },
        ])
        rows = rag_schema.search_tables_from_rag("销售额", top_k=3)
        assert len(rows) == 1
        assert rows[0]["table_name"] == "fact_sales"
        for forbidden in ("chunk_id", "document_id", "section_path"):
            assert forbidden not in rows[0], (
                f"{forbidden} 是 MCP 内部字段，不应透传到 schema 输出"
            )
