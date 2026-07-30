# Plan: 多 Agent 状态隔离 + 上下文窗口保护 — 全部修复

> 状态: 已完成（C-1~C-4/C-6~C-9；C-5 残留转 async）

> **Based on（依据）**：[2026-07-30-cross-agent-state-safety.md](2026-07-30-cross-agent-state-safety.md) 的 9 个污染向量（C-1 ~ C-9）。
>
> **Related**：[2026-07-30-backend-async-refactor.md](2026-07-30-backend-async-refactor.md)（Step 7 与该 plan 的 PG 连接池/async 化话题重叠；本 plan 选方案 A「保留 sync + ThreadedConnectionPool」，async-refactor 选方案 B「psycopg2→asyncpg」。两份不同时实施——见下「落地节奏」）、[2026-07-30-legacy-sql-bugs.md](2026-07-30-legacy-sql-bugs.md)（Step 1 与该 plan 关于 SQL 子图 error 反馈丢失的话题有部分重叠）。
>
> **可逆性**：HIGH。Step 8 改 legacy 全局行为 + Step 6 改 tracer 边界规约。回滚粒度建议拆 3 个 PR（见文末），每个 PR 单独 revert 不影响其他。

## 背景（Context）

桌面上对 ReportAgent 多 agent 拓扑做了一次彻底的污染审查，记录在 `2026-07-30-cross-agent-state-safety.md` 中。审查发现 9 个真实向量（C-1 ~ C-9），其中 C-1 是 HIGH（legacy 模式并发跨请求污染），其余 5 个 MEDIUM（C-2 ~ C-6）、3 个 LOW（C-7 ~ C-9）。

这一份**修复 plan**（不是 review）：把 9 个向量按依赖顺序编排成可执行步骤，每步给出**文件:行、修改方式、复用工具、新增测试、明确不做**。

### 依赖顺序（从底层到外层）

```
Step 1  C-6  SQL 子图 error 类型错位          — 改 SQLAgentState.error 类型
Step 2  C-7  requirement_parser schema 截断   — schema_text 加 cap
Step 3  C-3  _format_confirmed_requirement    — assumption 列表加单条 + 整段 cap
Step 4  C-8  _format_tools_for_prompt         — registry cache + 按调用方裁剪
Step 5  C-2  clarification_history 滚动窗口   — parent_graph.py clarify 加 max_turns
Step 6  C-4  Trace _local 改 ContextVar       — sdk.py + llm.py:99
Step 7  C-5  sync psycopg2 走线程池/共享池     — sql_tools 改造
Step 8  C-1  legacy _agent 单例 + MemorySaver — main.py + 选 PostgresSaver
Step 9  C-9  query_result 边界统一 validator   — report_graph 类型边界收敛
```

Step 8 在最末——它最大程度依赖前 7 步的边界规整。

---

## Step 1 — `SQLAgentState.error` 与父图类型对齐 (C-6, MEDIUM)

> **可逆性**：N/A（类型修复，不改语义）。

**问题**：`backend/app/agent/sql_graph.py:74` 声明 `error: Optional[str]`，父图 `ConfirmedExecutionState` / `parent_graph.AgentState` / `requirement_analysis_graph.State` 全部是 `Optional[ErrorDetail]`。当前唯一可行路径是 `_evaluate` 写 `error: str` 然后父图用——父图 line 189 是 `sub_error = ss.get("error")` 直接当 ErrorDetail model_validate。如果未来 SQL 节点真的写非空 str 进 error，子图→父图边界类型错位会无声 cast 失败。

**修改**：

1. `backend/app/agent/sql_graph.py:74`：`error: Optional[str]` → `error: Optional[ErrorDetail]`（注意 Pydantic BoundaryAdapter）
2. 同步把 `error: None` 在 import 路径显式从父图传入而非完全省略——让 LangGraph schema 校验器看到类型一致的默认值
3. `_evaluate`（line 412-441）所有 return 增加 `error=ErrorDetail(...) if error else None`

**复用**：`app.models.contracts.ErrorDetail`（已有 kind/message/code 字段，无需新 schema）。

**新增测试** `backend/tests/test_sql_limits.py`：

