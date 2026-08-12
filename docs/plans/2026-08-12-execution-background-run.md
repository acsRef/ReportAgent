# 2026-08-12-execution-background-run

> 状态: 已完成

## Context

**触发**：用户提出「前端终止对话时后端链路是否仍保持连接」——担心点「停止生成」或断连后回答不完整。逐层读码后确认现状机制：

SSE 响应（`EventSourceResponse`）由 uvicorn+Starlette 的 `listen_for_disconnect` 在客户端断连时取消响应任务 → `asyncio.CancelledError` 在 `event_generator` 当前 await 点抛出 → 传播进 `graph.ainvoke` → **撞哪断哪**：

- confirm/adjust 流有 `except asyncio.CancelledError`（P-3，标记 session error 后 re-raise），但 graph 可能停在任何中间节点（LLM 一半、persist_report 前）→ 半成品状态：draft 已 locked、report_version 缺行、session phase 残留。
- requirement-analysis 流无 CancelledError 处理 → session 可能停在 `parsing`。
- 同步 SQL 节点（`sql_execute`）在 executor 线程，取消断不掉，后台继续跑完（最多 30s `statement_timeout` 兜底）。

「撞哪断哪」不可控：取消到达时机决定后端行为，用户担心的「回答不完整」无法保证。现状成功路径还不写 `current_phase`（snapshot 的 phase 停在 `generating`）。

**用户决策（2026-08-12 brainstorming）**：

1. **取消语义 = 后台跑完**：graph 执行从 SSE 响应解耦成独立后台任务，断连只断前端渲染，任务继续跑到 `persist_report` 落库；重新打开会话能看到完整报告。
2. **并发 = 拒绝新请求**：session 有后台任务未完成时，报告路径返回 409 BUSY。
3. **范围 = 只解耦报告路径**（`/confirm`、`/retry`、`mode=adjust`）；requirement-analysis 保持同步流 + 补断连清理；legacy 不动（已待退休）。
4. **前端 = 停止后轮询通知**：复用 `GET /sessions/{sid}` snapshot，5s 轮询 phase 变化。

## Design

### 核心：ExecutionRegistry（进程内任务注册表）

新模块 `backend/app/infra/execution/registry.py`。模块级 `dict[session_id → ConfirmedTask]`（asyncio 单线程，无需锁）。

```python
class BusyError(Exception):
    """同 session 已有未完成的后台任务。HTTP 层映射 409 SESSION_BUSY。"""

class ConfirmedTask:
    session_id: str
    user_id: int
    kind: str                  # "confirm" | "adjust"
    task: asyncio.Task         # 独立后台任务——不在 SSE 响应任务的取消域内
    events: asyncio.Queue      # 完成事件队列（report/error/done）
    finished: bool
    result: list[dict] | None  # 最终事件序列（迟到订阅者幂等读取）
    started_at: datetime

def start_confirmed_task(session_id, user_id, kind, runner) -> ConfirmedTask:
    """runner: async (task: ConfirmedTask) -> None，内部负责 ainvoke + 事件入队 + phase 更新。
    已存在未完成条目 → raise BusyError。
    已存在完成条目 → 覆盖（每 session 至多一条）。"""

def get_confirmed_task(session_id) -> ConfirmedTask | None
```

**为什么能后台跑完**：`asyncio.create_task()` 创建的任务不会被创建它的响应任务的取消级联取消（除非显式 `cancel()` 或同 task group）。uvicorn 断连只取消 `event_generator`（响应任务）；后台 task 独立运行到 `persist_report` 落库。同步 SQL 线程池的查询照常跑完——「断不掉」从缺陷变成特性。

**惰性清理**：`start_confirmed_task` 时清除本 session 已完成条目（覆盖）；任务 finished 后保留供迟到的订阅者读取，进程重启即清（结果已落库，无一致性风险）。

### 事件流（SSE 契约与现状逐事件一致）

**后台任务 runner**（main.py 内新 helper `_run_confirmed_graph(task, graph, initial, session_id)`）：

1. `initial` state 在启动任务**前**构造（含 `trace_id`——在 `create_task` 前生成，不能等 SSE 回调）。
2. `graph.ainvoke(initial, config)`，整体 try/except：
   - `execution_status == FAILED` → `_build_sse_error` 事件 + `update_phase(session, "error", failed_action="sql")`
   - 成功 → `report` 事件（payload 同现状）+ **`update_phase(session, "report_ready")`**（新增：现状成功路径不更新 current_phase，前端轮询依赖此值）
   - `SecurityRejectedError` / `RequirementIncompleteError` / `SessionNotFoundError` → 对应 error 事件 + phase 更新
   - 其他异常 → INTERNAL error 事件 + phase；**绝不静默**（否则订阅者队列空、前端挂死）
   - `asyncio.CancelledError`（进程关闭/显式取消）→ error 事件（code `CANCELLED`）+ finished
