# P3 实施：Context Runtime 骨架 + State 五块归位（compatibility-first）

> **状态**: 进行中
> **上游**: [2026-08-25-refactor-master-freeze.md](../plans/2026-08-25-refactor-master-freeze.md) §六 / [state-contract.md](../architecture/state-contract.md) / [memory-architecture.md](../architecture/memory-architecture.md)
> **接续**: P2 实施 ([2026-08-26-p2-rag-mcp-boundary.md](2026-08-26-p2-rag-mcp-boundary.md)，master 7baa3d0，475+ passed)
> **后续**: P4 实施（Selective Recall 策略 + Semantic/Query/Conversation 底座 + Memory structured records 解耦）
> **本次落地日期**: 2026-08-27
> **Review v1 决议**: 已消化第一轮 review 的 P0×2 + P1×2 + 7 其他项 + checkpoint (γ) 落地路径

## Context（为什么做）

### 原始诉求
- CLAUDE.md §6 Memory Architecture 现状行：「Context Runtime 四文件目录不存在（勿假设已建）→ P3/P4」。
- CLAUDE.md §5 State Contract 现状行：「拆分未执行，现役各图 state 是五块的子集；P3 落地（注意 checkpoint 兼容风险）」。
- state-contract.md §三 现状映射：「ExecutionState 子集 → P3 归位改名」「RequestState 子集 → P3 收拢」「P3 建 RuntimeState」「checkpoint 兼容性风险（P3 plan 必须处理）」。
- 伞形 plan §六（134-178）：Context Runtime 四件套 `backend/app/context/{runtime,policy,decision,assembler}.py` 路径已冻结；Recall Before Agent 时机；Agent-specific Policy 表；Schema 永远不能被 Memory 覆盖。

### 现状问题（摸底）
1. `backend/app/context.py`（230 行）三类职责揉在一起：
   - **Conversation Engine**（L1/L2/L2.5 压缩）：`build_context` / `compress_and_extract` / `archive_to_l2_5` / `format_messages` / `format_context_block`
   - **Runtime storage glue**：`build_session_context` 拉 messages + digest → build_context → 回写 → 写 L3
   - **Memory 落库增强**：`_save_l3_facts` 直接 `from app.infra.memory` import UserMemory + mem0_extractor
2. `backend/app/infra/memory/memory_manager.MemoryManager.recall()` 当前是**全量召回**（`top_k_queries=2, top_k_preferences=3`），**返回 string**（`memory_manager.py:38 "\n".join(lines)`），不是 structured records。
3. State 散落在三个现役图：
   - `app/agent/sql_graph.py:69` `SQLAgentState`（TypedDict）
   - `app/agent/requirement_analysis_graph.py:43` `RequirementAnalysisState`（TypedDict, total=False）
   - `app/agent/confirmed_execution_graph.py:38` `ConfirmedExecutionState`（TypedDict, total=False）
   - legacy `app/legacy/agents/parent_graph.py:41` `AgentState`（TypedDict，20+ 字段，CLAUDE.md §13 不迁移）
4. Context Runtime 四件套目录尚未存在。
5. `build_session_context` 被 4 文件 6 处调用：`sql_graph.py:93` / `requirement_analysis_graph.py:203,212,216` / `confirmed_execution_graph.py:196,198` / `requirement_parser.py:16`。

### P3 5 个 scope 决策（已与用户对齐）

| # | 决策 | 选 |
|---|---|---|
| 1 | P3 vs P4 边界 | **C**：P3 = Runtime 骨架 + 接口协议；P4 = Memory recall 策略 |
| 2 | 现有 `context.py` 迁移策略 | **B**：兼容迁移，保留 facade；Conversation Engine 实现延 P4 |
| 3 | checkpoint 兼容策略 | **①+③ 组合 + (γ) 落地**：schema_version 显式标记 + adapter 显式转换 + graph 入口节点单点注入（不造 saver wrapper） |
| 4 | State 五块实现形态 | **A**：TypedDict 视图层（static ownership contract），零侵入 graph 节点访问方式 |
| 5 | `decision.py` 接口形态 | **a**：Protocol + `LegacyFallbackPolicy` 默认 fallback；`decide()` 入参带 `agent_policy` 闭合两层 abstraction |

## Design（做什么、模块怎么拼）

### 2.1 模块图

```text
backend/app/context/                # ← 旧 context.py 升级为 package（Python 不允许同目录 .py 与 / 并存）
├── __init__.py          # facade：re-export 旧 sync API + 新 API + DeprecationWarning
│                       # build_session_context 直调 _engine._prepare_conversation_context
│                       # （兼容路径，绕过 runtime，副作用 100% 等价）
├── _engine.py           # 旧 context.py 内容迁入（P4 会搬到 memory/conversation.py）
│                       # sync 纯函数 + async helper：_prepare_conversation_context
│                       # + _save_l3_facts（**Legacy Conversation/Memory Glue，P4 removes dependency**）
├── runtime.py           # ContextRuntime.build(...) → ContextBundle（伞形 plan §六 路径，新 API）
├── decision.py          # ContextDecisionPolicy Protocol + RecallDecision + LegacyFallbackPolicy
├── policy.py            # AgentContextPolicy 枚举 + ContextPolicyResolver
└── assembler.py         # RecallItem + ContextBundle + ContextAssembler（纯组装）

backend/app/state/
├── __init__.py          # 导出 5 TypedDict + split_state / merge_state
├── blocks.py            # 五块 TypedDict（static ownership contract，非 runtime enforcement）
└── checkpoint_adapter.py  # SCHEMA_VERSION + is_legacy_checkpoint + migrate_checkpoint + MigrationError

backend/app/agent/{requirement_analysis_graph, confirmed_execution_graph, sql_graph, data_graph, report_graph}.py
                       # (γ) graph 入口节点首行加 `state = migrate_checkpoint(dict(state))` 一行
                       # 实施时按各 graph 实际 compile 起点确认，预计 3-5 处

backend/app/context.py   # ← 删除（被 package 替代）
```