- `test_sql_graph_error_is_errordetail_instance`：mock `_classify_psycopg2_error` 返回 `("timeout", msg)`，跑完整 `sql_graph.ainvoke` → 最终 `state["error"]` 是 `ErrorDetail` 实例
- `test_sql_graph_error_kind_propagates`：验证 ErrorDetail.kind 完整传到主图

**不做**：不改 `_evaluate` 决策逻辑——已有 kind→message 中文映射正确。

---

## Step 2 — `requirement_parser._PARSE_PROMPT` schema_text 加 cap (C-7, LOW)

**问题**：`backend/app/agent/requirement_parser.py:36-66` 的 `_PARSE_PROMPT` 用 `{schema_text}` 占位，调用点 line 76 `_schema_text(schema)` 拼接 `f"表 {t.name} ({t.description}):\n" + "\n".join(...)`。10 表×30 列 × 200 字列描述 = 60K，未截断。

**修改**：

1. 在 `_schema_text(schema)` 函数（`requirement_parser.py` 实现 line 70-82 一带）加 cap：
   ```python
   def _schema_text(ctx) -> str:
       out = []
       for t in ctx.tables:
           cols = []
           for c in t.columns:
               desc = (c.description or "")[:120]   # 新增：单列 desc cap
               cols.append(f"  {c.name} ({c.type}){(' — ' + desc) if desc else ''}")
           desc = (t.description or "")[:160]     # 新增：单表 desc cap
           out.append(f"表 {t.name}{(' — ' + desc) if desc else ''}:\n" + "\n".join(cols))
       text = "\n".join(out)
       if len(text) > 8000:
           text = text[:8000] + "\n...（已截断，请按需 query）"
       return text
   ```
2. 顶层常量：`MAX_SCHEMA_CHARS = 8000`（放 `requirement_parser.py` 模块顶部）

**复用**：`TableSchema.description` / `ColumnSchema.description` 已存在字段。

**新增测试** `backend/tests/graphs/test_requirement_parser.py`（新文件）：

- `test_schema_text_truncates_to_max_chars`：mock schema 含 100 列 × 200 字 desc → `len(_schema_text()) <= 8100`
- `test_schema_text_truncates_per_column_descriptions`：单列 desc 1000 字 → 输出列描述 ≤ 120 字
- `test_schema_text_handles_missing_descriptions`：所有 desc 为空 → 不抛

---

## Step 3 — `_format_confirmed_requirement` assumption 加 cap (C-3, MEDIUM)

**问题**：`backend/app/agent/confirmed_execution_graph.py:196-222` 把 `card.assumptions[i].text` 整段拼接到 `"用户已接受的假设 = [...]; "`。无单条 cap、无整段 cap。恶意或长尾场景可爆 prompt。

**修改**：

```python
MAX_ASSUMPTION_TEXT = 200       # 单条假设 cap
MAX_ASSUMPTION_TOTAL = 2000     # 假设区块总 cap
MAX_FIELD_VALUE_CHARS = 300     # 每个 other 字段的 value cap

def _format_confirmed_requirement(card) -> str | None:
    if card is None:
        return None
    parts: list[str] = []
    if card.time_range:
        parts.append(f"时间范围 = {card.time_range[:MAX_FIELD_VALUE_CHARS]}")
    if card.scope:
        scope = ", ".join(s[:80] for s in card.scope[:20])
        parts.append(f"数据范围 = [{scope}]")
    if card.target_metrics:
        metrics = ", ".join(m[:80] for m in card.target_metrics[:10])
        parts.append(f"核心指标 = [{metrics}]")
    if card.dimensions:
        dims = ", ".join(d[:80] for d in card.dimensions[:10])
        parts.append(f"分析维度 = [{dims}]")
    if card.analysis_methods:
        methods = ", ".join(m[:80] for m in card.analysis_methods[:10])
        parts.append(f"分析方法 = [{methods}]")
    if card.assumptions:
        accepted = [a for a in card.assumptions if a.accepted is True]
        if accepted:
            texts = []
            for a in accepted:
                t = (a.text or "")[:MAX_ASSUMPTION_TEXT]
                texts.append(t)
            joined = "; ".join(texts)
            if len(joined) > MAX_ASSUMPTION_TOTAL:
                joined = joined[:MAX_ASSUMPTION_TOTAL] + "..."
            parts.append("用户已接受的假设 = [" + joined + "]")
    if not parts:
        return None
    return "\n".join(parts)
```

