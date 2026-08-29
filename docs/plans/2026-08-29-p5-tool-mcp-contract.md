# P5 实施：Tool & MCP Contract 收敛（14 字段 + Registry + PHASE2_MCP_ONLY ON）

> 状态: 已完成
> 上游: [2026-08-25-refactor-master-freeze.md](2026-08-25-refactor-master-freeze.md) §二/§七/§十八 P5 验收 + [2026-08-26-p2-rag-mcp-boundary.md](2026-08-26-p2-rag-mcp-boundary.md) 残留 Task 4/5 + CLAUDE.md §七/§十五
> 协作: P5+P6 分两 plan 顺序（用户选 B），本 plan 仅 P5；P6 另开 `2026-08-29-p6-llm-adapter.md`

## Context（为什么做）

### 宪法要求（CLAUDE.md §七 Tool & MCP Contract）
- Tool Metadata 统一面 14 字段：`name / purpose / when_to_use / when_not_to_use / input_schema / output_schema / preconditions / postconditions / failure_policy / side_effects / examples / risk_level / permission / source`。Tool Description 是 Agent Contract——必须回答「什么时候调/什么时候不调/调用前要什么/调用后得什么」。
- MCP 失败不许默默返回空数组伪装"没结果"——必须显式 unavailable/timeout。
- 现状（CLAUDE.md §七 现状行）：工具已有 description（最小面）；统一 Metadata 与 Registry 收敛 P5；迁移期 `PHASE2_MCP_ONLY` flag + contract 一致的 local fallback 允许存在，**Phase 5 起停止本地 fallback**。

### 现状盘点（2026-08-29 verify 后校正）
| 项 | 实际 | 待做 |
|---|---|---|
| ToolMetadata | 9 字段：`name/description/capability/agent_type/source/risk_level/permission_required/input_schema/output_schema`；`description` 为中文五要素长文本（用途/输入/输出/用于/不要用来） | 补 8 字段：`purpose/when_to_use/when_not_to_use/preconditions/postconditions/failure_policy/side_effects/examples`；`permission` 与 `permission_required` 命名收敛（保留后者+alias）；`description` 保留但拆四问可校验 |
| Registry | `registry.register` 硬编码 12 工具；`all_tools/list_by_capability/list_by_agent/get_metadata` 已有；无 `validate()` | 加 `validate()` + 14 字段必填校验 + Registry 收敛 |
| MCP fallback | `mcp_client.py:68 _resolve_phase2_flag` 已落地（E2E>显式>APP_ENV）；`rag_schema.py:126 _retrieve_dict` + `interface_dict_tools.py:208` 仍 flag-gated HTTP fallback | P5 flip ON：fallback 删除（`list_tables` 例外，见 NOT doing） |
| P2 残留 | Task 4 `docs/architecture/mcp-contract.md` 未建；Task 5 README 登记未做 | 本 plan 顺手收口 |
| 调用点 | 12 工具全注册；description 均含「用于：」 | 14 字段补齐后 description 四问可机器校验 |

### 与 P6 边界
P5 只做 Tool/MCP 契约与 Registry；不碰 `app/llm.py` / `llm_resilience.py`（P6 收敛为 `app/llm/` Adapter）。

## Design（做什么、模块怎么拼）

### D1 ToolMetadata 14 字段面
`backend/app/tools/registry.py` 的 `ToolMetadata(BaseModel)` 扩展为 14 字段契约面，同时保持向后兼容：

- 新增 8 字段：`purpose: str`、`when_to_use: str`、`when_not_to_use: str`、`preconditions: list[str]`、`postconditions: list[str]`、`failure_policy: str`、`side_effects: str`、`examples: list[dict]`；均为必填（空字符串/空列表显式填，不允许缺）。
- `permission` alias：`permission_required: list[str]` 保留，新增 `permission: list[str]` property alias 双向同步；14 字段表填 `permission`，实现兼容旧 `permission_required`。
- `description` 保留：作为 Agent Contract 长文本（四问），由 4 个新字段（`purpose/when_to_use/when_not_to_use/postconditions`）推导校验，不删除。
- `capability/agent_type` 保留：不在 14 字段内但为内部路由所需，视为扩展字段。
- `validate()`：校验 14 字段非空 + `risk_level ∈ {low,medium,high}` + `source ∈ {local,mcp}` + `input_schema/output_schema` 为 dict。

