> 状态: 已完成

# 数据字典 RAG 桥：ragent-py MCP + 字段语义澄清闭环

## Context

用户原始需求（2026-08-06）：

> 1、需要针对特定的接口来生成类似于报表的那种形式。但是在这个过程中可能需要你对用户的接口啊或者是其他的东西来做一个处理，就是每个字段什么意思，这些可能你需要问清楚的
> 2、mcp这里 D:\PyProject\ragent-py，需要针对这个rag系统，做一个mcp意思就是说需要表结构啊什么的都塞到rag里，这里你或许也需要看一下那个系统。

现状盘点（2026-08-06 代码核实）：

- ReportAgent 字段语义**没有权威存放处**：`init_pg.sql` / `seed_pg.sql` 中 `COMMENT ON` 为 0；表级中文描述硬编码在 `mcp_schema_server/registry.py` 与 `backend/app/tools/data_tools.py` 两处（既有显性冗余）；列级语义哪里都不存在，只能靠命名约定猜。
- ragent-py（RAG 系统）有完整摄入/检索链路（`/api/v1/documents/upload` 增量摄入、KB CRUD、三路混合检索 + RRF、embedding 熔断降级），但**没有 MCP 服务端**，也没有「只检索不生成」的轻量端点。
- 两项目共用同一 PG 实例与 `ragent` 库（ReportAgent 用 `public/app/agent/memory/observability`，ragent-py 用自己的 18 张表）。
- ReportAgent backend 目前没有真实 MCP 客户端代码；`app/tools/registry.py` 是工具注册的唯一入口。

目标：ragent-py 成为**数据字典**（表结构语义 + 接口字段字典）的载体；新增 MCP 服务作为灌入桥；ReportAgent 报表流程能查字段含义，含义不明时经既有 RequirementCard assumptions 通道向用户澄清，未确认不放行执行。

与用户确认的边界：

- 拆两个子项目，**先 A 后 B**：A = ragent-py MCP 服务 + `POST /api/v1/retrieve`；B = ReportAgent 字典工具 + 澄清闭环。
- 功能 1 的 v1 界线 = **字典查询 + 澄清**，不做接口/API 数据源取数报表（不建新数据源）。
- 字典内容 v1 即覆盖**表结构与普通接口**两类；接口字典需区分普通接口与长连接（protocol 标记，见 Design）。
- 字段语义权威源：**PG 表以 `COMMENT ON COLUMN` 为权威**（一次性补进 `seed_pg.sql`）；**接口以 `upsert_api_dictionary` 全量入参为权威**（接口没有 live schema）。RAG 文档对表是「投影」（可随时重灌），对接口是「本体」（全量覆盖）。

## Design

### 架构

```text
Claude Code（stdio）          ReportAgent backend
       │                            │ search_interface_dictionary
       │ MCP                        │（app/tools/registry 注册，httpx 直连）
       ▼                            ▼
ragent-py/mcp_server/ ──HTTP──► ragent-py FastAPI
  ingest_table_schemas              POST /api/v1/retrieve（新增，只检索不生成）
  upsert_api_dictionary             POST /api/v1/documents/upload（既有，增量）
  search_dictionary                 POST/GET /kb、/auth/login（既有）
  list_dictionary_docs
       │
       └─ introspect ──只读──► PG（information_schema + col_description）
```

### 决策基线

| 决策 | 结论 |
|---|---|
| 接入方式 | HTTP 桥接 MCP（stdio），`ragent-py/mcp_server/` 与 `app/` 平级；ragent-py 只加一个 `/retrieve` 端点 |
| 字典 KB | 专用 KB「数据字典」，`visibility=internal`，上传/检索同一服务账号（env 配置） |
| 幂等键 | **确定性文件名**（`dict-table_<schema>_<table>.md` / `dict-api_<name>.md`）→ `_resolve_document_id` 同名复用 `document_id` → content-hash 增量。已核实：首传显式 `document_id` 会 404（`documents.py::_resolve_document_id` 对不存在的 client_doc_id 直接抛错），不可用 |
| `/retrieve` 鉴权 | KB visibility/角色访问判定 + `doc.read_all`/admin bypass。不新增权限项（现有 RBAC 无 `doc.read`） |
| 隔离 | `hybrid_search` 直调、`kb_ids` pin 死，不走 cross-doc 跳转；question channel 跟随 `settings.question_channel_enabled` |
| 语义权威源 | 表：`COMMENT ON COLUMN`；接口：工具全量入参。RAG 不做表语义的权威 |
| 枚举采样 | 低基数列 distinct ≤20（类型白名单：varchar/char/bool/小整数），不带自由样例——避免触发 ragent-py PII 红线 |
| 接口类型 | `protocol ∈ {http, websocket, sse, long_poll}`（默认 `http`）；流式接口字段可用可选 `message` 归属标注消息/事件类型（如 `on_message`）。帧时序/心跳/重连语义不进 v1 |
| 消费方 | Claude Code（MCP stdio）+ ReportAgent（本地 httpx 工具，不经 MCP，避免为一次检索起子进程） |
| 澄清形态 | RequirementCard **assumptions 通道**：`key="field_meaning:<字段名>"`，`text` = LLM 最佳猜测释义，`alternatives` = 候选释义。gate 既有 "assumptions resolved" 检查兜底，未确认不放行。**零共享契约改动**（不加 RequirementFieldKey、不加卡片字段、不动前端） |

