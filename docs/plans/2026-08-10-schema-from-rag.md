# 2026-08-10 Schema 从 RAG 来：删硬编码 _TABLES，schema 工具改查 ragent-py 字典 KB

> 状态: 已完成（commit `e1de8f5`；含真实 E2E）

## Context

用户要求：删掉 `mcp_schema_server/registry.py` 与 `backend/app/tools/data_tools.py` 里硬编码的 `_TABLES`（两份 schema 目录），schema 发现工具（`search_tables` / `get_table_ddl` / `list_tables`）**只从 ragent-py 的字典知识库来**。用户确认：**全删、只从 rag**（ragent-py 挂了 schema 工具降级为空），**两份都改**。

现状（查证属实）：
- 两份硬编码 `_TABLES`（mcp_schema_server/registry.py:7，data_tools.py:17）是 schema 唯一来源，结构相同（10 张星型表：6 dim + 4 fact）。
- ReportAgent 实际用 `app/tools/data_tools.py`（`data_graph._search_schema` 调 `search_tables.invoke` → `_build_context` 用 `table_name/description/columns`）；MCP 面用 `registry.py`。
- ragent-py 字典 KB（`数据字典`, internal）**已有 33 个文档**（`ingest_table_schemas` 灌入），含每张表的结构文档，格式实测：`# 表 \`public.fact_sales\`` + 描述行 + `字段 <name> 类型 <type> 含义 <meaning> 枚举/FK` 逐行。
- 后端已有 `interface_dict_tools`（httpx + token 缓存 + `/api/v1/retrieve` 连字典 KB）可复用的检索模式。

## Design

两份硬编码各自换成「查 ragent-py 字典 KB + 解析」的实现；删除 `_TABLES`。解析器与检索逻辑在各包内各一份（延续双份部署模式，与既有 `_TABLES` 双份同构）。

### 1. 字典文档解析（两处各一 `_parse_table_doc`）

实测字典文档 chunk 文本格式：

```
# 表 `public.fact_sales`
销售记录事实表,每条记录代表一笔销售
## 字段
字段 sale_id 类型 integer 含义 销售记录主键 枚举/FK
字段 date_id 类型 integer 含义 销售日期 枚举/FK
...
```

`_parse_table_doc(text) -> dict | None`：
- 标题行 `# 表 \`<schema>.<table>\`` 取 `table_name`。
- 标题后到 `## 字段` 之间的行拼成 `description`。
- `字段 (\S+) 类型 (\S+) 含义 (.+)` 逐行解析 `columns: [{name, type}]`。
- 任一步失败返回 `None`（调用方跳过该 chunk，不视为致命）。

### 2. 检索（各包一 `_retrieve_dict / _list_dict_docs`）

- 后端：新 `app/tools/rag_schema.py`——httpx 连 ragent-py（复用 `interface_dict_tools` 的 token 缓存 + KB id 解析模式），`_retrieve_dict(query, top_k)` 调 `/api/v1/retrieve`（kb_ids pin 字典 KB），`_list_dict_docs()` 列出字典 KB 文档。
- MCP 面：`registry.py` 内同样实现（该包独立，不 import backend）。

### 3. 三个工具改造（data_tools.py + registry.py 各一份）

| 工具 | 新行为 |
|---|---|
| `search_tables(query, top_k)` | `_retrieve_dict(query, top_k*3)` → 过滤并 `_parse_table_doc` 每个命中 chunk → 返回 `[{table_name, description, columns, ddl(重建), score}]`；无命中/ragent-py 不可达 → `[]` |
| `get_table_ddl(table_name)` | 精确检索（query=表名 + 从 `_list_dict_docs` 找 `dict-table_*_<table>.md` 的文档 id → 取该文档 chunk）→ 解析 columns → `_build_ddl` 重建 CREATE TABLE；找不到 → `Table '<name>' not found` |
| `list_tables()` | `_list_dict_docs()` 过滤 `dict-table_` → 提取表名 + 列数 → 返回 `[{table_name, description, column_count}]` |

- `_build_ddl` 由 columns 重建（与既有 `registry._build_ddl` 同构）。
- 错误处理：ragent-py 不可达/未配置 → 各工具返回空数组/`not found`，**不抛**（SQL 生成降级，不崩）。

### 4. 重新灌数据（确保字典 KB 完整）

