# P11 Frontend / SSE Contract 实施

> 状态: 已完成
> 落地分支: `p11-frontend-sse`（5 commit: 4f3abe1 plan → e27aae2 backend → d60b842 frontend → 0d24502 docs）
> 上游: [伞形 plan §十六/§P11](2026-08-25-refactor-master-freeze.md) + [frontend-contract.md](../architecture/frontend-contract.md)（P1 冻结）+ [sse-v2.md](../sse-v2.md) + 交接 memory（P9/P10 冻结于 master `2665c94`，后端 925 passed / 前端 259 passed 基线）。

## Context

P11 验收清单（伞形 §392）：`API/SSE event schema 固定`、`前端可显示 Agent progress（Tool/SQL/Repair）`、`Report 正确渲染`、`Error/Empty 状态正确`、`Session resume 正确`。开工审计（逐条对照代码，commit `2665c94`）发现 8 个事实：

| # | Finding | 代码依据 |
|---|---|---|
| F1 | confirm 流「后台跑完→一次性重放」，执行期前端零事件；ProgressCard 靠 650ms 假定时器伪装进度 | `main.py` `_run_confirmed_graph`（events 只在 finally 进 `registry.complete`）+ `registry.py`（queue 只在 complete 时 put None）+ `WorkbenchPage.tsx` setInterval effect |
| F2 | 契约 parser `parseAnalysisSSEEvent` 未接任何流；`analysisClient.ts` / `confirmStream.ts` 各带一份内联 `parseSSEFrame`；七事件中 `trace`/`thinking` 前端不解析（timeline 是死状态） | `analysisEvents.ts`（仅测试引用）+ 两 client 各自 frame 循环 |
| F3 | `report` 事件 wire 形态与 sse-v2 文档（`version/parent_version/title/answer/trace`）不符：`_persist_report` 只 merge `version`；parser 却按 persist 域对象（`id/session_id/report` 键）校验——schema 三方不一致 | `confirmed_execution_graph.py` `_persist_report` 尾部 + `analysisEvents.ts` `isReportVersion` |
| F4 | requirement 流 chitchat 分支：`done.final_phase` 落 `'error'`（`phase` 变量未赋值，`phase if "phase" in dir() else "error"`），且 casual reply 前端无处渲染（openChat 路径无 report 处理） | `main.py` `_chat_requirement_analysis` finally + `WorkbenchPage.tsx` `handleSSEEvent` |
| F5 | adjust 走 `/chat`（`_chat_confirmed_execution` 后台流），其 `report` 事件前端不处理 → 新版本不刷新（refresh 只接在 confirm/retry 流） | `WorkbenchPage.tsx` `handleSSEEvent`（无 report 分支） |
| F6 | Session resume 不恢复 phase（`session/selected` 重置 idle 后靠 `report/received` 副作用伪装 report_ready）；busy 会话（generating/adjusting）恢复后不接后台轮询 | `WorkbenchPage.tsx` `loadSessionSnapshot` |
| F7 | P9-5（P9 Review 记录项）：generic exception 出口 `message=str(exc)[:300]` 原始 provider 异常直达用户；`user_message(kind)` 已存在未接 | `main.py` 两处 generic except + `reliability/errors.py:201` |
| F8 | `timeline` state + `timeline/received` action 无生产者（死状态） | reducer 仅测试引用 |

## Design

### 拍板点（用户 review 后如有异议再调；以下为实施基线）