### ragent-py 侧新增端点契约

`POST /api/v1/retrieve`

- 入参：`{query: str, kb_ids: list[str], top_k: int = 5}`
- 鉴权：`get_current_user`；可读性 = admin / `doc.read_all` bypass，否则按 KB visibility 与角色访问判定（`restricted` 需角色命中，`public`/`internal` 放行已认证用户）
- 行为：embedding（熔断/异常 → 零向量 BM25-only 降级，照 `retrieval.py` 既有模式抽共享 helper）→ `pgvector_store.hybrid_search(kb_ids=…, can_read_all=…, user_id=…)`
- 出参：`{items: [{chunk_id, document_id, text, title, section_path, score}], degraded: bool}`
- 错误路径：401 未认证；403 对某 kb_id 无可读权限（逐个判定，部分无权 → 403 并指明 kb_id）；空结果返回空 items（调用方话术「字典库无匹配」）

### MCP 工具契约（ragent-py/mcp_server/）

| 工具 | 入参（全部非凭据） | 行为 | 返回 |
|---|---|---|---|
| `ingest_table_schemas` | `schema`(默认 `public`)、`tables`(可选过滤) | introspect（`DICT_PG_DSN` 只读）→ 每表渲染 md（表名/注释/字段表：名称·类型·注释·枚举值/FK 指向）→ 登录 → ensure KB → 逐表 upload（确定性文件名）→ 轮询 `GET /documents/{id}` 至 `indexed`/`failed` | 每表 `{table, filename, document_id, status, chunk_count}`，失败带原因 |
| `upsert_api_dictionary` | `name`、`description`、`protocol`(默认 http)、`endpoint`、`auth`、`fields: [{name, type, required, desc, example, message}]` | 渲染接口字典 md（按 protocol/message 分节）→ upload（`dict-api_<name>.md`）→ 轮询状态 | `{filename, document_id, status, chunk_count}` |
| `search_dictionary` | `query`、`top_k`(默认 5) | 调 `/retrieve`，kb_ids pin 字典 KB | items 列表 |
| `list_dictionary_docs` | 无 | `GET /documents?kb_id=…` | 文档清单 + 摄入状态 |

配置（env，绝不进工具入参）：`RAGENT_URL`（默认 `http://localhost:8000`）、`RAGENT_USER`、`RAGENT_PASSWORD`、`DICT_PG_DSN`、`DICT_KB_NAME`（默认 `数据字典`）。

错误路径（第一公民，全部返回明确文本而非抛栈）：

| 场景 | 行为 |
|---|---|
| ragent-py 不可达 | `ragent-py 服务不可达（<url>）：…` |
| 登录 401 | `登录失败：请检查 RAGENT_USER / RAGENT_PASSWORD` |
| 无 `kb.create` 权限建 KB | `服务账号缺少 kb.create 权限` |
| 摄入 `failed` | 带回 `error_message`（含 PII 拒审情形） |
| 轮询超时（上限 180s） | 返回 `processing` + 提示用 `GET /documents/{id}` 续查 |
| `/retrieve` 空 | `字典库无匹配：<query>` |
| `RAGENT_*` 未配置（ReportAgent 侧工具） | 静默降级：返回「字典服务未配置」，不阻塞 SQL 生成 |

### ReportAgent 侧澄清闭环（子项目 B）

