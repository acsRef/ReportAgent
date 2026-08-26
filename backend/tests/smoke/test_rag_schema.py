"""Schema 从 ragent-py 字典 KB 来：文档解析 + 检索工具测试。

离线——mock ragent-py HTTP 面 / mock MCP client，不打真实服务。

P2 Task 2 增量：MCP-first dispatcher（search_tables_from_rag /
get_table_ddl_from_rag）；list_tables_from_rag 维持 HTTP 直连（MCP 无 list 工具）。
"""
from __future__ import annotations

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


def test_parse_table_doc_real_format():
    parsed = rag_schema._parse_table_doc(_FACT_SALES_DOC)
    assert parsed is not None
    assert parsed["table_name"] == "fact_sales"
    assert "销售记录事实表" in parsed["description"]
    assert len(parsed["columns"]) == 4
    assert parsed["columns"][0] == {"name": "sale_id", "type": "integer"}
    # 带空格类型 character varying(10) 也要完整取到
    assert parsed["columns"][2] == {"name": "channel", "type": "character varying(10)"}
    assert parsed["columns"][3] == {"name": "total_amount", "type": "numeric(12,2)"}


def test_parse_table_doc_garbage_returns_none():
    assert rag_schema._parse_table_doc("随便一段文本 没有表结构") is None
    assert rag_schema._parse_table_doc("") is None


# --- 检索工具（解析逻辑：mock MCP 成功路径）---


def test_search_tables_from_rag(monkeypatch):
    _mock_mcp(monkeypatch, matches=[
        {"text": _FACT_SALES_DOC, "title": "dict-table_public_fact_sales.md", "score": 0.9},
        {"text": "无关内容", "score": 0.1},
    ])
    rows = rag_schema.search_tables_from_rag("销售额", top_k=3)
    assert len(rows) == 1  # 无关 chunk 被解析器跳过
    assert rows[0]["table_name"] == "fact_sales"
    assert rows[0]["columns"][0]["name"] == "sale_id"
    assert "CREATE TABLE fact_sales" in rows[0]["ddl"]


def test_search_tables_from_rag_unavailable_returns_empty(monkeypatch):
    """MCP UNAVAILABLE + fallback allowed → 走 HTTP fallback。"""
    _mock_mcp(
        monkeypatch,
        side_effect=MCPBoundaryError(MCPErrorCode.MCP_UNAVAILABLE, "down"),
    )
    monkeypatch.setattr(rag_schema, "_fallback_allowed", lambda: True)
    # HTTP fallback 必须被调用
    http_called = []

    def _fake_http(query, top_k):
        http_called.append((query, top_k))
        return [{"text": _FACT_SALES_DOC, "score": 0.9}]

    monkeypatch.setattr(rag_schema, "_retrieve_dict_http", _fake_http)

    rows = rag_schema.search_tables_from_rag("销售额")
    assert len(rows) == 1
    assert http_called, "HTTP fallback should have been invoked"


def test_get_table_ddl_from_rag(monkeypatch):
    _mock_mcp(monkeypatch, matches=[
        {"text": _FACT_SALES_DOC, "score": 0.9},
    ])
    ddl = rag_schema.get_table_ddl_from_rag("fact_sales")
    assert ddl is not None
    assert "sale_id integer" in ddl
    assert "total_amount numeric(12,2)" in ddl


def test_get_table_ddl_from_rag_not_found(monkeypatch):
    _mock_mcp(monkeypatch, matches=[
        {"text": _FACT_SALES_DOC, "score": 0.9},
    ])
    assert rag_schema.get_table_ddl_from_rag("fact_does_not_exist") is None


def test_get_table_ddl_from_rag_unavailable(monkeypatch):
    """MCP UNAVAILABLE + fallback allowed → HTTP fallback 路径。"""
    _mock_mcp(
        monkeypatch,
        side_effect=MCPBoundaryError(MCPErrorCode.MCP_UNAVAILABLE, "down"),
    )
    monkeypatch.setattr(rag_schema, "_fallback_allowed", lambda: True)
    monkeypatch.setattr(
        rag_schema, "_retrieve_dict_http",
        lambda q, top_k: [{"text": _FACT_SALES_DOC, "score": 0.9}],
    )
    assert rag_schema.get_table_ddl_from_rag("fact_sales") is not None


def test_list_tables_from_rag(monkeypatch):
    """list_tables 无 MCP 等价工具，维持 HTTP 直连路径。"""
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


def test_is_analytical_table_filter():
    assert rag_schema._is_analytical_table("fact_sales") is True
    assert rag_schema._is_analytical_table("dim_region") is True
    assert rag_schema._is_analytical_table("users") is False
    assert rag_schema._is_analytical_table("documents") is False


def test_search_tables_from_rag_filters_system_tables(monkeypatch):
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