- **D1 progress 事件形态 = `trace` 载荷细化，不新增 SSE 顶层事件类型。** frontend-contract 原文「tool.* / sql.* / repair.* 由 trace 事件载荷细化」；sse-v2 §解析器白名单不动。trace 载荷升级为 `{ step, status, detail?, kind? }`，`kind ∈ agent|tool|sql|repair|report`；前端按 `kind × status` 推导 progress 族（running→*.started / success→*.completed / error→*.failed）。`agent.thinking ≈ 现 thinking`、`report.generated/updated` 由 report 节点 trace + 既有 report 事件覆盖——契约列举的十事件全部可表达。
- **D2 后端机制 = langgraph callbacks 转发 + registry live 事件队列。** 不改图结构、不引入 astream 重构：`_run_confirmed_graph` 保持 `ainvoke + run_with_timeout`（P9 超时语义不动），config 挂 `AsyncCallbackHandler`；三个子图调用点只转发 `callbacks` 子 dict（不带 `thread_id`——子图无 checkpointer，但只传 callbacks 更窄、无未来隐患）。registry 加 `publish()`（live 留档 + 推给在线订阅者），`complete()` 改为以 `live` 快照为重放源——迟到订阅者拿到全量 progress + 终态。
- **D3 前端事件面统一：transport → schema → dispatch 三层。** `api/sse.ts` 新增共享 `parseSSEFrameRaw`（仅拆帧）；`parseAnalysisSSEEvent` 补齐 `trace/thinking/report` 成为唯一 schema 层并真正接线两条流；`handleSSEEvent` 类型化重写。删除两份内联 `parseSSEFrame`。
- **D4 report wire 形态以 sse-v2 文档为准**：`{ version, parent_version, title, answer, trace? }`。后端 `_persist_report` merged 补 `parent_version/title`（row 已持有）；parser 校验放宽为 wire 形态（`answer` object 必须存在；`version` 可缺——chitchat 回复 `{answer:{text}}` 同一 guard 通过，消费侧按 `version` 是否为 number 分流）。
- **D5 ProgressCard 移除假定时器**，真信号驱动 + stage 单调不减（`Math.max`），保留停止按钮与 5s 轮询「后台跑完」语义不变。

### 事件流（P11 后的 confirm/adjust 流时序，写入 sse-v2.md）

```text
phase(generating|adjusting) → trace(plan, running) → trace(plan, success)
  → trace(generate_sql, running) → ... → trace(diagnose, running)?（修复环）
  → trace(run_step, running/success)* → report{version,parent_version,title,answer} 
  → phase(report_ready) → done{final_phase}
```

### 节点 → progress 映射表（确定性契约，`app/infra/execution/progress.py`）

| node name | kind | 用户文案 | 前端 stage |
|---|---|---|---|
| `plan` | agent | 规划查询 | 1 准备分析数据 |
| `data_agent` | tool | 准备分析数据 | 1 |
| `sql_agent` | agent | 执行 SQL 分析 | 1 |
| `generate_sql` | sql | 生成 SQL | 2 执行分析查询 |
| `validate` | sql | 校验 SQL | 2 |
| `execute` | sql | 执行查询 | 2 |
| `evaluate` | agent | 评估结果 | 2 |
| `diagnose` | repair | 诊断修复 | 2 |
| `report_agent` | report | 生成报告 | 3 组织分析报告 |
| `plan_analysis` | report | 组织报告结构 | 3 |
| `run_step` | report | 撰写报告内容 | 3 |
| `build_output` | report | 汇总报告 | 3 |

不在表内的节点（security_guard / load_confirmed_requirement / sql_gate / persist_report 等）不发事件。handler 带 `_NOISE_NAMES` 过滤（沿 legacy `_format_event` 的 Runnable* 名单）+ 每 node 状态跃迁去重（`run_step` 循环多触发不重复刷屏）。

### 前端 stage 推导（`progressModel.ts`）

`KIND_STAGE: agent|tool→1, sql|repair→2, report→3`；`stageFromTrace(kind,status,current) = status==='error' ? current : max(current, KIND_STAGE[kind] ?? current)`。error 不回退进度（error 事件负责终态）。

## Files to change