1. `seed_pg.sql` 为 10 张星型模型表全列补 `COMMENT ON COLUMN`（语义素材：`registry.py` 既有表级描述 + 字段命名语义，人工审定）。**重跑 seed 脚本即迁移**（脚本含 `DROP … CASCADE`）。
2. 新增 `search_interface_dictionary(query, top_k)` 工具：httpx 直连 `/retrieve`（服务账号 env + token 缓存/401 重登），在 `app/tools/__init__.py::register_all_tools` 注册（`agent_type="data"`、`capability="dictionary_search"`、`risk_level="low"`，描述按五要素格式）。
3. 需求分析阶段：需求分析是程序化管线（非 LLM 自主 tool-call），故字典查询也走程序化——在 `requirement_analysis_graph.py::_requirement_parse`（它本就负责组装 parse 输入，conversation_context 也在此取）加一次字典检索，命中结果经**新增的 `dictionary_context` 参数**传入 `parse_requirement`（与既有 `conversation_context` 同构：失败降级为空串，绝不阻塞解析）。
4. `requirement_parser` prompt 规则：用户提及的字段若在字典上下文中有释义则直接采用；**无释义或歧义 → 生成 assumption**（`key="field_meaning:<字段>"`，`text`=最佳猜测，`alternatives`=候选释义，经 `build_assumption` 构造）。
5. confirm gate 既有校验（assumptions resolved）天然拦截未确认项。已确认 assumption 的注入路径**已存在**：`confirmed_execution_graph._format_confirmed_requirement` 把 accepted assumptions 序列化进 `confirmed_requirement`，`sql_graph._plan` 经 `confirmed_block` 注入 prompt——**无需改动这两个文件**。注意：新工具注册进 registry 后会出现在所有**未传 whitelist** 的 `_format_tools_for_prompt()` 调用点（如 `sql_graph.py` 的 `intent_analyze`），但 `app/llm.py::_INTENT_TOOL_WHITELIST` 默认白名单只含 5 个分析工具（chart_advisor/insight_analyst/trend_analysis/group_compare/detect_anomaly），新工具自然隔离——B3 落地验证 26 用例 `test_sql_generation.py` 与 5 用例 `test_tool_descriptions.py` 全绿。

B4 落地确认（ab810df）：`_PARSE_PROMPT` 中「字段释义规则」段实际位置在「维度判断规则」**之前**（plan 原写"之后"），属合理偏离——LLM 在做维度判断前先确定字段含义，可避免歧义传染到 metric/scope 推断。功能等价、无回归、测试通过。B5 接线时只需在 `_requirement_parse` 内 monkeypatch-free 程序化调用 `search_interface_dictionary.invoke(...)` 即可。

### 数据流

```text
摄入: ingest_table_schemas ─ introspect(只读 DSN) → render md → login
      → ensure KB → upload(确定性文件名) → 轮询状态 → 每表结果
检索: search_dictionary / search_interface_dictionary
      → POST /retrieve{query, kb_ids=[字典KB], top_k}
      → embedding(熔断→BM25-only) → hybrid_search(pin kb_ids) → items
澄清: 需求分析查字典 → 命中即用；未命中/歧义 → field_meaning assumption
      → 用户 accept/选 alternative → confirm 注入 SQL prompt → 出报表
```

## Files to change

**ragent-py**（实施时在其 `docs/plans/` 登记对应条目）：

- 新增 `app/api/retrieve.py`——端点 + 可读性判定 + 错误分支
- `app/models/schemas.py`——`RetrieveRequest` / `RetrieveResponse` / `RetrievedItem`
- `app/main.py`——挂载 router（一行）
- `app/core/retrieval.py`——抽出 `embed_query_with_fallback()` 共享 helper（现地两处调用，不复制粘贴熔断逻辑）
- 新增 `mcp_server/{__init__,server,client,introspect,render}.py` + `mcp_server/requirements.txt`

**ReportAgent**：