def test_list_tables_from_rag_unavailable(monkeypatch):
    monkeypatch.setattr(rag_schema, "_list_dict_docs", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert rag_schema.list_tables_from_rag() == []


# --- data_tools 委托（@tool 契约）---


def test_data_tools_search_tables_tool(monkeypatch):
    from app.tools.data_tools import search_tables
    import json

    _mock_mcp(monkeypatch, matches=[
        {"text": _FACT_SALES_DOC, "score": 0.9},
    ])
    raw = search_tables.invoke({"query": "销售额", "top_k": 3})
    parsed = json.loads(raw)
    assert parsed and parsed[0]["table_name"] == "fact_sales"


def test_data_tools_get_table_ddl_tool_not_found(monkeypatch):
    from app.tools.data_tools import get_table_ddl

    _mock_mcp(monkeypatch, matches=[])
    assert get_table_ddl.invoke({"table_name": "fact_x"}) == "Table 'fact_x' not found"


# ─────────────────────────────────────────────────────────────
# P2 Task 2: MCP-first dispatcher 行为契约
# ─────────────────────────────────────────────────────────────


class TestSearchTablesDispatcher:
    """search_tables_from_rag 的 MCP-first dispatcher 行为。

    契约（docs/plans/2026-08-26-p2-rag-mcp-boundary.md 决策 1/4）：
      - MCP 成功 → 走 MCP 路径
      - MCPBoundaryError(UNAVAILABLE) + _fallback_allowed() → HTTP fallback
      - MCPBoundaryError(UNAVAILABLE) + flag 锁定 → 返回 []（graceful 契约不变）
      - MCPBoundaryError(INVALID_RESPONSE) → 返回 []（不 retry 不 fallback）
      - MCP 合法空命中 → 返回 []（合法空，不算错）
    """

    def test_mcp_path_used_when_available(self, monkeypatch):
        fake = _mock_mcp(monkeypatch, matches=[
            {"text": _FACT_SALES_DOC, "title": "fact_sales", "score": 0.9},
        ])
        rows = rag_schema.search_tables_from_rag("销售额", top_k=3)
        assert len(rows) == 1
        assert rows[0]["table_name"] == "fact_sales"
        # MCP 必被调用，参数含 query/top_k
        fake.call_tool.assert_called_once_with(
            "search_dictionary", {"query": "销售额", "top_k": pytest.approx(3 * 6)}
        )

    def test_fallback_to_http_on_mcp_unavailable(self, monkeypatch):
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

        rows = rag_schema.search_tables_from_rag("销售额", top_k=3)
        assert len(rows) == 1
        assert len(http_calls) == 1, "HTTP fallback should be invoked exactly once"

    def test_flag_locked_returns_empty_without_fallback(self, monkeypatch):
        """flag 锁定 → MCP 失败 → 返回 []，不调 HTTP。"""
        _mock_mcp(
            monkeypatch,
            side_effect=MCPBoundaryError(MCPErrorCode.MCP_UNAVAILABLE, "down"),
        )
        monkeypatch.setattr(rag_schema, "_fallback_allowed", lambda: False)
        http_called = []

        def _boom_http(query, top_k):
            http_called.append(1)
            return []

        monkeypatch.setattr(rag_schema, "_retrieve_dict_http", _boom_http)

        rows = rag_schema.search_tables_from_rag("销售额")
        assert rows == []
        assert not http_called, "flag 锁定时不应走 HTTP fallback"

    def test_invalid_response_returns_empty_without_fallback(self, monkeypatch):
        """MCP_INVALID_RESPONSE（协议错）→ 不 fallback（重试同结果）→ 返回 []。"""
        _mock_mcp(
            monkeypatch,
            side_effect=MCPBoundaryError(MCPErrorCode.MCP_INVALID_RESPONSE, "bad json"),
        )
        monkeypatch.setattr(rag_schema, "_fallback_allowed", lambda: True)
        http_called = []

        def _fake_http(query, top_k):
            http_called.append(1)
            return []

        monkeypatch.setattr(rag_schema, "_retrieve_dict_http", _fake_http)

        rows = rag_schema.search_tables_from_rag("销售额")
        assert rows == []
        assert not http_called, "INVALID_RESPONSE 不应触发 HTTP fallback"

    def test_mcp_empty_result_returns_empty(self, monkeypatch):
        """MCP 合法空命中（matches=[]）→ 返回 []，不算错。"""
        fake = _mock_mcp(monkeypatch, matches=[])
        rows = rag_schema.search_tables_from_rag("完全不存在的概念xyz", top_k=3)
        assert rows == []
        fake.call_tool.assert_called_once()

    def test_mcp_result_filters_internal_fields(self, monkeypatch):
        """MCP 返回 items 里的内部字段（chunk_id/document_id/section_path/degraded）
        不进 schema 输出（数据契约稳定集）。"""
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
        # 内部字段不应出现在 schema 输出
        for forbidden in ("chunk_id", "document_id", "section_path"):
            assert forbidden not in rows[0], (
                f"{forbidden} 是 MCP 内部字段，不应透传到 schema 输出"
            )


class TestGetTableDdlDispatcher:
    """get_table_ddl_from_rag 的 MCP-first dispatcher 行为。"""

    def test_mcp_path_used_when_available(self, monkeypatch):
        _mock_mcp(monkeypatch, matches=[
            {"text": _FACT_SALES_DOC, "score": 0.9},
        ])
        ddl = rag_schema.get_table_ddl_from_rag("fact_sales")
        assert ddl is not None
        assert "sale_id integer" in ddl

    def test_fallback_to_http_on_mcp_unavailable(self, monkeypatch):
        _mock_mcp(
            monkeypatch,
            side_effect=MCPBoundaryError(MCPErrorCode.MCP_UNAVAILABLE, "down"),
        )
        monkeypatch.setattr(rag_schema, "_fallback_allowed", lambda: True)
        http_calls = []

        monkeypatch.setattr(
            rag_schema, "_retrieve_dict_http",
            lambda q, top_k: (http_calls.append(1), [{"text": _FACT_SALES_DOC, "score": 0.9}])[1],
        )
        ddl = rag_schema.get_table_ddl_from_rag("fact_sales")
        assert ddl is not None
        assert http_calls, "HTTP fallback should have been invoked"

    def test_flag_locked_returns_none(self, monkeypatch):
        """flag 锁定 → 返回 None（与既有契约一致）。"""
        _mock_mcp(
            monkeypatch,
            side_effect=MCPBoundaryError(MCPErrorCode.MCP_UNAVAILABLE, "down"),
        )
        monkeypatch.setattr(rag_schema, "_fallback_allowed", lambda: False)
        ddl = rag_schema.get_table_ddl_from_rag("fact_sales")
        assert ddl is None


class TestRetrieveDictDispatcher:
    """_retrieve_dict dispatcher 直接测试（search_tables/get_table_ddl 内部都走它）。"""

    def test_mcp_unavailable_with_fallback_returns_http_result(self, monkeypatch):
        _mock_mcp(
            monkeypatch,
            side_effect=MCPBoundaryError(MCPErrorCode.MCP_UNAVAILABLE, "down"),
        )
        monkeypatch.setattr(rag_schema, "_fallback_allowed", lambda: True)
        monkeypatch.setattr(
            rag_schema, "_retrieve_dict_http",
            lambda q, top_k: [{"text": "from http"}],
        )
        items = rag_schema._retrieve_dict("q", top_k=3)
        assert items == [{"text": "from http"}]

    def test_mcp_unavailable_flag_locked_propagates(self, monkeypatch):
        """flag 锁定 → MCPBoundaryError 必须上抛，让调用方按各自契约处理。"""
        _mock_mcp(
            monkeypatch,
            side_effect=MCPBoundaryError(MCPErrorCode.MCP_UNAVAILABLE, "down"),
        )
        monkeypatch.setattr(rag_schema, "_fallback_allowed", lambda: False)
        with pytest.raises(MCPBoundaryError) as ei:
            rag_schema._retrieve_dict("q", top_k=3)
        assert ei.value.code is MCPErrorCode.MCP_UNAVAILABLE

    def test_mcp_invalid_response_propagates_without_fallback(self, monkeypatch):
        """INVALID_RESPONSE → 必须上抛，不走 fallback。"""
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


class TestMcpResponseSchemaValidation:
    """MCP response schema validation（review 第 2 轮 P1 修订）。

    search_tables_from_rag / get_table_ddl_from_rag 必须拒绝坏 MCP response
    而不是把空 text 当成「没找到表」。
    """

    def test_search_tables_mcp_missing_text_returns_empty(self, monkeypatch):
        """MCP items 缺 text → search_tables_from_rag 返回 []（graceful 契约）。

        _retrieve_dict_via_mcp 抛 MCPBoundaryError(INVALID_RESPONSE) →
        dispatcher 上抛 → search_tables_from_rag except 兜底返回 []。
        """
        _mock_mcp(monkeypatch, matches=[{"score": 0.9, "title": "x"}])  # 缺 text
        rows = rag_schema.search_tables_from_rag("x")
        assert rows == []

    def test_search_tables_mcp_missing_score_returns_empty(self, monkeypatch):
        _mock_mcp(monkeypatch, matches=[{"text": "# 表 `public.fact_sales`\nsale", "title": "x"}])
        rows = rag_schema.search_tables_from_rag("x")
        assert rows == []

    def test_get_table_ddl_mcp_missing_text_returns_none(self, monkeypatch):
        _mock_mcp(monkeypatch, matches=[{"score": 0.9}])
        assert rag_schema.get_table_ddl_from_rag("fact_sales") is None

    def test_search_tables_mcp_matches_not_list_returns_empty(self, monkeypatch):
        _mock_mcp(monkeypatch, matches="not a list")  # type: ignore[arg-type]
        rows = rag_schema.search_tables_from_rag("x")
        assert rows == []

    def test_search_tables_mcp_extra_fields_passed_through(self, monkeypatch):
        """MCP 内部字段（chunk_id/document_id/section_path）不影响解析。"""
        _mock_mcp(monkeypatch, matches=[{
            "text": _FACT_SALES_DOC,
            "score": 0.9,
            "chunk_id": "c1",
            "document_id": "d1",
            "section_path": "internal/path",
        }])
        rows = rag_schema.search_tables_from_rag("销售额", top_k=3)
        assert len(rows) == 1
        assert rows[0]["table_name"] == "fact_sales"