**复用**：`RequirementCard.assumptions` 已有 `text` 字段；`RequirementCard` 在 `app/models/requirement.py`。

**新增测试** `backend/tests/graphs/test_confirmed_planner.py`（如不存在则建）：

- `test_format_assumptions_truncates_per_text`：单条 1000 字 → 输出截 200 字
- `test_format_assumptions_truncates_total`：100 条 × 100 字 → joined 截 2000 字并加省略号
- `test_format_does_not_include_unaccepted_assumptions`：混合 accepted/rejected → 只 accepted

---

## Step 4 — `_format_tools_for_prompt` cache + 按调用方裁剪 (C-8, LOW)

**问题**：`backend/app/llm.py:30-62` 每次 `call_llm` 都重拼 `tools_block`，全量 registry。当前 9 条 3K；50 条 × 1K = 50K，无 cap 无 cache。

**修改**：

1. `app/llm.py:30` 抽 cache key：(tool_signature, caller_capability_set)
2. 新增 `format_tools_for_prompt(caller: str, capability_filter: set[str]) -> str` 入参
   - `caller` ∈ `{"intent_analyze", "plan", "generate_sql", "plan_analysis", "clarify", "parse_requirement"}`
   - 每个 caller 维护一个白名单 capability → tool name
3. 工具的 5 元素描述限制到 5 行 / 500 字（tool registry 改动 `<description>` 时校验）

```python
_TOOL_CACHE: dict[tuple[str, frozenset[str]], str] = {}

def format_tools_for_prompt(caller: str, capability_filter: frozenset[str]) -> str:
    key = (caller, capability_filter)
    if key not in _TOOL_CACHE:
        tools = registry.get_for_capabilities(capability_filter)
        _TOOL_CACHE[key] = "\n\n".join(_format_one_tool(t) for t in tools)
    return _TOOL_CACHE[key]
```

4. `app/tools/registry.py` 增加 `get_for_capabilities(caps: set[str]) -> list[Tool]`（已有 `registry.get(caps)` 借用）
5. 各调用点显式传 caller 与 capability_filter：
   - `_intent_analyze`（`sql_graph.py:114`）：`"intent_analyze", {"intent_classification"}`
   - `_plan`（`sql_graph.py:257`）：`"plan", {"sql_planning"}`
   - `_plan_analysis`（`report_graph.py:51`）：`"plan_analysis", {"data_analysis"}`
   - `_clarify`（`parent_graph.py:483`）：`"clarify", {"clarification"}`
   - `parse_requirement`（`requirement_parser.py:78`）：`"parse_requirement", {"requirement_parsing"}`

**复用**：`app.tools.registry.registry.get(caps)` 已有 capability→tool 索引。

**新增测试** `backend/tests/test_llm.py`（新文件）：

- `test_format_tools_for_prompt_caches_result`：第二次调用同 key 返回同一对象（identity equal）
- `test_format_tools_for_prompt_filters_by_capability`：把 `chart_advisor` 挪到 `data_analysis` capability，`caller=plan` 不应包含
- `test_format_tools_for_prompt_per_caller_independent`：同样 capability 不同 caller 缓存分离

**约束**：单 tool description 5 元素总长 cap 500 字（实施时机看 Step 4 之外；本次不改 registry，只把限制接口搭好）。

---

## Step 5 — `clarification_history` 滚动窗口 (C-2, MEDIUM)

**问题**：`backend/app/agent/parent_graph.py:36, 492, 497`：
- line 36 `clarification_history: list = []`
- line 492 `clarification_history.append({"q": current_q, "a": answer})`
- line 497 `current_query = f"{current_q}\n\n补充信息: {answer}"`（**累积**）

跨多轮无 cap。

**修改**：

```python
# parent_graph.py 顶部常量
CLARIFY_MAX_TURNS = 5
CLARIFY_QUERY_MAX_CHARS = 2000

# line 497 改为：
truncated_answer = (answer or "")[:200]
pruned_history = state.get("clarification_history", [])[-(CLARIFY_MAX_TURNS - 1):]
pruned_history.append({"q": current_q[:200], "a": truncated_answer})
new_query = f"{current_q[:CLARIFY_QUERY_MAX_CHARS]}\n\n补充信息: {truncated_answer}".strip()
if len(new_query) > CLARIFY_QUERY_MAX_CHARS:
    new_query = new_query[-CLARIFY_QUERY_MAX_CHARS:]
return {
    ...
    "clarification_history": pruned_history,
    "current_query": new_query,
    ...
}
```

