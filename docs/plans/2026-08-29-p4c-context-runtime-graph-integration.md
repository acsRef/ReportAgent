# P4c 实施：ContextRuntime 真正接入主图 + assembler 真实装 + golden before/after

> **状态**: 已完成（p4c 分支，5 commit：9fefa44 graph caller 翻转 / 8b16835 主链 smoke / 7d58eb0 selective 收益 / 23331aa assembler real Filter & Budget / 78bd6b7 golden before/after）
> **上游**: [伞形 plan](../plans/2026-08-25-refactor-master-freeze.md) §六 / [memory-architecture.md](../architecture/memory-architecture.md) §二/§三/§八 / [agent-flow.md](../architecture/agent-flow.md) §四/§五
> **接续**: P4a ([2026-08-27-p4a-conversation-memory-decouple.md](2026-08-27-p4a-conversation-memory-decouple.md)) + P4b ([2026-08-27-p4b-memory-lifecycle-selective-recall.md](2026-08-27-p4b-memory-lifecycle-selective-recall.md))；p3 分支 HEAD `efaeeb5` 已合 master
> **优先级骨架**: 用户两轮 review PASS 后给出的 5 步（见 [[p4c-start]]）
> **本次落地日期**: 2026-08-29

## Context（为什么做）

### 原始诉求
- CLAUDE.md §6 Memory Architecture §八：ContextRuntime `build(session_id, user_id, query, agent)` 是统一入口；P3 已建 4 件套（runtime / decision / policy / assembler）但 **graph 0 caller**。
- CLAUDE.md §2 Forbidden Patterns：「不让 Agent 自拼 Context——Context Runtime 是唯一入口（P3 落地前沿用 build_session_context）」——前沿豁免到期，P4c 必须**真正**把 graph 切到 ContextRuntime。
- 伞形 plan §六 "Recall Before Agent" 时机：现役 6 处等价（4 文件）仍走 facade `build_session_context` 兼容路径，无 Memory recall / policy.decide 副作用——架构契约形同虚设。
- 用户 [[p4c-start]] 5 步优先级是「验证这套架构是否真的 work」的**收口阶段**，不是设计阶段。

### 现状问题（摸底）
1. **4 个 graph caller 仍走 facade**：
   - `backend/app/agent/requirement_analysis_graph.py:214`（requirement 入口）
   - `backend/app/agent/confirmed_execution_graph.py:198`（confirmed 主链）
   - `backend/app/agent/requirement_parser.py:124`（format_context_block 注入）
   - `backend/app/agent/sql_graph.py:351, 498`（同函数 2 处 format_context_block 注入）
2. **ContextRuntime.build() 接口已稳定**（P3 + P4b）：5 步编排完整，含 `decision.conversation/semantic/query` 分流（P4b F11 把 SelectiveRecallPolicy.decision.conversation 从死字段激活）。
3. **SelectiveRecallPolicy 已实装**（P4b T5）：contract test 钉好四触发 + §三分流；但**真实主链 0 触发**，selective 行为只在 contract test 中验证。
4. **assembler 简化拼接**（P4a）：`Filter / Conflict / Budget` 三个 pipeline 占位都是 default-no-op；assembled_context 仅做 recall_block + conversation_context 拼接。无去空、无 dedup、无 Token Budget 截断。
5. **golden 套件在位**（tests/golden/）：P4b 走 facade 路径，主 graph 未接入 recall，golden 在 P4a/P4b 期间行为未变。

### P4c 与 P4b 边界（已与 [[p4c-start]] 对齐）

| 领域 | P4b（已完成） | P4c（本 plan） |
|---|---|---|
| SelectiveRecallPolicy | ✅ contract test 钉 | 主图真实触发 selective |
| ContextRuntime 接口 | ✅ 5 步编排 | 实施端到端接入 |
| assembler 拼接 | ✅ 简化版本 | 真实 Filter / Conflict / Token Budget |
| 4 graph caller | ❌ 保持 facade | ✅ 翻转到 ContextRuntime |
| golden before/after | ❌ | ✅ |

## Design（做什么、模块怎么拼）

### 2.1 整体路径

```text
Task 1（最高风险）  graph caller 翻转  → backend/app/agent/{requirement_analysis_graph, confirmed_execution_graph, requirement_parser, sql_graph}.py
                                          + backend/app/context/__init__.py facade 兼容保留
Task 2              主链行为验证      → backend/tests/graphs/test_context_runtime_main_chain.py
                                          + 跑 p3/p4a 既有套件确认不回退
Task 3              selective 收益验证 → backend/tests/contracts/test_selective_recall_benefit.py
Task 4              assembler 真实装  → backend/app/context/assembler.py
                                          + backend/tests/contracts/test_context_assembler_real_filter_budget.py
Task 5              golden before/after → 跑 tests/golden/ + 写 docs/p4c-golden-before-after.md
```

### 2.2 Task 1 — graph caller 翻转

每个 caller 切到 ContextRuntime.build()，传 `state["current_query"]` 或当前 `query` + `agent` 名：

- **`requirement_analysis_graph.py:214`** → `bundle = await context_runtime.build(session_id=..., user_id=..., query=state["current_query"], agent="requirement_analyze")`
- **`confirmed_execution_graph.py:198`** → 同 shape，`agent="confirmed_execution"`
- **`requirement_parser._analyze()`**（输入已是 conversation_context 字符串）→ 把 `format_context_block(conversation_context)` 改为同时透传 `bundle.assembled_context`（含 recall_items 拼接）
- **`sql_graph._plan` + `_generate_sql`** → 同上

**facade `build_session_context` 保留兼容**：`tests/contracts/test_context_package_facade.py` 仍要绿；外部 import 路径不破（CLAUDE.md §13 legacy import freeze 类似策略——facade 是稳定接口层）。

