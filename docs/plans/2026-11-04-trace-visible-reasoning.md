> 状态: 已完成

# 执行过程可视（Doubao 式「看得见的推理」）+ 异常态表达

## Context（背景）

用户要求（2026-11-04）：
1. **不做 L3（不显示模型 CoT）**，但要「能看到推理过程」——像豆包那样，用户能看到 Agent 在
   做什么、为什么这么做；
2. **必须把异常情况说清楚**：模型回复中断、超时、用户点停止、后端报错、刷新重连等，
   前端不能只留一个转圈或一句「处理失败」。

## 现状（代码取证）

| 能力 | 现状 |
|---|---|
| trace 帧（`{step, status, detail, kind}`） | `/confirm`、`/adjust` 有（`ProgressTraceHandler` → `registry.publish`）；**需求解析轮一条都没有**（只发 `phase=parsing`） |
| 前端消费 trace | 只在 `handleProgress` 里折算成 `stageIndex` + 一行 `liveDetail`（喂给 `ProgressCard`）；**没有过程列表** |
| store 里的 `timeline` | reducer/state 早就有（`timeline/received`，但**没有任何地方 dispatch**）；legacy 视图另有一套 |
| 异常表达 | 断流：`STREAM_INTERRUPTED` 错误卡 + 后台轮询（2026-11-03 已做）；停止：`已停止显示` toast；超时：60s toast；**过程面板上没有任何痕迹** |

## Design（设计）

### D1 后端：需求图也发 trace（复用既有机制，不新增事件类型）

- `infra/execution/progress.py::PROGRESS_NODES` 补需求图节点：
  `security_guard`（安全校验）、`classify_intent`（识别意图）、`requirement_parse`（解析需求）、
  `requirement_preview`（口径预览）、`persist_draft`（保存需求卡）。
- `main.py::_run_requirement_graph` 的 `graph.ainvoke` 挂 `ProgressTraceHandler(on_frame=registry.publish)`，
  与 confirmed 图同一套（P11 契约：不新增顶层事件类型，progress 由 `trace` 载荷细化）。
- 帧里的 `detail` 留空由前端按 `kind` 补文案；**决策级细节（EXPLAIN 失败原因、diagnose 决策）暂不塞进帧**，
  列为后续（避免把大段状态塞进 SSE）。

### D2 前端：`TracePanel`（折叠的「执行过程」）

- 数据源：`AnalysisState.timeline`（`timeline/received` 现在真正接线）+ `timelineNote`。
- 形态（对齐豆包）：
  - 折叠头：运行中 `正在执行 · {当前步骤}`；结束 `执行过程 · {n} 步 · {耗时}`；异常 `执行过程 · 中断于 {步骤}`；
  - 展开体：按步骤竖排，`✓` 成功 / `◐` 进行中 / `✕` 失败 / `⏸` 已停止，附「步骤标签 + 用时」；
  - 默认折叠，运行中自动展开一次（用户手动收过就不再抢）。
- 步骤去重：同一节点 `running → success` 只保留一行（**reducer 按 `nodeName` 去重**，此前按 `id`，
  而每个帧的 `id` 都不同 → 会越叠越长）。

### D3 异常态（本 plan 的重点）

| 场景 | 触发点 | 前端表现 |
|---|---|---|
| 流**静默中断**（网络/服务端崩） | `openChat` 的 `onClose(!sawTerminal && !aborted)` | 过程面板：进行中的步骤标 `✕ 中断` + 顶部说明「连接中断，任务可能在后台继续」；沿用既有错误卡与后台轮询 |
| 用户点**停止**（报告流 / 解析轮） | `handleStop` / `handleStopParsing` | 进行中步骤标 `⏸ 已停止` + 「已停止显示，后台继续执行，完成后自动更新」 |
| **超时**（60s 慢响应提示闸） | `sendTimeoutRef` | 不发新状态，只在面板头追加「仍在执行…」（步骤继续按 trace 更新） |
| 后端**error 事件** | `handleSSEEvent('error')` | 进行中步骤标 `✕ 失败`，错误卡照旧；面板保留已完成的步骤（便于定位失败点） |
| **刷新/重连** | `loadSessionSnapshot` 返回 busy | 面板为空 + 说明「（重连）上一轮正在后台执行，完成后自动更新」；轮询完成 → 追加「后台执行完成」步骤 |
| 后台任务**完成** | `useExecutionPoll.onDone` | 追加一条 `✓ 后台执行完成`，清掉 interrupt/stop 说明 |

