"""Schema 从 ragent-py 字典 KB 来：文档解析 + 检索工具测试。

离线——mock ragent-py HTTP 面，不打真实服务。
"""
from __future__ import annotations

import pytest

from app.tools import rag_schema

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


# --- 检索工具 ---


def test_search_tables_from_rag(monkeypatch):
    monkeypatch.setattr(rag_schema, "_retrieve_dict", lambda q, top_k: [
        {"text": _FACT_SALES_DOC, "score": 0.9},
        {"text": "无关内容", "score": 0.1},
    ])
    rows = rag_schema.search_tables_from_rag("销售额", top_k=3)
    assert len(rows) == 1  # 无关 chunk 被解析器跳过
    assert rows[0]["table_name"] == "fact_sales"
    assert rows[0]["columns"][0]["name"] == "sale_id"
    assert "CREATE TABLE fact_sales" in rows[0]["ddl"]


def test_search_tables_from_rag_unavailable_returns_empty(monkeypatch):
    def _boom(query, top_k):
        raise RuntimeError("字典服务不可达")

    monkeypatch.setattr(rag_schema, "_retrieve_dict", _boom)
    assert rag_schema.search_tables_from_rag("销售额") == []


def test_get_table_ddl_from_rag(monkeypatch):
    monkeypatch.setattr(rag_schema, "_retrieve_dict", lambda q, top_k: [
        {"text": _FACT_SALES_DOC, "score": 0.9},
    ])
    ddl = rag_schema.get_table_ddl_from_rag("fact_sales")
    assert ddl is not None
    assert "sale_id integer" in ddl
    assert "total_amount numeric(12,2)" in ddl


def test_get_table_ddl_from_rag_not_found(monkeypatch):
    monkeypatch.setattr(rag_schema, "_retrieve_dict", lambda q, top_k: [
        {"text": _FACT_SALES_DOC, "score": 0.9},
    ])
    assert rag_schema.get_table_ddl_from_rag("fact_does_not_exist") is None


def test_get_table_ddl_from_rag_unavailable(monkeypatch):
    monkeypatch.setattr(rag_schema, "_retrieve_dict", lambda q, top_k: (_ for _ in ()).throw(RuntimeError("down")))
    assert rag_schema.get_table_ddl_from_rag("fact_sales") is None


def test_list_tables_from_rag(monkeypatch):
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
    monkeypatch.setattr(rag_schema, "_retrieve_dict", lambda q, top_k: [
        {"text": _FACT_SALES_DOC, "score": 0.9},
        {"text": system_doc, "score": 0.8},
    ])
    rows = rag_schema.search_tables_from_rag("用户", top_k=5)
    assert all(rag_schema._is_analytical_table(r["table_name"]) for r in rows)
    assert "users" not in [r["table_name"] for r in rows]


def test_list_tables_from_rag_unavailable(monkeypatch):
    monkeypatch.setattr(rag_schema, "_list_dict_docs", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert rag_schema.list_tables_from_rag() == []


# --- data_tools 委托（@tool 契约） ---


def test_data_tools_search_tables_tool(monkeypatch):
    from app.tools.data_tools import search_tables
    import json

    monkeypatch.setattr(rag_schema, "_retrieve_dict", lambda q, top_k: [
        {"text": _FACT_SALES_DOC, "score": 0.9},
    ])
    raw = search_tables.invoke({"query": "销售额", "top_k": 3})
    parsed = json.loads(raw)
    assert parsed and parsed[0]["table_name"] == "fact_sales"


def test_data_tools_get_table_ddl_tool_not_found(monkeypatch):
    from app.tools.data_tools import get_table_ddl

    monkeypatch.setattr(rag_schema, "_retrieve_dict", lambda q, top_k: [])
    assert get_table_ddl.invoke({"table_name": "fact_x"}) == "Table 'fact_x' not found"