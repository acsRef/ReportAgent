# Review: 多 Agent 间的状态隔离与上下文污染

> 状态: 只读评审

> **Follow-up plan（对应的 fix 实施）**：[2026-07-30-cross-agent-state-fix.md](2026-07-30-cross-agent-state-fix.md) — 把本 review 中 C-1 ~ C-9 编排成 9 步可执行任务。

不修改任何代码，仅基于实地探查（`backend/app/agent/{parent_graph,requirement_analysis_graph,confirmed_execution_graph,sql_graph,data_graph,report_graph}.py` + `backend/app/main.py` + `backend/app/infra/trace/sdk.py` + `backend/app/infra/db/postgres.py` + `backend/app/llm.py` + `backend/app/tools/registry.py`），围绕用户的三个问题做汇报：

1. 子 agent 的状态如何不污染父 agent（或反过来）？
2. 多请求在同 session 上并发时，是否会互相覆盖？
3. LLM prompt 是否会随时间膨胀、压爆上下文窗口？

---

## 子图 schema ↔ 父图 schema：字段落盘是否安全？

### 各图 State 字段对齐表

| 字段 | parent.AgentState | req-analysis.State | confirmed-exec.State | sql.State | data.State | report.State |
|---|---|---|---|---|---|---|
| `user_query` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `user_id` | ✓ | ✓ | ✓ | **—** | **—** | **—** |
| `session_id` | ✓ | ✓ | ✓ | **—** | **—** | **—** |
| `trace_id` | ✓ | ✓ | ✓ | **—** | **—** | **—** |
| `requirement_card` | ✓ | ✓ | ✓ | **—** | **—** | **—** |
| `schema_context` | ✓ | ✓ | ✓ | ✓ | — | — |
| `chosen_tool` | ✓ | — | — | ✓ | — | — |
| `query_plan` | ✓ | — | — | ✓ | — | — |
| `retry_counters` | — | — | — | ✓ | — | — |
| `confirmed_requirement` | — | — | — | ✓ | — | — |
| `error`（**类型不一致**）| `ErrorDetail` | `ErrorDetail` | `ErrorDetail` | **`str`** | — | — |

### 结论

**字段落盘**：LangGraph 对未声明的 key 默认**静默丢弃**。但经 `confirmed_execution_graph.py:165-178`、`requirement_analysis_graph` 调用点逐一核对，目前**所有父图传入子图的字段都有声明**——没有静默 drop。

**真正的隐患在两处**：

1. **`error` 类型不一致**：`sql_graph.SQLAgentState.error: Optional[str]`，而父图都是 Pydantic `ErrorDetail`。边界处用 `error: None` 屏蔽。如果未来 sql_graph 子图真的写了字符串进 `state["error"]`，父图 `_confirmed_sql_agent:189` 取它出来构造 `ErrorDetail(message=...)`——目前唯一可行路径是 None，不会触发，但**这是定时炸弹**。
2. **`query_result` 在子图边界类型错位**：父图传 dict（`model_dump()`），`ReportAgentState.query_result` 声明 `Optional[QueryResult]`。三处防御性 `QueryResult(**qr_raw)` 解析垫着（`report_graph.py:36, 79, 136`）——形态一旦变就静默失效。

---

## 并发场景：同一 session_id 上两个请求会怎样？

### 关键发现：仅 legacy 模式不安全

| 模式 | 图实例 | MemorySaver | 同 session 并发安全？ |
|---|---|---|---|
| **legacy** (`mode=legacy`) | **模块全局 singleton** `_agent = build_parent_graph()` at `main.py:177` | **共享** `MemorySaver()`，按 `thread_id=session_id` 分桶 | **不安全** |
| `requirement_analysis` | `build_requirement_analysis_graph()` 每次重建（`main.py:293`） | **每次新** `MemorySaver()` | 安全 |
| `confirmed_execution` | `build_confirmed_execution_graph()` 每次重建（`main.py:378`） | **每次新** `MemorySaver()` | 安全 |
| `confirm` (POST `/sessions/{sid}/confirm`) | `build_confirmed_execution_graph()` 每次重建（`main.py:773`） | **每次新** `MemorySaver()` | 安全 |

### Legacy 模式的具体污染路径

`main.py:539-540`：

```python
_agent.update_state(config, {"chosen_tool": request.chosen_tool})   # 请求 A
snapshot = _agent.get_state(config)                                  # 请求 B 也跑同一行
```

- `_agent` 是同一对象
- `config["configurable"]["thread_id"]` 都是 `session_id`
- MemorySaver dict 同一桶，`thread_id` 同 key