`_clarify` 函数完整重写时只动 line 492-503。

**复用**：`parent_graph.AgentState.clarification_history` 现有。

**新增测试** `backend/tests/graphs/test_parent_graph_clarify.py`：

- `test_clarify_caps_history_length`：跑 10 轮 → `len(history) == CLARIFY_MAX_TURNS`
- `test_clarify_caps_current_query`：单轮 answer 100K 字 → `current_query <= CLARIFY_QUERY_MAX_CHARS`

---

## Step 6 — Trace `_local` 改 ContextVar (C-4, MEDIUM)

**问题**：`backend/app/infra/trace/sdk.py:15` 的 `_local: dict[str, Tracer]` 是 module-global。`llm.py:99` 的 `for _t in _local.values(): _t.add_llm_call(...)` 让任意在途请求的 LLM 调用被记到所有 tracer。同时 `flush()` 失败时 `_local.pop` 不发生，内存泄露。

**修改**：

1. `app/infra/trace/sdk.py:15` 改为 `contextvars.ContextVar`：
   ```python
   import contextvars
   _current_tracer: contextvars.ContextVar[Optional[Tracer]] = contextvars.ContextVar(
       "current_tracer", default=None
   )
   ```
2. `get_tracer(trace_id, ...)` 兼容现有调用点，但返回 Tracer 不再带 key lookup——直接用 ContextVar：
   ```python
   def get_tracer(trace_id=None, ...) -> Tracer:
       existing = _current_tracer.get()
       if existing is not None:
           return existing
       t = Tracer(trace_id or uuid.uuid4().hex, ...)
       _current_tracer.set(t)
       return t
   ```
3. `traced_node` 的 wrapper（`sdk.py:114-141`）改为 `token = _current_tracer.set(tracer); try: ... finally: _current_tracer.reset(token)`
4. `flush()` 内 `_local.pop` 全部移除——彻底用 ContextVar 后不存在 key 释放问题
5. `llm.py:99` 的兜底改为：
   ```python
   tracer = _current_tracer.get()
   if tracer is not None:
       tracer.add_llm_call(...)
   ```
   ——只贴 ContextVar 那个 tracer，不再 fan-out 到所有

**复用**：`contextvars` Python 标准库。

**新增测试** `backend/tests/test_trace_sdk.py`：

- `test_contextvar_propagates_through_async_ainvoke`：两个并发 `ainvoke(trace_id=A)` 与 `ainvoke(trace_id=B)`，每个 span 写到自己的 tracer，**不**串
- `test_contextvar_resets_after_flush`：tracer.flush() 后 ContextVar 为 None
- `test_llm_call_only_attached_to_current_tracer`：mock call_llm 两次（不同 contextvar 下），每个 tracer 只看到自己的 LLM call

**API 变更影响**：`flush()` 不再从 `_local` 移除——但调用方语义不变（仍然能 flush 一次）。`_local` 字段整体删除。`MemorySaver` 等旧依赖无 `_local` 引用——grep 确认只 `sdk.py` 内部用。

**注意**：`asyncio.to_thread` 不会跨 contextvar；如果某个 sync 节点被线程池跑（langgraph `coerce_to_runnable` 的 `run_in_executor`）——需要 `contextvars.copy_context()` 包装。本次 Step 6 仅处理 SDK，线程池边界问题归 Step 7 顺带处理。

---

## Step 7 — sync psycopg2 走共享池或线程池 (C-5, MEDIUM)

**问题**：`backend/app/tools/sql_tools.py:59` 的 `_get_pg_conn()` 每次 `psycopg2.connect(...)` 新建连接，与 asyncpg pool (`postgres.py:20`, max_size=10) 互不协调——并发撞 PG `max_connections`。

**修改方案 A（最小）**：用 `psycopg2.pool.ThreadedConnectionPool` 替换 `_get_pg_conn()`。

```python
# sql_tools.py 顶部
_pg_pool = None

def _get_pool():
    global _pg_pool
    if _pg_pool is None:
        _pg_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2, maxconn=10,
            dsn=PG_DSN,
            connect_timeout=CONNECT_TIMEOUT_S,
            options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
        )
    return _pg_pool

def _get_pg_conn():
    return _get_pool().getconn()

def _close_pg_conn(conn):
    try:
        _get_pool().putconn(conn)
    except Exception:
        try: conn.close()
        except Exception: pass
```