### D2 Tool Description 是 Agent Contract
`backend/app/tools/__init__.py` 的 12 工具 `description` 重写为四问可校验面（保持中文五要素风格）：
每条 description 必含「什么时候调/什么时候不调/调用前要什么/调用后得什么」四段（关键词：`什么时候调`/`什么时候不调`/`调用前`/`调用后`），由 `test_tool_contract_14_fields.py` 机器校验。

### D3 Tool Registry 收敛
`registry.py` 新增：
- `validate()`：遍历 `_tools` 校验 14 字段完整性 + 取值合法性
- `all_tools()` 已有，`list_by_capability/list_by_agent` 保留
- `register()` 时同步校验（fail-fast）

### D4 PHASE2_MCP_ONLY ON + flag-gated fallback（默认不走）
- `backend/app/tools/mcp_client.py`：`_resolve_phase2_flag()` 默认改 `True`（P5 起默认不走 fallback），`REPORTAGENT_E2E=1` 仍覆盖；`_fallback_allowed()` 语义不变（`not flag`）。
- `backend/app/tools/rag_schema.py`：`_retrieve_dict` 保留 flag-gated fallback（`MCP_UNAVAILABLE + _fallback_allowed` 时走 `_retrieve_dict_http`），但默认 ON 时直接上抛；`_retrieve_dict_http` 保留，`list_tables` 的 `_list_dict_docs` 仍为正路（无 MCP 等价）。
- `backend/app/tools/interface_dict_tools.py`：`search_interface_dictionary` 同样保留 flag-gated fallback；`_search_dict_http` 保留；默认 ON 时不走。
- `backend/app/tools/faq_tools.py`：同上保留 flag-gated 本地 fallback，默认 ON 时不走。
- 实现选择：flag-gated + 默认 ON 而非硬删，理由：保留 `test_mcp_failure_matrix` P5 矩阵的 flag 注入验证；三处 fallback 代码测试显式 OFF 时可达，生产默认跳过。
- `list_tables` 例外：仍 `source=local` 走 `_list_dict_docs` HTTP（无 MCP 等价，P2 review 决议），在 `mcp-contract.md` 单列豁免。

### D5 P2 残留收口
- 新建 `docs/architecture/mcp-contract.md`（第六份架构文档，双段结构：`> 状态: 冻结（P5，2026-08-29）` + 契约正文 + 现状映射表）：系统边界图、工具白名单 12、I/O schema 稳定/内部字段表、failure 五分类、RAG vs ReportAgent 职责、`PHASE2_MCP_ONLY` 收口语义、list_tables 豁免。
- 更新 `docs/plans/README.md`：P2 进行中 Task 4/5 完成态，P5 登记进行中。

## Files to change

| 路径 | 变更模式 |
|---|---|
| `backend/app/tools/registry.py` | 扩展 ToolMetadata 14 字段 + permission alias + validate() |
| `backend/app/tools/__init__.py` | 12 工具补 8 字段 + description 四问重写 |
| `backend/app/tools/mcp_client.py` | `_resolve_phase2_flag` 默认 ON |
| `backend/app/tools/rag_schema.py` | 保留 flag-gated fallback 但默认 ON 时直接上抛 |
| `backend/app/tools/interface_dict_tools.py` | 同上保留 flag-gated fallback |
| `backend/app/tools/faq_tools.py` | 同上保留 flag-gated 本地 fallback |
| `backend/tests/contracts/test_tool_contract_14_fields.py` | 新建：14 字段完整性 + 四问 + 取值合法性 |
| `backend/tests/contracts/test_mcp_tool_allowlist_freeze.py` | 同步更新（如需）：source 约束保持，list_tables 豁免重申 |
| `docs/architecture/mcp-contract.md` | 新建：第六份架构文档 |
| `docs/plans/README.md` | 登记 P5 进行中，P2 残留完成 |

## Reused existing utilities（复用优先）

