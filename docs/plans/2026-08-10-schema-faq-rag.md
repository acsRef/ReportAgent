# 2026-08-10 Schema RAG Phase 1：FAQ 知识库 + search_faq + SQL Agent 融合

> 状态: 已完成（commit `1f2e9c3`）

## Context

用户转投的 GitHub issue「[架构] Schema RAG 集成方案」Phase 1 FAQ RAG 提出三点：

1. 创建 FAQ 知识库（常见问题 + SQL 示例）。
2. MCP Server 新增 `search_faq()` 工具（向量搜索）。
3. SQL Agent 集成 FAQ 背景（prompt 融合 FAQ + 表定义），目标 SQL 准确率 70% → 80%。

现状（查证属实）：MCP Schema Server（`mcp_schema_server/registry.py`）和 backend 本地 fallback（`app/tools/data_tools.py`）都是**无状态纯内存的关键词匹配实现**，不连 PostgreSQL（AGENTS.md Known Quirks 明确这两者是独立实现）。SQL Agent 的 `_generate_sql`（`app/agent/sql_graph.py:384`）用 `schema_text` + `_FK_CHAIN_HINTS` + `_SQL_GENERATION_RULES` 拼 prompt，但**没有历史成功案例/业务口径参考**——业务术语（如「毛利率 = profit/total_amount」「出勤率 = 正常记录/总记录」「退货率 = 退货金额/销售额」）全靠 LLM 从字段名猜，准确率有限。

## Design

### 关键决策：FAQ 数据源用 JSON，不用 PG 表（对 Copilot 提案的偏差）

Copilot 提议 `backend/scripts/schema_faq.sql`（PG 表 + 向量搜索）。但：

- MCP registry 与 backend 本地 data_tools 都不连 PG；`search_faq` 若读 PG 表，会让**无状态 MCP server 新增数据库连接依赖**，与现有架构违背。
- 向量搜索是「把文本 embed 进 pgvector」的能力，Phase 1 用**关键词评分**即可达到 RAG 首版目的；向量化是自然的 Phase 2 演进（把同一份 JSON embed 进 pgvector），不阻塞。

故 Phase 1 定为：**FAQ 知识库 = 独立 JSON 文件 `backend/scripts/schema_faq.json`**（代码数据形式的单一数据源），约 20 条高价值条目（覆盖 4 张事实表常见的分析模式 + 易错口径），每条含 `question / keywords / tables / sql / note`。`note` 字段承载「业务口径解释」（RAG 的核心价值——不只给例子，还说明为什么这么算）。

**Phase 2（本次明确不做）**：用 embedding 服务把同一份 JSON 的 `question` embed 进 PG `pgvector` 表做语义向量检索。

### 1. `backend/app/tools/faq_tools.py`（新建）

- `_FAQ_PATH`：基于 `Path(__file__)` 定位 `backend/scripts/schema_faq.json`（与 cwd 无关）。
- `_FAQ_ENTRIES`：惰性加载 JSON（模块级缓存；文件缺失返回 `[]` 不报错——降级为无 FAQ，不改主流程行为）。
- `search_faq(query, top_k=3) -> list[dict]`：关键词评分（复刻 `search_tables` 的 scoring 思路：question 命中 ×2、keywords 命中 ×3、tables 命中 ×1），返回 top-K 条 `{question, sql, note, tables, score}`。空/无命中返回 `[]`。

### 2. SQL Agent 融合（`app/agent/sql_graph.py` `_generate_sql`）

- 顶部 import `from app.tools.faq_tools import search_faq`。
- `_generate_sql` 里用 `state["user_query"]` 调 `search_faq(top_k=3)`，命中则拼一个 `faq_block` 追加进 prompt（位置：`_FK_CHAIN_HINTS` 之后、`规则:` 之前）：
  - 每条例出「问题 / 示例 SQL / 要点(note)」。
  - 加一句防御措辞：**示例 SQL 仅作参考，表名/字段名必须以上面「可用表结构」为准**——防止例子里名称与真实 schema 冲突时误导。
- 放 try/except：`search_faq` 出错不影响 SQL 生成主流程（降级为无 FAQ）。

### 3. MCP Server 暴露 `search_faq` 工具（parity）

- `mcp_schema_server/registry.py`：加 `_FAQ_PATH` + `_FAQ_ENTRIES`（加载同一份 JSON）+ `SchemaRegistry.search_faq(query, top_k)`（同 scoring）。
- `mcp_schema_server/server.py`：`handle_list_tools` 注册 `search_faq` 工具（描述写清用途/输入/输出/不要用来/失败处理），`handle_call_tool` 路由到 `registry.search_faq`。