**两个 SSE 流并发时**：A 在 `interrupt()` 中等用户选工具，B 注入 `chosen_tool` → `update_state` 写入；A 醒来读 `get_state` 看到的是 B 的 `chosen_tool`，**A 的中断上下文被覆盖**。同 session 同时间发两条 legacy chat 即可复现。`clarification_history`、`current_query`、`pending_card` 都会跨流污染。

**`thread_id` 不带 `user_id` 是设计漏洞**：用户 A 和用户 B 在同一 session_id 上的 MemorySaver 不隔离。`chat()` 顶层有 `user["id"]` 校验，但 MemorySaver 层没有。

### 其他并发向量

- **PG 连接池双路**：asyncpg 池 `max_size=10` (`postgres.py:20`) vs. sync `_get_pg_conn()` 每次新开 psycopg2。两条路径**互不协调**，都会撞 PG `max_connections`（默认 100）。24 个 LangGraph 线程 × 4 次重试 = 96 个 socket 高并发很常见
- **`_local` tracer dict 双重污染**：
  1. 每个 trace_id 唯一——独立 bucket，**不**互相串——但 `llm.py:99` 的 `for _t in _local.values(): _t.add_llm_call(...)` 兜底逻辑会让**任何在途请求的 LLM 调用同时被记到所有 tracer**，span attribution 错位
  2. `_local.pop(trace_id)` 仅在 `flush()` 内（`sdk.py:104`）。handler 在 `finally` 之前抛错 → tracer 永驻 dict，**内存泄露**
- **`MemorySaver` per-request 是把双刃剑**：每次重建保证「并发请求不互相污染 state」，但 also **state 不持久**——`interrupt()` 跨请求继承被破坏。`parent_graph.py:485` 的 legacy `interrupt()` 依赖单一 MemorySaver 才能跨请求记忆 clarify round，所以**这两个需求天然冲突**：要么安全（每请求重建）要么能跨请求（共享 saver），legacy 模式选了后者就承担了污染

---

## LLM prompt 是否会随时间膨胀爆窗口？

### 字段级审视

| 来源 | 当前上限 | 风险 |
|---|---|---|
| `parent._classify_intent` 的 `memory_context` | `top_k_queries=2 + top_k_preferences=3`，每行 `[:80]/[:120]` ≈ 600 chars | ✓ 已限 |
| `requirement_parser._PARSE_PROMPT` 的 `schema_text` | 当前 10 表 ≈ 几 K；50 表 × 30 列 ≈ 50K | ⚠ 业务增长时爆 |
| `sql_graph._plan` 的 `confirmed_requirement` | `_format_confirmed_requirement` (`confirmed_execution_graph.py:196-222`) 拼 `card.assumptions` 原始文本，**无单条长度 cap**。Pydantic `Assumption.text` 上限 300，100 条假设 = 30K | ⚠ 恶意构造可爆 |
| `sql_graph._plan` 的 `schema_text` | 全表 × 全列 | ⚠ 同 requirement_parser |
| `sql_graph._generate_sql` 重试 prompt | `prev_sql` + `prev_validation["error"]`——**单轮 prompt 替换而非列表追加**（`sql_graph.py:370`），不累积 | ✓ |
| `_format_tools_for_prompt` (`llm.py:30-62`) | `registry.all_tools()` 全量拼，每条 200-500 chars。当前 9 条 ≈ 3K；50 条 × 1K = 50K | ⚠ registry 增长时爆 |
| `clarification_history`（`parent_graph.py:36, 492`） | `list` 无上限；`_clarify` 节点 line 497 把上轮答案追加进 `current_query`：`current_query + "\n\n补充信息: {answer}"` ——**每次 clarify 后用户查询文本不断被嵌入**。MemorySaver 持久化，5 轮就 5× | ⚠ 跨多轮可爆 |

### 真实风险

1. **`clarification_history` 累积没有截断**——legacy 模式 + interrupt 流程用，跨多轮会让 `current_query` 字符串随轮次线性增长
2. **`_format_tools_for_prompt` 无 cache**——每次 `call_llm`（含中断后 resume 的 plan / generate_sql / plan_analysis / clarify）都重拼 tools 块
3. **`confirmed_requirement.assumptions` 无单条切片**——`_format_confirmed_requirement` line 196-222 是 `for a in accepted: parts.append(...)`，无 truncation

---

## 值得跟踪的偶发风险（不在本次 review 焦点内）

- **`MemorySaver` 改为 `PostgresSaver`** 是 CLAUDE.md 已标注的生产改进——不修就**永远**回到「MemorySaver per-request / shared-singleton」的二分法
- **`asyncio.CancelledError` 不被 `except Exception` 抓**：`main.py` 多处；客户端断连后 session phase 卡 `generating`（review P-3）
- **`MAX(version)+1` 在 READ COMMITTED 下竞态**：并发 confirm 返 500（review B-7）
- **`_server_start_time` 模块全局 `import`-时赋值**：`uvicorn --reload` 重启时这个时间戳会变，影响 `_server_start_time` 比对逻辑（review P-2）

