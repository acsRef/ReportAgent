# 2026-08-10 Schema FAQ RAG：ReportAgent 端 stdio MCP client 连 ragent-py

> 状态: 已完成（commit `8b06978`；含真实 E2E 冒烟）

## Context

用户明确要求：「用 MCP，接入 RAG 里来查询」，RAG 项目在 `D:\PyProject\ragent-py`。已确认范围：

- **Phase A（已完成，book `94c4930`）**：ragent-py 加独立 FAQ 知识库 + `ingest_faq`/`search_faq`/`list_faq_docs` MCP 工具（stdio），`faq_seed.json` 20 条种子。
- **Phase B（本 plan）**：ReportAgent 后端**真正搭一个 stdio MCP client**，连 ragent-py 的 `mcp_server`，调 `search_faq` 检索 FAQ，注入 `_generate_sql` prompt。

现状（查证属实）：ReportAgent `backend/requirements.txt` 已有 `mcp>=1.0.0`；后端当前**没有任何真实 MCP 客户端**（`app/tools/registry.py` 全是本地工具）。`_generate_sql`（`app/agent/sql_graph.py`）现在经 `app/tools/faq_tools.py::search_faq`（本地 JSON）检索 FAQ。ragent-py `mcp_server` 是 stdio 传输、需 `rag` env（`D:/miniConda/envs/rag/python.exe`）运行、内部经 HTTP 连 ragent-py FastAPI（读 `RAGENT_URL/RAGENT_USER/RAGENT_PASSWORD`）。

## Design

### 1. 持久 stdio MCP client（新 `backend/app/tools/mcp_faq_client.py`）

`MCPFaqClient`：进程级懒加载单例，后台线程跑专用事件循环，持一个 ragent-py `mcp_server` 的 stdio 会话，跨调用复用（避免每次 SQL 生成都起子进程）：

- `_ensure_loop()`：起后台线程 + `asyncio.new_event_loop().run_forever()`。
- `_ensure_session()`（在后台循环上）：`stdio_client(StdioServerParameters(command,args,env,cwd))` → `ClientSession` → `initialize()`。首次调用初始化，后续复用；失败置空以便下次重试。
- `search_faq(query, top_k) -> str`（同步、线程安全）：`run_coroutine_threadsafe(self._await_search_faq(...), self._loop).result(timeout=RAGENT_MCP_TIMEOUT)`。后台循环上用 `asyncio.Lock` 串行化 `session.call_tool("search_faq", {...})`。
- 解析：`result.content` 的 `TextContent` 拼回 JSON 文本返回。
- 失败语义：子进程起不来/握手失败/调用错误/超时 → 抛 `MCPFaqClientError`（中文），由调用方降级。

配置（env，默认贴合用户环境，可覆盖）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `RAGENT_MCP_PYTHON` | `D:/miniConda/envs/rag/python.exe` | 起 `mcp_server` 的 python（须是 ragent-py 的 env） |
| `RAGENT_MCP_MODULE` | `mcp_server.server` | 模块 |
| `RAGENT_MCP_CWD` | `D:/PyProject/ragent-py` | 子进程 cwd（让 `mcp_server` 可导入）、经父进程 env 透传 `RAGENT_URL/RAGENT_USER/RAGENT_PASSWORD/FAQ_KB_NAME` |
| `RAGENT_MCP_TIMEOUT` | `15.0` | 单次 `search_faq` 调用超时（秒） |
| `FAQ_KB_NAME` | `FAQ` | 与 ragent-py 侧一致 |

客户端信号：配置未齐（环境变量缺 ragent-py 路径/cwd）→ 不初始化直接抛「MCP FAQ 服务未配置」。

### 2. `faq_tools.search_faq` 主从切换（`app/tools/faq_tools.py`）

`@tool search_faq` 改为：**优先走 MCP client** → 失败/未配置降级本地 `_search_faq_rows`（`backend/scripts/schema_faq.json`，留存作离线 fallback）。返回契约不变：`{"matches": [...]}` JSON 字符串——`_generate_sql` 及其 graph 测试零改动。

```
try:
    rows = MCP client.search_faq(...) 解析 matches
    若 MCP 返回非空 → 用 MCP 结果
except (MCPFaqClientError, Timeout, ...):
    logger.warning(...) 降级本地
本地: rows = _search_faq_rows(query, top_k)
```

### 3. SQL 注入不变

`_generate_sql` 已调 `search_faq.invoke({"query", "top_k"})` 并注入 `faq_block`——`search_faq` 内部切到 MCP 后，调用面与注入逻辑**无需改动**。