scoring 在两处各写一份（约 20 行）——与现有 `_TABLES` 在 MCP/backend 双份的事实类同（Known Quirks 已接受），FAQ 数据本体（JSON）是单一来源、不重复。

## Files to change

- `backend/scripts/schema_faq.json`（新建）：FAQ 知识库，约 20 条。
- `backend/app/tools/faq_tools.py`（新建）：`search_faq` + JSON 加载。
- `backend/app/agent/sql_graph.py`：`_generate_sql` 注入 `faq_block`。
- `mcp_schema_server/registry.py`：`search_faq` 方法。
- `mcp_schema_server/server.py`：`search_faq` 工具注册 + 路由。
- `docs/plans/2026-08-10-schema-faq-rag.md`（本文件）。

## Reused existing utilities

- `backend/app/tools/data_tools.py` 的 `search_tables` scoring 思路：`search_faq` 复刻其关键词评分权重，不另起炉灶。
- `app/agent/sql_graph.py` 既有的 `_generate_sql` prompt 拼装与 `state["user_query"]`：FAQ 块是其中一段增量，不重构 prompt 结构。
- `mcp_schema_server/server.py` 既有的 tool 注册模板（`Tool(...)` + `handle_call_tool` 路由）：`search_faq` 照同一模式加。
- AGENTS.md「数据字典 RAG」`interface_dict_tools` 与 `schema_faq` 是并行通道，互相独立。

## Verification

```bash
cd backend && pytest tests/smoke/test_schema_faq.py -v
```

新增 `backend/tests/smoke/test_schema_faq.py`（离线）：

1. `faq_tools.search_faq("退货率")` → 命中退货相关 FAQ（`score > 0`，含 `sql` + `note`）。
2. `search_faq("不存在的词汇xyz")` → `[]`（无强噪命中）。
3. `search_faq("各区域销售额排名", top_k=3)` → 命中销售排名 FAQ，且 `len <= top_k`。
4. 文件缺失降级：monkeypatch `_FAQ_PATH` 指向不存在路径，`search_faq` 返回 `[]` 不抛。
5. MCP parity：临时把 repo root 挂上 `sys.path`，`from mcp_schema_server.registry import registry; registry.search_faq("退货率")` 非空。
6. **prompt 注入**（`tests/graphs/test_sql_generation.py` 加用例，复用 `_capture_prompt`）：monkeypatch `graph_mod.search_faq` 返回固定 FAQ，断言传给 `call_llm` 的 prompt 含示例 SQL 与「仅作参考」防御措辞。

回归：

```bash
cd backend && pytest -q
```

## Explicitly NOT doing

- **不做** PG 表 FAQ + 向量搜索——MCP/backend 本地均无 PG 依赖，Phase 1 关键词评分足够；向量化留 Phase 2（embed 进 pgvector）。
- **不做** 50+ 条灌水——本轮交付约 20 条**经核实的正确 SQL**（覆盖 4 事实表 + 易错口径），宁缺毋滥；扩充是长期饲养。
- **不做** Metric RAG / Report RAG（Phase 2/3）——本期只做 Phase 1 FAQ。
- **不改** requirement_analysis graph（只挂 schema 工具）——FAQ 只在 confirmed 执行的 `_generate_sql` 注入，意图分析阶段不加。

## 修订（2026-08-10 落地后，按用户反馈）

落地后用户澄清诉求：「用 MCP，接入 RAG 里来查询」——即 FAQ 检索要走**注册工具通道**，而非藏在 `_generate_sql` 里的本地旁路纯函数。据此修订：

- `faq_tools.search_faq` 改为 `@tool`（langchain），`invoke` 返回 `{"matches": [...]}` JSON——镜像 `search_interface_dictionary` 的字典 RAG 先例。
- `backend/app/tools/__init__.py` `register_all_tools()` 注册 `search_faq`（capability `faq_search`, agent_type `data`），成为 agent 一等 schema 工具（与 `search_tables`/`get_table_ddl`/`list_tables`/`search_interface_dictionary` 并列）。
- `_generate_sql` 经 `search_faq.invoke({"query": …, "top_k": 3})` 检索并解析注入，不再直接调纯函数。
- 纯 scoring 抽成 `_search_faq_rows` 供单测与复用。

「用 MCP」在此代码库的落地含义：后端无真正 MCP 客户端连接，agent 的 schema 工具集就是 `app/tools/registry` 注册的这批工具（AGENTS.md 将其视为 MCP schema 工具）；`register_all_tools()` + agent 节点 `.invoke()` 即「走工具/RAG 通道检索」。MCP server 侧 `search_faq` 工具（`mcp_schema_server/`）保留为外部进程可调的一致面。