> **关键技术决策**（review v1 后）：
> - LangGraph `StateGraph.compile()` 实测无 payload-level hook；`BaseCheckpointSaver` 22 个 public 方法，wrapper override 不全 → bypass 风险高。**P3 不造 SchemaVersioningSaver**，走 (γ) graph 入口节点单点注入。
> - Python 不允许 `app/context.py` 与 `app/context/` 并存 → 旧 `context.py` 删除，内容进 `_engine.py`，外部 `from app.context import xxx` 通过 facade 走通。
> - facade `build_session_context` 不能转发到 `ContextRuntime.build()`——后者需要 query/agent 入参，且会引入 `MemoryManager.recall()` 副作用，违反"P3 行为 100% 等价"。

### 2.2 Context Runtime 四件套

#### runtime.py（**新 API，P3 不接入 graph**）
- `class ContextRuntime`：
  - `async def build(self, *, session_id: str, user_id: int, query: str, agent: str, state_dict: dict | None = None) -> ContextBundle`
  - **编排顺序（5 步）**：
    1. **解析 agent_policy**：`agent_policy = self._resolver.resolve(agent)` → `AgentContextPolicy.{REQUIREMENT|EXECUTION|REPORT}`
    2. **Recall 决策**：`decision = self._policy.decide(query=query, agent_policy=agent_policy, session_state=state_dict or {})`
    3. **Conversation context**：`conversation_context = await _engine._prepare_conversation_context(session_id, user_id)`
    4. **Memory recall**：若 `decision.semantic or decision.query` → `text = await MemoryManager.recall(...)` → `recall_items = [RecallItem(raw_text=text, source="legacy_memory_manager")]` if text else `[]`（**1:1 包装整段 string，不解析**）；都不召时 `recall_items = []`
    5. **Assemble**：`assembler.assemble(conversation_context=..., recall_items=..., agent_policy=agent_policy)` → 返回 ContextBundle
  - 构造函数：`ContextRuntime(policy: ContextDecisionPolicy | None = None, resolver: ContextPolicyResolver | None = None, assembler: ContextAssembler | None = None)`；默认从 settings 读 `P3_CONTEXT_POLICY`（默认 `"legacy_fallback"` → `LegacyFallbackPolicy`）+ 默认 `ContextPolicyResolver()` + 默认 `ContextAssembler()`
  - 模块级 `context_runtime = ContextRuntime()` 单例
  - **P3 不接入现役 graph**——4 文件 6 处调用方仍走 `app.context.build_session_context`（兼容路径）；新 API 通过 contract test 验证；P4 选择性迁移

#### decision.py
- `class RecallDecision(BaseModel)`：
  ```python
  conversation: bool = True
  semantic: bool = True
  query: bool = True
  top_k_queries: int = 2
  top_k_preferences: int = 3
  rationale: str = ""
  ```
- `class ContextDecisionPolicy(Protocol)`：
  ```python
  def decide(
      self,
      *,
      query: str,
      agent_policy: AgentContextPolicy,   # ← P1 #4 闭合：runtime 先 resolve 再喂入
      session_state: dict,
  ) -> RecallDecision: ...
  ```
- `class LegacyFallbackPolicy`（P3 默认实现）：
  - `decide()` 返回 `RecallDecision(conversation=True, semantic=True, query=True, top_k_queries=2, top_k_preferences=3, rationale="legacy fallback")`
  - **不**调任何 Memory API——`ContextRuntime.build` step 4 拿到 decision 后自己包装 `MemoryManager.recall()` 返回值；fallback 行为下等同当前全量召回
  - `agent_policy` 入参暂时忽略（fallback 不分流）；P4 `SelectiveRecallPolicy` 按 agent_policy 分流
- P3 不实现 `SelectiveRecallPolicy`（P4 落地四触发条件判定器）

#### policy.py
- `class AgentContextPolicy(str, Enum)`：`REQUIREMENT / EXECUTION / REPORT`
- `class ContextPolicyResolver`：
  - `def resolve(self, agent_name: str) -> AgentContextPolicy`
  - `"requirement_*"` → `REQUIREMENT`
  - `"confirmed_execution_*" / "sql_*" / "_generate_sql" / "data_*"` → `EXECUTION`
  - `"report_*"` → `REPORT`
  - 未知 → `REQUIREMENT`（保守 fallback）