| 模式 | 路径 |
|---|---|
| 修改 | `backend/app/infra/execution/registry.py`（publish/live/complete 签名） |
| 新建 | `backend/app/infra/execution/progress.py`（映射表 + `format_progress_frame` + `ProgressTraceHandler`） |
| 修改 | `backend/app/main.py`（handler 装配、子图 callbacks 转发在 graph 侧、generic 出口 `user_message`、chitchat `phase='idle'`、`_DEFAULT_ERROR_EVENTS` 不动） |
| 修改 | `backend/app/agent/confirmed_execution_graph.py`（三个子图调用点签名加 `config` + 只转发 callbacks；`_persist_report` merged 补 parent_version/title） |
| 测试 | `backend/tests/contracts/test_execution_registry.py`、`backend/tests/api/test_confirm_background.py`、新建 `backend/tests/api/test_progress_frames.py` |
| 修改 | `frontend/src/api/sse.ts`（+`parseSSEFrameRaw`）、`frontend/src/api/analysisEvents.ts`（trace/thinking/report wire 形态 + `isAnalysisPhase` 导出）、`frontend/src/api/analysisClient.ts`、`frontend/src/api/confirmStream.ts` |
| 修改 | `frontend/src/types/report.ts`（TimelineEntry + `kind?`）、`frontend/src/components/workbench/progressModel.ts`、`ProgressCard.tsx`、`frontend/src/pages/WorkbenchPage.tsx` |
| 测试 | `analysisEvents.test.ts`、`confirmStream.test.ts`、`progressModel.test.ts`、新建 `ProgressCard.test.tsx`、`WorkbenchPage.test.tsx` |
| 文档 | `docs/sse-v2.md`（trace 载荷 + report 双形态 + confirm 时序）、`README.md`（SSE 事件表若涉）、`CLAUDE.md` §9 现状行、本 plan 落地记录 + 索引翻转 |

## Reused existing utilities

- `reliability/errors.py` `user_message(kind)` / `classify_exception`（F7 只接线不新增）。
- `reliability/timeout.py` `run_with_timeout`（D2 保持其包裹 `ainvoke` 不动）。
- `infra/execution/registry.py` 队列语义（D2 在其上扩展 publish，不重写）。
- legacy `_format_event` 的 Runnable* noise 名单（映射到 progress.py `_NOISE_NAMES`）。
- 前端 `parseAnalysisSSEEvent` 白名单思路（`ANALYSIS_PHASES` → 导出 `isAnalysisPhase` 复用于 resume）。
- `progressModel.ts` `CONFIRM_STAGES/progressPercent/stagePrefix`（保留，只换驱动源）。

## Tasks（TDD：每任务 red→green→commit；命令一律在对应目录内跑）

### T1 backend：registry live 事件（F1 基座）

**Files:** `backend/app/infra/execution/registry.py` + `tests/contracts/test_execution_registry.py`

- [ ] Step 1 red：`test_execution_registry.py` 追加三条——`test_publish_streams_to_subscriber_before_complete`（start 后 publish 两条 → 订阅协程先收到两条再收 None）、`test_complete_replays_live_snapshot`（publish 两条 → complete() → 新订阅者拿同样两条）、`test_result_none_fallback preserved`（runner 未 complete → 订阅者走 `_DEFAULT_ERROR_EVENTS` 兜底，语义不变）。
- [ ] Step 2 跑：`cd backend && D:/miniConda/envs/agent/python.exe -m pytest tests/contracts/test_execution_registry.py -x`，确认新 3 例 FAIL（publish 不存在）。
- [ ] Step 3 实现：

```python
@dataclass
class ConfirmedTask:
    ...
    live: list[dict] = field(default_factory=list)  # P11：已发布事件留档（迟到订阅者重放）

def publish(entry: ConfirmedTask, evt: dict) -> None:
    """执行中发布实时事件：推给在线订阅者并留档。"""
    entry.live.append(evt)
    entry.events.put_nowait(evt)

def complete(entry: ConfirmedTask) -> None:
    """任务正常结束：live 快照成为重放源，唤醒订阅者。"""
    entry.result = list(entry.live)
    entry.finished = True
    entry.events.put_nowait(None)
```

`main.py` `_run_confirmed_graph` finally 改为 `for evt in events: registry.publish(task, evt)` + `registry.complete(task)`（result 不再单独传）；`_subscribe_events` 改为 drain 循环：

```python
if task.finished:
    for evt in task.result or _DEFAULT_ERROR_EVENTS:
        yield evt
    return
yield {"event": "phase", "data": json.dumps({"phase": phase_label}, ensure_ascii=False)}
while True:
    evt = await task.events.get()
    if evt is None:
        break
    yield evt
```

- [ ] Step 4 同步修 `tests/api/test_confirm_background.py` 中 `complete(...)` 旧签名调用（grep `registry.complete(` 全库）。
- [ ] Step 5 green + 全文件回归；commit `feat(p11): registry live publish + replay-from-live + plan: p11-frontend-sse`。

