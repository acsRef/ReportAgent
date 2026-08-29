# P3+P4a+P4b Cumulative Review & 修复清单

> **状态**: 进行中（review 已完成，修复待下一对话）
> **范围**: p3 分支 `8e146ed..367a582`（14 commits，P3 + P4a + P4b 三 Phase 累计）
> **基线**: `cd backend && pytest --ignore=tests/e2e -q` → **614 passed / 0 failed / 1 warning**（warning = facade DeprecationWarning by design）
> **关联 plan**: [P3](2026-08-27-p3-context-runtime.md) / [P4a](2026-08-27-p4a-conversation-memory-decouple.md) / [P4b](2026-08-27-p4b-memory-lifecycle-selective-recall.md)

## Context（为什么做）

### 原始诉求
按 CLAUDE.md §14 + 用户既定节奏「P3 → P4a → P4b 连续落地 → 第一次集中 Code Review → 修问题 → P5/P6」，P3+P4a+P4b 三 Phase 累计 14 commits 必须经过 cumulative review 后才能进 P5/P6。本文件沉淀 review findings 与修复方案，供下一对话直接落地。

### Review 模式
5 维度并行 review（每维度独立 code-reviewer agent），全部「只审不改」：
- Forbidden Patterns（CLAUDE.md §2 十条）
- Architecture（依赖方向 / 模块边界 / 5 份架构契约 / Plan 偏离）
- Plan Discipline + Style（CLAUDE.md §14 + AGENTS.md）
- Correctness（bug / 状态机 / checkpoint adapter / lifecycle 转换）
- Tests（覆盖 / TDD / pollution / fixture / AST 钉子有效性）

### Review 结果一览
| Agent | VERDICT | 严重 | 中 | 低 |
|---|---|---|---|---|
| Forbidden Patterns | **APPROVE** | 0 | 0 | 0 |
| Architecture | APPROVE-WITH-FIXES | 0 | 2 | 2 |
| Plan Discipline + Style | APPROVE-WITH-FIXES | 3 | 4 | 3 |
| Correctness | APPROVE-WITH-FIXES | **2** | 3 | 5 |
| Tests | APPROVE-WITH-FIXES | **5** | 6 | 3 |

## Findings 总览（去重 14 项）

| # | 严重 | 标题 | 关联 agent | 单源 / 同源 |
|---|---|---|---|---|
| F1 | 严重 | UserMemory.save promote 不更新 `memory_type` | Correctness #1 | 单源 |
| F2 | 严重 | migrate_checkpoint partial v1 fixture（仅 `insight_text`）静默放过 | Correctness #2 | 单源（与 review #7 防误判钉子张力） |
| F3 | 严重 | `app/memory/{semantic,query}.py` thin views 未建 | Architecture #1 + Plan S1 | 同源 |
| F4 | 严重 | `test_semantic_entry_migration.py`（persistence）未建 | Plan S2 + Tests #1 | 同源 |
| F5 | 严重 | SelectiveRecallPolicy 4 触发条件负例缺失 | Tests #3 | 单源 |
| F6 | 严重 | SelectiveRecallPolicy REQUIREMENT agent query_exp 分流未测 | Tests #4 | 单源 |
| F7 | 严重 | supersede SQL 端到端未钉 | Tests #2 | 单源 |
| F8 | 中 | `state/__init__.py` 未 re-export 5 TypedDict + split/merge | Plan S3 | 单源 |
| F9 | 中 | `remember_conversation_facts` 缺 `session_id` 形参 | Correctness #3 | 单源 |
| F10 | 中 | `report_graph` 用 v1 `insight_text` | Correctness #4 | 单源 |
| F11 | 中 | `SelectiveRecallPolicy.decision.conversation` 是死字段 | Correctness #5 | 单源 |
| F12 | 中 | `recall_structured` `memory_semantic` source 路径未钉 | Tests #5 | 单源 |
| F13 | 中 | `app/memory/manager.py` → `infra.memory.{policy,mem0_extractor}` 边界 contract test 缺钉 | Architecture #2 | 单源 |
| F14 | 中 | `test_recall_still_returns_str` 用 `asyncio.run()` 嵌套 sync 函数 | Tests 中 #5 | 单源 |