#### assembler.py
- `class RecallItem(TypedDict)`（review P0 #2 决议：**1:1 包装 MemoryManager 输出，不假装 structured**）：
  ```python
  raw_text: str                              # MemoryManager.recall() 返回的整段 string
  source: Literal["legacy_memory_manager"]  # P3 仅一种；P4 扩展 "memory_query" / "memory_semantic" 等
  ```
- `class ContextBundle(TypedDict)`：
  ```python
  conversation_context: str      # 透传 runtime step 3；不含 <对话上下文> 包裹
                                 # （保证与现 build_session_context 100% 等价）
  recall_items: list[RecallItem] # runtime step 4 结果（P3 始终 0 或 1 条；P4 扩到 N 条）
  assembled_context: str         # 按 memory-architecture §七 固定序拼接后的完整 context
                                 # （仍**不**含 format_context_block 包裹）
  agent_policy: str              # AgentContextPolicy enum value
  schema_version: str            # "v2"
  ```
- `class ContextAssembler`：
  - `def assemble(self, *, conversation_context: str, recall_items: list[RecallItem], agent_policy: AgentContextPolicy) -> ContextBundle`
  - Pipeline（**纯组装，不依赖任何外部 API**）：Filter（drop 空 `raw_text` item / 空 conversation）→ Conflict Resolution（P3 简化版：按 memory-architecture §七 固定序拼接；不主动重排）→ Budget（P3 仅留接口，默认不裁剪）→ Assembly（生成 `assembled_context`，**不**调 `format_context_block()` —— 包裹由 caller 决定）
- Filter/Conflict/Budget 接口位置预留，P4 实现精细算法时无需改 assembler 公共签名

### 2.3 State 五块 TypedDict 视图

#### blocks.py 五块字段（与 state-contract.md §一一字一致）

```python
class RequestState(TypedDict, total=False):
    request_id: str
    session_id: str
    user_id: int
    original_query: str   # immutable
    current_query: str

class RequirementState(TypedDict, total=False):
    normalized_query: str
    schema_candidates: list
    requirement_card: dict
    missing_dimensions: list
    clarification_history: list
    confirmation_status: str

class ExecutionState(TypedDict, total=False):
    confirmed_requirement: Optional[str]
    schema_context: Optional[dict]
    query_plan: Optional[dict]
    generated_sql: Optional[str]
    validation_result: Optional[dict]
    query_result: Optional[dict]
    execution_status: str
    error: Optional[dict]
    retry_count: int

class ReportState(TypedDict, total=False):
    report_spec: Optional[dict]
    report_version: Optional[int]
    chart_config: Optional[dict]
    insight: Optional[str]

class RuntimeState(TypedDict, total=False):
    trace_id: str
    active_agent: str
    memory_context: str
    tool_calls: list
    mcp_calls: list
```

#### 现役 State → 五块字段映射（review P1 #3：**仅 deterministic 1:1；其他字段保留 state_dict 顶层不强行归类**）

| 五块 State | 来源字段（**仅 deterministic**） |
|---|---|
| **RequestState** | `RequirementAnalysisState.{user_id, session_id}`；`ConfirmedExecutionState.{user_id, session_id}`；legacy `AgentState.{original_query, current_query, session_id, user_id}` |
| **RequirementState** | `RequirementAnalysisState.requirement_card`；legacy `AgentState.clarification_history` |
| **ExecutionState** | `SQLAgentState.{schema_context, query_plan, generated_sql, validation_result, query_result, execution_status, error}`；`ConfirmedExecutionState.{schema_context, query_result, execution_status, error}`；legacy `AgentState.{schema_context, query_plan, query_result, execution_status, error}` |
| **ReportState** | legacy `AgentState.{report_spec, chart_config, insight_text → insight}`（deterministic rename，type 相同 `Optional[str]`） |
| **RuntimeState** | `RequirementAnalysisState.trace_id`；`ConfirmedExecutionState.trace_id`；legacy `AgentState.{trace_id, active_sub_agent → active_agent, memory_context}`（deterministic rename，type 相同 `str`） |

#### unmapped 字段清单（review P1 #3 决议：**保留在 state_dict 顶层，P3 不归类**）

| 字段 | 来源 | 不映射原因 | 归属决议 |
|---|---|---|---|
| `intent` | legacy / `RequirementAnalysisState` | 语义是 `report / interface / dashboard` 分类标签，与 `RequirementState.normalized_query`（规范化用户查询）不同 | P4：可能新增 `RequirementState.intent_classification` |
| `intent_reason` | `RequirementAnalysisState` | 与 `intent` 配对 | P4 |
| `dict_context` | `RequirementAnalysisState` | 字典检索临时上下文，跨 session 无意义 | P4 |
| `casual_reply` | `RequirementAnalysisState` | 闲聊快路径产物 | P4 |
| `draft_id` | `RequirementAnalysisState / ConfirmedExecutionState` | 是 requirement 阶段产物但带 lifecycle | P4 |
| `clarification_context` | legacy | 类型 `dict`，与 `RequirementState.clarification_history: list` 不同 | P4 决议废弃或归一 |
| `security_score / security_level / security_warning` | `RequirementAnalysisState` / legacy | contract §一未列 | P4 可能新增 `RuntimeState.security_*` |
| `chosen_tool` | `SQLAgentState` | 是 tool 选择记录，contract §一未列 | P4 |
| `sql_result` | `SQLAgentState` | 与 `query_result` 语义重叠（contract §一只列 `query_result`） | P4 决议废弃 |
| `retry_counters` | `SQLAgentState` | 类型 `dict`，与 `ExecutionState.retry_count: int` 不同；无 deterministic conversion | `retry_count` 是新字段，graph 节点显式写；`retry_counters` 保留顶层 |
| `base_report_version / adjustment_text` | `ConfirmedExecutionState` | adjustment 链路产物 | P4 |
| `report_payload` | `ConfirmedExecutionState` | 与 `ReportState.report_spec` 形态不同 | P4 决议 |
| `user_query` | 全部 state | 与 `RequestState.current_query` 语义重叠但未在 contract 明确等价 | P4 |