### T2 backend：progress handler + report wire 形态（F1、F3）

**Files:** `backend/app/infra/execution/progress.py`（新建）、`confirmed_execution_graph.py`、`main.py`、`tests/api/test_progress_frames.py`（新建）

- [ ] Step 1 red（映射纯函数 + handler）：

```python
def test_format_known_node_running():
    frame = format_progress_frame("generate_sql", "running")
    assert frame["event"] == "trace"
    data = json.loads(frame["data"])
    assert data == {"step": "生成 SQL", "status": "running", "detail": "", "kind": "sql"}

def test_format_unknown_node_returns_none(): assert format_progress_frame("sql_gate", "running") is None

@pytest.mark.asyncio
async def test_handler_emits_transition_only():
    seen = []
    h = ProgressTraceHandler(on_frame=seen.append)
    meta = {"langgraph_node": "execute"}
    await h.on_chain_start(None, {}, name="execute", metadata=meta)
    await h.on_chain_start(None, {}, name="RunnableSeq", metadata=meta)   # 噪声名，同 node → 去重
    await h.on_chain_end(None, {}, name="execute", metadata=meta)
    await h.on_chain_end(None, {}, name="execute", metadata=meta)          # 同态重复 → 去重
    statuses = [json.loads(f["data"])["status"] for f in seen]
    assert statuses == ["running", "success"]

async def test_handler_ignores_unknown_node(): ...
```

- [ ] Step 2 red（子图 callbacks 转发）：graph 集成测试——构造 `StateGraph` 节点名 `plan/generate_sql`（用映射表内名字）+ 挂 `ProgressTraceHandler` 的 config 走 `ainvoke`，断言收到 4 帧（2 节点 × start/end）。
- [ ] Step 3 实现 `progress.py`：映射表（见 Design）+ `format_progress_frame(node, status) -> dict | None` + `ProgressTraceHandler(AsyncCallbackHandler)`（`on_chain_start/end/error` → running/success/error；`name or metadata.langgraph_node` 命中表；`_NOISE_NAMES` 过滤；`self._last: dict[str, str]` 跃迁去重）。
- [ ] Step 4 实现 graph 侧转发（`confirmed_execution_graph.py`）：

```python
def _callbacks_only(config):  # 只透传 callbacks——不带 thread_id 进无 checkpointer 子图
    cbs = (config or {}).get("callbacks")
    return {"callbacks": cbs} if cbs else None

async def _confirmed_data_agent(state, config=None):
    ...
    ds = await data_graph.ainvoke({...}, _callbacks_only(config))
# _confirmed_sql_agent / _confirmed_report_agent 同签名同处理
```

- [ ] Step 5 red→green（report wire 形态）：`test_confirm_background.py` 断言 success 路径 report 事件 data 含 `version/parent_version/title/answer`；`_persist_report` merged 改为：

```python
merged = {
    **state["report_payload"],
    "version": row["version"],
    "parent_version": row.get("parent_version"),
    "title": row.get("title") or "报告",
}
```

- [ ] Step 6 装配（`main.py` `_run_confirmed_graph`）：

```python
handler = ProgressTraceHandler(on_frame=lambda frame, _task=task: registry.publish(_task, frame))
config = {"configurable": {"thread_id": session_id}, "callbacks": [handler]}
result = await run_with_timeout(graph.ainvoke(initial, config), MAX_TASK_DURATION)
```

超时/异常路径不变（已 publish 的 progress 天然已流式送达；终态 events 照旧进 finally 的 publish）。
- [ ] Step 7 green：新测试 + `tests/api/test_confirm_background.py` + `tests/graphs/` 回归；commit `feat(p11): confirm 流 live progress（trace kind 细化）+ report 事件补 sse-v2 wire 形态 + plan: p11-frontend-sse`。

### T3 backend：P9-5 user_message + chitchat final_phase（F7、F4 后端半）

**Files:** `main.py`、`tests/api/`（并进 T2 新建文件或 test_confirm_background）