### 已验证 ✓（不修，确认守住）
- **Forbidden Patterns 十条全过**：`test_legacy_import_freeze.py` 双测 PASS；RAG 直连 / graph 自拼 context / Agent 直调 Memory DB / provider SDK 直连 / Tool 无 description / 无限 retry / MCP 旁路 / utils2 generic 文件夹等全无
- **架构契约一致性**：依赖方向（context → memory → infra.memory）、State 五块所有权、Lifecycle 状态机、Confidence 固定规则、Selective Recall 四触发 + 三 agent 表、Backwards Compat（4 文件 6 处 `build_session_context` + 1 处 `format_context_block` 零修改）、Plan §Explicitly NOT doing 全部守住
- **Plan Discipline**：14 commit 全对应 plan Task；每个 docs commit 含 `+ plan: <slug>` 锚；Drive-by edits = 0；README/CLAUDE.md 已同步
- **Tests**：614 passed / 0 failed；TDD paired commits；AST-based 钉子覆盖 TYPE_CHECKING；cross-test 隔离全用 `with` 块 / fixture，无 sys.modules 操纵；E2E 覆盖符合 P12 纪律（未引入新 e2e）
- **AGENTS.md Style**：`from __future__ import annotations` 首行；imports 分组；公开 API 类型注解齐；naming 一致；error handling 全 `except Exception as exc: logger.warning(...)` + 选择性 raise；async/sync 分工正确；DB $1 参数绑定贯穿；无 wildcard import；行宽 < 120；docstring 普遍存在

## 修复方案

### 必修（红框，5 项）

#### F1. UserMemory.save promote 不更新 `memory_type`
- **位置**：`backend/app/infra/memory/user_memory.py:69-81`
- **失败场景**：
  1. 用户 LLM-inferred "I prefer bar charts" → `remember_inferred_facts` 写 `memory_type='insight', status='candidate', confidence='low'`
  2. 用户 explicit "以后都用柱状图显示" → `supersede_stable_preference` 谓词 `memory_type='stable_preference'` 不匹配 → 0 rows superseded → `remember_preference(memory_type='stable_preference', status='active', confidence='high')`
  3. `UserMemory.save` 命中既存行 → `promote=True` → UPDATE **不**改 `memory_type`
  4. 行变 `status='active', confidence='high', memory_type='insight'`
  5. `get_user_preferences()` 过滤 `memory_type IN ('stable_preference', 'temporary_preference')` → 该行被排除 → 用户显式偏好**永远不被偏好召回路径命中**
- **修复**（最小，2 处改动）：
  ```python
  + (", memory_type=$6, status=$3, confidence=$4, scope=$5, updated_at=NOW()" if promote else "")
  ...
  *( (existing["id"], status, confidence, scope, memory_type) if promote else (existing["id"],) )
  ```
- **同步测试**：在 `backend/tests/persistence/` 新建 `test_user_memory_promote.py` 钉「候选 insight 行被显式偏好覆盖后出现在 `get_user_preferences()`」。

#### F2. migrate_checkpoint partial v1 fixture 静默放过
- **位置**：`backend/app/state/checkpoint_adapter.py:39, 59-63`
- **失败场景**：checkpoint 仅 `insight_text='x'`（v1 字段名）+ 无 `schema_version` + 不含其他 v1 markers → `is_legacy_checkpoint` 返回 False（需要 `active_sub_agent` + `original_query` **都在**）→ fresh input path 注入 `schema_version=v2` 但 `insight_text` 仍存在 → 下游 graph 节点读 `state["insight"]`（v2 名）→ `None`
- **与 review #7 防误判钉子张力**：当前 `_LEGACY_MARKER_FIELDS = {"active_sub_agent", "original_query"}` 需要两都存在（避免误把 fresh input 当 legacy）。修 #2 需扩展 marker 含 `insight_text` 或放宽为「任一 v1 字段即视为 v1」。
- **建议 grill 决策**（先讨论再修）：
  - (a) 扩 `_LEGACY_MARKER_FIELDS` 含 `insight_text`，匹配规则改「任一即可」——简单但可能误判
  - (b) 把 `report_graph` 改用 v2 字段名 `insight`（根治）—— 见 F10
  - (c) audit 日志 + metrics 监测有多少 partial v1 走到 fresh-input——不改行为只观测
  - 推荐 (b)，与 F10 合并修

#### F3. `app/memory/{semantic,query}.py` thin views 未建
- **plan 锚**：`docs/plans/2026-08-27-p4b-memory-lifecycle-selective-recall.md §Files to change` 顶部「新增」节：
  > `backend/app/memory/semantic.py` / `query.py`（thin structured 视图）