#### blocks.py 辅助函数（review P1 #4：**TypedDict 是 static ownership contract，不是 runtime enforcement**）

- `def split_state(state_dict: dict) -> tuple[dict[BlockName, dict], dict]`：按映射表投影到五块子 dict；返回 `(blocks, unmapped)`；**unmapped 字段不删**
- `def merge_state(blocks: dict[BlockName, dict], *, unmapped: dict | None = None) -> dict`：合并回 state dict（保留原 dict 全部字段 + 补 unmapped）
- Property：∀ state_dict，`merge_state(*split_state(s)) == s`（键集合相等，值允许 default 填充）

#### DoD 边界

> **TypedDict = static ownership contract；split_state = projection；merge_state = compatibility utility。P3 验证字段归属与 migration correctness；单写者 enforcement / 跨块依赖声明留给各 graph 后续 phase（P4 起）。**

### 2.4 Checkpoint 兼容（**(γ) graph 入口节点单点 migrate**，不造 saver wrapper）

#### schema_version 字段（review #7 显式判据）

`backend/app/state/checkpoint_adapter.py`：

```python
LEGACY_SCHEMA_VERSION = "v1"
CURRENT_SCHEMA_VERSION = "v2"

class MigrationError(RuntimeError):
    """checkpoint 既不是已知 v1 shape 也不是 v2 shape，拒绝自动迁移。"""

def is_legacy_checkpoint(checkpoint: dict) -> bool:
    """显式判据：缺 schema_version 且包含 legacy AgentState 标志字段组合
    （`active_sub_agent` + `original_query` 同时存在）。
    未知 fixture / 第三方 checkpoint → False → migrate_checkpoint raise MigrationError。
    """

def migrate_checkpoint(checkpoint: dict) -> dict:
    """三分支：
    - schema_version == CURRENT_SCHEMA_VERSION → 透传（idempotent）
    - schema_version == LEGACY_SCHEMA_VERSION → adapter → 返回 v2 shape
    - schema_version 缺失 + is_legacy_checkpoint → adapter → 返回 v2 shape
    - 其他 → raise MigrationError
    """

def inject_schema_version(checkpoint: dict) -> dict:
    """在写入前调用，确保 checkpoint 顶层带 schema_version=CURRENT_SCHEMA_VERSION。"""
```

#### (γ) graph 入口节点注入点

每个现役 graph 入口节点（P3 实施时按 `compile_graph` 起始节点确认，预计 3-5 处）首行加：

```python
from app.state.checkpoint_adapter import migrate_checkpoint

async def _intent_analyze(state: RequirementAnalysisState) -> dict:
    state = migrate_checkpoint(dict(state))   # ← P3 注入点
    # ... 原有逻辑不变
```

**为什么可以接受这一行**（review 边界澄清）：
- **不**重写 graph 节点对 `state["xxx"]` 字段的访问方式（contract §一零侵入意图保持）
- 仅在 graph 边界做一次性 schema 适配（**1 行 = compatibility adapter**）
- migrate 后 state 已是 v2 形态，graph 内逻辑不变；下次 LangGraph `aput` 写入的就是 v2

#### 副作用：LangGraph observability 路径

`alist / list / copy_thread` 等路径不在 graph 执行主链路上：
- P3 dev 环境（MemorySaver）：进程重启即丢，无 observability 历史读路径依赖
- P3 prod 环境（AsyncPostgresSaver）：observability API 从 checkpoint 读出 v1 payload 时**不**自动 migrate；现网若有 observability 查询历史 v1 checkpoint 的需求，返回 v1 shape（不影响 graph 执行）
- P13 接 Langfuse 前可视为低优先级；P13 实施时决定是否在 observability API 读路径加 migrate

#### legacy → new 字段映射表（checkpoint_adapter.py 内；**仅 deterministic**）

| legacy `AgentState` 字段 | 目标五块 State（v2 形态） | 映射类型 |
|---|---|---|
| `original_query` | `RequestState.original_query` | rename（同名） |
| `current_query` | `RequestState.current_query` | rename（同名） |
| `session_id` | `RequestState.session_id` | rename（同名） |
| `user_id` | `RequestState.user_id` | rename（同名） |
| `clarification_history` | `RequirementState.clarification_history` | rename（同名同类型） |
| `schema_context` | `ExecutionState.schema_context` | rename（同名） |
| `query_plan` | `ExecutionState.query_plan` | rename（同名） |
| `query_result` | `ExecutionState.query_result` | rename（同名） |
| `execution_status` | `ExecutionState.execution_status` | rename（同名） |
| `error` | `ExecutionState.error` | rename（同名） |
| `report_spec` | `ReportState.report_spec` | rename（同名） |
| `chart_config` | `ReportState.chart_config` | rename（同名） |
| `insight_text` | `ReportState.insight` | rename（deterministic 字段名调整） |
| `trace_id` | `RuntimeState.trace_id` | rename（同名） |
| `active_sub_agent` | `RuntimeState.active_agent` | rename（deterministic 字段名调整） |
| `memory_context` | `RuntimeState.memory_context` | rename（同名） |