- [ ] Step 1 red：`test_generic_exception_message_uses_user_copy`——mock graph 抛 `RuntimeError("minimax sdk internal blah")` → 订阅 error 事件 `message == user_message("other")`（=`查询执行失败,请稍后重试或调整需求`）且不含 `minimax`；`test_requirement_generic_exception_same`；`test_chitchat_done_phase_idle`——chitchat 流断言出现 `phase(idle)` 事件且 `done.final_phase == "idle"`。
- [ ] Step 2 实现：两处 generic except 的 `"message": str(exc)[:300]` → `"message": user_message(envelope.kind)`；chitchat 分支 `yield` report 前补 `phase = "idle"` 与 `{"event": "phase", "data": {"phase": "idle"}}` 帧（置于 report 之前，UI 先复位再渲染闲聊泡）。
- [ ] Step 3 green + commit `fix(p11): 泛化异常 SSE 文案走 user_message（P9-5 接线）+ chitchat 终态 idle + plan: p11-frontend-sse`。

### T4 frontend：事件面统一 transport→schema→dispatch（F2、F3、F4、F5）

**Files:** `api/sse.ts`、`api/analysisEvents.ts`、`api/analysisClient.ts`、`api/confirmStream.ts`、`types/report.ts`、`pages/WorkbenchPage.tsx` + 各测试

- [ ] Step 1 red（`sse.ts`）：

```ts
export function parseSSEFrameRaw(frame: string): { eventName: string; data: string } | null
```

行为：`event:` 无 → null；多 `data:` 行 join；**不 JSON.parse**。测试：含 `\r` 行尾、多 data 行、无 event 名。
- [ ] Step 2 red（`analysisEvents.ts`）：类型升级——

```ts
export interface ReportEventPayload {            // sse-v2 wire 形态（D4）
  version?: number
  parent_version?: number | null
  title?: string
  answer: { text?: string; table?: unknown; chart?: unknown; insight?: string | null }
  trace?: unknown[]
}
export type AnalysisStreamEvent =
  | { type: 'phase'; phase: AnalysisPhase; reason?: string }
  | { type: 'requirement'; requirement: RequirementCard }
  | { type: 'trace'; entry: TimelineEntry }
  | { type: 'thinking'; phase?: string; text?: string }
  | { type: 'report'; report: ReportEventPayload }
  | { type: 'error'; error: AnalysisError }
  | { type: 'done'; finalPhase: AnalysisPhase }
export function isAnalysisPhase(v: unknown): v is AnalysisPhase
```

`parseAnalysisSSEEvent` 补 case：`trace`（`step:string` + `status ∈ running|success|error` → TimelineEntry `{id: \`${Date.now()}-${step}\`, nodeName: step, status, detail, kind, timestamp}`；非法 → null）；`thinking`（passthrough，可缺 text）；`report` 校验改 wire 形态（`answer` object 必须、`version` number 可缺）；`isReportVersion` 删除。测试：trace 合法/缺 step/非法 status；thinking；report 带 version / chitchat 无 version / 缺 answer 拒绝。
- [ ] Step 3 red（`analysisClient.ts`）：`openChat` onEvent 回调类型改 `AnalysisStreamEvent`；内部帧循环改用 `parseSSEFrameRaw` + `parseAnalysisSSEEvent`（null 丢弃）。
- [ ] Step 4 red（`confirmStream.ts`）：同上替换；ctx 增加 `onTrace?: (entry: TimelineEntry) => void`；typed switch：`report` → `sawReport=true; await ctx.onReport(typeof r.version === 'number' ? r.version : undefined)`（保留现 dispatch report_ready）；`trace` → `ctx.onTrace?.(entry)`。删除本地 `parseSSEFrame`（测试同步迁移）。
- [ ] Step 5 red（`WorkbenchPage.tsx` `handleSSEEvent` 类型化 + F5/F4）：导出该函数（沿 `analysisEvents __test__` 先例）；typed switch 增加：

```ts
} else if (evt.type === 'report') {
  const r = evt.report
  if (typeof r.version === 'number') {
    void refreshVersionsAndSelectLatest(sid, dispatch)   // F5：adjust 新版本刷新
    msgApi.success(`报告 v${r.version} 已生成`)
  } else if (typeof r.answer?.text === 'string') {
    setCasualReply(r.answer.text)                        // F4：chitchat 闲聊泡
  }
}
```