---

## 按优先级排序的真实污染向量

| 等级 | 编号 | 位置 | 机制 | 触发 |
|---|---|---|---|---|
| **HIGH** | C-1 | `main.py:177` + `parent_graph.py:542` | legacy `_agent` 模块全局 + 共享 `MemorySaver` 按 `thread_id` 分桶（无 user_id） | 同 session_id 并发 legacy 请求，或用户 A/B 持同 session_id |
| **MEDIUM** | C-2 | `parent_graph.py:36 + 492 + 497` | `clarification_history` 无上限；`current_query` 累积旧答案 | 同一会话 ≥5 轮 clarify，prompt 超出 LLM 上下文窗口 |
| **MEDIUM** | C-3 | `confirmed_execution_graph.py:196-222` | `_format_confirmed_requirement` 无单条 truncate | 用户接受 ≥100 条假设；或恶意假设文本（每条 300 chars） |
| **MEDIUM** | C-4 | `infra/trace/sdk.py:15` + `llm.py:99` | `_local` 是 dict 共享；`for _t in _local.values()` 兜底 | 任意异常路径未到 `flush()` → 内存泄露；并发请求 span attribution 错位 |
| **MEDIUM** | C-5 | `postgres.py:20` + `sql_tools.py:59` | asyncpg `max_size=10` vs sync `_get_pg_conn()` 每次新开 psycopg2 互不协调 | 5+ 并发请求触发 `PG max_connections` 上限 → `connection` 错 |
| MEDIUM | C-6 | `sql_graph.py:74` vs `parent_graph.py:50` | `error` 字段类型 `str` ↔ `ErrorDetail` 不一致 | 未来 sql_graph 写非 None error 时父图构造 ErrorDetail 触发类型边界 bug |
| LOW | C-7 | `requirement_parser._PARSE_PROMPT` (line 36) | `schema_text` 无截断，DB schema 增长时爆 | 加列、加表 |
| LOW | C-8 | `llm._format_tools_for_prompt` (line 30-62) | registry 全量拼、无 cap | tool 数增长 |
| LOW | C-9 | `confirmed_execution_graph.py:189` + `report_graph.py:18, 36, 79, 136` | `query_result` dict ↔ `QueryResult` 类型错位，依赖三处防御解析 | 解析 shape 改 |

---

## 修复方向（按 ROI，不在本轮做）

| 优先级 | 编号 | 修复 |
|---|---|---|
| P0 | C-1 | legacy mode 删 `_agent` 单例 → 每次 `build_parent_graph()` 并用 `PostgresSaver`；或 `MemorySaver` key 改 `(thread_id, user_id, request_id)` + `update_state`/`get_state` 加 asyncio.Lock per thread |
| P1 | C-2 | `clarification_history` 滚动窗口（`max_turns=5`），老的合并或丢弃；`current_query` 拼接前 truncate 到 2K |
| P1 | C-3 | `_format_confirmed_requirement` 每条 assumption `a.text[:200]`、整段 `total_chars[:4000]` 截断 |
| P1 | C-4 | `_local` 改 `ContextVar`；`llm.py:99` 兜底改为 `tracerlocal.get({})` → 当前 ContextVar；`flush()` 之前如果有异常走 `try/finally` 也清 |
| P1 | C-5 | `_get_pg_conn()` 走 `asyncio.run_in_executor(loop, sync_psycopg2_connect)` + 同池；或 `psycopg2_pool.Pool`（同步专用池），与 asyncpg 共用一个 `max_connections` 预算 |
| P2 | C-6 | 把 `SQLAgentState.error` 改 `Optional[ErrorDetail]`；或加 boundary 转换函数 |
| P2 | C-7 | `data_tools._TABLES` 描述截断到 200 字 / 列 |
| P2 | C-8 | `_format_tools_for_prompt` 缓存 + 按调用方裁剪（plan 只需 schema 工具、generate_sql 只需 sql 工具） |
| P2 | C-9 | `query_result` 在 boundary 处统一 `QueryResult.model_validate(qr_raw)`，子图内禁止接受 dict |

---

## 本轮不做

- **任何代码改动**——这是 review，不是 patch
- **不展开为独立 PR**——C-1 ~ C-9 是 9 个互相独立的小改造，每个 30-100 行工作量。等用户决策开哪几个 plan
- **不与已落地的 plan 合并**——这次发现和 `confirmed-exec-three-state`、`sql-row-cap-and-export`、未来的 `backend-async-refactor` 都是平行的横向话题