**unmapped（保留在 state_dict 顶层）**：`user_query / intent / clarification_context / security_score / security_level / retry_count`（注：legacy `retry_count: int` 是同名保留，与 `retry_counters: dict` 区分）—— adapter 不动这些字段；下次 aput 写入仍带原样。

#### adapter 流程
1. 读 checkpoint dict
2. 抽 `schema_version`：
   - `== CURRENT_SCHEMA_VERSION` → 透传（idempotent）
   - `== LEGACY_SCHEMA_VERSION` 或（缺失 + `is_legacy_checkpoint`）→ 走 legacy 映射表
   - 其他 → `raise MigrationError`
3. legacy 路径：按上表 deterministic rename → 返回新五块结构 + `schema_version=CURRENT`；unmapped 字段保留在 state_dict 顶层
4. 写入侧永远 `CURRENT_SCHEMA_VERSION`（graph 节点返回的 state 已被 migrate 过，LangGraph 自然写入 v2）
5. 缺失字段 → TypedDict `total=False` 缺省值（不抛错）

### 2.5 `app.context` facade（review P0 #1 决议：**兼容路径不转发 runtime**）

#### `backend/app/context/_engine.py`（私有模块，P3 新增）

- **sync 纯函数**：`RECENT_WINDOW / COMPRESS_BATCH / L2_MAX_CHARS / L2_5_MAX_CHARS / L2_ARCHIVE_INTERVAL` 常量 + `format_messages` / `format_context_block` / `archive_to_l2_5` / `compress_and_extract` / `build_context`
- **async helper**（私有，前缀 `_`）：
  - `_prepare_conversation_context(session_id, user_id) -> str`：封装现 `build_session_context` async glue 实质（`get_messages` + `session_manager.get_context_state` + `build_context` + `save_context_state` + `_save_l3_facts`），返回 conversation_context 字符串
  - `_save_l3_facts(user_id, updates, compressed_batch) -> None`：**Legacy Conversation/Memory Glue**（review #9 决议）—— 仍 `from app.infra.memory import mem0_extractor / UserMemory`；**P4 removes this dependency from context package**（伞形 plan §六 V1 简化 + memory-architecture §八 Context Runtime 统一入口落地后，Memory 写入归 Memory Manager）
- 这些函数**不依赖 runtime**；P4 整体搬到 `memory/conversation.py`，runtime 接口不变

#### `backend/app/context/__init__.py`（facade，P3 新增）

- re-export 旧 sync API：`from app.context._engine import build_context, format_messages, format_context_block, compress_and_extract, archive_to_l2_5, ...`
- **定义 `build_session_context` 直调 `_engine._prepare_conversation_context`（绕过 ContextRuntime）**，签名 `async def build_session_context(session_id, user_id) -> str` 与现行为 100% 等价（无 Memory recall 副作用，无 policy.decide 副作用）
- re-export 新 API：`ContextRuntime / context_runtime / AgentContextPolicy / ContextPolicyResolver / ContextBundle / RecallItem / RecallDecision / LegacyFallbackPolicy / ContextDecisionPolicy`
- 模块顶部加 `warnings.warn(..., DeprecationWarning, stacklevel=2)`：提示新代码优先 `from app.context.runtime import ContextRuntime`，但旧 import 路径仍 100% 可用
- `__all__` 明确导出

#### 兼容性保证
- `from app.context import build_context, build_session_context, format_messages, format_context_block` 行为等价；facade 走 _engine 路径，**不**触发 `MemoryManager.recall()` / `ContextDecisionPolicy.decide()` 任何副作用（review #10 侧效应测试验证）
- 4 文件 6 处 import 不需任何改动（含 `format_context_block` 三处实际调用：`sql_graph.py:351,498` / `requirement_parser.py:124`）
- 新 API 调用方在 P3 通过 contract test 验证；P4 选择迁移 graph 节点

### 2.6 调用方现状（review P0 #1 决议后保持）

调用方（4 文件 6 处）走 `app.context.build_session_context` 兼容路径：
- `app/agent/sql_graph.py:93`
- `app/agent/requirement_analysis_graph.py:203, 212, 216`
- `app/agent/confirmed_execution_graph.py:196, 198`
- `app/agent/requirement_parser.py:16`

**P3 不改任何调用方**——facade `build_session_context` 直调 _engine 路径，副作用 100% 等价。新 API `ContextRuntime.build()` 在 P3 通过 contract test 验证；P4 选择迁移部分 graph 节点（如 Requirement Agent 接新 runtime 走 selective recall）。

## Files to change

### 新增（13 个）