- `backend/scripts/seed_pg.sql`——10 表全列 `COMMENT ON COLUMN`
- 新增 `backend/app/tools/interface_dict_tools.py`——httpx 客户端（登录/token 缓存）+ `search_interface_dictionary` 工具
- `backend/app/tools/__init__.py`——注册新工具（五要素描述）
- `backend/app/agent/requirement_analysis_graph.py`——`_requirement_parse` 内加程序化字典检索（失败降级为空）
- `backend/app/agent/requirement_parser.py`——`parse_requirement` / `_call_llm_for_parse` 加 `dictionary_context` 参数 + prompt 增加字段释义采用/澄清规则
- `backend/app/agent/sql_graph.py`——**仅在**新工具污染被钉住 prompt 断言时用 whitelist 隔离（否则不动）
- `backend/.env.example`——补 `RAGENT_URL/RAGENT_USER/RAGENT_PASSWORD/DICT_KB_NAME`
- 测试：`tests/graphs/test_requirement_analysis_sqlgate.py` 扩展（字典工具可达、SQL 工具仍不可达）、字典工具 stub 单测、assumption 澄清契约测试

**前端：零改动**（assumptions 的 accept/reject + alternatives 交互已存在）。若 alternatives 选择交互实际缺失，v1 降级为仅 accept/reject（reject 后由多轮对话补充），不新增组件。

## Reused existing utilities

ragent-py：

- `app/store/pgvector_store.py::hybrid_search`——三路 RRF 混合检索，`/retrieve` 直接调用
- `app/core/retrieval.py`（约 110-135 行）——embedding 熔断 → 零向量 BM25-only 降级模式（抽 helper 复用）
- `app/api/documents.py` upload 链路——`_resolve_document_id` 同名复用 + 增量 hash 管线，upsert 白拿
- `app/middleware/auth.py::get_current_user`、`app/api/kb.py` 的 KB 按名 ensure 模式

ReportAgent：

- `app/tools/registry.py::ToolMetadata` + `register_all_tools`——工具注册唯一入口
- `app/llm.py::_format_tools_for_prompt(whitelist)`——prompt 工具块渲染（带缓存）
- `app/models/requirement.py::RequirementAssumption` + `app/agent/requirement_options.py::build_assumption`——澄清载体与构造器
- `tests/graphs/test_requirement_analysis_sqlgate.py`——SQL 门控钉桩模式
- httpx（`requirements.txt` 已有）

## Verification

离线测试命令：

```bash
# ragent-py（必须用 rag 环境）
D:/miniConda/envs/rag/python.exe -m pytest tests/unit -q          # 新增：retrieve 鉴权/隔离/降级/空结果 + render 格式 + client 登录重试
D:/miniConda/envs/rag/python.exe -c "import app.main"             # 导入链

# ReportAgent
cd backend && pytest                                               # 全量离线套件
cd backend && pytest tests/graphs/test_requirement_analysis_sqlgate.py -v
cd backend && pytest -k "interface_dict"                           # 新工具单测
```

手工冒烟矩阵（docker PG 已起）：

1. 起 ragent-py → MCP 客户端调 `ingest_table_schemas` → 10 表全部 `indexed`，`chunk_count > 0`
2. 连跑第二次 → 全部 unchanged（document_id 复用），`chunk_count` 稳定
3. `search_dictionary("2024年各区域销售额")` → 命中 `fact_sales`/`dim_region` 字典文档
4. `upsert_api_dictionary`（protocol=http 样例）→ indexed → 可检索；再传 protocol=websocket 样例 → 渲染含「长连接」分节与 message 归属
5. 错误路径：错误密码 → 401 话术；停 ragent-py → 不可达话术；无权账号建 KB → 权限话术
6. `psql -c "SELECT col_description('fact_sales'::regclass, 1)"` 有值（COMMENT 落地）
7. ReportAgent 闭环：提问含未收录字段 → 卡片出现 `field_meaning:*` assumption → 确认 → confirm 放行出报表；未确认时 confirm 被 gate 拦

## Explicitly NOT doing

- **不做接口/API 数据源取数报表**——v1 功能 1 = 字典查询 + 澄清；报表数据仍来自 PG 星型模型
- 不把 ragent 当 ReportAgent 的通用 RAG——仅字典 KB
- 不动 `mcp_schema_server` / `data_tools.py` 的硬编码描述——两份既有冗余记为 follow-up（长期应收敛到 `COMMENT ON`），本轮不越界
- 不做 DDL 变更自动侦测同步——`ingest_table_schemas` 手工/工具触发
- 不新增 RBAC 权限项、不新增 RequirementFieldKey、不加 RequirementCard 字段、不改前端
- 不做流式协议行为建模（帧时序、心跳、重连）——字典只记字段类型与含义
- 不做 chunk 级字典可视化/全文预览 UI
- ReportAgent 不通过 MCP 子进程检索字典——httpx 直连（MCP 服务留给 Claude Code 等外部客户端）