`casualReply` 为 WorkbenchPage 本地 state（`AgentBubble` 渲染、新 send/session 切换/`analysis/reset` 时清空、进 `WorkbenchEmpty` 显隐条件）。`handleSend` 传入 `setCasualReply` 与 sessionId。测试：`handleSSEEvent` report 带 version → refresh 被调（spy dispatch/store）；无 version text → casual set；phase/error/done 行为回归不变。
- [ ] Step 6 green：`cd frontend && npm run test:run`；commit `feat(p11): 前端事件面统一到 parseAnalysisSSEEvent + adjust report 刷新 + chitchat 闲聊泡 + plan: p11-frontend-sse`。

### T5 frontend：ProgressCard 真信号（F1 前端半）

**Files:** `progressModel.ts`、`ProgressCard.tsx`、`WorkbenchPage.tsx`、`confirmStream.ts`（onTrace 已在 T4）+ 测试

- [ ] Step 1 red（`progressModel.test.ts`）：

```ts
stageFromTrace('sql', 'running', 1) === 2
stageFromTrace('report', 'success', 2) === 3
stageFromTrace('sql', 'error', 2) === 2        // error 不回退
stageFromTrace(undefined, 'running', 1) === 1  // 未知 kind 不动
// 单调性 property：任意序列后 stageIndex 只增不减
```

- [ ] Step 2 red（新 `ProgressCard.test.tsx`）：`liveDetail` prop 渲染到 `.wb-progress-detail`。
- [ ] Step 3 实现：`progressModel.ts` 加 `KIND_STAGE`/`stageFromTrace`/`liveDetailFromEntry(entry): string`（running → `正在${step}…`、success → `${step} 完成`）；`ProgressCard` 加 `liveDetail?: string`（有值覆盖默认 detail 行）；`WorkbenchPage` 删除 650ms setInterval effect，confirm 流 ctx.onTrace / openChat trace 分支统一 `setStageIndex(cur => stageFromTrace(entry.kind, entry.status, cur))` + `setLiveDetail(...)`；generating 进入时初始 `stageIndex=1`。
- [ ] Step 4 green + `npm run lint`；commit `feat(p11): ProgressCard 真 trace 信号驱动，移除 650ms 假定时器 + plan: p11-frontend-sse`。

### T6 frontend：Session resume（F6）

**Files:** `WorkbenchPage.tsx` + `WorkbenchPage.test.tsx`

- [ ] Step 1 red：`loadSessionSnapshot` 返回 `boolean`（busy 与否）——`phase === 'generating' || 'adjusting'` → true；版本/requirement 恢复后以 `isAnalysisPhase(snap.session.phase)` 为真值 `dispatch({type:'phase/received', phase})`（覆盖 `report/received` 的 report_ready 副作用）；`handleSelectSession` busy → `setExecPolling(true)`。测试三例：resume awaiting_missing 会话 phase 停在 awaiting_missing；resume generating 会话启动轮询（mock fetchSession 挂起 + fake timers）；resume error 会话 phase=error。
- [ ] Step 2 实现；Step 3 green + commit `fix(p11): session resume 恢复真实 phase + busy 会话接后台轮询 + plan: p11-frontend-sse`。

### T7 docs：契约文档回写

- [ ] `docs/sse-v2.md`：§trace 载荷 `{step,status,detail?,kind?}` + progress 推导表；§report wire 形态（D4，chitchat `{answer:{text}}` 补文档）；§事件时序补 confirm/adjust live 流时序。
- [ ] `README.md` SSE v2 事件表若有 trace 字段描述则同步。
- [ ] commit `docs(p11): sse-v2 trace progress 载荷 + report wire 形态 + plan: p11-frontend-sse`。

### T8 收尾：全量回归 + CLAUDE.md 现状行 + plan/index 翻转

- [ ] `cd backend && D:/miniConda/envs/agent/python.exe -m pytest`（基线 925 passed 不回退 + 新增全绿）
- [ ] `cd frontend && npm run test:run && npm run lint`（基线 259 passed 不回退 + 新增全绿）
- [ ] 手动门（P12 前唯一一次）：起三服务真实 confirm 一轮，肉眼验证 progress 阶段推进 / 停止→后台→轮询通知 / resume。
- [ ] CLAUDE.md §9 现状行改 P11 落地描述；本 plan 状态改 `已完成` + 落地记录；`docs/plans/README.md` 索引移入已完成。
- [ ] commit `docs(p11): CLAUDE.md §9 现状 + plan 落地记录 + plan: p11-frontend-sse`。