| 路径 | 用途 |
|---|---|
| `backend/app/context/__init__.py` | facade：re-export + DeprecationWarning + `build_session_context` 直调 `_engine._prepare_conversation_context` |
| `backend/app/context/_engine.py` | 旧 `context.py` 内容迁入；含 sync 纯函数 + `_prepare_conversation_context` async helper + `_save_l3_facts`（**Legacy Memory Glue**） |
| `backend/app/context/runtime.py` | `ContextRuntime` 入口类（5 步编排，**新 API，P3 不接入 graph**） |
| `backend/app/context/decision.py` | `RecallDecision` / `ContextDecisionPolicy` Protocol / `LegacyFallbackPolicy` |
| `backend/app/context/policy.py` | `AgentContextPolicy` 枚举 + `ContextPolicyResolver.resolve()` |
| `backend/app/context/assembler.py` | `RecallItem` TypedDict + `ContextBundle` TypedDict + `ContextAssembler` |
| `backend/app/state/__init__.py` | 导出 5 TypedDict + `split_state` / `merge_state` |
| `backend/app/state/blocks.py` | 五块 TypedDict 定义 + 辅助函数 |
| `backend/app/state/checkpoint_adapter.py` | `LEGACY_SCHEMA_VERSION` / `CURRENT_SCHEMA_VERSION` / `MigrationError` / `is_legacy_checkpoint` / `migrate_checkpoint` / `inject_schema_version` / 映射表 |
| `backend/tests/contracts/test_context_runtime_contract.py` | 新 API 接口契约测试（含 LegacyFallbackPolicy 行为） |
| `backend/tests/contracts/test_state_blocks_contract.py` | TypedDict 字段名 + split/merge round-trip（含 unmapped 保留） |
| `backend/tests/contracts/test_checkpoint_adapter_contract.py` | legacy fixture / v2 dict / unknown shape 三场景（含 `MigrationError` raise） |

### 修改（5 个，**含 graph 入口节点 (γ) migrate 注入**）

| 路径 | 变更模式 |
|---|---|
| `backend/app/agent/requirement_analysis_graph.py` | 入口节点 `_intent_analyze` 首行加 `state = migrate_checkpoint(dict(state))` |
| `backend/app/agent/confirmed_execution_graph.py` | 入口节点（`_load_draft` 或 `_confirmed_sql_agent`，P3 实施时按 `compile_graph` 起始节点确认）首行加 `state = migrate_checkpoint(dict(state))` |
| `backend/app/agent/sql_graph.py` | 入口节点 `_intent_analyze`（CLAUDE.md §13 现役共用入口）首行加 `state = migrate_checkpoint(dict(state))` |
| `backend/app/agent/data_graph.py` | 入口节点首行加 `state = migrate_checkpoint(dict(state))`（P3 实施时确认是否 graph 起点；若不是入口节点则不改） |
| `backend/app/agent/report_graph.py` | 入口节点首行加 `state = migrate_checkpoint(dict(state))`（P3 实施时确认） |

> **graph 入口注入是 ≤5 行变更，不是 5 处重构**——每处仅 1 行 `migrate_checkpoint` 调用，**不**改任何 state 字段访问方式 / 节点逻辑。

### 删除（1 个）

| 路径 | 原因 |
|---|---|
| `backend/app/context.py` | Python 不允许同目录 module 与 package 并存；内容已迁入 `app/context/_engine.py`，外部 import 通过 `app/context/__init__.py` facade 走通，零调用方改动 |

### 不变（CLAUDE.md §13 / §17 + State zero-intrusion + LangGraph infrastructure 不重造）

- `backend/app/infra/checkpoint/factory.py`（review #8 决议：**不**造 SchemaVersioningSaver wrapper，factory 出口保持原样）
- `backend/app/legacy/**`（CLAUDE.md §13 P15 删除；legacy graph 走旧 schema 不接入 migrate）
- `backend/app/infra/memory/**`（P4 落地；P3 `_engine._save_l3_facts` 仍依赖 `infra.memory` 作为兼容 glue）
- 所有 graph 节点对 `state["xxx"]` 字段的访问方式（零侵入：contract §一不变）
- `build_session_context` 4 处调用方（兼容路径不转发 runtime）

## Reused existing utilities（复用优先）

| 复用对象 | 路径 | 复用方式 |
|---|---|---|
| L1/L2/L2.5 压缩逻辑 | `backend/app/context/_engine.py`（从旧 `context.py` 迁入） | runtime 第 3 步 + facade 第 2 步调 `_engine._prepare_conversation_context` |
| `MemoryManager.recall()` | `backend/app/infra/memory/memory_manager.py` | runtime 第 4 步直接调用；返回 string 1:1 包装为 `RecallItem`（review P0 #2 决议：不假装 structured） |
| `SessionManager.get_context_state` / `save_context_state` | `backend/app/infra/checkpoint/session.py` | `_engine._prepare_conversation_context` 通过既有接口读 digest 状态 |
| `infra.conversation.repository.get_messages` | `backend/app/infra/conversation/repository.py` | `_engine._prepare_conversation_context` 拉历史消息 |
| Checkpointer 工厂 | `backend/app/infra/checkpoint/factory.py` | **不**改动（review #8 决议）；migrate 通过 graph 入口注入 |
| `SchemaContext` / `QueryResult` / `ErrorDetail` / `RequirementCard` | `backend/app/models/` | 五块 State 字段类型直接复用 |
| `format_context_block()` | `backend/app/context/_engine.py` | P3 不重写；caller（`sql_graph.py:351,498` / `requirement_parser.py:124`）经 facade 继续复用 |
| legacy import freeze 断言 | `backend/tests/contracts/test_legacy_import_freeze.py` | 保持绿；P3 不动 legacy |
| `test_session_context_state.py` | `backend/tests/persistence/` | 保留；P3 不重写 digest 持久化语义 |