**关键 invariant**：caller 拿到 `ContextBundle` 后必须仍能取 `conversation_context: str`（现有字段访问方式不变——零侵入 contract §一保持）。

### 2.3 Task 2 — 主链行为验证

跑现有套件 + 新增 smoke 钉子：
- `tests/graphs/` 全过（含 `test_requirement_analysis_sqlgate.py` / `test_sql_generation.py` / `test_confirmed_execution.py`）
- 新增 `tests/graphs/test_context_runtime_main_chain.py`：
  - 单测 `Requirement Agent` 走 `ContextRuntime` 后 `state["conversation_context"]` 不为空且包含 conversation block
  - 单测 `Confirmed Execution Agent` 走 `ContextRuntime` 后 `state["conversation_context"]` 不为空
  - 注入 mock memory → 验证 `assembled_context` 包含 recall_items 的 raw_text（仅 decision.semantic=True 时）

### 2.4 Task 3 — Selective Recall 收益验证

新增 `tests/contracts/test_selective_recall_benefit.py`，8 个对照用例：

| # | query | agent | 期望 decision |
|---|---|---|---|
| 1 | "你好"（闲聊） | requirement | conv=True sem=False q=False |
| 2 | "再按产品细分"（历史引用） | requirement | conv=True sem=True q=False |
| 3 | "以后都用柱状图"（长期偏好） | requirement | conv=True sem=True q=False |
| 4 | "GMV 是什么口径？"（业务定义） | requirement | conv=True sem=True q=False |
| 5 | "上月各区域销售"（高相似 query） | execution | conv=True sem=True q=True |
| 6 | "渲染当前查询的报告" | report | conv=True sem=True q=False（§三分流） |
| 7 | "完整 query 含完整 metric+dim" | execution | decision 由 SelectiveRecallPolicy 自行判断 |
| 8 | 空 query / 无 query | requirement | 防爆——graceful fallback |

**对照基线**：同样的 query 走 `LegacyFallbackPolicy` vs `SelectiveRecallPolicy` 时，decision 差异（至少 1 个 bool 不同 → recall 副作用真实分流）。

### 2.5 Task 4 — assembler 真实 Filter + Token Budget

`assembler.py` 加两个 pipeline 步骤实装（review P3 P1 #4 决议：assembler 是纯组装不依赖外部 API）：

- **Filter 真实版**：
  - 1) drop empty `raw_text` (P4a 已做)
  - 2) dedup by (source, ref_id) 保留 score 最高
  - 3) 按 `kind` 排序：query > semantic > preference（§七 固定序）
- **Conflict Resolution 真实版**：保留 P3 简化固定序拼接 + 上面 dedup；不强语义重排（伞形 plan §六 V1 简化）
- **Token Budget 真实版**：估算 budget=`min(REMAINING_PROMPT_BUDGET, ASSEMBLER_TOKEN_BUDGET)`（取 settings 或环境变量，默认 4000 tokens ≈ 12000 chars），按 char count 截断 assembled_context

**不重写 contract**：ContextBundle 输出字段 / 既有 contract test 全过。

### 2.6 Task 5 — golden before/after

- **before**：p3 分支（HEAD `efaeeb5`）跑 `pytest backend/tests/golden/ -v`，记录每个用例的 `query_snapshot.sql` + `answer.table` 形状
- **after**：p4c 分支同一命令再跑，对比
- 写 `docs/p4c-golden-before-after.md`：列出每个用例**预期/实际**——若 assembled_context 注入改变了 LLM SQL 生成，**黄金失败/快照更新都要**显式确认（这是 contract test 正常维护，不是回归）

## Files to change

### 修改（8 个）

| 路径 | 变更模式 |
|---|---|
| `backend/app/agent/requirement_analysis_graph.py` | 入口节点：把 `await build_session_context(...)` → `bundle = await context_runtime.build(...)`；取 `bundle["conversation_context"]` 当 conversation_context；取 `bundle["assembled_context"]` 经 `format_context_block` 拼注入 |
| `backend/app/agent/confirmed_execution_graph.py` | 同上，`agent="confirmed_execution"` |
| `backend/app/agent/requirement_parser.py` | `_analyze` 入参 `conversation_context: str | None` 改为 `bundle: ContextBundle | None`（或保持原签名 + 新增 assembled_context 入参，向后兼容） |
| `backend/app/agent/sql_graph.py` | `_plan` + `_generate_sql` 同上 |
| `backend/app/context/assembler.py` | `ContextAssembler.assemble()` 加真实 Filter（dedup + 排序）+ Token Budget 截断 |
| `backend/app/context/__init__.py` | `build_session_context` facade **保留** + DeprecationWarning 仍触（CLAUDE.md §2「前沿用 build_session_context」豁免到期但兼容路径不删） |
| `docs/plans/README.md` | 索引「进行中」区登记本 plan |
| `docs/p4c-golden-before-after.md` | 新增：before/after 对比文档 |

### 新增（4 个测试 + 1 个文档）

| 路径 | 用途 |
|---|---|
| `backend/tests/graphs/test_context_runtime_main_chain.py` | Task 2 主链 smoke：3 个 graph 真实触发 ContextRuntime 后 state 不空 + recall 透传钉子 |
| `backend/tests/contracts/test_selective_recall_benefit.py` | Task 3 selective 收益 8 用例 |
| `backend/tests/contracts/test_context_assembler_real_filter_budget.py` | Task 4 assembler Filter/Budget 真实装 |
| `backend/tests/contracts/test_p4c_graph_caller_integration.py` | Task 1 caller 翻转后 integration 钉子（4 文件 6 处 import 行为符合新契约） |
| `docs/p4c-golden-before-after.md` | Task 5 对比文档 |

### 不变（CLAUDE.md §2 + P4a/P4b 落地保护）

