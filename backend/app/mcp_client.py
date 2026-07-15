from __future__ import annotations

import json

from langchain_core.tools import tool


class MCPSchemaClient:
    def __init__(self):
        self._session = None
        self._exit_stack = None
        self._connected = False

    async def connect(self):
        from contextlib import AsyncExitStack
        from mcp.client.stdio import stdio_client, StdioServerParameters

        server_params = StdioServerParameters(
            command="python",
            args=["-m", "mcp_schema_server.server"],
        )
        self._exit_stack = AsyncExitStack()
        read_stream, write_stream = await self._exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        from mcp.client.session import ClientSession
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await self._session.initialize()
        self._connected = True

    async def disconnect(self):
        if self._exit_stack:
            await self._exit_stack.aclose()
            self._session = None
            self._exit_stack = None
            self._connected = False

    async def search_tables(self, query: str, top_k: int = 3) -> list[dict]:
        if not self._connected:
            return []
        result = await self._session.call_tool("search_tables", {
            "query": query,
            "top_k": top_k,
        })
        return json.loads(result.content[0].text)

    async def get_table_ddl(self, table_name: str) -> str | None:
        if not self._connected:
            return None
        result = await self._session.call_tool("get_table_ddl", {
            "table_name": table_name,
        })
        text = result.content[0].text
        if text.startswith("Table '"):
            return None
        return text

    async def list_tables(self) -> list[dict]:
        if not self._connected:
            return []
        result = await self._session.call_tool("list_tables", {})
        return json.loads(result.content[0].text)

    @property
    def search_tables_wrapper(self):
        @tool
        async def search_tables(query: str, top_k: int = 3) -> str:
            """语义搜索数据库表：输入自然语言查询，返回最相关的表和字段结构。"""
            results = await self.search_tables(query, top_k)
            return json.dumps(results, ensure_ascii=False)
        return search_tables

    @property
    def get_table_ddl_wrapper(self):
        @tool
        async def get_table_ddl(table_name: str) -> str:
            """获取某张表的完整 CREATE TABLE DDL，包含所有字段名和类型。"""
            ddl = await self.get_table_ddl(table_name)
            return ddl or f"Table '{table_name}' not found"
        return get_table_ddl

    @property
    def list_tables_wrapper(self):
        @tool
        async def list_tables() -> str:
            """列出数据库中所有可用的表及其简要描述。"""
            results = await self.list_tables()
            return json.dumps(results, ensure_ascii=False)
        return list_tables

    def get_tool_definitions(self) -> list[dict]:
        return [
            {
                "name": "search_tables",
                "description": "语义搜索数据库表：输入自然语言查询，返回最相关的表和字段结构。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "自然语言查询"},
                        "top_k": {"type": "integer", "description": "返回结果数", "default": 3},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "get_table_ddl",
                "description": "获取某张表的完整 CREATE TABLE DDL，包含所有字段名和类型。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "table_name": {"type": "string", "description": "表名"},
                    },
                    "required": ["table_name"],
                },
            },
            {
                "name": "list_tables",
                "description": "列出数据库中所有可用的表及其简要描述。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        ]


schema_client = MCPSchemaClient()