## Files to change

- `backend/app/tools/mcp_faq_client.py`（新建）：`MCPFaqClient` / `get_mcp_faq_client()` / `MCPFaqClientError`。
- `backend/app/tools/faq_tools.py`：`search_faq` @tool 主从切换（保 `_search_faq_rows` fallback）。
- `backend/.env.example`：补 `RAGENT_MCP_*` / `FAQ_KB_NAME`。
- `docs/plans/2026-08-10-schema-faq-mcp-client.md`（本文件）。

## Reused existing utilities

- `mcp` 包 `ClientSession` / `StdioServerParameters` / `stdio_client`（backend 已有 `mcp>=1.0.0` 依赖）。
- `faq_tools._search_faq_rows` 既有实现：MCP 不可用时的离线 fallback。
- `_generate_sql` 的 `search_faq.invoke(...)` + `faq_block` 注入：调用面不变，仅 `search_faq` 内部换后端。
- `app/tools/__init__.py` 已注册 `search_faq` 工具（Phase B 不重注册）。

## Verification

```bash
cd backend && pytest tests/smoke/test_schema_faq.py tests/smoke/test_mcp_faq_client.py -v
cd backend && pytest -q                     # 全量离线回归
```

新增 `backend/tests/smoke/test_mcp_faq_client.py`（离线，不打真实 ragent-py）：

1. `faq_tools.search_faq` MCP 成功：patch 客户端返回 `{"matches":[...]}` → 返回 MCP matches，**不落本地**。
2. MCP 抛错降级：patch 客户端抛 `MCPFaqClientError` → 落到本地 `_search_faq_rows`（非空）。
3. MCP 未配置：patch `get_mcp_faq_client` 抛「未配置」→ 本地 fallback 非空。
4. MCP 返回空 + 本地有 → 本地 fallback 或空（断言不抛）。
5. `MCPFaqClient.search_faq` 同步桥接：patch 后台协程返回罐装文本，断言返回串（线程桥接契约）。
6. 既有 graph 注入测试（`test_sql_generation.py`）保持绿——`search_faq.invoke` 返回 `{"matches":[...]}` 契约不变。

手工冒烟（需 ragent-py FastAPI + docker PG 起，且 FAQ 已灌）：

1. 起 ragent-py → ReportAgent 里 `search_faq("退货率")` 返回 ragent-py 的 FAQ matches（走 MCP）。
2. 停 ragent-py → `search_faq` 降级本地，SQL 生成不崩。

## 落地记录（2026-08-10，含真实 E2E）

报告提交前做了一次真实 E2E（起 ragent-py FastAPI + 经 MCP 灌 20 条 FAQ 到 FAQ KB + 经 `faq_tools.search_faq` 检索）：

- `ingest_faq` 经 stdio MCP 灌 20/20 索引成功（embedding 走 SiliconFlow）。
- `search_faq` 检索「退货率 → 区域退货率」「毛利率 → 毛利率Top产品」「销售额排名 → 各区域销售额排名」「库存周转率 → 库存周转」「出勤率 → 出勤率」全部命中正确。
- 单例 `get_mcp_faq_client()` 复用会话避免登录 429。
- **环境约束（更正）**：早前误报「ragent-py 运行时共享 PG 角色状态让持久化测试失败、需停 ragent-py」。复核后确认这是**误判**——真实原因是 E2E 测试脚本留下的孤儿 mcp_server 子进程造成暂态干扰，已随进程退出消失；全量 338 passed 在 **ragent-py 起着时也稳定成立**。为根治孤儿化，`MCPFaqClient` 新增 `close()`（退出会话杀子进程 + 停后台线程）并接入 main.py 关闭流程（commit `3d27d10`）。

## Explicitly NOT doing

- **不做** 把 `search_interface_dictionary`（字典检索）也迁到 MCP——字典桥既定用 HTTP 直连，本次只动 FAQ 路径。
- **不做** langchain-mcp-adapters 把整个 MCP 工具面挂进 agent——只建一个精确的 `search_faq` MCP client，收敛注入。
- **不做** 移除 `backend/scripts/schema_faq.json`——留存作离线 fallback（与「MCP 挂时本地兜底」哲学一致）。
- **不做** HTTP/SSE transport for MCP——沿用 ragent-py 现有 stdio。
- **不做** 常驻健康检查/自动重连协议——失败降级本地即可，恢复在下次调用自然重试。