- `backend/app/context/_engine.py` re-export facade（P4a 已落地）
- `backend/app/context/runtime.py` ContextRuntime 接口（P3 + P4b 已稳定）
- `backend/app/context/decision.py` SelectiveRecallPolicy（P4b T5）
- `backend/app/context/policy.py` AgentContextPolicy / Resolver（P3）
- `backend/app/memory/{conversation,semantic,query,manager}.py`
- `backend/app/infra/memory/{user_memory,query_memory,memory_manager}.py`
- `legacy/agents/parent_graph.py` 仍走 `mm.recall()->str`（CLAUDE.md §13 P15 删除）
- `build_session_context` facade 兼容路径（保留 + DeprecationWarning）
- 既有 golden/contract smoke tests

## Reused existing utilities（复用优先）

| 复用对象 | 路径 | 方式 |
|---|---|---|
| `ContextRuntime.build()` 完整 5 步编排 | `backend/app/context/runtime.py` | Task 1 直接调，零改 |
| `SelectiveRecallPolicy` 四触发判定器 | `backend/app/context/decision.py` | Task 1 默认 policy；Task 3 验证 |
| `ContextAssembler.assemble()` | `backend/app/context/assembler.py` | Task 4 加 Filter/Budget；不动公共签名 |
| `format_context_block()` | `backend/app/memory/conversation.py` | caller 注入 assembled_context 时复用 |
| `MemoryManager.remember_preference` | `backend/app/infra/memory/memory_manager.py` | P4a 已落，P4c 不动 |
| 既有 contract test `test_context_runtime_contract.py` / `test_structured_recall_contract.py` / `test_selective_recall_contract.py` | `backend/tests/contracts/` | 全部要在 P4c 落地后仍绿——钉子保护接口契约 |

## Verification（端到端验证）

### 单元 / contract

```bash
cd backend
pytest tests/contracts/test_context_package_facade.py -v        # facade 兼容回归
pytest tests/contracts/test_context_runtime_contract.py -v     # 5 步编排接口
pytest tests/contracts/test_structured_recall_contract.py -v   # structured 路径
pytest tests/contracts/test_selective_recall_contract.py -v    # §二四触发 + §三分流 钉子
pytest tests/contracts/test_p4c_graph_caller_integration.py -v  # Task 1 新增
pytest tests/contracts/test_selective_recall_benefit.py -v      # Task 3 新增
pytest tests/contracts/test_context_assembler_real_filter_budget.py -v  # Task 4 新增
```

### 主链（Task 2）

```bash
pytest tests/graphs/test_context_runtime_main_chain.py -v
pytest tests/graphs/ -v                       # 全部 graph 不回退
```

### 全量离线（CLAUDE.md §15 红线）

```bash
cd backend && pytest   # 614 passed 不回退
```

### Golden Set

```bash
pytest backend/tests/golden/   # 行为差异要在 docs/p4c-golden-before-after.md 显式说明
```

### 冒烟矩阵（5 项）

1. **Facade 兼容回归**：facade `build_session_context` 仍能调（不抛）+ DeprecationWarning 触；4 文件 import 路径保留可工作
2. **新 caller 真实路径**：`requirement_analysis_graph` / `confirmed_execution_graph` 入口跑通后 `state["conversation_context"]` 非空；selective policy 启动后 `assembled_context` 含 `format_context_block` 包裹 + recall items
3. **selective 收益钉**：8 用例矩阵过
4. **assembler 真实装**：`recall_items` 含 3 条 query + 2 条 preference → 截断到 `top_k_queries=2, top_k_preferences=3`；空 recall → assembled_context 仅含 conversation
5. **forbidden patterns 自查**（CLAUDE.md §2）：Agent 不自拼 Context（caller 全走 ContextRuntime）✅、不让 Agent 直访 Memory DB（仍仅 MemoryManager）✅、不绕过 MCP 直连 RAG ✅

### 持久化冒烟（DATABASE_URL 必需）

- P4b `semantic_entry` 表 + P4c 真实接入不动 schema
- 跑 migration 幂等段不报错
- 真实跑 requirement 分析 session → memory 召回在 CONTEXT 内注入 → 后续 query LLM 生成 SQL 形同 P4b baseline

## Explicitly NOT doing

| 不做 | 归属 | 理由 |
|---|---|---|
| `MemoryManager.recall_structured()` 签名改 | 不做 | P4b 已落 |
| `semantic_entry` 表扩展 | 不做 | P4b 已落 |
| `_engine._save_l3_facts` 时机拆分 | 不做 | 伞形 plan 留后期（P4c 不动时机） |
| Behavior promotion pipeline（candidate → evidence_count → active） | 不做 | 伞形 plan §六 V1 冻结 |
| Semantic/Query SQL 实现从 `infra/memory/{user,query}_memory.py` → `app/memory/{semantic,query}.py` | 不做 | P4c 只用 thin view，不搬持久化 |
| LangGraph 节点对 state 的 dict 访问方式重写 | 不做 | CLAUDE.md §一零侵入 |
| State 单写者 enforcement | 不做 | P3 移出（P8 选题） |
| SchemaVersioningSaver wrapper | 不做 | P3 review #8 决议保持 |
| 触碰 legacy parent_graph `mm.recall()->str` | 不做 | CLAUDE.md §13；P15 删除 |
| 调整 Forbidden Patterns | 不做 | CLAUDE.md §2 冻结 |
| 让 LLM 决定 confidence | 不做 | 伞形 plan §六 V1 |
| 让 `_engine.compress_and_extract` 改 LLM Adapter | 不做 | P6 选题 |
| 移除 `build_session_context` facade | 不做 | 兼容路径保留（外部 import 仍可用） |
| Token Budget 精细算法（按 model 实际 token 数） | 不做 | P4c 仅 char count 估算；精细算法 P14 数据驱动 |
| Conflict Resolution 语义重排（同 key 跨域冲突） | 不做 | 仅 dedup + 固定序；语义重排属 P14 |

