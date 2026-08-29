# P4c golden before/after

## 概览

- **before**: p3 分支 HEAD `efaeeb5`（P3/P4a/P4b 全部已落）
- **after**: p4c 分支（ContextRuntime 真正接入主图 + assembler 真实装 Filter/Budget）
- **测试矩阵**: 离线能跑的全跑，作为 P4c 行为不变的 proxy

## 现实约束

`pytest backend/tests/golden/` 计划路径**不存在**——P0 baseline 套件在 `evaluation/`（顶层 repo 目录）：
- `evaluation/baseline_cases.json`（20 例含行为期望）
- `evaluation/checker.py`（offline 校验）
- `evaluation/runner.py`（真实 API runner，要 backend :8100 + LLM key + PG）
- `evaluation/tests/test_schema.py`（40 schema 测试，offline）

真端到端 runner 在 P4c 环境**不可达**（缺 PG + LLM key + 真起 backend），按 CLAUDE.md §15 "e2e P12 前手动门" 留 P12 一起验证。本文档用**离线 proxy + 新增契约测试**作为 P4c 行为不变的证据。

## 离线 diff 结果

| 测试套件 | before (p3) | after (p4c) | 备注 |
|---|---|---|---|
| `evaluation/tests/test_schema.py` | 40/40 PASS | **40/40 PASS** | baseline cases schema 不破 |
| `tests/contracts/test_context_runtime_contract.py` | 通过 | **通过**（test 改名 + 期望更新反映 dedup 行为） | 5 步编排接口不变 |
| `tests/contracts/test_context_package_facade.py` | 通过 | **通过** | facade compat 仍绿 |
| `tests/contracts/test_structured_recall_contract.py` | 通过 | **通过** | RecallItem 字段不破 |
| `tests/contracts/test_memory_write_pipeline_contract.py` | 通过 | **通过** | selective + write pipeline 不破 |
| `tests/contracts/test_selective_recall_benefit.py` | N/A | **24/24 PASS**（新增） | Task 3 |
| `tests/contracts/test_context_assembler_real_filter_budget.py` | N/A | **6/6 PASS**（新增） | Task 4 |
| `tests/contracts/test_p4c_graph_caller_integration.py` | N/A | **8/8 PASS**（新增） | Task 1 |
| `tests/graphs/test_context_runtime_main_chain.py` | N/A | **3/3 PASS**（新增） | Task 2 |
| `tests/graphs/test_requirement_analysis_sqlgate.py` | 3/3 PASS | **3/3 PASS**（mock 加 assembled_context kwarg） | 既有 spy 不破 |

**新增契约层总和**: 41/41 PASS（24 + 6 + 8 + 3 = 41 个新钉子）

## P4c 接入后行为变化清单

| 字段 / 路径 | 行为变化 | 接受理由 |
|---|---|---|
| `_requirement_parse` 入口 | `build_session_context` → `ContextRuntime.build`；增加 memory recall 副作用 | Task 2 主链 smoke 测试覆盖；test 2 与 3 验证 recall_items 透传 |
| `_confirmed_sql_agent` 入口 | 同上；agent string 改为 `confirmed_execution_sql_agent`（符合 resolver prefix 规则） | test_context_runtime_main_chain 验证 agent_policy=execution |
| `parse_requirement` + `_call_llm_for_parse` | 新增 `assembled_context` kwarg（向后兼容）；prompt 注入优先用 assembled_context | spy_parse_requirement 已同步加 kwarg |
| `sql_graph._plan` + `_generate_sql` | state 取 `assembled_context` 优先，fallback `conversation_context` | Task 1 integration 钉子验证 |
| `ContextAssembler.assemble` | 加 dedup by (source, ref_id) + §七 kind 排序 + Token Budget 截断 | 见 test_assemble_preserves_items_with_unique_keys 修订 |

## 真端到端 golden before/after 留 P12 手动门

按 CLAUDE.md §15，P4c 后真 e2e / golden runner 应在 P12 (Playwright) 一起跑。需要：
1. PG + MCP schema server + 后端 :8100 + LLM key（MiniMax）
2. `REPORTAGENT_E2E=1 python -m pytest backend/tests/e2e/test_full_flow.py -s`
3. `python -m evaluation.runner --base-url http://127.0.0.1:8100` 跑 20 例 baseline

P4c 关键接入点（caller + assembler）已经在离线层级验证。**所有可观测的契约保护点**:

1. **ContextRuntime 5 步编排接口不变** — `tests/contracts/test_context_runtime_contract.py`（含 Step 5 修改后的 `assemble_preserves_items_with_unique_keys`）
2. **ContextBundle 公共字段不变** — 既有 contract 4 文件全绿
3. **facade `build_session_context` 兼容路径保留** — `tests/contracts/test_context_package_facade.py` 全绿
4. **SelectiveRecallPolicy 4 触发 + §三分流** — `tests/contracts/test_memory_write_pipeline_contract.py` + P4c Task 3 新增 24 钉
5. **graph caller 真接入** — `tests/contracts/test_p4c_graph_caller_integration.py` 8/8 + `tests/graphs/test_context_runtime_main_chain.py` 3/3

## 后续阶段 input — `remaining_token_budget` 主图 caller 真传

**P4c 第二轮 post-review (REQUEST CHANGES P1) 决议（诚实降级）**：
- Assembler `min(remaining, configured)` 接口与算法在 `assembler.py` + `runtime.py` 已实装
- 真实 graph caller (`requirement_analysis_graph._requirement_parse` + `confirmed_execution_graph._confirmed_sql_agent`) 当前**不传** `remaining_token_budget`（None → 4000 tokens configured-only 路径）
- **不做** fake pass：项目当前**没有** unified input context window / prompt budget accounting（CLAUDE.md §8 P5/P6 Unified LLM Migration 收敛点）
- 防护钉 `test_graph_caller_does_not_invent_remaining_budget` 拦截任何给 `remaining_token_budget` 传字面量（4000/8000 等）的潜在伪装
- 等 P5/P6 或后续 Context Budget 阶段补上——届时须先升 plan + 人工检查防护钉

**影响**：P4c F2 在 assembler 层完整闭合；graph caller 传值由后续阶段负责。offline 681 passed baseline 不破。

## 验收

- ✅ Task 5 Step 1-3（offline proxy + 新钉 + before vs after diff）完成
- ⏭ 真端到端 runner 留 P12 手动门（CLAUDE.md §15）
- ⏭ Step 6: commit 文档 + README 索引更新（Task 5 final step）