- **现状**：`recall_structured` 结构化映射（`memory_manager.py:33-58` 的 kind/source/ref_id 构造）落在 infra 层，domain 层 (`app/memory/`) 没机会 enforce 它。
- **修复**（最轻量）：新建 `app/memory/semantic.py` + `app/memory/query.py`，各 5–10 行委托 `infra/memory/memory_manager.MemoryManager.recall_structured`，由 `ContextRuntime` 经 `app.memory.{semantic,query}.recall_structured` 调用，不直接 `from app.infra.memory.memory_manager import MemoryManager`。
- **同步修 F13**：见下方。

#### F4. `test_semantic_entry_migration.py`（persistence）未建
- **plan 锚**：P4b plan §Verification 显式要求 DB 端 e2e 钉子。
- **修复**：新建 `backend/tests/persistence/test_semantic_entry_migration.py`，用真 DB 跑：
  1. `DELETE FROM memory.semantic_entry WHERE user_id='migration_test_user'`
  2. 校验 columns（scope/confidence/status/session_id/expires_at/updated_at）存在 + 默认值正确
  3. 插入旧 shape 行（无 lifecycle 列的入参）→ 验证默认值落 `'active'`/`'user'`/`'medium'`
  4. 跑 migration SQL 第二次 → 不报错（idempotent）
  5. `SELECT 1 FROM pg_indexes WHERE indexname='idx_semantic_entry_status_user' → True`
- **注意**：persistence test 自动 skip without DATABASE_URL，需要 PG 起来才能跑——review baseline 已确认 PG 起来。

#### F5. SelectiveRecallPolicy 4 触发条件负例缺失
- **位置**：`backend/tests/contracts/test_memory_write_pipeline_contract.py:111-156`
- **plan 锚**：「四触发条件各 1 正 1 负；§三 agent 表 3 行」
- **修复**：补 4 负例 + 2 正例：
  ```python
  def test_no_history_ref_keeps_conversation_off():
      d = SelectiveRecallPolicy().decide(query="2024 销售排名", agent_policy=AgentContextPolicy.REQUIREMENT, session_state={})
      assert d.conversation is False
  def test_pref_keyword_triggers_semantic():
      d = SelectiveRecallPolicy().decide(query="帮我做个图表", agent_policy=AgentContextPolicy.REQUIREMENT, session_state={})
      assert d.semantic is True
  def test_no_biz_def_skips_semantic():
      d = SelectiveRecallPolicy().decide(query="查看用户列表", agent_policy=AgentContextPolicy.REQUIREMENT, session_state={})
      assert d.semantic is False
  def test_execution_no_data_verb_skips_query():
      d = SelectiveRecallPolicy().decide(query="休息一下", agent_policy=AgentContextPolicy.EXECUTION, session_state={})
      assert d.query is False
  ```

#### F6. SelectiveRecallPolicy REQUIREMENT agent query_exp 分流未测
- **位置**：同上文件
- **修复**：补 2 个测试：
  ```python
  def test_requirement_query_with_history_and_data_recalls_query():
      d = SelectiveRecallPolicy().decide(query="再按区域统计一下", agent_policy=AgentContextPolicy.REQUIREMENT, session_state={})
      assert d.query is True
      assert d.top_k_queries == 1
  def test_requirement_query_data_only_skips_query():
      d = SelectiveRecallPolicy().decide(query="统计各区域销售", agent_policy=AgentContextPolicy.REQUIREMENT, session_state={})
      assert d.query is False
      assert d.top_k_queries == 0
  ```

#### F7. supersede SQL 端到端未钉
- **位置**：`backend/app/infra/memory/user_memory.py:224-238` + `backend/tests/contracts/test_memory_write_pipeline_contract.py`
- **修复**：新建 `backend/tests/persistence/test_supersede_e2e.py`：
  1. 插 2 条 active stable_preference + 1 条 candidate
  2. 调 `remember_explicit_preference` 重申其中 1 条
  3. 验证 1 条 active + 1 条 superseded + candidate 不动

### 中修（橙框，6 项）

#### F8. `state/__init__.py` 未 re-export 5 TypedDict + split/merge
- **位置**：`backend/app/state/__init__.py`（当前 1 行 docstring）
- **修复**：补 `from app.state.blocks import (RequestState, RequirementState, ExecutionState, ReportState, RuntimeState, split_state, merge_state)`。同步测试 `from app.state import RequestState, ...` 可用。

#### F9. `remember_conversation_facts` 缺 `session_id` 形参
- **位置**：`backend/app/memory/manager.py:34-37`
- **plan 锚**：`docs/plans/2026-08-27-p4b-memory-lifecycle-selective-recall.md:90`
- **修复**：补 `*, session_id: str | None = None` 形参 + 透传给 `remember_inferred_facts(user_id, ..., session_id=session_id)`。