3. 末尾：`task.finished = True`；`task.result = [report|error 事件, done 事件]`；`tracer.flush()`

**SSE event_generator 订阅逻辑**（confirm/retry/adjust 三处统一为 helper `_subscribe_events(task, phase_label)`）：

- `task.finished` → 从 `task.result` 依次 yield（迟到订阅者，幂等）
- 未完成 → `yield phase:generating|adjusting` → `await task.events.get()` → yield 完成事件 → yield done
- 客户端断连 → CancelledError → `except asyncio.CancelledError: raise`（**不**标记 session——任务还在后台跑，phase 由任务完成时写）

### 并发：409 BUSY

- 三处入口（`confirm_session` / `retry_session` / `_chat_confirmed_execution`）在构造 initial 前调 `start_confirmed_task`；`BusyError` → `HTTPException(409, "SESSION_BUSY")`（SSE 流开始前，与 `SESSION_NOT_FOUND` 的 404 同模式）。
- requirement-analysis（new/supplement）**不检查**——等报告时开新分析是合理行为，只在 confirm 阶段拦截。
- 跨实例双跑由现状 DB draft 锁（`lock_for_execution`）兜底；本 plan 假设单实例（与现状一致）。

### requirement-analysis 补丁

`_chat_requirement_analysis` 补 `except asyncio.CancelledError: raise`（显式声明断连语义，不再依赖 `except Exception` 漏过的隐式传播）。需求分析不更新 phase（现状即如此），断连后停在 `parsing`，但 chat 端点不检查 phase、重发消息即覆盖——功能不阻塞，仅补显式处理。

## Files to change

- `backend/app/infra/execution/registry.py`（**新**）：`ConfirmedTask` / `BusyError` / `start_confirmed_task` / `get_confirmed_task` + 惰性清理。
- `backend/app/main.py`：
  - `confirm_session` / `retry_session` / `_chat_confirmed_execution`：接入 registry，抽 `_run_confirmed_graph`（后台 runner）+ `_subscribe_events`（订阅式 event_generator）两个 helper，三处共用。
  - `_chat_requirement_analysis`：补 `except asyncio.CancelledError: raise`。
  - `_run_confirmed_graph` 内成功路径补 `update_phase(session_id, "report_ready")`（现状缺失，本 plan 顺带修复）。
- `frontend/src/api/confirmStream.ts`：SSE 流开始前 409 分支 → `toast.warning('该会话正在后台生成中，请稍候')`；`reader.read()` 循环包 try/catch，`AbortError` 静默 return（修现状 abort 中途冒泡成「PATCH 失败」toast / unhandled rejection）。
- `frontend/src/pages/WorkbenchPage.tsx`：`handleStop` 语义改「停止显示」——abort 后 toast「已停止显示，报告仍在后台生成」+ 启动轮询；新增 `useBackgroundExecutionPolling`（5s 间隔 `fetchSessionSnapshot`，phase 变 `report_ready`/`error` → toast「报告 vN 已生成」+ `refreshVersionsAndSelectLatest` + 停轮询）。
- 测试：
  - `backend/tests/contracts/test_execution_registry.py`（新，`-m contracts`）：启动 / 二次启动 BusyError / 完成释放 / 覆盖 / 异常不挂死。
  - `backend/tests/api/test_confirm_background.py`（新）：mock graph——409 BUSY、后台完成落库（`update_phase` 被调）、迟到订阅者拿到完整事件、取消任务入队 CANCELLED。
  - `frontend/src/api/__tests__/confirmStream.test.ts`：409 分支、mid-stream AbortError 静默返回。
  - `frontend/src/pages/__tests__/WorkbenchPage.test.tsx`：轮询 hook 相位检测 / 停止 / toast。

## Reused existing utilities

- `session_manager.update_phase`（backend/app/infra/checkpoint/session.py）——任务完成写 phase，原样复用。
- `snapshot_service.get_session_snapshot`（backend/app/services/snapshot_service.py）——前端轮询端点，**零新端点**。
- `_build_sse_error` / `_normalize_sql_snippet`（backend/app/main.py）——error 事件构造，原样复用。
- `get_tracer` / `tracer.flush()`（backend/app/infra/trace/sdk.py）——任务内 flush。
- `build_confirmed_execution_graph`（backend/app/agent/confirmed_execution_graph.py）——图本身不动。
- 前端 `fetchSessionSnapshot`（api）、`refreshVersionsAndSelectLatest`（WorkbenchPage 现有）——轮询与完成刷新。

