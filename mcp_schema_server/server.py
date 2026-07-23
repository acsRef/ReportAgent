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
            description="Semantic search for database tables relevant to a query. "
                        "Example: search_tables('退货率趋势') finds returns, sales tables",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query describing what data you're looking for",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (default: 3)",
                        "default": 3,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_table_ddl",
            description="Get the full CREATE TABLE DDL for a specific table. "
                        "Example: get_table_ddl('fact_sales')",
            inputSchema={
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "Name of the table (e.g. 'fact_sales', 'dim_product', 'dim_region')",
                    },
                },
                "required": ["table_name"],
            },
        ),
        Tool(
            name="list_tables",
            description="List all available tables with brief descriptions and column counts.",
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