#### F10. `report_graph` 用 v1 `insight_text`
- **位置**：`backend/app/agent/report_graph.py:22, 162` + `backend/app/main.py:740` + `backend/app/agent/confirmed_execution_graph.py:339`
- **修复**：把 `ReportAgentState.insight_text` 改为 `insight`，`_build_output` 返回值 `insight` 改名（同时影响 main.py:740 / confirmed_execution_graph.py:339 的 `rs.get("insight_text")`）。P3 plan §2.4 review P1 #3 只列了 deterministic rename 方向，没强制 report_graph 跟进——是 plan 残留缺口。

#### F11. `SelectiveRecallPolicy.decision.conversation` 死字段
- **位置**：`backend/app/context/runtime.py:53-55`
- **plan 锚**：plan §二 "不召回：纯闲聊"
- **修复**：Step 3 加 `if decision.conversation:` 守卫；assembler 也对应处理空 conversation 情况。

#### F12. `recall_structured` `memory_semantic` source 路径未钉
- **位置**：`backend/app/infra/memory/memory_manager.py:50-51` + `backend/tests/contracts/test_structured_recall_contract.py`
- **修复**：增 `test_recall_structured_semantic_item_uses_memory_semantic_source`：fake `_Rank(22, "华东=region_east", "insight", 0.7)` → 验证 `items` 中 `kind="semantic"` 且 `source="memory_semantic"`，`ref_id=22`。

#### F13. `app/memory/manager.py` → `infra.memory.{policy,mem0_extractor}` 边界 contract test 缺钉
- **位置**：`backend/tests/contracts/test_memory_conversation_decouple.py`
- **修复**：补一条「`app/memory/**` 不 import `infra.memory.{user_memory, query_memory}`」（raw 原语禁面扩展到 memory 域）；或新建 `test_memory_domain_boundary_contract.py`，明确 domain 域只允许 `infra.memory.{memory_manager.MemoryManager, policy.MemoryPolicy, mem0_extractor}` 三处入口。

#### F14. `test_recall_still_returns_str` 用 `asyncio.run()` 嵌套 sync 函数
- **位置**：`backend/tests/contracts/test_memory_conversation_decouple.py:153-166`
- **修复**：把 `def test_recall_still_returns_str` 改 `@pytest.mark.asyncio async def`，内部去掉 `asyncio.run` 与嵌套 `_go`。

### 可延后（绿框，3 项，建议入 P4c 或 P5 启动前 sweep）
- **F15 (低)**: `assembler.py` 模块 docstring 过期（仍写「RecallItem 1:1 包装 string」）—— 改 docstring 对齐 P4b 现状
- **F16 (低)**: SelectiveRecallPolicy `_CHITCHAT/_HISTORY_REF/_BIZ_DEF/_DATA_VERB/_PREF_TASK` 5 关键词常量无字面钉子——在 write-pipeline contract 补 `test_selective_keywords_match_plan_four_triggers` 锁住 5 tuple 内容
- **F17 (低)**: `test_build_empty_recall_yields_empty_items` 缺 schema_version 断言——补 `assert bundle.schema_version == "v2"`

## Files to change（按修改模式 + 代表路径）

**真 bug fix**（必修）：
- `backend/app/infra/memory/user_memory.py`（F1，1 处 SQL）
- `backend/app/agent/report_graph.py` + `backend/app/main.py` + `backend/app/agent/confirmed_execution_graph.py`（F2+F10）
- `backend/app/memory/manager.py`（F9，1 形参）

**Plan deviation 补文件**（F3+F4+F7+F8）：
- 新建 `backend/app/memory/semantic.py`（F3，5–10 行 thin view）
- 新建 `backend/app/memory/query.py`（F3，5–10 行 thin view）
- 新建 `backend/tests/persistence/test_semantic_entry_migration.py`（F4，~50 行）
- 新建 `backend/tests/persistence/test_supersede_e2e.py`（F7，~50 行）
- 新建 `backend/tests/persistence/test_user_memory_promote.py`（F1 同步测试，~50 行）
- 修改 `backend/app/state/__init__.py`（F8，加 re-export）
- 修改 `backend/app/context/runtime.py`（F11，加守卫）

**Test 补钉**（F5+F6+F12+F13+F14）：
- 修改 `backend/tests/contracts/test_memory_write_pipeline_contract.py`（F5+F6 补 6 个测试）
- 修改 `backend/tests/contracts/test_structured_recall_contract.py`（F12 补 1 测试）
- 修改 `backend/tests/contracts/test_memory_conversation_decouple.py`（F13+F14）