修改 `validate_sql` / `execute_sql`：
```python
def validate_sql(sql):
    safe, msg = check_sql_safety(sql)
    if not safe:
        return json.dumps({"valid": False, "error": msg}, ...)
    conn = _get_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"EXPLAIN {sql}")
        return json.dumps({"valid": True, ...})
    except Exception as exc:
        return json.dumps({"valid": False, ...})
    finally:
        _close_pg_conn(conn)
```

**复用**：`psycopg2.pool.ThreadedConnectionPool` 标准库；连接参数（DSN、timeout）已存在。

**新增测试** `backend/tests/test_sql_limits.py`：

- `test_get_pg_conn_returns_pooled_connection`：连续 100 次调用 → 实际 TCP 连接数 ≤ `maxconn`
- `test_pool_closes_after_invalidation`：mock 连接出错 → 池返回该连接但不抛
- `test_validate_and_execute_reuse_pooled_connections`：连续跑 5 次 `_get_pg_conn` → 同一 pool

**风险**：
- `psycopg2.pool.ThreadedConnectionPool` 是同步池——线程安全但慢。async event loop 上用 `pool.getconn()` 走的是 sync 阻塞。当前 sync node 在 LangGraph 线程池里跑——没问题。
- 池大小和 asyncpg pool 大小需要协调：建议 sync 池 `maxconn=10` + asyncpg `max_size=10` 共 20，预留 PG `max_connections=100` 余量。

**方案 B（更彻底但工作量更大）**：把 sync 节点都转 async + 用 asyncpg（属于 `backend-async-refactor.md` 范围）。Step 7 仅做方案 A。

---

## Step 8 — legacy `_agent` 单例 + MemorySaver (C-1, HIGH)

**问题**：`backend/app/main.py:177` **和 line 477** 两个位置都是 `_agent = build_parent_graph()` 模块全局（life-span 阶段与 `_chat_legacy` 入口处各一次），都共享 `parent_graph.py:542` 的 `MemorySaver`。同 session 并发 legacy 请求互相污染（`main.py:539-540` `update_state` / `get_state` 跨流竞争）。**注意：line 477 是 lazy-init (`if _agent is None:`) 但仍走同一单例，不是修复方案。**

**修改方案 A（推荐，最小破坏）**：每请求 `build_parent_graph()` + MemorySaver。

```python
# main.py 删 _agent 全局
# _chat_legacy 内：
async def _chat_legacy(request: ChatRequest, req: Request, user: dict):
    if request.mode == "legacy":
        agent = build_parent_graph()  # 每请求新建
        ...
```

MemorySaver 自动按 process 内 dict 增长——进程生命周期内累积几十个 saver 对象是 OK 的（每个 <1KB），关键是不会跨请求污染 state。

**修改方案 B（生产推荐）**：改 `PostgresSaver`——CLAUDE.md 已标注。需要 PG schema 改动 + 启动初始化。**本轮不做**，留作独立 PR。

**复用**：`langgraph.checkpoint.memory.MemorySaver`（已存在）。

**新增测试** `backend/tests/legacy/test_parent_graph_isolation.py`：

- `test_two_concurrent_requests_independent_state`：起两个 task `asyncio.gather(_chat_legacy(...) A, _chat_legacy(...) B)` 同 session_id，每个最终有独立 `pending_card` 状态
- `test_legacy_request_does_not_pollute_global`：触发 `interrupt()` 后立刻并发第二个请求，第二个不读到第一个的 `chosen_tool`

**Step 6 → Step 8 顺序的依赖**：Step 6 解决 tracer 隔离，Step 8 解决编译图隔离。两者平行。这一步同时验证 Step 6 的 ContextVar 在并发 astream_events 下正确传播。

**遗留风险**：`_chat_legacy` 的 `_intent_analyze` `pre-emptive` 注入 (`main.py:539` `_agent.update_state(config, {"chosen_tool": request.chosen_tool})`) 现在变成 `agent.update_state(...)`——per-request agent 是新对象，没问题。

---

## Step 9 — `query_result` 边界统一 validator (C-9, LOW)