## Verification

```bash
# 后端全量（必须在 backend/ 内跑，仓库根跑会假失败）
cd backend && D:/miniConda/envs/agent/python.exe -m pytest
# 前端
cd frontend && npm run lint && npm run test:run
# 针对性
cd backend && D:/miniConda/envs/agent/python.exe -m pytest tests/contracts/test_execution_registry.py tests/api -x
cd frontend && npx vitest --run src/api/__tests__/analysisEvents.test.ts src/components/workbench/__tests__/progressModel.test.ts
```

冒烟矩阵（手动门）：① confirm 全程 progress 阶段 1→3 推进且 detail 文案随 trace 变化；② 停止→后台跑完→5s 轮询通知→版本刷新；③ chitchat「你好」→ 闲聊泡展示 + 无 error 态；④ adjust → 新版本自动刷新选中；⑤ 断网重进会话 → phase/requirement/版本恢复，generating 会话恢复轮询；⑥ SQL 失败 → ErrorCard kind 文案（不再出现 provider 原始异常）。

## Explicitly NOT doing

- **不新增 SSE 顶层事件类型**（agent.started 等十事件名由 `trace kind×status` 表达，D1）。
- **KPI block 前端渲染**——无生产者 + P10-3 subset aggregate 前置（P9/P10 Review 记录项），有真实 KPI 生产时再做。
- **timeline UI / RightRail 改造**——`timeline` 死状态保持现状（F8 记录不修），P12 Playwright 若需要再拾起。
- **requirement 流 live trace**——parsing 阶段 ParsingCard 已覆盖，progress 只做执行段（验收原文 Tool/SQL/Repair）。
- **ReportVersion V2 能力**（diff/favorite/delete/compare）与收藏按钮真实化——伞形 §十六 V1 范围外。
- **ReportPaper 渲染重构**——EMPTY/FAILED/三层 block 已合规（P10 验收），本轮零改动。
- **不重写 registry/graph 结构**——publish 是增量，`ainvoke+run_with_timeout` 不动（D2）。
- **不动 `MAX_TASK_DURATION`/retry 预算等 P9 契约**。

## 落地记录

### 验收清单（伞形 §392）

- [x] **API/SSE event schema 固定** —— 后端 `_persist_report` merged `version/parent_version/title`（wire 形态），`parseAnalysisSSEEvent` 校验 wire 形态；前端 `transport(sse.ts parseSSEFrameRaw) → schema(analysisEvents.ts 唯一 parser) → dispatch(stores/sessionEvents handleSSEEvent)` 三层。两流共用同一 schema + dispatch 入口，删除两份内联 parser。
- [x] **前端可显示 Agent progress（Tool/SQL/Repair）** —— 后端 `infra/execution/progress.py` 节点→kind×status 映射 + `AsyncCallbackHandler` 接 `ainvoke` config 实时发 trace 帧，`registry.publish` 推给在线订阅者、留档供迟到重放；前端 `progressModel.stageFromTrace` 单调驱动 `ProgressCard`，移除 650ms 假定时器；`liveDetailFromEntry` 渲染「正在生成 SQL…」。
- [x] **Report 正确渲染** —— P10 验收保持零改动；`report` 事件 wire 形态（version/parent_version/title/answer）经 parser 校验后 `handleSSEEvent` 自动调 `refreshVersionsAndSelectLatest`（adjust/retry 走 /chat 的新版本 F5）。
- [x] **Error/Empty 状态正确** —— ErrorCard（kind 分类）保持；P9-5 接线 `user_message()`（`main.py` 两处泛化 except）；chitchat 终态 `idle`（不再 `error`）；闲聊回复 `{answer:{text}}` 经 handleSSEEvent 渲染 AgentBubble（F4）。
- [x] **Session resume 正确** —— `loadSessionSnapshot` 返回 busy（phase generating/adjusting）+ 恢复真实 phase（不被 report/received 的 report_ready 副作用覆盖）；`handleSelectSession` busy → `setExecPolling(true)` 接后台轮询。