**Plan amendment**（不修代码，只补 plan 状态注记）：
- 修改 `docs/plans/2026-08-27-p4b-memory-lifecycle-selective-recall.md` 头部「执行偏差」节，标注 F3+F4+F7 未在原 plan 中明确推迟到 P4c
- 修改 `docs/plans/2026-08-27-p3-context-runtime.md` 头部「执行偏差」节，标注 F8 re-export 推迟

## Reused existing utilities

- **Lifecycle enum + status**: `app/memory/lifecycle.py:MemoryStatus / MemoryScope / MemoryConfidence` (F1 + F4)
- **MemoryManager 网关**: `app/infra/memory/memory_manager.py:MemoryManager.recall_structured / remember_preference / supersede_stable_preference` (F1 + F7 + F13)
- **Contract test fixtures**: `test_memory_write_pipeline_contract.py:_SavedCall / mm_spy` (F5 + F6)
- **Persistence test fixture pattern**: 既有 `tests/persistence/test_checkpoint_compat.py` 的 `MemorySaver` + AsyncMock 风格 (F4 + F7)
- **AGENTS.md `__init__.py` 0-bytes 注释**: F8 re-export 维持 docstring 头部一行（与既有 `app/memory/__init__.py` 一致）

## Verification

### 端到端验证命令
```bash
# 1. 全套件回归（必须仍 614+ passed / 0 failed / 1 warning）
cd backend && pytest --ignore=tests/e2e -q

# 2. Contracts 层（钉子全过）
cd backend && pytest tests/contracts/ -v

# 3. Persistence 层（需 PG 起来，验证 F1/F4/F7 新 test）
cd backend && pytest tests/persistence/ -v

# 4. Forbidden Patterns 回归（必须仍 2 PASSED）
cd backend && pytest tests/contracts/test_legacy_import_freeze.py -v

# 5. Migration 钉子（F4 新 test）
cd backend && pytest tests/persistence/test_semantic_entry_migration.py -v

# 6. Promote 钉子（F1 新 test）
cd backend && pytest tests/persistence/test_user_memory_promote.py -v

# 7. Supersede 钉子（F7 新 test）
cd backend && pytest tests/persistence/test_supersede_e2e.py -v
```

### 冒烟矩阵（手工）
- [ ] `from app.state import RequestState, RequirementState, ExecutionState, ReportState, RuntimeState, split_state, merge_state` 不抛 ImportError（F8）
- [ ] `from app.memory.semantic import recall_structured` / `from app.memory.query import recall_structured` 不抛 ImportError（F3）
- [ ] 用户先说"I prefer bar charts"再 explicit 重申，"柱状图"出现在 `get_user_preferences()` 结果（F1）
- [ ] checkpoint 仅 `insight_text='x'` 走 migrate 后下游 `state["insight"]` 不为 None（F2）

## Explicitly NOT doing

- **不**改 `app/infra/memory/memory_manager.py` 的 recall_structured 内部实现（F3 只新增 thin view 委托，不动 infra）
- **不**重写 SelectiveRecallPolicy 5 关键词元组（F16 仅补字面钉子，不调整规则）
- **不**改 Plan §设计 / §Reused utilities / §Verification 内容（仅补「执行偏差」节）
- **不**触发 P5 启动（修完仍在本 plan 范围内）
- **不**清空 p3 分支已有 commit history（14 commit 全保留，rebase 不必要）

## 落地 commit 序列建议

```
1. fix(memory): UserMemory.save promote 同步 memory_type (F1) + persistence test
2. feat(memory): 新建 semantic.py / query.py thin domain views (F3) + ContextRuntime 改走 domain (F11 联动)
3. fix(graph): report_graph 用 v2 insight 字段名 (F10) + 联动 main.py / confirmed_execution_graph.py
4. fix(memory): remember_conversation_facts 补 session_id 形参 (F9)
5. fix(state): state/__init__.py re-export 5 TypedDict (F8)
6. test(persistence): semantic_entry migration 钉子 (F4)
7. test(persistence): supersede 端到端钉子 (F7)
8. test(contracts): SelectiveRecallPolicy 4 触发负例 + REQUIREMENT 分流 (F5 + F6)
9. test(contracts): recall_structured memory_semantic 路径 (F12)
10. test(contracts): memory domain 边界 contract (F13) + asyncio test 改 @pytest.mark.asyncio (F14)
11. docs(plans): P3+P4b plan 头部「执行偏差」节 (plan amendment)
```

预估 11 个 commit，全部修完预计 ~3-4 小时工作量（含套件回归与 debug）。修完 push p3 后再与用户确认是否合 master。