## Verification（端到端验证）

### 单元测试（不需 DATABASE_URL）
```bash
cd backend
pytest tests/contracts/test_context_runtime_contract.py -v
pytest tests/contracts/test_state_blocks_contract.py -v
pytest tests/contracts/test_checkpoint_adapter_contract.py -v
```

### 集成测试（DATABASE_URL 必需）
```bash
pytest tests/persistence/test_checkpoint_compat.py -v       # 新增
pytest tests/persistence/test_session_context_state.py -v   # 回归
```

### 全量离线（CLAUDE.md §15 红线）
```bash
cd backend && pytest   # 475+ passed 不回退
# 跳过 e2e（CLAUDE.md §15 e2e P12 前手动门）
```

### 冒烟矩阵（review #10 修订，**6 项**）

1. **Legacy side-effect 隔离**（review P0 #1 + #10 决议）：
   - mock `MemoryManager.recall = raise AssertionError("recall must not be called in legacy path")`
   - mock `ContextDecisionPolicy.decide = raise AssertionError(...)`
   - 调 `app.context.build_session_context(session_id, user_id)` → **不抛错**（证明 facade 直调 _engine 绕过 runtime）
   - 调 `ContextRuntime.build(session_id, user_id, query, agent)` → **抛 AssertionError**（证明新 API 走 runtime 真引入 recall 副作用）
2. **import 兼容**：`from app.context import build_context, build_session_context, format_messages, format_context_block` 仍可用，行为等价（实现迁到 `app.context._engine`，facade re-export）；触发 `DeprecationWarning`，但不强制。验证 caller 端 4 文件 6 处 import 零修改。
3. **新 API contract**：`ContextRuntime.build(...)` 返回 ContextBundle 结构正确；`LegacyFallbackPolicy.decide()` 入参 `agent_policy` 被 resolver 正确解析；`RecallItem` 1:1 包装 string（不解析）。
4. **legacy import freeze**：`pytest backend/tests/contracts/test_legacy_import_freeze.py` 仍 0 命中。
5. **Forbidden Patterns 自查**：CLAUDE.md §2 十条逐条对照（详见附录 B）。
6. **新 State 不破坏 graph**：`pytest backend/tests/graphs/` 全过（含 `test_sql_generation.py` / `test_requirement_analysis.py` / `test_confirmed_execution.py`）。

### 持久化冒烟（DATABASE_URL 必需）

- 真实 `AsyncPostgresSaver` 写入 v1 shape fixture → 启动 graph 跑入口节点 → `migrate_checkpoint` 自动转 v2 → 下次 aput 写入带 `schema_version="v2"`
- 同 fixture 第二次跑时 checkpoint 顶层带 `schema_version="v2"`，`migrate_checkpoint` 透传（idempotent）
- 喂一个 unknown shape（缺 `schema_version` + 不含 `active_sub_agent` 与 `original_query` 并存）→ `migrate_checkpoint` 抛 `MigrationError`
- dev 环境（MemorySaver）走相同路径，验证两种 saver 行为一致

### Golden Set
```bash
pytest backend/tests/golden/   # CLAUDE.md §十四 16/0/4 不回退
```

### 手动 API（可选，CLAUDE.md §十五 e2e P12 前手动门）
```bash
curl -X POST http://localhost:8100/api/v1/chat -d '{"user_query":"...","session_id":"smoke","mode":"new"}'
# 验 SSE 流正常；session 上下文注入与 P2 基线等价
```

## Explicitly NOT doing（review #9 + 各项边界）