状态用 reducer 里的 `timelineNote: string \| null` + `timeline/closed`（`reason: done|stopped|interrupted|error`）
表达，避免在组件里各自为政。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/infra/execution/progress.py` | `PROGRESS_NODES` 补需求图 5 个节点 |
| `backend/app/main.py` | `_run_requirement_graph` 挂 `ProgressTraceHandler` |
| `backend/tests/api/test_chat_requirement_background.py` | 需求轮发出 trace 帧的断言（stub graph + 回调） |
| `frontend/src/stores/analysisReducer.ts` | timeline 按 `nodeName` 去重；新增 `timelineNote` + `timeline/closed` / `timeline/note` / `timeline/cleared` |
| `frontend/src/stores/analysisStore.ts` | 新字段写回（逐字段枚举的坑，必须同步） |
| `frontend/src/stores/sessionEvents.ts` | `trace` → `timeline/received`；`error` → `timeline/closed(error)` |
| `frontend/src/components/workbench/TracePanel.tsx`（新） | 折叠过程面板 + 4 种步骤状态 + 异常说明 |
| `frontend/src/pages/WorkbenchPage.tsx` | 发送时清 timeline；停止/中断/轮询完成/重连时打标记 |
| `frontend/src/styles/workbench.css` | 面板样式（token 驱动） |
| 测试 | `TracePanel.test.tsx`、`analysisReducer.test.ts`（去重/closed/note）、`sessionEvents.test.ts` |
| 文档 | `docs/plans/README.md`、`docs/hand-off.md` |

## Reused existing utilities（复用工具）

`ProgressTraceHandler` / `format_progress_frame` / `registry.publish`（需求与执行共用）、
`AnalysisState.timeline` + `timeline/received`（reducer 已有）、`ProgressCard`（生成中主视图保留）、
`useExecutionPoll` / `loadSessionSnapshot`（后台补齐）、`STREAM_INTERRUPTED`（既有错误通道）。

## Verification（验证）

- `pytest backend/tests/api/test_chat_requirement_background.py -q`（需求轮 trace 帧）
- `cd frontend && npx vitest --run src/components/workbench/__tests__/TracePanel.test.tsx src/stores/__tests__/analysisReducer.test.ts`
- `npm run test:run` / `npx tsc -b` / `npm run lint`
- 浏览器：`.e2elogs/probe_trace_panel.mjs` 覆盖四条路径——正常两轮（面板步数/耗时）、
  解析中刷新页面（重连说明 + 后台完成）、点停止（⏸ + 后台继续说明）、错误轮（✕ 定位失败步骤）。

## Explicitly NOT doing（不做事项）

- **不做 L3**：不接 `reasoning_content`、不改 `strip_think_tags`、不换推理模型；
- 不把 `EXPLAIN` 原文 / prompt / SQL 全文塞进 trace 帧（面板只显示步骤与一句话；细节仍在可观测页）；
- 不新增 SSE 顶层事件类型（沿用 `trace`），不加新的 `phase`；
- 不做「每个 token 级流式打字」。

## 落地记录（2026-11-04）

### 后端

1. `progress.PROGRESS_NODES` 补需求图 5 个节点：`security_guard`(安全检查) / `classify_intent`(识别意图) /
   `requirement_parse`(解析需求) / `requirement_preview`(口径预览) / `persist_draft`(保存需求卡)。
2. `_run_requirement_graph` 的 `ainvoke` 挂 `ProgressTraceHandler(on_frame=registry.publish)`，
   跑完 `handler.close_all()`、超时/异常 `handler.fail_pending()`。
3. **抓到一个 P11 遗留的静默缺陷**：本仓库 langgraph 版本的节点 `on_chain_end`
   **既无 `metadata.langgraph_node` 也无 `name`**（实测打印：`END None node=None`），
   于是 progress 帧**历史上只发过 `running`、从没发过 `success`**——契约写「started/completed」，
   实现只落了 started。修法（确定性、不依赖 end 归因）：
   - 线性图假设：新节点开始时把此前挂起的节点收成 success（`_close_pending(except_node=...)`，
     保留正在开始的节点，避免同节点内部 Runnable 重复触发导致 running/success 横跳）；
   - runner 收尾 `close_all()`；失败路径 `fail_pending()`。
   修完真链路帧序列：`安全检查 running/success → 识别意图 … → 保存需求卡 success`。

### 前端

1. `AnalysisState` 新增 `timelineNote`，reducer 新增 `timeline/cleared` / `timeline/note` /
   `timeline/closed(reason)`；`timeline/received` 改**按 `nodeName` 去重**（progress 帧每帧 id 不同）。
   `timeline/closed` 的定性：`done` → success（正常收尾的最后一步没来得及发 success 帧）、
   `stopped` → pending、`interrupted/error` → error。
2. `sessionEvents`：`trace` 真正写进 timeline；`error`/`done` 各自收尾过程。
3. 新组件 `TracePanel`：折叠头（运行中「正在执行 · 当前步骤」/ 失败「中断于 X」/
   停止「已停止于 X（后台继续）」/ 正常「执行过程 · n 步 · 耗时」）+ 步骤列表（✓ ◐ ✕ ⏸ + 类别 + 耗时）；
   运行中/失败/停止自动展开，正常结束折叠成一行（豆包式）。
4. **异常与「离开」场景**（用户补充要求）：
   - 流静默中断 → `timeline/closed(interrupted)` + 错误卡 + 后台轮询；
   - 点停止（报告流 / 解析轮）→ `timeline/closed(stopped)` + 「后台继续」说明；
   - 后端 error → `timeline/closed(error)` + 面板点名失败步骤；
   - **切换会话** → `abortStream()` 后切会话，**并给流加身份守卫**（`activeSessionId !== sid` 的事件直接丢弃，
     修掉「旧会话事件写进新会话界面」的串台）；切回来若仍 busy → 面板显示「（重连）后台执行中」；
   - **离开工作台/登出** → `useEffect` 卸载钩子 + `handleLogout` 都断流并 reset（后端任务照常跑完落库）；
   - 后台补齐完成 → 面板追加「后台执行完成」并清掉中断说明。

### 验证

| 层 | 命令 | 结果 |
|---|---|---|
| 后端 | `pytest backend/tests/{graphs,api,contracts,smoke} -q` | **950 passed**（新增需求轮 trace 帧用例 + 需求节点映射用例） |
| 前端 | `npm run test:run` / `npx tsc -b` / `npm run lint` | **391 passed**（TracePanel 8 例 + reducer 7 例）/ 0 error / 0 error |
| 真链路 | `node .e2elogs/probe_trace_panel.mjs` | 面板可见 ✔；帧含 success（`安全检查 完成 / 识别意图 进行中`）；停止后 `已停止显示，任务在后台继续执行` + 步骤 `pending` ✔ |
