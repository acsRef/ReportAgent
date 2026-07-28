from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.types import Tool, TextContent

from mcp_schema_server.registry import registry


server = Server("schema-registry")


@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_tables",
            description=(
                "根据中文业务关键词语义搜索数据库表，返回最相关的表名、字段结构、DDL 和匹配分。\n"
                "\n"
                "用途：不知道数据在哪个表时用来「找表」。用户问题里提到了销售额、退货率、库存、考勤等业务概念时优先用这个。\n"
                "\n"
                "输入：\n"
                "  query（必填）— 中文自然语言描述，如 '2024年各区域销售额'\n"
                "  top_k（可选，默认 3）— 返回前 N 个最相关的表\n"
                "\n"
                "输出：JSON 数组，每项包含：\n"
                "  table_name（表名，如 fact_sales）\n"
                "  description（中文业务描述）\n"
                "  columns（字段列表 [{name, type}]）\n"
                "  ddl（完整建表语句）\n"
                "  score（匹配分，越高越相关）\n"
                "\n"
                "示例：\n"
                "  search_tables('退货原因分析') → 返回 fact_returns（退货记录表）\n"
                "  search_tables('每个月的销售趋势') → 返回 fact_sales + dim_date\n"
                "\n"
                "不要用来：\n"
                "  - 已经知道表名、只需要看字段结构时 → 用 get_table_ddl\n"
                "  - 想看数据库里总共有哪些表时 → 用 list_tables\n"
                "  - 查具体的业务数据行（销售额是多少、退货有几笔）→ 这不是查数据的工具\n"
                "\n"
                "失败处理：无匹配表时返回默认 Top 表（按优先级排列），不会报错。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "中文自然语言查询，描述你要找的数据内容，如 '销售额趋势'、'退货原因分析'、'库存情况'",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回前 N 个最相关的表（默认 3）",
                        "default": 3,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_table_ddl",
            description=(
                "获取指定单张表的完整 CREATE TABLE DDL，包含所有字段名、类型、精度。\n"
                "\n"
                "用途：已经通过 search_tables 或 list_tables 确定了目标表名，需要看精确字段名和类型来写 SQL。\n"
                "SQL Agent 在生成 SQL 前用这个工具确认字段存在性。\n"
                "\n"
                "输入：\n"
                "  table_name（必填）— 精确表名（英文下划线格式），如 'fact_sales'、'dim_product'。\n"
                "\n"
                "输出：CREATE TABLE 文本，包含所有字段名和类型。\n"
                "\n"
                "示例：\n"
                "  get_table_ddl('fact_sales') → 返回含 sale_id、total_amount、profit 等字段的建表语句\n"
                "  get_table_ddl('dim_region') → 返回含 region_name、province、city、tier 的建表语句\n"
                "\n"
                "不要用来：\n"
                "  - 不知道表名时 → 先用 search_tables 找表\n"
                "  - 想看所有表的总览 → 用 list_tables\n"
                "  - 表名拼写错误（如 'fact_sale' 少写了 s）→ 会返回 'Table xxx not found'\n"
                "  - 获取业务数据或统计数据 → 这不是数据查询工具\n"
                "\n"
                "失败处理：表不存在时返回 \"Table 'xxx' not found\"。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "精确表名（英文下划线格式），如 'fact_sales'、'dim_product'、'dim_region'",
                    },
                },
                "required": ["table_name"],
            },
        ),
        Tool(
            name="list_tables",
            description=(
                "列出数据库中所有表的表名、中文描述和字段数量。\n"
                "\n"
                "用途：\n"
                "  - 完全不熟悉数据库结构时，先整体了解有哪些维度表和事实表\n"
                "  - search_tables 未返回理想结果时作为兜底方案\n"
                "  - 快速确认某个表是否存在\n"
                "\n"
                "输入：无。不需要任何参数。\n"
                "\n"
                "输出：JSON 数组，每项包含：\n"
                "  table_name（表名，如 fact_sales）\n"
                "  description（中文描述，如 '销售记录事实表'）\n"
                "  column_count（字段数量，如 12）\n"
                "\n"
                "示例：\n"
                "  返回 10 张表，含 4 张事实表（fact_sales、fact_returns、fact_inventory、fact_attendance）\n"
                "  和 6 张维度表（dim_date、dim_region、dim_product、dim_customer、dim_warehouse、dim_employee）\n"
                "\n"
                "不要用来：\n"
                "  - 已经确定了目标表 → 用 get_table_ddl 查看具体字段结构\n"
                "  - 需要按业务概念搜索 → 用 search_tables（语义匹配更精确）\n"
                "  - 查询业务数据 → 这不是数据查询工具\n"
                "\n"
                "失败处理：数据库不可用时返回空数组。"
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
    if arguments is None:
        arguments = {}

    if name == "search_tables":
        query = arguments.get("query", "")
        top_k = arguments.get("top_k", 3)
        results = registry.search_tables(query, top_k)
        return [TextContent(
            type="text",
            text=json.dumps(results, ensure_ascii=False, indent=2),
        )]

    elif name == "get_table_ddl":
        table_name = arguments.get("table_name", "")
        ddl = registry.get_table_ddl(table_name)
        if ddl:
            return [TextContent(type="text", text=ddl)]
        return [TextContent(type="text", text=f"Table '{table_name}' not found")]

    elif name == "list_tables":
        tables = registry.list_tables()
        return [TextContent(
            type="text",
            text=json.dumps(tables, ensure_ascii=False, indent=2),
        )]

    raise ValueError(f"Unknown tool: {name}")


async def main():
    from mcp.server.stdio import stdio_server

    count = registry.build_index()
    print(f"Schema registry initialized: {count} tables indexed", file=sys.stderr)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="schema-registry",
                server_version="1.0.0",
                capabilities={"tools": {}},
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