---

## Task 1 — graph caller 翻转（最高风险）

**Files:**
- Modify: `backend/app/agent/requirement_analysis_graph.py:212-218`
- Modify: `backend/app/agent/confirmed_execution_graph.py:196-202`
- Modify: `backend/app/agent/requirement_parser.py:109-160`
- Modify: `backend/app/agent/sql_graph.py:351-353, 498-500`
- Test: `backend/tests/contracts/test_p4c_graph_caller_integration.py`（新增）

- [ ] **Step 1: 写 integration 失败钉子 —— 钉 ContextRuntime 必须被 4 个 graph caller 调用**

```python
# backend/tests/contracts/test_p4c_graph_caller_integration.py
"""P4c Task 1：4 graph caller 必须真正接入 ContextRuntime。

钉子：
- Requirement Agent 入口节点：调用链包含 ContextRuntime.build
- Confirmed Execution Agent 入口节点：调用链包含 ContextRuntime.build
- requirement_parser：注入 ContextBundle.assembled_context
- sql_graph._plan + _generate_sql：注入 ContextBundle.assembled_context
"""
import inspect
import pytest

from app.context.runtime import context_runtime


@pytest.mark.parametrize("module_path,func_name", [
    ("backend.app.agent.requirement_analysis_graph", "_intent_analyze"),
    ("backend.app.agent.confirmed_execution_graph", "_load_draft"),
])
def test_graph_entry_node_imports_context_runtime(module_path, func_name):
    """入口节点函数源含 `context_runtime.build(` 调用。"""
    import importlib
    mod = importlib.import_module(module_path)
    func = getattr(mod, func_name)
    src = inspect.getsource(func)
    assert "context_runtime.build(" in src, (
        f"{module_path}.{func_name} 仍未调用 context_runtime.build()"
    )


@pytest.mark.parametrize("module_path,func_name", [
    ("backend.app.agent.requirement_parser", "_analyze"),
    ("backend.app.agent.sql_graph", "_plan"),
    ("backend.app.agent.sql_graph", "_generate_sql"),
])
def test_prompt_injectors_use_assembled_context(module_path, func_name):
    """4 个 prompt 注入点必须从 ContextBundle.assembled_context 注入。"""
    import importlib
    mod = importlib.import_module(module_path)
    func = getattr(mod, func_name)
    src = inspect.getsource(func)
    assert "assembled_context" in src, (
        f"{module_path}.{func_name} 未引用 assembled_context"
    )


def test_facade_build_session_context_still_callable():
    """facade 兼容路径保留：旧 import 路径仍工作（仅 DeprecationWarning）。"""
    import warnings
    from app.context import build_session_context
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert callable(build_session_context)
```

- [ ] **Step 2: 跑钉子验证先红**

```bash
cd backend && pytest tests/contracts/test_p4c_graph_caller_integration.py -v
```

Expected：5 个 parametric test 全 FAIL（caller 仍走 facade）。

- [ ] **Step 3: 翻转 `requirement_analysis_graph.py:212-218`**

```python
# 旧：
# from app.context import build_session_context
# conversation_context = await build_session_context(state["session_id"], state["user_id"])

# 新：
from app.context import context_runtime
from app.context.runtime import ContextRuntime

bundle = await ContextRuntime().build(
    session_id=state["session_id"],
    user_id=int(state["user_id"]),
    query=state.get("current_query", state.get("original_query", "")),
    agent="requirement_analyze",
    state_dict=dict(state),
)
conversation_context = bundle["conversation_context"]   # 透传，签名稳定
assembled_context = bundle["assembled_context"]         # 含 recall_items，供 prompt 注入
state["assembled_context"] = assembled_context          # 透给下游节点
```

- [ ] **Step 4: 翻转 `confirmed_execution_graph.py:196-202`**

```python
# 同样 shape，agent="confirmed_execution"
from app.context.runtime import ContextRuntime

bundle = await ContextRuntime().build(
    session_id=state["session_id"],
    user_id=int(state["user_id"]),
    query=state["current_query"],
    agent="confirmed_execution",
    state_dict=dict(state),
)
conversation_context = bundle["conversation_context"]
state["conversation_context"] = conversation_context
state["assembled_context"] = bundle["assembled_context"]
```

- [ ] **Step 5: 修 `requirement_parser._analyze` —— 把 `format_context_block(conversation_context)` 升级为 assembled_context**

```python
# 旧入参：conversation_context: str | None
# 新入参：assembled_context: str | None  （向下兼容，caller 未传时 fallback 到 conversation_context 拼接）

async def _analyze(
    user_query: str,
    schema_context: dict | None,
    conversation_context: str | None,    # 保留
    dictionary_context: dict | None,
    assembled_context: str | None = None,  # ← 新增（caller 翻转后由 Requirement Agent 传）
):
    if assembled_context:
        prompt = f"{format_context_block(assembled_context)}\n\n{prompt}"
    elif conversation_context:
        prompt = f"{format_context_block(conversation_context)}\n\n{prompt}"
    # ... 原有逻辑不变
```

- [ ] **Step 6: 修 `sql_graph._plan` + `_generate_sql` 同 Step 5 shape**

- [ ] **Step 7: 跑钉子验证转绿**

```bash
cd backend && pytest tests/contracts/test_p4c_graph_caller_integration.py -v
```

Expected：7 个 case 全 PASS。

- [ ] **Step 8: 跑全量离线 + 持久化**

```bash
cd backend && pytest   # 614+ passed 不回退
```

- [ ] **Step 9: commit**

```bash
git add backend/app/agent/ backend/app/context/ backend/tests/contracts/test_p4c_graph_caller_integration.py
git commit -m "feat(context): graph caller 翻转接入 ContextRuntime + plan: p4c-context-runtime-graph-integration

P4c Task 1 落地：
- 4 graph caller 真正接入 ContextRuntime.build()
- facade build_session_context 兼容路径保留
- assembled_context 字段向下游透传
- integration 钉子钉 4 caller 行为契约"
```

---

## Task 2 — 主链行为验证

**Files:**
- Test: `backend/tests/graphs/test_context_runtime_main_chain.py`（新增）

- [ ] **Step 1: 写主链 smoke 钉子**

```python
# backend/tests/graphs/test_context_runtime_main_chain.py
"""P4c Task 2：ContextRuntime 接入后主链不能 break。

钉 3 件事：
1) Requirement Agent 入口跑通后 conversation_context 非空
2) Confirmed Execution Agent 入口跑通后 conversation_context 非空
3) selective policy 启动后 recall_items 透传到 assembled_context
"""
import pytest

from app.context.runtime import ContextRuntime


@pytest.mark.asyncio
async def test_requirement_agent_entry_has_conversation_context(monkeypatch):
    """Requirement Agent 入口：ContextRuntime.build() 返回 bundle["conversation_context"] 非空。"""
    from app.memory.conversation import prepare_conversation_context

    async def fake_prepare(sid, uid):
        return "<L1>history</L1><L2>summary</L2>"

    monkeypatch.setattr(prepare_conversation_context, "__wrapped__", None, raising=False)
    monkeypatch.setattr(
        "app.context.runtime.prepare_conversation_context", fake_prepare
    )
    bundle = await ContextRuntime().build(
        session_id="session-A", user_id=1,
        query="上月销售", agent="requirement_analyze",
    )
    assert bundle["conversation_context"]
    assert "history" in bundle["conversation_context"]
    assert bundle["agent_policy"] == "requirement"


@pytest.mark.asyncio
async def test_confirmed_execution_agent_entry_has_conversation_context(monkeypatch):
    """Confirmed Execution Agent 入口：ContextRuntime.build() 返回 bundle["conversation_context"] 非空 + 含 L2 digest。"""
    from app.context.runtime import prepare_conversation_context

    async def fake_prepare(sid, uid):
        return "<L2>confirmed-context</L2>"

    monkeypatch.setattr(
        "app.context.runtime.prepare_conversation_context", fake_prepare
    )
    bundle = await ContextRuntime().build(
        session_id="session-B", user_id=1,
        query="再按产品细分", agent="confirmed_execution",
    )
    assert bundle["conversation_context"]
    assert "confirmed-context" in bundle["conversation_context"]
    assert bundle["agent_policy"] == "execution"


@pytest.mark.asyncio
async def test_selective_policy_injects_recall_into_assembled(monkeypatch):
    """P4b SelectiveRecallPolicy：decision.semantic=True 时 assembled_context 包含 recall_items raw_text。"""
    from app.context.runtime import prepare_conversation_context, ContextRuntime
    from app.context.decision import SelectiveRecallPolicy
    from app.memory import semantic as semantic_memory

    async def fake_prepare(sid, uid):
        return "<L1>conv</L1>"

    async def fake_recall(q, uid, *, top_k_preferences=3):
        return [{
            "raw_text": "user prefers bar charts",
            "source": "memory_semantic",
            "kind": "preference",
            "score": 0.9,
            "ref_id": 42,
        }]

    monkeypatch.setattr(
        "app.context.runtime.prepare_conversation_context", fake_prepare
    )
    monkeypatch.setattr(semantic_memory, "recall_structured", fake_recall)

    bundle = await ContextRuntime(policy=SelectiveRecallPolicy()).build(
        session_id="s", user_id=1,
        query="再按产品细分", agent="requirement_analyze",
    )
    assert "user prefers bar charts" in bundle["assembled_context"]
    assert bundle["recall_items"][0]["source"] == "memory_semantic"
```

- [ ] **Step 2: 跑钉子验证（不接入前应 FAIL，接入后转 PASS）**

```bash
cd backend && pytest tests/graphs/test_context_runtime_main_chain.py -v
```

- [ ] **Step 3: 跑全量 graphs/ 套件确认不回退**

```bash
cd backend && pytest tests/graphs/ -v
```

- [ ] **Step 4: manual SSE smoke（CLAUDE.md §15 e2e 手动门）**

```bash
cd backend && uvicorn app.main:app --port 8100 --reload &
curl -X POST http://localhost:8100/api/v1/auth/login -H 'Content-Type: application/json' -d '{"username":"admin","password":"admin123"}'  # 取 token
curl -N -X POST http://localhost:8100/api/v1/chat -H 'Content-Type: application/json' -H "Authorization: Bearer <token>" -d '{"user_query":"上月各区域销售","session_id":"smoke-p4c","mode":"new"}'
```

Expected：SSE 流正常；report 阶段 SSE 事件含 query_snapshot.sql 非空、answer.table 行。

- [ ] **Step 5: 报告主链行为 — assembled_context 是否对 LLM SQL 生成有可见影响**

写 `docs/p4c-task2-main-chain-observation.md`：记录主链 smoke 结果（哪些字段被注入、LLM 是否引用 recall）。

- [ ] **Step 6: commit**

```bash
git add backend/tests/graphs/test_context_runtime_main_chain.py docs/p4c-task2-main-chain-observation.md
git commit -m "test(graphs): ContextRuntime 主链 smoke 3 钉子 + plan: p4c-context-runtime-graph-integration"
```

---

## Task 3 — Selective Recall 收益验证

**Files:**
- Test: `backend/tests/contracts/test_selective_recall_benefit.py`（新增）

- [ ] **Step 1: 写 8 个对照用例失败钉子**

```python
# backend/tests/contracts/test_selective_recall_benefit.py
"""P4c Task 3：验证 SelectiveRecallPolicy 真的分流掉无关召回。

对照 8 类 query × 3 种 agent 的 decision 矩阵。
收益钉：LegacyFallbackPolicy vs SelectiveRecallPolicy 在同一 query 下至少 1 个 bool 应不同。
"""
import pytest
from app.context.decision import (
    LegacyFallbackPolicy, SelectiveRecallPolicy, RecallDecision,
)


@pytest.mark.parametrize("query,agent,expected_conversation,expected_semantic,expected_query,description", [
    ("你好", "requirement", True, False, False, "闲聊：sem/query 必全 False，省 embedding 二次往返"),
    ("再按产品细分", "requirement", True, True, False, "历史引用：sem=True 启动相关 semantic recall"),
    ("以后都用柱状图", "requirement", True, True, False, "长期偏好：sem=True"),
    ("GMV 是什么口径？", "requirement", True, True, False, "业务定义：sem=True"),
    ("上月各区域销售", "execution", True, True, True, "高相似：q=True 全开"),
    ("渲染当前查询的报告", "report", True, True, False, "Report agent §三分流：q=False"),
    ("完整 metric='revenue' dim='region' time_range='2024-Q3'", "execution", True, None, None, "完整 query：decision 由 selector 自行判断（只判 None 即可）"),
    ("", "requirement", True, None, None, "空 query：防爆 graceful"),
])
def test_selective_recall_decision_matrix(
    query, agent, expected_conversation, expected_semantic, expected_query, description
):
    agent_policy_map = {"requirement": "requirement", "execution": "execution", "report": "report"}
    state = {"session_id": "s", "user_id": 1}
    decision = SelectiveRecallPolicy().decide(
        query=query,
        agent_policy=type("AP", (), {"value": agent_policy_map[agent]})(),
        session_state=state,
    )
    assert decision.conversation == expected_conversation, f"[{description}] conv mismatch"
    if expected_semantic is not None:
        assert decision.semantic == expected_semantic, f"[{description}] sem mismatch"
    if expected_query is not None:
        assert decision.query == expected_query, f"[{description}] q mismatch"


@pytest.mark.parametrize("query,agent", [
    ("你好", "requirement"),
    ("再按产品细分", "requirement"),
    ("上月各区域销售", "execution"),
])
def test_selective_vs_legacy_differs(query, agent):
    """收益钉：同样 query，SelectiveRecallPolicy 与 LegacyFallbackPolicy decision 至少 1 个 bool 不同。"""
    sel = SelectiveRecallPolicy().decide(
        query=query,
        agent_policy=type("AP", (), {"value": agent})(),
        session_state={},
    )
    leg = LegacyFallbackPolicy().decide(
        query=query,
        agent_policy=type("AP", (), {"value": agent})(),
        session_state={},
    )
    assert (sel.semantic, sel.query) != (leg.semantic, leg.query), (
        f"selective 与 legacy 在 q='{query}' agent='{agent}' 时全等 → 没分流"
    )
```

- [ ] **Step 2: 跑钉子验证**

```bash
cd backend && pytest tests/contracts/test_selective_recall_benefit.py -v
```

Expected：8 + 3 = 11 case 全 PASS（selective policy 已实装）；3 个 diff case 全 PASS（说明 selective 与 legacy 行为不同）。

- [ ] **Step 3: 跑全量 contract 套件确认不回退**

```bash
cd backend && pytest tests/contracts/ -v
```

- [ ] **Step 4: commit**

```bash
git add backend/tests/contracts/test_selective_recall_benefit.py
git commit -m "test(contracts): Selective Recall 8 用例收益矩阵 + legacy 对照 + plan: p4c-context-runtime-graph-integration"
```

---

## Task 4 — assembler 真实 Filter + Token Budget

**Files:**
- Modify: `backend/app/context/assembler.py`
- Test: `backend/tests/contracts/test_context_assembler_real_filter_budget.py`（新增）

- [ ] **Step 1: 写 assembler 真实 Filter/Budget 失败钉子**

```python
# backend/tests/contracts/test_context_assembler_real_filter_budget.py
"""P4c Task 4：assembler 加真实 Filter（dedup + 排序）+ Token Budget 截断。

钉 3 件事：
1) dedup by (source, ref_id) 保留 score 最高
2) kind 排序：query > semantic > preference（§七 固定序）
3) Token Budget 截断：超长 recall 时 assembled_context 截到 budget
"""
import pytest
from app.context.assembler import ContextAssembler, RecallItem
from app.context.policy import AgentContextPolicy


def _item(source, kind, ref_id, text="raw", score=0.5):
    return RecallItem(raw_text=text, source=source, kind=kind, score=score, ref_id=ref_id)


def test_dedup_by_source_ref_id_keeps_highest_score():
    asm = ContextAssembler()
    items = [
        _item("memory_query", "query", 1, "A_low", 0.3),
        _item("memory_query", "query", 1, "A_high", 0.9),  # dup，ref_id 相同
        _item("memory_query", "query", 2, "B", 0.7),
    ]
    bundle = asm.assemble(
        conversation_context="",
        recall_items=items,
        agent_policy=AgentContextPolicy.EXECUTION,
    )
    raw_texts = [it["raw_text"] for it in bundle["recall_items"]]
    assert "A_low" not in raw_texts
    assert raw_texts.count("A_high") == 1
    assert raw_texts.count("B") == 1
    assert len(bundle["recall_items"]) == 2


def test_kind_sort_query_semantic_preference():
    asm = ContextAssembler()
    items = [
        _item("memory_semantic", "preference", 1, "PREF"),
        _item("memory_query", "query", 2, "QRY"),
        _item("memory_semantic", "semantic", 3, "SEM"),
    ]
    bundle = asm.assemble(
        conversation_context="",
        recall_items=items,
        agent_policy=AgentContextPolicy.EXECUTION,
    )
    assert [it["kind"] for it in bundle["recall_items"]] == ["query", "semantic", "preference"]


def test_token_budget_truncates_recall_block(monkeypatch):
    """Token Budget 截断：超长 recall → assembled_context 长 ≤ budget × char_ratio。"""
    monkeypatch.setenv("P4C_ASSEMBLER_TOKEN_BUDGET", "100")
    asm = ContextAssembler()
    items = [_item("memory_query", "query", i, "x" * 100, 0.5) for i in range(20)]
    bundle = asm.assemble(
        conversation_context="conv",
        recall_items=items,
        agent_policy=AgentContextPolicy.EXECUTION,
    )
    # char ratio 默认 3 (estimate 1 token ≈ 3 chars)；预算 100 tokens ≈ 300 chars 包含前缀+conversation
    assert len(bundle["assembled_context"]) <= 600


def test_filter_drops_empty_raw_text():
    asm = ContextAssembler()
    items = [
        _item("memory_query", "query", 1, ""),
        _item("memory_semantic", "semantic", 2, "kept"),
    ]
    bundle = asm.assemble(
        conversation_context="conv",
        recall_items=items,
        agent_policy=AgentContextPolicy.REQUIREMENT,
    )
    raw_texts = [it["raw_text"] for it in bundle["recall_items"]]
    assert "" not in raw_texts
    assert "kept" in raw_texts
```

- [ ] **Step 2: 跑钉子验证先红**

```bash
cd backend && pytest tests/contracts/test_context_assembler_real_filter_budget.py -v
```

Expected：4 case 全 FAIL（assembler 还无 dedup / sort / budget）。

- [ ] **Step 3: 实装真实 Filter / Token Budget 到 `assembler.py`**

```python
# backend/app/context/assembler.py 加 3 个步骤
# 假设 context 与现有 imports 不变

import os
from typing import Literal

KIND_ORDER = {"query": 0, "semantic": 1, "preference": 2}


def _filter_dedup(items: list[RecallItem]) -> list[RecallItem]:
    """Drop empty + dedup by (source, ref_id) keep highest score + sort by §七 序。"""
    by_key: dict[tuple, RecallItem] = {}
    for it in items:
        if not it.get("raw_text"):
            continue
        key = (it.get("source"), it.get("ref_id"))
        if key not in by_key or it.get("score", 0.0) > by_key[key].get("score", 0.0):
            by_key[key] = it
    return sorted(by_key.values(), key=lambda x: KIND_ORDER.get(x.get("kind", ""), 99))


def _apply_token_budget(text: str) -> str:
    """按 settings/env `P4C_ASSEMBLER_TOKEN_BUDGET` 截断。1 token ≈ 3 chars。"""
    budget_tokens = int(os.getenv("P4C_ASSEMBLER_TOKEN_BUDGET", "4000"))
    char_cap = budget_tokens * 3
    if len(text) <= char_cap:
        return text
    return text[:char_cap]


class ContextAssembler:
    def assemble(self, *, conversation_context, recall_items, agent_policy):
        filtered = _filter_dedup(recall_items)
        recall_block = "\n".join(f"[{it['kind']}] {it['raw_text']}" for it in filtered)
        if recall_block and conversation_context:
            assembled = f"{recall_block}\n\n{conversation_context}"
        else:
            assembled = recall_block or conversation_context
        assembled = _apply_token_budget(assembled)
        return ContextBundle(
            conversation_context=conversation_context,
            recall_items=filtered,
            assembled_context=assembled,
            agent_policy=agent_policy.value if hasattr(agent_policy, "value") else str(agent_policy),
            schema_version="v2",
        )
```

- [ ] **Step 4: 跑钉子验证转绿**

```bash
cd backend && pytest tests/contracts/test_context_assembler_real_filter_budget.py -v
```

Expected：4 case 全 PASS。

- [ ] **Step 5: 跑 ContextRuntime contract 全套，确认未破坏**

```bash
cd backend && pytest tests/contracts/test_context_runtime_contract.py tests/contracts/test_context_package_facade.py tests/contracts/test_structured_recall_contract.py tests/contracts/test_selective_recall_contract.py -v
```

Expected：全绿（既有 5 步编排接口不变；过滤/dedup 内置不影响 bundle 字段契约）。

- [ ] **Step 6: 跑全量离线 + 持久化**

```bash
cd backend && pytest   # 614+ passed
```

- [ ] **Step 7: commit**

```bash
git add backend/app/context/assembler.py backend/tests/contracts/test_context_assembler_real_filter_budget.py
git commit -m "feat(context): assembler 真实 Filter (dedup + §七 排序) + Token Budget 截断 + plan: p4c-context-runtime-graph-integration"
```

---

## Task 5 — golden before/after

**Files:**
- Add: `docs/p4c-golden-before-after.md`（新增）
- Test: 既有 `backend/tests/golden/`

- [ ] **Step 1: before baseline —— p3 分支 snapshot**

```bash
cd backend && pytest tests/golden/ -v > /tmp/p4c-golden-before.log 2>&1
```

记录每个用例：
- `query_snapshot.sql`（关键）
- `answer.table` 形状
- 通过/失败状态

- [ ] **Step 2: after —— p4c 分支同命令再跑**

```bash
cd backend && pytest tests/golden/ -v > /tmp/p4c-golden-after.log 2>&1
```

- [ ] **Step 3: diff before / after**

```bash
diff /tmp/p4c-golden-before.log /tmp/p4c-golden-after.log > /tmp/p4c-golden-diff.txt
cat /tmp/p4c-golden-diff.txt | head -100
```

若无差异 → golden 行为 **完全不变**（最优）；若有差异 → 写 before/after 文档显式列出哪些用例 SQL/table 变化，是否接受。

- [ ] **Step 4: 写对比文档 `docs/p4c-golden-before-after.md`**

```markdown
# P4c golden before/after

## 概览
- before：p3 分支 HEAD efaeeb5
- after：p4c 分支（ContextRuntime 接入 + assembler 真实装）
- 测试套件：backend/tests/golden/

## diff 结果
- 用例通过率：X / Y（before） vs A / B（after）
- 行为差异（逐项列出）：
  - 用例 X：query_snapshot.sql 变化（接受/拒绝）
  - 用例 Y：answer.table row count 变化（接受/拒绝）

## 结论
- [ ] 无回退（最优）
- [ ] 有受控行为变化（N 处），每处逐一 grant
- [ ] 有不可接受变化（rollback）
```

- [ ] **Step 5: 若有不可接受变化 → 回滚 Task 1/4 范围到上一次 commit，然后重跑 before 对比**

（保护门：若 ContextRuntime 真实接入改变了 SQL 生成质量、且变化非收益，必须明示出来不允许"沉默扩行为"。）

- [ ] **Step 6: commit 文档 + 索引更新**

```bash
git add docs/p4c-golden-before-after.md docs/plans/README.md
git commit -m "docs(p4c): golden before/after 对比 + README 索引登记 + plan: p4c-context-runtime-graph-integration"
```

---

## Final 收口

- [ ] **Step F1: 跑全量离线 + contract + graphs + golden 一气过**

```bash
cd backend && pytest    # 614+ passed
pytest tests/golden/    # 与 before 同
```

- [ ] **Step F2: 更新 CLAUDE.md §6 现状行**

```markdown
- 现状（2026-08-29 P4c 后）：4 graph caller 翻转到 ContextRuntime.build()；assembler 真实 Filter/Budget；selective 在主链真触发；golden 与 P3/p4a baseline 对比结论见 [docs/p4c-golden-before-after.md](../p4c-golden-before-after.md)
```

- [ ] **Step F3: plan 状态改 `已完成`**

`docs/plans/2026-08-29-p4c-context-runtime-graph-integration.md` 顶部 `> 状态: 已完成`

- [ ] **Step F4: push origin 与合并 master**

```bash
git push -u origin p4c
# 用户 review 后由用户合并
```

- [ ] **Step F5: 更新 memory**

更新 `MEMORY.md` 把 [[p4c-start]] 标"已完成"；新增 [[p4c-landed]] 沉淀 before/after 关键事实（golden diff 摘要 + selective 收益数据）。

---

## Self-Review

1. **Spec coverage**：
   - Step 1 (5 步优先级)：Task 1 ✅ / Task 2 ✅ / Task 3 ✅ / Task 4 ✅ / Task 5 ✅
   - 4 graph caller 翻转：Task 1 Step 3-6 ✅
   - selective 收益验证：Task 3 ✅
   - assembler Filter/Budget 真实装：Task 4 ✅
   - golden before/after：Task 5 ✅
   - 不动时机：Explicitly NOT doing ✅
   - 不动 legacy：Explicitly NOT doing ✅
2. **Placeholder 扫描**：无 TBD / TODO / "fill in details"；每个 code block 是完整代码
3. **Type 一致性**：
   - `ContextRuntime().build(*, session_id, user_id, query, agent, state_dict=None)` — 与 `backend/app/context/runtime.py:40` 一致
   - `ContextBundle` TypedDict 字段 `conversation_context / recall_items / assembled_context / agent_policy / schema_version` — 与 `assembler.py` 一致
   - `RecallItem` 字段 `raw_text / source / kind / score / ref_id` — 与 P4b T4 一致
   - `SelectiveRecallPolicy.decide(*, query, agent_policy, session_state)` — 与 P4b T5 一致
   - `AgentContextPolicy.{REQUIREMENT,EXECUTION,REPORT}` — 与 policy.py 一致

## 附录 A：与 P4a/P4b 接口衔接

```text
P3（已落地）
├── ContextRuntime 4 件套：runtime / decision / policy / assembler
├── facade build_session_context 兼容路径

P4a（已落地）
├── app/memory/{conversation,manager}.py
├── context 包零 infra.memory 依赖
└── facade 仍直调 _engine（实现走 memory.conversation）

P4b（已落地）
├── semantic_entry lifecycle 字段迁移
├── write pipeline confidence/status 规则
├── MemoryManager.recall_structured() -> list[RecallItem]
├── SelectiveRecallPolicy 四触发条件
└── RecallItem 字段全（raw_text / source / kind / score / ref_id）

P4c（本 plan）
├── Task 1 graph caller 翻转接入 ContextRuntime          ← 4 文件
├── Task 2 主链行为 smoke（防 break）
├── Task 3 selective 收益矩阵 8 用例 + legacy 对照
├── Task 4 assembler 真实 Filter (dedup + §七 序) + Token Budget
└── Task 5 golden before/after 验收
```

## 附录 B：Forbidden Patterns 自查（CLAUDE.md §2）

| 条款 | P4c 是否触及 |
|---|---|
| 不直接 import RAG 项目代码 | 否（仅 `app.context.runtime` / `app.memory.*`） |
| 不让 Agent 自拼 Context | **✅ 关键**——4 caller 翻转到 ContextRuntime，前沿豁免到期 |
| 不让 Agent 直接访问 Memory DB | 否（仅 `MemoryManager` / `app.memory.*` 视图） |
| 不让 Agent 直接调用 provider SDK | 否（不动 `_engine.compress_and_extract`） |
| 不让 Tool 没有 description | 否（不涉及 Tool） |
| 不让 Report Agent 编造数据 | 否（不涉及 Report Agent） |
| 不无限 retry | 否（不涉及 retry） |
| 不绕过 MCP 直连 RAG 内部机制 | 否（不涉及 RAG/MCP） |
| 不新增 legacy import | 否（facade 兼容路径不动 legacy） |
| 不新建 `utils2/managers/runtime/helpers/common2/` | 否（路径全在伞形 plan 冻结目录） |
