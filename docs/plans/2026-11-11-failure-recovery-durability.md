# 失败可恢复性：记录 → 收敛 → 重试（P0–P3）

> 状态: 已完成（2026-11-11）

## Context

用户问的是「节点断了之后，有没有考虑重试、记录下来、再重试」。做了一次破坏性实验（任务跑到 6s 强杀后端）：

| 时刻 | `current_phase` | `last_failed_action` | `report_version` |
|---|---|---|---|
| 杀之前 | generating | NULL | 0 |
| **强杀瞬间** | generating | **NULL** | **0** |
| 重启后 | generating | NULL | 0 |

结论：**重试链路是通的，但「记录」在进程崩溃时是空的。**

已有的三块各自断一环：

| 能力 | 现状 | 断点 |
|---|---|---|
| 节点级重试 | `RETRY_BUDGETS`（SQL 2 / MCP 2 / LLM 2 / plan 1）+ `DiagnosePolicy` 纯函数决策表 | 只在进程活着时有效 |
| 记录 | 每节点 span、`report_version` 三态、`session.last_failed_action` | 崩溃时三者全空；span 是内存缓冲、请求结束才 flush（实测崩溃后只剩 23 个需求阶段 span，`sql_plan` 一个不剩） |
| 再重试 | `POST /sessions/{sid}/retry` | 实测**能救**（被杀的会话 retry 后 45s 出 done）；但前端按钮要求 `phase==='error' && error.recoverable`，而崩溃后 `phase` 卡在 `generating`、没有 error 事件 → 用户看不到任何按钮，轮询永不结束 |

一句话：**这套设计默认进程不会死。** 本 plan 把这条链路补成「进程死了也能记下来、也能接着重试」。

## Design

### P0 启动时孤儿任务收敛（`services/startup_recovery.py`）

启动钩子里扫 `agent.session.current_phase ∈ {parsing, generating, adjusting}`，逐条判定：

| 判定 | 动作 |
|---|---|
| 该 draft 已有 `status='done'` 的报告版本 | 说明任务其实跑完了、只是 phase 没更新 → 只把 phase 修成 `report_ready` |
| 没有 | 落一行 `report_version`（`status=error`，`error_kind=interrupted`）+ `phase=error` + `last_failed_action=confirm` → 前端下次打开就是 ErrorCard + 重试按钮 |

- 复用 `report_version_service.persist_error_run`（不新写落库路径）；
- 新增错误 kind `interrupted`：加进 `SQL_ERROR_KINDS` / `USER_RECOVERABLE_KINDS`（用户重试有意义）、`kind_to_error_code` 映射到 `SQL_EXECUTION_ERROR`、`user_message` 给一句「任务被中断（服务重启），可以重试」；
- **单实例假设写进 docstring**：多实例部署时这个扫描会误杀别的进程正在跑的任务，需要先落「任务归属（owner/lease）」。

### P1 重试计数持久化（`agent.retry_attempt`）

现状 `retry_counters` 只在 graph state 里（dev = MemorySaver）→ 重启归零，**预算可被无限绕过**。

```sql
create table agent.retry_attempt (
  id bigserial primary key,
  session_id varchar(64) not null,
  user_id integer not null,
  requirement_draft_id bigint,
  attempt integer not null,          -- 第几次
  budget_key text not null,          -- sql_generation | plan | mcp_schema | auto
  action text not null,              -- retry_sql | replan | retry_mcp_schema_retrieval | auto_retry
  error_kind text,
  reason text,
  created_at timestamptz not null default now()
);
```

- 写：`sql_graph._diagnose` 在决策是**重试类**时落一行（best-effort，失败只告警）；
- 读：`_confirmed_sql_agent` 进子图前用 `counters_for(session, draft)` 把计数播种进去 → 重启后预算不会被清零；
- 为此给 `SQLAgentState` 加 `session_id` / `requirement_draft_id` 两个透传字段（legacy 路径传空，仓储对空 id 直接 no-op）。

### P2 自动重试（有预算、有退避、可审计）

后台任务以 FAILED 结束且 kind 可恢复时，自动排一次重试：

- 预算 `AUTO_RETRY_MAX`（默认 **1**，0 = 关闭）+ 指数退避 `AUTO_RETRY_BACKOFF_S`（默认 3s，×2 递增）；
- 每次自动重试在 `agent.retry_attempt` 落 `budget_key='auto'` 行，**先记账再跑**（崩溃后不会重复计数）；
- 次数用尽仍失败 → 保持 `last_failed_action='confirm'`，交给人；
- 复用既有 `/retry` 的执行函数（不新开一条执行路径），入队前检查 `registry` 是否有在跑的任务（有就跳过，避免叠加）。

### P3 span 增量落盘（崩溃时保住现场）

`infra/trace/sdk.py`：`save_trace` / `save_span` 都是纯 INSERT，所以增量落盘要**自己记游标**：

- `Tracer` 增加 `_flushed_spans` / `_flushed_llm_calls` / `_trace_saved`；
- `flush_partial()`：首次 `save_trace`，之后只补写游标之后的 span；`llm_call` 同理；
- 触发点放在 `traced_node` 的 finally（那里是 async，且有确切边界）：每累积 `TRACE_FLUSH_EVERY`（默认 **10**）个 span 落一次；`0` = 关闭（回到旧行为）；
- 终态 `flush()` = `flush_partial()` + `update_trace(status/end)` + Langfuse sink；
- 单次增量 flush 也套 `TRACE_FLUSH_TIMEOUT`，DB 卡住不影响主流程。

## Files to change

