> 状态: 已完成

# `mcp_schema_server` 迁到 MCP SDK 2.x：能启动、能返回真数据、可扩展

## Context（背景）

用户原话：**「mcp 是一定要启动起来的，不然后面如果要扩展的话 怎么扩展」**。

现状与问题（2026-11-05 现场取证）：

```
python -m mcp_schema_server.server
→ AttributeError: 'Server' object has no attribute 'list_tools'
```

三个叠加原因：

1. **SDK 版本代差**：环境里是 `mcp 2.1.1`，`Server` 不再有 `list_tools()/call_tool()` 装饰器
   （2.x 把 `FastMCP` 改名 `MCPServer`，低层 `Server` 改成 `add_request_handler`）。
   `pip install "mcp<2"` 在当前源（清华镜像）不可得。
2. **字典文档格式漂移**：即使协议能起来，`registry._parse_table_doc` 只认旧格式
   （`# 表 \`public.x\`` + `字段 a 类型 b 含义 …`），而 ragent-py 现在渲染的是
   `【表 public.x / 字段详解】` + `# 表 public.x` + Markdown 表格 → **search_tables 全空、
   get_table_ddl 全部 "not found"**。
3. **`list_tables` 用错字段**：把 `chunk_count`（文档切片数）当 `column_count` 返回，
   7 张表里出现「1 列」「2 列」。
4. HTTP 常驻模式没有父进程透传 env → 连不上字典服务（`RAGENT_URL` 为空）。

## Design（设计）

### D1 迁到 `mcp.server.mcpserver.MCPServer`

用 2.x 的装饰器写法重写 `server.py`：一个工具 = 一个 `@server.tool(name=…, description=…)`
函数，返回 `str`（JSON），序列化交给 SDK。工具名 / 描述 / 返回文本契约**逐字保留**
（Tool Description 就是 Agent Contract，见 CLAUDE.md §7）。

### D2 两种启动形态

| 模式 | 命令 | 用途 |
|---|---|---|
| stdio（默认） | `python -m mcp_schema_server.server` | MCP 客户端自己 spawn（`StdioServerParameters`） |
| HTTP 常驻 | `MCP_SCHEMA_TRANSPORT=streamable-http MCP_SCHEMA_PORT=8765 …` | 当一个可观测的服务跑着，多客户端共用/调试 |

HTTP 模式自己 `load_dotenv(<repo>/.env)`，不再依赖父进程环境。

### D3 解析器与 ragent-py 现行格式对齐

`registry._parse_table_doc` 兼容新旧两种标题与字段写法（与
`backend/app/tools/rag_schema.py` 同一套判定），描述从「表 `public.x` 用途:…」这一行提取，
不再留空。

### D4 `list_tables` 返回真实字段数

改为按表名检索一次字典文档并解析，如实给出 `column_count` 与 `description`
（单表失败只影响它自己）。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `mcp_schema_server/server.py` | 重写为 `MCPServer` + 4 个 `@server.tool`；stdio/HTTP 双形态；启动时加载 `.env` |
| `mcp_schema_server/registry.py` | `_parse_table_doc` 兼容新格式 + 描述提取；`list_tables` 修正 `column_count` |
| `docs/architecture/mcp-contract.md` | 追加：历史资产已可独立启动，作为本仓 MCP 扩展落点（正路仍是 ragent-py MCP） |

## Reused existing utilities（复用工具）

- `mcp_schema_server/registry.py` 的检索能力与 DDL 拼装原样复用，只修解析；
- 字典数据源仍是 ragent-py 的字典知识库（`DICT_KB_NAME`），不新增本地索引；
- 后端解析逻辑（`backend/app/tools/rag_schema.py::_parse_table_doc`）作为解析口径的唯一参照。

## Verification（验证）

1. **stdio**：`python .e2elogs/probe_mcp_schema_server.py`（真 MCP 客户端 spawn 子进程）
   - `list_tools` → 4 个工具；
   - `search_tables('2024年华东销售额趋势')` → `dim_store` 等带字段与 DDL；
   - `get_table_ddl('fact_orders')` → 完整 CREATE TABLE；
   - `search_faq('区域退款率')` → 带示例 SQL 与口径说明；
   - `list_tables` → 7 张表，`column_count` 为真实字段数（如 `fact_payments`=6）。
2. **HTTP**：`MCP_SCHEMA_TRANSPORT=streamable-http` 起服务，`streamable_http_client`
   连 `http://127.0.0.1:8765/mcp` → 同样 4 个工具、同样真实数据。

## Explicitly NOT doing（不做事项）

- **不把后端 schema 检索切到本 server**：契约（`mcp-contract.md` §一）规定正路是
  `mcp_client → ragent-py mcp_server`，切过去会同时改契约与 freeze 测试，本轮不越界；
- 不改 ragent-py 字典文档格式（数据侧由 ragent-py 负责）；
- 不引入新的传输（SSE）或鉴权，只保留 stdio + streamable-http 两种；
- 不扩工具集（extension 示例写在 `server.py` 模块 docstring 里，加工具按同样三步走）。
