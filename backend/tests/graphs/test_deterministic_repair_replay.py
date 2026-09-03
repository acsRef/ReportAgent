"""⑤ 确定性 object_not_found → MCP schema retrieval → repair replay（Final Hardening）。

背景（review 发现的真缺口）：object_not_found → MCP schema 刷新 → 重新生成 →
EXPLAIN → EXECUTE → SUCCESS 的**全链确定性验证**此前不存在——repair 路径只在
real-LLM 双分支 live 测试里出现过（LLM 漂移 → 断言只能是「SUCCESS 或诚实
FAILED」），决策/路由是单点钉的。

本测试用 MockLLMAdapter（fixture 语义 kind + 调用序）驱动**完整 sql_graph**：
  sql_generate:1 故意引用不存在的列（o.bogus_column）
    → validate EXPLAIN 真跑 PG → UndefinedColumn → object_not_found
    → DiagnosePolicy → retry_mcp_schema_retrieval
    → MCP schema 边界注入 canned DDL（确定性，不依赖 MCP 进程）
    → sql_generate:2 返回修复 SQL → EXPLAIN → 真执行 → SUCCESS
全部 LLM 输出确定、EXPLAIN/EXECUTE 走真实 PG——同时钉住
「MCP 刷新确实发生（schema_context.source='mcp_search_schema'）」
「计数正确（sql_generation=2 / mcp_schema=1）」「终态 SUCCESS 且数据非空」。

需要真实 PostgreSQL（DATABASE_URL / ANALYSIS_DSN）；缺失自动 skip。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.models.contracts import ColumnSchema, SchemaContext, TableSchema

pytestmark = pytest.mark.graphs

_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "llm_responses"
_CASE = "replay-object-not-found"

_NEEDS_DB = pytest.mark.skipif(
    not (os.getenv("DATABASE_URL") or os.getenv("ANALYSIS_DSN")),
    reason="replay 需要真实 PG（EXPLAIN/EXECUTE 不走 mock）",
)


def _canned_ddl(table_name: str) -> str:
    """MCP schema retrieval 的确定性替身：返回零售 schema 真实 DDL 文本。

    真实链路的 MCP seam 已由 contracts + live e2e 覆盖；此处只钉 graph 行为。
    """
    ddl = {
        "fact_orders": (
            "CREATE TABLE fact_orders (\n"
            " order_id integer,\n order_date date,\n store_id integer,\n"
            " customer_id integer,\n product_id integer,\n promotion_id integer,\n"
            " quantity integer,\n order_amount numeric(10,2),\n"
            " payment_method varchar(16)\n);"
        ),
        "dim_store": (
            "CREATE TABLE dim_store (\n"
            " store_id integer,\n store_name varchar(64),\n region varchar(16),\n"
            " city varchar(16),\n store_type varchar(16),\n open_date date\n);"
        ),
    }
    return ddl[table_name]


class _FakeTool:
    """langchain @tool 的最小替身：sql_graph 以 `.invoke({...})` 调 get_table_ddl。"""

    def __init__(self, fn) -> None:
        self._fn = fn

    def invoke(self, payload: dict) -> str:
        return self._fn(payload["table_name"])


def _retail_schema() -> SchemaContext:
    tables = [
        TableSchema(
            name="fact_orders", description="订单事实",
            columns=[
                ColumnSchema(name="order_id", type="integer"),
                ColumnSchema(name="order_date", type="date"),
                ColumnSchema(name="store_id", type="integer"),
                ColumnSchema(name="quantity", type="integer"),
                ColumnSchema(name="order_amount", type="numeric(10,2)"),
                ColumnSchema(name="payment_method", type="varchar(16)"),
            ],
        ),
        TableSchema(
            name="dim_store", description="门店维度",
            columns=[
                ColumnSchema(name="store_id", type="integer"),
                ColumnSchema(name="region", type="varchar(16)"),
                ColumnSchema(name="city", type="varchar(16)"),
            ],
        ),
    ]
    return SchemaContext(version="1.0", source="test", tables=tables, status="SUCCESS")


@_NEEDS_DB
def test_object_not_found_mcp_refresh_repair_succeeds(monkeypatch) -> None:
    from app.agent import sql_graph as sg
    from app.llm.mock import MockLLMAdapter

    # 1) LLM 侧：确定性 mock（单例实例——adapter 内 kind 调用序计数器跨调用累积，
    #    每次 new 会让 seq 永远停在 1，generate:2 永远取不到）
    adapter = MockLLMAdapter(_FIXTURES_DIR, _CASE)
    monkeypatch.setattr("app.llm.get_llm_adapter", lambda: adapter)
    # 2) FAQ 检索离线不触发（replay 只关心 repair 链，faq 不影响路由）
    monkeypatch.setattr(sg.registry, "get", lambda names: [])
    # 3) MCP schema retrieval 边界 → canned DDL（确定性替身，带 .invoke 契约）
    monkeypatch.setattr(sg, "get_table_ddl", _FakeTool(_canned_ddl))

    graph = sg.build_sql_graph()
    final = graph.invoke({
        "user_query": "2024年各区域销售额排名",
        "schema_context": _retail_schema(),
    })

    # 修复链完整走通：两次 generate（坏 SQL → 修复 SQL）、一次 MCP schema 刷新
    assert final.get("retry_counters", {}).get("sql_generation") == 2, final.get("retry_counters")
    assert final.get("retry_counters", {}).get("mcp_schema") == 1, final.get("retry_counters")
    assert final.get("execution_status") == "SUCCESS"
    # MCP 刷新确实发生且替换了 schema_context（source 标记）
    refreshed = final.get("schema_context")
    assert refreshed is not None and refreshed.source == "mcp_search_schema"
    # 最终 SQL 是修复版（不残留坏列）
    assert "bogus_column" not in (final.get("generated_sql") or "")
    assert "fact_orders" in (final.get("generated_sql") or "")
    # 真执行返回真实数据
    qr = final.get("query_result")
    assert qr is not None and qr.status == "SUCCESS"
    assert qr.rows and len(qr.rows) > 0
    assert qr.row_count > 0
