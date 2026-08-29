# MCP Contract（RAG/MCP 边界与 Tool 契约）

> 状态: 冻结（P5，2026-08-29）— 目标契约。P2 已落地 MCP 通道与 failure 语义，P5 收口 14 字段与 PHASE2_MCP_ONLY ON。
> 上游: [2026-08-25-refactor-master-freeze.md](../plans/2026-08-25-refactor-master-freeze.md) §二/§八/§十八 P5 + [2026-08-26-p2-rag-mcp-boundary.md](../plans/2026-08-26-p2-rag-mcp-boundary.md) + CLAUDE.md §七。

## 一、系统边界（冻结）

```text
ReportAgent (Application/Runtime)
    │  唯一正路：mcp_client.call_tool(name, args) → stdio MCP
    ▼
ragent-py mcp_server (Retrieval Runtime, 已有子进程)
    ├── search_dictionary   ← schema/字典检索统一入口
    └── search_faq          ← FAQ 检索
```

- ReportAgent 不得重新实现 Embedding / Chunking / Vector Search / Reranking / RAG Indexing，只经 `MCP Client → RAG MCP Server` 使用（Forbidden Patterns 第 1/8 条）。
- `mcp_schema_server/` 为本仓历史资产，无 backend 消费者；P5 判定保留但不在正路。
- 工具层以外禁止 `import app.tools.rag_schema / interface_dict_tools / mcp_client / mcp_errors` 与 `D:/PyProject/ragent-py` 硬编码（`test_mcp_boundary_freeze` 钉住）。

## 二、Tool 白名单与禁入清单

Agent 可见工具（12 个，`test_mcp_tool_allowlist_freeze` 双向断言）：

| 域 | 工具 | source | 风险 |
|---|---|---|---|
| data | search_tables | mcp | low |
| data | get_table_ddl | mcp | low |
| data | list_tables | local | low |
| data | search_interface_dictionary | mcp | low |
| data | search_faq | mcp | low |
| sql | validate_sql | local | medium |
| sql | execute_sql | local | high |
| report | chart_advisor | local | medium |
| report | insight_analyst | local | low |
| report | trend_analysis | local | low |
| report | group_compare | local | low |
| report | detect_anomaly | local | low |

禁入：`embedding / vector_search / rerank / chunk / query_pgvector / ingest / upsert / list_docs / kb_manage` 子串（违反即红）。

## 三、Tool Metadata 14 字段（P5 收口）

每个 Tool 必须完整填 14 字段（`registry.ToolMetadata` + `registry.validate()` 钉住）：

`name / purpose / when_to_use / when_not_to_use / input_schema / output_schema / preconditions / postconditions / failure_policy / side_effects / examples / risk_level / permission / source`

- `permission` 与 `permission_required` 双写同步（alias 兼容）。
- `risk_level ∈ {low,medium,high}`，`source ∈ {local,mcp}`。
- Description 是 Agent Contract：必须可回答「什么时候调/什么时候不调/调用前要什么/调用后得什么」（四问关键词 `什么时候调/什么时候不调/调用前/调用后` 机器校验，`test_tool_contract_14_fields`）。

## 四、I/O Schema（稳定 vs 内部）

| 方向 | 字段 | 稳定性 |
|---|---|---|
| request | `query: str` / `top_k: int` / `kb_ids`（由 client 按 DICT_KB_NAME 解析，Agent 不传） | Agent 可依赖 |
| response | `items[]: {text: str, score: float, title?: str, section_path?: str}` 规范化后 → 工具层 `matches` | Agent 可依赖 |
| response 内部 | `chunk_id / document_id / embedding / rerank_score / kb_id` | boundary strip，不透出（`_strip_internal_fields` + `test_mcp_contract_schema` 快照） |

`_validate_matches_contract` 校验 `matches[].text:str + score:numeric`，缺字段 → `MCP_INVALID_RESPONSE`。

## 五、Failure 五分类（ErrorEnvelope 前身）

| 情形 | 行为 |
|---|---|
| `MCP_TIMEOUT` | 重试预算内 retry（固定 2 次，宪法 §11）；仍败 → 显式上抛，不伪装空结果 |
| `MCP_UNAVAILABLE` | 默认显式上抛（`{"error": "MCP_UNAVAILABLE: ..."}`），flag ON 时不走 fallback；显式 OFF 时测试矩阵可走 fallback |
| `MCP_INVALID_RESPONSE` | 不 retry 不 fallback → 显式上抛 |
| `EMPTY_RESULT` | 合法 `[]`，与 unavailable 严格区分 |
| quality insufficient | P14 Evaluation 范畴，本契约不做质量判断 |

`PHASE2_MCP_ONLY` 默认 ON（`mcp_client._resolve_phase2_flag` 返回 True）；`REPORTAGENT_E2E=1` 仍覆盖；flag ON 时 fallback 分支跳过直接上抛，显式 `PHASE2_MCP_ONLY=false` 时 fallback 可达（仅 `test_mcp_failure_matrix` 矩阵测试用，保留 flag-gated 而非硬删）。

## 六、RAG vs ReportAgent 职责划分

```text
RAG (ragent-py)：ingestion / chunking / embedding / retrieval / reranking / evidence retrieval
ReportAgent：requirement understanding / schema reasoning / SQL generation / SQL repair / report synthesis / orchestration
```

一句话：RAG 是独立知识服务，ReportAgent 只消费 retrieval capability，不把 RAG 当 Python library 嵌进来。

## 七、list_tables 豁免（P5 单列）

`list_tables` 无 MCP 等价工具，正路为 `rag_schema._list_dict_docs` HTTP 直连（`GET /api/v1/documents`），故 `source=local`。此为 P5 唯一豁免；trace/审计中 `source=local` 为真，不谎报 `mcp`。

## 八、现状映射（截至 P5）

| 契约要素 | 现状 |
|---|---|
| MCP 通道 | `mcp_client.RagMCPClient` 单例 + `search_dictionary/search_faq` MCP-first；P5 默认 MCP-only（PHASE2_MCP_ONLY ON 时不走 fallback） |
| 14 字段 | `registry.ToolMetadata` 14 字段 + `validate()` + 四问 description 全绿 |
| Failure 语义 | `MCPBoundaryError` 三码 + retry 2 + 显式 error JSON；`test_mcp_failure_matrix` P5 矩阵全绿 |
| 边界断言 | `test_mcp_boundary_freeze`（import 禁）+ `test_mcp_tool_allowlist_freeze`（白名单/source/禁词/用于）+ `test_mcp_contract_schema`（稳定/内部字段）全绿 |
| Fallback | flag-gated 保留但默认不走（`rag_schema._retrieve_dict` / `interface_dict_tools.search_interface_dictionary` / `faq_tools.search_faq` 均 flag ON 时直接上抛，显式 OFF 时可达供测试） |