### 实施偏差（与计划草稿的差异）

1. **T1 `complete()` 签名保留** —— 计划草稿写「无参 `complete(entry)`」，实现改「保留 `(entry, result)`，result = live + final 快照」。原因：final 事件（report/error/done）由调用方（`_run_confirmed_graph` finally、`test_*_background` 测试 harness）经 `complete(task, events)` 推入队列；改无参会破坏所有现有调用点 + 测试，而 plan 草稿的「final 事件经 publish 推」属过度设计。Online 订阅者通过队列拿到 live + final（in-order），迟到订阅者通过 `task.result = live + final` 整体重放。
2. **T1+T2 合并提交** —— `registry.publish/live` + `progress.py` 映射/handler + `_persist_report` merged + 子图 callbacks 转发均在 main.py/registry/confirmed_execution_graph 跨文件，按 hunk 拆分不净；合并为一次 backend commit `e27aae2`。
3. **T4+T5+T6 合并提交** —— 事件面统一把 `handleSSEEvent/loadSessionSnapshot/refreshVersionsAndSelectLatest` 从 WorkbenchPage 抽到 `stores/sessionEvents.ts`（消除 `react(only-export-components)` 警告 + 与组件解耦），同时含 T6 session resume + T5 progress 真信号；一次性 frontend commit `d60b842`。
4. **legacy 事件类型从 canonical union 中移除** —— 计划原意「不新增顶层事件类型」，偏离的是把原 analysisClient 自持的 `{type:'legacy', data:any}` member 也一并删除（理由：frontend-contract §一明确「前端只消费公开事件契约」，`parseAnalysisSSEEvent` 对 legacy 事件返回 null 自然丢弃；`legacyAdapter.adaptLegacyEvent` 自持 `LegacyEvent` 类型继续服务仍存续的旧组件，P15 随 legacy 一并删）。属偏离记录，非遗漏。
5. **额外补 F4 前端修复** —— 计划 T4 只列「adjust 流 report 处理」（F5），实际同时修了 chitchat 闲聊泡（F4）—— 两者都属 handleSSEEvent report 分支的统一处理；已在 T4 落地。

### 落地偏差（plan §Not doing 的边界）

- **KPI block 前端渲染**：按 plan 未做（P10-3 subset aggregate 前置 + 无生产者）。维持 NOT doing。
- **timeline UI / RightRail 改造**：F8 记录不修——timeline state 保持现状。
- **requirement-analysis 流 live trace**：未做（parsing 阶段 ParsingCard 覆盖；progress 只做执行段，符合验收「Tool/SQL/Repair」）。

### 数字 / 验证

- 后端全量：**941 passed / 0 failed / 1 skipped / 5 warnings**（基线 925 + T1-T3 新增 16；warnings 全为既有 legacy 未引入新）。
- 前端全量：**296 passed**（基线 259 + T4/T5/T6 新增 37）。`tsc -b` 通过，`oxlint` 仅余 2 条与本 Phase 无关的预存 warning（WorkbenchPage:50 `useExecutionPoll` 导出 + WorkbenchPage:113 useCallback deps，pre-existing）。
- 手动门（基线（P0）+ P11 新项）：confirm 全程 progress 阶段 1→3 推进且 detail 文案随 trace 变化；停止→后台跑完→5s 轮询通知；chitchat「你好」→ 闲聊泡 + 无 error 态；adjust → 新版本自动刷新选中；断网重进会话 → phase/requirement/版本恢复，generating 会话恢复轮询；SQL 失败 → ErrorCard kind 文案（不再出现 provider 原始异常）。

### 后续 Phase 候选（P11 不做）

- **P9-4 ErrorCode canonicalization**（P9 Review 记录项）：SQL producer 仍发 legacy `code="EXECUTION_ERROR"`（sql_graph.py:563/570/666）；接通时 SSE 用户码（QUERY_*）与 persist error_detail 断言面需一致化。
- **P10-3 KPI subset aggregate**：若 P12+ 真生产 KPI（filter/subset），必须先升级 validator。