**问题**：`confirmed_execution_graph.py:189` 传 `qr.model_dump()` dict 到 `report_graph.a_invoke`，子图 `ReportAgentState.query_result: Optional[QueryResult]`。3 处防御性 `QueryResult(**qr_raw)` 解析（`report_graph.py:36, 79, 136`）。

**修改方案 A（推荐）**：保留 dict，在子图入口处一次性 validator，三处防御解析合并为一个 helper。

```python
# report_graph.py 顶部加
def _validate_qr(qr_raw):
    if qr_raw is None:
        return None
    if isinstance(qr_raw, QueryResult):
        return qr_raw
    return QueryResult.model_validate(qr_raw)

# 三处用法统一改成：
qr = _validate_qr(state.get("query_result"))
```

**复用**：`pydantic.BaseModel.model_validate` 已支持。

**新增测试** `backend/tests/test_report_graph.py`：

- `test_validate_qr_handles_dict`：dict 入参 → QueryResult 实例
- `test_validate_qr_handles_pydantic`：QueryResult 入参 → 返回原对象
- `test_validate_qr_handles_none`：None → None
- `test_validate_qr_raises_on_invalid_shape`：dict 缺 columns 字段 → ValidationError

**风险**：Pydantic `model_validate` 与 `**qr_raw` 不同——`model_validate` 会做类型强制（dict 字段允许）而 `**qr_raw` 严格字段-名匹配。前者更宽容。**利好**——一次性收敛边界。

---

## 文件改动（Files to change）

| 步骤 | 文件 | 行 | 动作 |
|---|---|---|---|
| 1 | `backend/app/agent/sql_graph.py` | 74, _evaluate 处 | `error: Optional[str]` → `Optional[ErrorDetail]` |
| 2 | `backend/app/agent/requirement_parser.py` | _schema_text + 顶部常量 | 加 cap |
| 3 | `backend/app/agent/confirmed_execution_graph.py` | _format_confirmed_requirement line 196-222 | assumption 与各字段 cap |
| 4 | `backend/app/llm.py` | _format_tools_for_prompt + 各调用点 | 缓存 + caller 参数化 |
| 4 | `backend/app/tools/registry.py` | `get_for_capabilities` 已有 | 复用，不改 |
| 5 | `backend/app/agent/parent_graph.py` | _clarify line 492-503 + 顶部常量 | history / current_query cap |
| 6 | `backend/app/infra/trace/sdk.py` | 整个 `_local` + `get_tracer` + `traced_node` wrapper | ContextVar 化 |
| 6 | `backend/app/llm.py` | line 99 fan-out 逻辑 | 只贴 current_tracer |
| 7 | `backend/app/tools/sql_tools.py` | _get_pg_conn + 调用点 + 顶部 `_pg_pool` | ThreadedConnectionPool |
| 8 | `backend/app/main.py` | `_agent` 全局 + `_chat_legacy` line 496 | 每请求 build_parent_graph |
| 9 | `backend/app/agent/report_graph.py` | 顶部 helper + 三处调用替换 | `_validate_qr` |

**测试文件**：

- `backend/tests/test_sql_limits.py`：扩 Step 1 + Step 7 用例
- `backend/tests/graphs/test_requirement_parser.py`（新）：Step 2 用例
- `backend/tests/graphs/test_confirmed_planner.py`（若不存在则新）：Step 3
- `backend/tests/test_llm.py`（新）：Step 4
- `backend/tests/graphs/test_parent_graph_clarify.py`（新）：Step 5
- `backend/tests/test_trace_sdk.py`（新）：Step 6
- `backend/tests/legacy/test_parent_graph_isolation.py`（新）：Step 8
- `backend/tests/test_report_graph.py`（新）：Step 9

## 复用工具（Reused existing utilities）

- `app.models.contracts.ErrorDetail`、`QueryResult`、`RequirementCard.assumptions` — Step 1, 3, 9
- `app.tools.registry.registry` 已带 capability 索引 — Step 4
- `app.infra.db.postgres.get_pool()` (asyncpg) — Step 7 的姊妹池，与 sync 池协作
- `langgraph.checkpoint.memory.MemorySaver` — Step 8（仍用 in-memory，本轮不切 PostgresSaver）
- `contextvars` Python stdlib — Step 6
- `psycopg2.pool.ThreadedConnectionPool` stdlib — Step 7
- `pydantic.BaseModel.model_validate` — Step 9