| 复用对象 | 路径 | 方式 |
|---|---|---|
| `ToolRegistry` 现有 `all_tools/list_by_capability/get_metadata` | `backend/app/tools/registry.py` | 扩展不重写 |
| `register_all_tools` 幂等注册 | `backend/app/tools/__init__.py` | 补字段不改注册拓扑 |
| `MCPBoundaryError/MCPErrorCode/_validate_matches_contract` | `backend/app/tools/mcp_errors.py` / `mcp_client.py` | fallback 删除后错误分类仍用 |
| `RagMCPClient` 单例 + `_fallback_allowed` | `backend/app/tools/mcp_client.py` | 仅改 flag 默认 |
| `_parse_table_doc/_build_ddl/_is_analytical_table` | `backend/app/tools/rag_schema.py` | 纯解析保留 |
| `test_mcp_tool_allowlist_freeze` 快照手法 | `backend/tests/contracts/` | 新 contract test 照搬 |
| `test_context_package_facade` DeprecationWarning 模式 | `backend/tests/` | 不相关，仅复用测试风格 |

## Verification（端到端验证）

```bash
# 单测：14 字段契约
cd backend && pytest tests/contracts/test_tool_contract_14_fields.py -v

# 存量契约不破
pytest tests/contracts/test_mcp_tool_allowlist_freeze.py tests/contracts/test_mcp_contract_schema.py tests/contracts/test_mcp_boundary_freeze.py -v

# MCP fallback 删除后：flag ON 时 UNAVAILABLE 不走 fallback
pytest tests/smoke/test_mcp_failure_matrix.py -v
pytest tests/contracts/test_mcp_client.py -v

# 全量离线（CLAUDE.md §15 红线：681+ 不回退）
cd backend && pytest -q

# 前端不破
cd frontend && npm run test:run
```

冒烟矩阵 4 项：
1. 14 字段完整性：12 工具 × 14 字段全非空
2. 四问可校验：description 含 什么时候调/不调/调用前/调用后
3. PHASE2_MCP_ONLY ON：mock UNAVAILABLE → 不触发 HTTP fallback，直接抛 MCPBoundaryError
4. list_tables 仍 local 可用：source=local 且返回非空

## Explicitly NOT doing

| 不做 | 理由 |
|---|---|
| 改 `app/llm.py` / `llm_resilience.py` → `app/llm/` Adapter | P6 范畴 |
| 改 ContextRuntime/assembler/SelectiveRecallPolicy | P4c 已 PASS |
| 删 `build_session_context` 兼容路径 | facade 保留 |
| 动 `legacy/agents/parent_graph.py` | CLAUDE §13 P15 |
| 重写 `mcp_client` 传输层 / RAG 项目代码 | P2 已落地，本 plan 仅改 flag 默认 |
| 让 Tool 没 description / Agent 直调 provider SDK | Forbidden Patterns |
| 新建 utils2/managers2/helpers/common2 | 禁 generic 文件夹 |
| 删 `list_tables` 的 `_list_dict_docs` HTTP | 无 MCP 等价，P5 豁免（mcp-contract.md 单列） |
| 改 `capability/agent_type` 语义 | 内部路由字段，保留 |

---

## TDD Tasks（bite-sized）

### T1 ToolMetadata 14 字段 + registry 收敛
- [ ] Step 1 写失败钉子 `test_tool_contract_14_fields.py`：12 工具 × 14 字段非空 + risk_level/source 取值 + validate() 绿
- [ ] Step 2 扩展 `registry.ToolMetadata` 14 字段 + permission alias + validate()
- [ ] Step 3 跑 `pytest test_tool_contract_14_fields -v` 红 → 绿

### T2 Tool Description 四问重写
- [ ] Step 1 加四问断言：description 含 什么时候调/不调/调用前/调用后
- [ ] Step 2 重写 `__init__.py` 12 description
- [ ] Step 3 跑 allowlist + 14 字段全绿

### T3 PHASE2_MCP_ONLY ON + flag-gated fallback（默认不走）
- [ ] Step 1 写 flag ON 时 UNAVAILABLE 不走 fallback 的矩阵钉子
- [ ] Step 2 改 `mcp_client._resolve_phase2_flag` 默认 True；`rag_schema/_retrieve_dict` / `interface_dict_tools` / `faq_tools` 保留 flag-gated fallback（默认 ON 时跳过，显式 OFF 时可达供测试）
- [ ] Step 3 跑 `test_mcp_client`/`test_mcp_failure_matrix` 全绿

### T4 mcp-contract.md + P2 残留收口
- [ ] Step 1 新建 `docs/architecture/mcp-contract.md`
- [ ] Step 2 更新 `docs/plans/README.md` 索引
- [ ] Step 3 全量回归 `pytest -q` + `npm run test:run`