| 文件 | 改动 |
|---|---|
| `backend/app/services/startup_recovery.py` | 新增：孤儿扫描 + 二分判定 + 落 error / 修 phase |
| `backend/app/main.py` | 启动钩子调用收敛；后台任务结束处接 P2 自动重试 |
| `backend/app/reliability/errors.py` | 新增 `interrupted` kind（分类、用户码、文案、可恢复表） |
| `backend/scripts/init_pg.sql` | 追加 `agent.retry_attempt` 建表（幂等 `if not exists`） |
| `backend/app/infra/db/retry_attempt_repository.py` | 新增：`record()` / `counters_for()` / `count_auto()` |
| `backend/app/agent/sql_graph.py` | `_diagnose` 落重试记录；`SQLAgentState` 加两个透传字段 |
| `backend/app/agent/confirmed_execution_graph.py` | 进子图前播种计数 + 透传 session/draft |
| `backend/app/infra/trace/sdk.py` | `flush_partial()` + `traced_node` 阈值触发 |
| `backend/tests/persistence/test_startup_recovery.py` | 新增：卡住会话 → 收敛后 `phase=error` + 一条 error 版本；已完成会话只修 phase |
| `backend/tests/contracts/test_retry_attempt_repository.py` | 新增：记录/计数/播种 |
| `backend/tests/contracts/test_auto_retry.py` | 新增：可恢复才排、预算用尽不排、在跑时不叠加 |
| `backend/tests/contracts/test_trace_incremental_flush.py` | 新增：阈值触发、游标不重复、关闭时不写 |
| `docs/plans/README.md` | 登记 |

## Reused existing utilities

- `report_version_service.persist_error_run`：P0 的落库（不新开写路径）。
- `report_version_repository.find_done_by_draft`：判定「其实跑完了」。
- `infra/execution/registry.get_confirmed_task`：P2 判断是否已有任务在跑。
- `main.py` 既有的后台执行函数（`_start_confirmed_stream` 的 runner）：P2 复用它，不新写执行器。
- `reliability/errors` 的 kind 表 + `user_message`：P0 新增 kind 就走这套。
- `TraceRepository.save_trace / save_span`：P3 直接复用，只加游标。

## Verification

1. **破坏性实验复现**（本轮的核心验收）：起 confirm → 6s 强杀后端 → 重启 → **期望库里出现一条 `status=error` + `error_kind=interrupted` 的版本、`phase=error`、`last_failed_action=confirm`**；前端打开即 ErrorCard + 重试按钮；点重试能出结果。
2. `pytest tests/persistence/test_startup_recovery.py tests/contracts/test_retry_attempt_repository.py tests/contracts/test_auto_retry.py tests/contracts/test_trace_incremental_flush.py`
3. 崩溃后 span 检查：杀在 `sql_plan` 期间，重启后 trace 里应有需求阶段 + 已完成的 sql 阶段 span（而不是 0 个）。
4. 全量 backend + frontend。
5. 真的点一次「重试」走通端到端。

### 实测结果（2026-11-11）

三次同样的破坏性实验（confirm → 数秒后 `Stop-Process -Force` → 重启）：

| 检查 | 修之前 | 修之后 |
|---|---|---|
| 崩溃后 `current_phase` | generating（永久卡住） | generating → **重启后 `error`** |
| `last_failed_action` | NULL | **`confirm`** |
| `agent.report_version` | 0 行（没留下任何东西） | **1 行 `status=error` / `kind=interrupted` / 「任务在执行中被中断（服务重启），可以直接重试」** |
| 崩溃时落库的 span | 23 个（只有需求阶段，`sql_plan` 一个不剩） | **54 个**（含 `sql_validate` / `sql_execute` / `sql_build_output`），`trace.status=RUNNING` 本身成为「被中断」的证据 |
| 前端 | 永远「生成中」，无按钮 | 会话 pill「生成失败」、报告 v1 标题「任务中断」、右栏「任务未完成」、主区 **「重试当前任务」** 按钮 |

| 检查 | 结果 |
|---|---|
| 定向 pytest（新增 17 条） | 28 passed（契约 12 + 持久化 4 + 单元 12 中的相关项） |
| 全量 backend | **1844 passed / 18 skipped**（基线 1827 → +17） |
| 全量 frontend | **500 passed（60 文件）**，`tsc -b` 0 error |
| 浏览器（真 dev server） | 被中断会话打开即 ErrorCard +「重试当前任务」，0 console error |

两个必须交代的点：

1. **两处旧契约测试被我的改动打红，是我故意的**——`test_reliability_errors` 与
   `test_diagnose_policy_sources` 用断言钉死了 kind 词表，加 `interrupted` 必须显式过一遍。
   已连同「为什么它不在 AGENT_RECOVERABLE 表里」一起写进注释。
2. **P1 的重试记账在本轮真机里没有产生行**（`agent.retry_attempt` = 0）：这几次请求都一次成功，
   没有触发 repair。它的正确性由真 PG 的 roundtrip 测试（`test_retry_attempt_roundtrip`）
   与播种测试保证，而不是靠这次跑通。


## Explicitly NOT doing

- **不做分布式任务队列 / leader election**：P0 的扫描是单实例假设，已在 docstring 标注；多实例需要 owner lease，另开 plan。
- **不做断点续跑**：重试仍是「整轮重跑」，不从中断的节点继续（子图是 `.ainvoke()`、checkpoint 只存父 State）。
- **不做自动重试的无限退避**：默认 1 次，配置化，避免对一个永久坏掉的查询反复烧钱。
- **不改 SSE 协议**：不新增事件类型，沿用 `error` + `done`。
- **不动 legacy 图**：只在 v2 两图链路上生效。