## 验证（Verification）

每步完成后，**全套测试必须仍绿**：

```bash
cd backend && pytest --ignore=tests/e2e -q    # 当前 90 → 目标 110+
cd frontend && npm run lint && npm run test:run    # 241 ✓
cd backend && REPORTAGENT_E2E=1 pytest tests/e2e/test_full_flow.py -s    # e2e 仍通过
```

### 端到端人工矩阵（Step 8 后必跑）

| 场景 | 期望 | 验证 |
|---|---|---|
| legacy 模式同 session 两个并发 chat | 两个流程独立完成，无跨流污染 | `test_two_concurrent_requests_independent_state` + 手动开两个 tab |
| legacy `interrupt()` 后再 chat | 第一轮中断状态丢失，新 chat 干净开始 | 手动 verify |
| 修改 50 个 assumption，每条 200 字 | LLM 收到截断后的假设区块，总 cap 2000 字 | `test_format_assumptions_truncates_total` |
| 修改 schema 加 100 列 × 200 字 desc | parser 收到截断 | `test_schema_text_truncates_to_max_chars` |
| 一个请求同时 in-flight，tracer A、B、C 并存 | 每个 tracer 只看到自己的 span/LLM call | `test_contextvar_propagates_through_async_ainvoke` |
| 100 个并发 `_get_pg_conn` | TCP socket ≤ pool maxconn | `test_get_pg_conn_returns_pooled_connection` |

### 性能 baseline（可选）

Step 7 前后各跑一次并发 20-请求对比，记录 P50/P95 延迟。同步连接池应该让 PG 端 `max_connections` 错误消失。

## 明确不做（Explicitly NOT doing）

- **不切 PostgresSaver**：留在 CLAUDE.md 标注项，独立 PR
- **不改 LangGraph async 重构**：步骤 6 顺带为它铺路（ContextVar 已经准备好），不展开
- **不动 `backend-async-refactor.md` 中 15 个 enum（"P0 阻塞" 错估）**：独立 PR（已被 review 标记）
- **不动 security review / prompt injection**：与本次 review 平行，不混入
- **不动 frontend**：本轮纯后端
- **不引入新依赖**：`contextvars`、`psycopg2.pool`、`ThreadedConnectionPool` 都是 stdlib
- **不修 B-7 `MAX(version)+1` 竞态**：review 已立项 `2026-07-30-query-execution-safety-and-reporting.md` 层 6，独立 PR
- **不动子图 TypedDict 加 `trace_id` 声明**（review B-3）：与 Step 6 ContextVar 化目标部分重叠，但子图 state 改造需要更细的 LangGraph 验证，独立 PR

---

## 落地节奏建议（PR 切分）

按 ROI 与风险分散到 3 个 PR：

| PR | 步骤 | 改动量 | 风险 | 影响 |
|---|---|---|---|---|
| **PR-1 类型 + 截断** | Step 1, 2, 3, 9 | ~150 行 + 5 文件 | LOW | 关闭数据/类型边界隐患 |
| **PR-2 上下文保护** | Step 4, 5, 6 | ~250 行 + 4 文件 | MEDIUM | 关闭 prompt 膨胀 + tracer 隔离 |
| **PR-3 并发隔离** | Step 7, 8 | ~200 行 + 4 文件 | HIGH-MEDIUM | 关闭并发污染 + PG socket 爆 |

每个 PR 独立 review、回滚粒度清晰。

### PR 之间的依赖约束（执行顺序硬性）

1. **PR-1 必须在 PR-2 之前**：Step 6 改 ContextVar 时 tracer 会读取子图 state；若 Step 1 的 ErrorDetail 类型在子图中已就位，span/llm_call attribution 才不会被 ErrorDetail 序列化打断
2. **PR-3 必须在 PR-1 之后**：Step 8 改 legacy 全局 `_agent`，需要 Step 1 的统一类型先稳定，否则 lazy-init 时拿到的 SQL 子图 state schema 与原版不兼容会爆 KeyError
3. **PR-3 内的 Step 7 与 Step 8 顺序可交换**：但建议 Step 8 在 Step 7 之后——Step 7 的 ThreadedConnectionPool 与 async event loop 兼容性需要先验证，再放开 legacy 全局行为