## Verification

自动化：

```bash
cd backend && pytest tests/contracts/test_execution_registry.py -v
cd backend && pytest tests/api/test_confirm_background.py -v
cd frontend && npm run test:run
cd backend && pytest          # 全量离线回归，确认既有测试无破坏
```

手动矩阵（PG + 后端 :8100 + LLM key）：

1. 发起 confirm → 点「停止生成」→ 前端提示「已停止显示，报告仍在后台生成」→ 任务完成时轮询 toast「报告 vN 已生成」→ 版本列表出现新版本、可打开完整报告（SUCCESS/EMPTY/FAILED 三态之一如实呈现）。
2. 停止后立即刷新页面 → snapshot `phase=report_ready` + 最新 report version，报告完整。
3. 停止后（任务未完成）立即重新 confirm → 409 toast「该会话正在后台生成中」；任务完成后重试成功。
4. 停止后立即发起新需求分析（mode=new）→ 正常分析不拦截；confirm 时任务未完成 → 409。
5. requirement-analysis 期间断连 → 重发消息覆盖正常，无半截状态。
6. 后台任务异常（临时停 DB）→ 轮询收到 error、session `phase=error`、无挂死。

## Explicitly NOT doing

- **不做显式 `POST /cancel` 接口**——语义已定为后台跑完，「取消」= 停止显示。
- 不改 legacy 流（已待退休）。
- 不后台化 requirement-analysis（保持同步 + 补 CancelledError）。
- 不改同步 SQL 执行层（`sql_tools.py` 线程池 / `statement_timeout`）——后台跑完语义下现状即正确。
- 不改 draft 锁生命周期（locked 状态恢复行为维持现状；如 persist 后 locked 阻塞后续 PATCH 属现状既有问题，另开 plan）。
- 不做多实例 409（进程内注册表，单实例假设；DB 锁兜底跨实例安全）。
- 不做前端静默 SSE 重连（轮询已够）。

## 落地记录

**实现**：
- 新 `backend/app/infra/execution/registry.py`：`ConfirmedTask` / `BusyError` / `start_confirmed_task` / `get_confirmed_task` / `complete`；`asyncio.create_task` 独立任务 + `events` 完成信号队列 + `result` 事件序列；wrapper 兜底（异常/取消/忘 complete 均唤醒订阅者，不静默挂死）；TTL 惰性清理。
- `main.py`：三处入口（`confirm_session` / `retry_session` / `_chat_confirmed_execution`）统一走 `_confirmed_initial` + `_start_confirmed_stream` + `_run_confirmed_graph`（后台 runner，成功补 `update_phase(report_ready)`）+ `_subscribe_events`（SSE 订阅，断连自然传播不清理）；`_chat_requirement_analysis` 补 `except asyncio.CancelledError: raise`。
- 前端：`confirmStream.ts` 补 409 分支（toast「该会话正在后台生成中」）+ mid-stream AbortError 静默返回；`WorkbenchPage.tsx` `handleStop` 改「停止显示」语义 + 新增 `useExecutionPoll`（可注入 intervalMs，5s 轮询 snapshot，phase 回 report_ready/error → toast + 刷新版本 + 停）。

**测试**：后端 12 新增（registry 5 + 后台集成 7）全过；前端 15 新增（confirmStream 409/AbortError + 轮询 hook）全过；全量回归 376 passed（2 预存在失败 `tests/smoke/test_schema_faq.py` 与本次无关，git stash 基线验证）+ 前端 256 passed + build 通过。

**真实浏览器验证（playwright + 系统 Chrome）**：打开浏览器 → 登录 → 发消息 → 需求卡 → 接受假设 → 确认生成 → 停止按钮可见（SSE 建立）→ **关闭浏览器** → 后端任务后台跑完（~50s）→ 重新打开浏览器 → 完整报告可见（可视化 华南 41.28% + 数据明细 华南 156989.55 + 报告 v1 + 完成度 100%）。API 层另验证：断开后 phase generating → report_ready、report_version 落库 SUCCESS 5 行；任务进行中再 confirm → 409 SESSION_BUSY；完成后重开 SSE 幂等重放。

**发现（现状既有，未在本 plan 修）**：confirm 成功后 draft 永久 `locked`（`lock_for_execution` 仅在无 report_version 行时重置），导致「同一 draft 重新 confirm」与「adjust 对已执行会话」报 `REQUIREMENT_INCOMPLETE: failed to lock draft`。与本次后台化无关（失败发生在 graph 内部 `_sql_gate`，执行方式未变），另开 plan 处理。