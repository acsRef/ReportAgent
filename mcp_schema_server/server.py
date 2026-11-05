"""ReportAgent 的表结构 / FAQ 数据字典 MCP 服务。

## 这个服务是什么

把 `mcp_schema_server/registry.py` 的检索能力（表结构 / DDL / FAQ 口径）以 **MCP 工具**暴露出去：
`search_tables` / `get_table_ddl` / `search_faq` / `list_tables`。

Registry 的数据来源是 ragent-py 的字典知识库（`DICT_KB_NAME`），所以本服务是「MCP 客户端 →
本服务 → ragent-py 字典 KB」这条链的中间层，换 schema / 加口径都改数据，不改代码。

## 怎么跑

```bash
# 1) stdio 模式（默认）——MCP 客户端自己 spawn 的方式
python -m mcp_schema_server.server

# 2) HTTP 模式——当成一个常驻服务，便于调试/多客户端共用
MCP_SCHEMA_TRANSPORT=streamable-http MCP_SCHEMA_PORT=8765 python -m mcp_schema_server.server
#   → http://127.0.0.1:8765/mcp
```

## 怎么扩展（加一个工具）

1. 在 `registry.py` 里实现检索/查询函数；
2. 在下面 `server` 上挂一个 `@server.tool(...)` 函数，**description 写清
   「什么时候用 / 什么时候不用 / 输入输出 / 失败怎么返回」**（Tool Description 就是 Agent Contract，
   写得模糊 Agent 必乱调用，见 CLAUDE.md §7）；
3. 返回 `str`（JSON 文本）即可，序列化由 MCPServer 负责。

## 2026-11-05：从 mcp 1.x 低层 API 迁到 2.x `MCPServer`

原来用 `Server(...) + @server.list_tools() + @server.call_tool()`（1.x 低层 API）。
当前环境装的是 `mcp 2.1.1`：`list_tools/call_tool` 装饰器已不存在（2.x 改名 `MCPServer`，
即原 `FastMCP`），于是这个 server 一启动就 `AttributeError`。
迁移到 `mcp.server.mcpserver.MCPServer` 的装饰器写法，协议行为（工具名/描述/返回文本）保持不变。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer

from mcp_schema_server.registry import registry

_SEARCH_TABLES_DESC = (
    "根据中文业务关键词语义搜索数据库表，返回最相关的表名、字段结构、DDL 和匹配分。\n"
    "\n"
    "用途：不知道数据在哪个表时用来「找表」。用户问题里提到了销售额、退款、趋势等业务概念时优先用这个。\n"
    "\n"
    "输入：\n"
    "  query（必填）— 中文自然语言描述，如 '2024年各区域销售额'\n"
    "  top_k（可选，默认 3）— 返回前 N 个最相关的表\n"
    "\n"
    "输出：JSON 数组，每项包含：table_name / description / columns / ddl / score。\n"
    "\n"
    "不要用来：\n"
    "  - 已经知道表名、只需要看字段结构时 → 用 get_table_ddl\n"
    "  - 想看数据库里总共有哪些表时 → 用 list_tables\n"
    "  - 查具体的业务数据行（销售额是多少、退货有几笔）→ 这不是查数据的工具\n"
    "\n"
    "失败处理：无匹配表时返回默认 Top 表（按优先级排列），不会报错。"
)

_GET_DDL_DESC = (
    "获取指定单张表的完整 CREATE TABLE DDL，包含所有字段名、类型、精度。\n"
    "\n"
    "用途：已经确定了目标表名，需要看精确字段名和类型来写 SQL。\n"
    "\n"
    "输入：\n"
    "  table_name（必填）— 精确表名（英文下划线格式），如 'fact_orders'、'dim_product'。\n"
    "\n"
    "输出：CREATE TABLE 文本，包含所有字段名和类型。\n"
    "\n"
    "不要用来：\n"
    "  - 不知道表名时 → 先用 search_tables 找表\n"
    "  - 想看所有表的总览 → 用 list_tables\n"
    "  - 获取业务数据或统计数据 → 这不是数据查询工具\n"
    "\n"
    "失败处理：表不存在时返回 \"Table 'xxx' not found\"。"
)

_SEARCH_FAQ_DESC = (
    "根据中文业务关键词检索历史 FAQ 知识库，返回最相关的常见问题、示例 SQL 和业务口径要点。\n"
    "\n"
    "用途：写 SQL 前查「这类问题以前怎么算」——业务口径（销售额口径、退款率、销售占比、月度趋势等）"
    "和常见分组/排序模板都在这。不知道指标怎么算时先查这里。\n"
    "\n"
    "输入：\n"
    "  query（必填）— 中文自然语言描述，如 '区域退款率'、'月度销售趋势'\n"
    "  top_k（可选，默认 3）— 返回前 N 个案例\n"
    "\n"
    "输出：JSON 数组，每项包含：question / sql / note / tables / score。\n"
    "\n"
    "不要用来：\n"
    "  - 找数据在哪个表 → 用 search_tables\n"
    "  - 查具体业务数据行 → 这不是数据查询工具\n"
    "\n"
    "失败处理：无匹配案例时返回空数组，不报错。"
)

_LIST_TABLES_DESC = (
    "列出数据库中所有表的表名、中文描述和字段数量。\n"
    "\n"
    "用途：\n"
    "  - 完全不熟悉数据库结构时，先整体了解有哪些维度表和事实表\n"
    "  - search_tables 未返回理想结果时作为兜底方案\n"
    "  - 快速确认某个表是否存在\n"
    "\n"
    "输入：无。不需要任何参数。\n"
    "\n"
    "输出：JSON 数组，每项包含：table_name / description / column_count。\n"
    "\n"
    "不要用来：\n"
    "  - 已经确定了目标表 → 用 get_table_ddl 查看具体字段结构\n"
    "  - 需要按业务概念搜索 → 用 search_tables（语义匹配更精确）\n"
    "  - 查询业务数据 → 这不是数据查询工具\n"
    "\n"
    "失败处理：数据库不可用时返回空数组。"
)

server = MCPServer(
    name="schema-registry",
    version="1.0.0",
    instructions=(
        "ReportAgent 的数据库结构 / 业务口径 MCP 服务。"
        "找表用 search_tables，确认字段用 get_table_ddl，"
        "查历史口径与示例 SQL 用 search_faq，整体浏览用 list_tables。"
    ),
)


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


@server.tool(name="search_tables", description=_SEARCH_TABLES_DESC)
def search_tables(query: str, top_k: int = 3) -> str:
    return _dump(registry.search_tables(query, top_k))


@server.tool(name="get_table_ddl", description=_GET_DDL_DESC)
def get_table_ddl(table_name: str) -> str:
    ddl = registry.get_table_ddl(table_name)
    if ddl:
        return ddl
    return f"Table '{table_name}' not found"


@server.tool(name="search_faq", description=_SEARCH_FAQ_DESC)
def search_faq(query: str, top_k: int = 3) -> str:
    return _dump(registry.search_faq(query, top_k))


@server.tool(name="list_tables", description=_LIST_TABLES_DESC)
def list_tables() -> str:
    return _dump(registry.list_tables())


def _load_env() -> None:
    """加载仓库根 `.env`（HTTP 常驻模式下没有父进程透传 env，缺它连不上字典服务）。

    stdio 模式下客户端会透传 env，这里再读一次也无副作用；`python-dotenv` 缺失时静默跳过。
    """
    try:
        from pathlib import Path

        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except Exception:  # noqa: BLE001 - 没有 dotenv 就靠真实环境变量
        pass


def main() -> None:
    """启动 MCP 服务：默认 stdio（客户端 spawn），`MCP_SCHEMA_TRANSPORT=streamable-http` 走常驻 HTTP。"""
    _load_env()
    registry.build_index()
    print(
        "Schema registry initialized: schema sourced live from ragent-py dictionary KB",
        file=sys.stderr,
    )

    transport = os.getenv("MCP_SCHEMA_TRANSPORT", "stdio").strip().lower()
    if transport in ("http", "streamable-http", "streamable_http"):
        host = os.getenv("MCP_SCHEMA_HOST", "127.0.0.1")
        port = int(os.getenv("MCP_SCHEMA_PORT", "8765"))
        print(f"schema-registry MCP serving on http://{host}:{port}/mcp", file=sys.stderr)
        server.run(transport="streamable-http", host=host, port=port)
        return
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