| 不做 | 归属 | 理由 |
|---|---|---|
| Selective Recall 触发条件（历史引用/长期偏好/业务定义/Query 相似）判定器 | P4 | memory-architecture §九 / CLAUDE.md §6 现状行 |
| Semantic/Query/Conversation Memory 底座归位（`memory/{semantic,query,conversation}.py`） | P4 | memory-architecture §九；P3 仅建 Context Runtime 骨架 |
| `semantic_entry` 表字段扩展（scope/confidence/status/source/session_id/expires_at） | P4 | memory-architecture §六 Lifecycle |
| `_engine._save_l3_facts` 从 `infra.memory` 解耦 | P4 | review #9 决议：P3 标 **Legacy Conversation/Memory Glue**；P4 把 Memory 写入归 Memory Manager 后移除 context 包对 `infra.memory` 的直接依赖 |
| `MemoryManager.recall()` 原生返回 structured records | P4 | review P0 #2 决议：P3 用 `RecallItem` 1:1 包装现有 string 输出，不假装 structured |
| Behavior promotion pipeline（candidate/evidence_count/promotion） | 暂缓 | 伞形 plan §六 V1 简化（行为证据不自动入长期记忆） |
| Confidence 让 LLM 自己拍 | 不做 | 伞形 plan §六 V1 简化（规则固定） |
| Agent-specific 精细化 Prompt 注入策略 | P4 | P3 仅枚举 `AgentContextPolicy` 骨架 |
| Token Budget 算法（context 长度裁剪） | P4 / Evaluation 数据驱动 | P3 仅留接口与默认不裁剪 |
| Conflict Resolution 完整算法 | P4 | P3 留接口与默认按 memory-architecture §七 固定序拼接 |
| 迁移 legacy `AgentState` 全部 20+ 字段到五块 | 不做 | state-contract §三「不迁移，随 P15 删除」；P3 adapter 仅 deterministic 1:1 映射 |
| State 字段语义重设计（`intent → normalized_query` 等） | 不做 | review P1 #3：contract 字段语义不变；unmapped 字段保留 state_dict 顶层 |
| LangGraph 节点对 state 的 dict 访问方式重写 | 不做 | 零侵入；五块是 logical view；P3 仅在 graph 入口加 1 行 migrate（不是访问重写） |
| 造 `SchemaVersioningSaver` 完整代理 `BaseCheckpointSaver` | 不做 | review #8 决议：LangGraph 22 个方法，wrapper 实现易 bypass；P3 用 (γ) graph 入口节点单点 migrate |
| 实现 State 单写者 enforcement / 跨块依赖声明 | P4 起 | review #4 决议：P3 是 static ownership contract；enforcement 留各 graph 后续 phase |
| Conversation Engine（L1/L2/L2.5）实现迁移到 `memory/conversation.py` | P4 | P3 通过 facade 保留 `app.context._engine` 实现 |
| 触碰 legacy graph（`parent_graph.py` 等） | 不做 | CLAUDE.md §13 / §17 |
| 新建 `utils2/ managers2/ runtime2/ helpers/ common2/` 类 generic 文件夹 | 不做 | CLAUDE.md §2 Forbidden Patterns |
| 绕过 MCP 直连 RAG 内部机制 | 不做 | CLAUDE.md §2 |
| 让 Agent 直接访问 Memory DB | 不做 | CLAUDE.md §2 |
| P2 任务（Task 4 mcp-contract.md / Task 5 README 索引登记） | 不做 | P2 plan 范围内；P3 不越界 |
| 调整 Forbidden Patterns 十条 | 不做 | CLAUDE.md §2 冻结 |

## 附录 A：P3/P4 接口契约图（review 修订）

```text
P3（本 plan）
├── 新 API：ContextRuntime.build(...)              ← P3 contract test 验证，graph 不接入
├── 兼容 API：build_session_context(...)           ← 直调 _engine，graph 6 处调用零改动
├── ContextDecisionPolicy Protocol                  ← P4 入口
├── LegacyFallbackPolicy（本 plan，忽略 agent_policy） ← P3 默认
├── RecallItem TypedDict（1:1 包装，不解析）        ← P4 MemoryManager 原生 structured 后扩字段
├── State 五块 TypedDict 视图（static contract）    ← P4 起 graph 节点 enforcement 单写者
└── checkpoint_adapter（v1/v2 + MigrationError）    ← (γ) graph 入口节点 migrate 一行

                              ↑ 调用
P4（后续 plan）
├── SelectiveRecallPolicy 实现 ContextDecisionPolicy（按 agent_policy 分流）
├── MemoryManager.recall 原生返回 list[RecallItem]（structured records）
├── _engine._save_l3_facts 解耦 infra.memory 直接依赖 → 写入归 MemoryManager
├── memory/{conversation,semantic,query}.py 落地
├── semantic_entry 表字段扩展（migration）
├── graph 节点选择性接入 ContextRuntime.build(...)（按 Agent Policy 迁移）
└── State 单写者 enforcement / 跨块依赖声明（contract §二落地）
```

## 附录 B：与 CLAUDE.md Forbidden Patterns 自查

| 条款 | P3 是否触及 |
|---|---|
| 不直接 import RAG 项目代码 | 否（仅复用现有 `app.context` / `infra.memory` / `infra.checkpoint`） |
| 不让 Agent 自拼 Context | **部分**——P3 新 API `ContextRuntime` 已立契约但 graph 未接入；P4 完成入口切换后此条完全生效 |
| 不让 Agent 直接访问 Memory DB | 否（`MemoryManager.recall` 仍为唯一入口，P4 维持） |
| 不让 Agent 直接调用 provider SDK | 否（`_engine.compress_and_extract` 仍走 `call_llm`，P3 不动；P6 统一 LLM Adapter 时收口） |
| 不让 Tool 没有 description | 否（不涉及 Tool） |
| 不让 Report Agent 编造数据 | 否（不涉及 Report Agent） |
| 不无限 retry | 否（不涉及 retry 逻辑） |
| 不绕过 MCP 直连 RAG 内部机制 | 否（不涉及 RAG / MCP） |
| 不新增 legacy import | 否（P3 不动 legacy；facade 不增加新 legacy 依赖） |
| 不新建 `utils2/ managers/ runtime/ helpers/ common2/` | 否（`context/` 与 `state/` 目录为伞形 plan §六冻结路径） |

9/10 完全通过；「不让 Agent 自拼 Context」P3 立契约，P4 完成入口切换后完全生效。