- 用 ragent-py `ingest_table_schemas` 重灌 10 张表结构到字典 KB（若已存在同名文档则幂等更新）。
- 验证 `search_tables('退货率')` 命中 `fact_returns`、`get_table_ddl('fact_sales')` 返回 12 列 DDL、`list_tables()` 返回 10 张表。

## Files to change

- `backend/app/tools/rag_schema.py`（新建）：`_parse_table_doc` + `_retrieve_dict` + `_list_dict_docs` + `search_tables_from_rag` / `get_table_ddl_from_rag` / `list_tables_from_rag`。
- `backend/app/tools/data_tools.py`：删 `_TABLES`，`search_tables`/`get_table_ddl`/`list_tables` 委托 rag_schema。
- `mcp_schema_server/registry.py`：删 `_TABLES`，三个方法内嵌同样的检索+解析（该包独立实现）。
- `docs/plans/2026-08-10-schema-from-rag.md`（本文件）。

## Reused existing utilities

- `backend/app/tools/interface_dict_tools.py`：token 缓存 + 401 重登 + KB 按名解析的 httpx 模式（`_login_token` / `_dict_kb_id`），`rag_schema` 复用同构逻辑。
- `registry.py::_build_ddl`：columns → DDL 重建。
- ragent-py `POST /api/v1/retrieve`（混合检索 + RRF）与 `GET /api/v1/documents`：字典 KB 检索/列文档。

## Verification

```bash
cd backend && pytest tests/smoke/test_rag_schema.py -v
cd backend && pytest -q
```

新增 `backend/tests/smoke/test_rag_schema.py`（离线，mock ragent-py HTTP）：

1. `_parse_table_doc`：真实格式 → 正确提取 table_name/description/columns；畸形文本 → None。
2. `search_tables`：mock `/retrieve` 返回 fact_sales 文档 → 返回带 columns 的 schema dict；无命中 → []。
3. `get_table_ddl`：mock 文档 id 解析 → 返回含 12 列的 DDL；找不到表 → `not found`。
4. `list_tables`：mock 文档列表 → 返回 10 张表（过滤 dict-table_）。
5. ragent-py 未配置/不可达 → 各工具空数组/not found，不抛。

真实冒烟（起 ragent-py + docker）：

1. `ingest_table_schemas` 重灌 → 10 表文档 indexed。
2. `search_tables('退货率')` → 命中 fact_returns；`get_table_ddl('fact_sales')` → 12 列；`list_tables()` → 10 张。
3. 停 ragent-py → 三个工具返回空/not found，SQL 生成不崩。

## 落地记录（2026-08-10，真实 E2E）

- 数据字典 KB 实测**已有 10 张分析表文档**（`ingest_table_schemas` 之前已灌），`list_tables` 返回正好 10 张 `dim_*`/`fact_*`，fact_sales 12 列齐全——**无需重新插入**。
- 真实检索：`search_tables('退货率')`→fact_returns/dim_region/fact_inventory；`search_tables('各区域销售额')`→fact_sales(12列)/dim_region；`get_table_ddl('fact_sales')`→12 列 DDL；`search('用户')`→dim_customer/dim_employee（无系统表）。
- **关键过滤**：两项目共享 `ragent` 库，字典 KB 混入 ragent-py 系统表（users/documents 等），`search_tables`/`get_table_ddl`/`list_tables` 一律过滤成 `dim_*`/`fact_*`（与 `check_sql_safety` 白名单一致），避免系统表污染 SQL 生成。
- 离线全量 351 passed；`mcp_schema_server/registry.py` 与 `backend/app/tools/rag_schema.py` 双份实现（解析逻辑同构，延续既有双份部署模式）。ragent-py 不可达 → 三工具返回空/not found，SQL 生成降级不崩。

## Explicitly NOT doing

- **不做** 保留本地 JSON/硬编码 fallback——用户确认全删、只从 rag（可用性依赖接受）。
- **不做** 把 schema 解析器做成跨包共享库——延续双份部署模式，避免 sys.path 耦合。
- **不做** 改 ragent-py 侧（字典文档格式/检索）——解析器适配现有文档格式。
- **不做** 向量化 schema 检索优化——先用既有 `/retrieve`，命中率不足再议。
- **不做** `get_table_ddl` 的精确文档全文拉取若不需要——先从检索 chunk 重建，够用即止。