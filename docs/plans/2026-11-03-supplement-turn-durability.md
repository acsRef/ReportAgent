> 状态: 已完成

# 补充轮（mode=supplement）结果不再随连接消失：需求解析后台化 + 前端断流兜底

## Context（背景）

`docs/hand-off.md` §3 P0 记录：浏览器里发 `只看华东`（`mode=supplement`）后，后端日志显示已解析出新卡
（`summary='华东区域销售分析'`、`scope=['华东']`），但界面仍显示旧卡（`未限定（全部区域）`）。

### 取证结论（2026-11-03，只要结论不要过程）

1. **刷新链路本身是好的**，两个独立复现都成功把新卡渲染出来：
   - API 直调（`.e2elogs/probe_scope.py`）：`turn2 events=['phase','phase','requirement','done']`，
     `scope=['华东']`、`summary='只看华东区域的数据'`；
   - 真浏览器（Playwright，登录→问→确认→发 `只看华东`）：卡片变成
     `华东区域销售情况分析`、chip 出现 `华东`、变更提示 `↻ 已按你的补充更新：数据范围 → 华东`。
2. **失败的真相是「那一轮的结果根本没落库」**。失败会话 `80b8c302-…`（补充轮 21:00:57 发起，
   `parse_requirement parsed` 于 21:01:32）之后：
   - 后端日志再无任何记录（没有 `persist_draft failed`、没有 error、没有 done）；
   - `GET /sessions/{sid}` 的 `current_requirement` 仍是**第 1 轮的旧卡**
     （`id=draft-1793710798080` / `scope=[]` / `summary='2024年各区域销售额分析'`），行版本停在 2。
   → 结论：`graph.ainvoke` 随 SSE 响应任务一起被取消（客户端刷新页面 / 断网 / HMR 整页重载），
   `persist_draft` 事务回滚，于是**新卡既没落库也没送达**；页面重进后快照只能给出旧卡。
   截图 `.e2elogs/ui-supplement.png`（21:01:26）正好停在 `parsing` 中途，与该结论一致。
3. 这个失败形态被两个前端缺陷放大成「永久卡死」：
   - `api/analysisClient.openChat` 只在收到 `requirement/error/done` 时解锁 `sending`；
     流**静默结束**（无终态事件）时没有任何回调 → 发送框永久禁用、phase 停在 `parsing`；
   - `parsing` 阶段没有任何「停止」入口（`ProgressCard` 只在 `generating/adjusting` 渲染），
     用户唯一出路是 F5 —— 而 F5 恰好触发第 2 条的结果丢失。

### 本轮要解决的问题（一个根因 + 两个放大器）

- 根因：**需求解析轮的结果依赖客户端连接存活**（`persist_draft` 在 SSE 响应任务里跑）。
- 放大器 A：前端对「流静默结束」没有任何兜底，把可恢复的中断变成永久锁死。
- 放大器 B：解析中没有停止入口，也没有中断后的自动补齐。

## Design（设计）

### D1 后端：需求解析轮后台化（复用 ExecutionRegistry，不新建机制）

`POST /api/v1/chat` 的 `mode=new|supplement` 分支改为「独立后台任务 + SSE 只做订阅」，与
`/confirm`、`/retry`、`mode=adjust` 已有形态完全一致（`docs/plans/2026-08-12-execution-background-run.md`
的同一条语义：**客户端断连只断渲染，任务跑完并落库**）。

- `_chat_requirement_analysis` 保留两段：
  1. **图前短路**（P16.5 explicit preference）留在请求内联完成——纯正则 + 一次 DB 写，不后台化；
  2. 其余交 `registry.start_confirmed_task(session_id, user_id, "requirement", runner)`；
     `BusyError` → `409 SESSION_BUSY`（与 confirm 一致，前端映射成「该会话正在解析中」）。
- 新增 `_run_requirement_graph(task, graph, initial, session_id, mode, mcp_down_flag)`：
  - 起手 `session_manager.update_phase(session_id, "parsing")`（**新增落库点**：刷新页面后快照能看见
    「解析中」，前端据此接后台轮询；此前 parsing 相位从不落库，快照永远滞后一轮）；
  - `with mcp_down.scoped(flag)` 包住 `graph.ainvoke`（P15 e2e seam 语义不变）；
  - `run_with_timeout(graph.ainvoke(...), MAX_TASK_DURATION)`（与执行轮同一总预算；挂死不再无限等）；
  - 事件序列与现状**逐事件同形**：`security_level=HIGH` → `error(SECURITY_REJECTED)`；
    `intent=chitchat` → `phase(idle)` + `report({answer:{text}})`；否则
    `phase(awaiting_missing|awaiting_confirm)` + `requirement(card)`；
  - 异常：`asyncio.TimeoutError` → `error(TASK_TIMEOUT)` + phase 落 `error`；其它 →
    `classify_exception` + `user_message`（P9 出口不变）；
  - `finally`：补 `done(final_phase)` + `registry.complete(task, events)` + tracer flush
    （复用 `_run_confirmed_graph` 的收尾约定）。
- SSE 侧直接复用 `_subscribe_events(task, "parsing")`：未完成时先发 `phase=parsing`，随后 drain 队列；
  断连只会取消订阅，任务继续；迟到订阅者在 TTL 内可重放结果。

### D2 前端：中断不再静默锁死

1. `api/analysisClient.openChat(request, onEvent, signal, onClose?)`：读到 EOF（或网络异常）后回调
   `onClose({ sawTerminal })`；`sawTerminal` 由 `requirement / report / error / done` 任一事件置位。
   不合成假事件（前端契约「不伪造成功」）。
2. `WorkbenchPage.handleSend`：`onClose` 里若 `!sawTerminal` → `setSending(false)` +
   `dispatch analysis/failed { code:'STREAM_INTERRUPTED', recoverable:true, failed_action: mode }` +
   `setExecPolling(true)`（转后台跟随）。`analysisReducer.canRetryFailedAction` 已认
   `new/supplement`，重试按钮直接可用，无需改白名单。
3. `useExecutionPoll` 的完成判定从「phase ∈ {report_ready,error}」泛化为「**不再 busy**」
   （`!isBusyPhase(phase)`，`parsing`/`generating`/`adjusting` 都算忙）：完成时先
   `loadSessionSnapshot()`（拿新 requirement / 版本 / 真实 phase），再选中最新版本。这样
   「停止」或「断流」之后，后台解析完成 → 轮询把新卡刷回来（这正是 P0 现象的正解）。
4. `ParsingCard` 增可选 `onStop`；`parsing` 时渲染「停止等待」→ 走同一个 `handleStop` 语义：
   `abortStream()` + 解锁 + `setExecPolling(true)` + phase 回到最后已知的稳定相位
   （有卡按 `missing_fields` 落 `awaiting_missing/awaiting_confirm`，无卡落 `idle`）+ 提示
   「已停止等待，需求解析在后台继续，出结果后自动更新」。
5. `openChat` 对 `/chat` 的 `409` 单独给文案（`该会话正在解析中，请稍候`），不再落成
   `HTTP_ERROR: chat request failed: 409`。

### D3 错误路径枚举（kind → 用户可见 → 落库 → 测试）

| 场景 | 后端 | 用户可见 | 落库 | 测试 |
|---|---|---|---|---|
| 断连（刷新/断网） | 任务不取消，跑完 | 无（重进会话看新卡） | `persist_draft` 正常 + `current_phase=awaiting_*` | `test_chat_requirement_background.py` 断连用例 |
| 图超时（> MAX_TASK_DURATION） | `TASK_TIMEOUT` | 「后台任务超过 …s 未完成，请稍后重试」 | `current_phase=error` | 同文件超时用例 |
| 图异常 | `classify_exception` + `user_message` | 稳定文案（P9 出口） | `current_phase=error` + `last_failed_action` | 同文件异常用例 |
| 同会话并发发起 | `409 SESSION_BUSY` | 「该会话正在解析中，请稍候」 | 无 | api 用例（BusyError → 409） |
| 流静默结束 | 无（已断） | `STREAM_INTERRUPTED` toast + 重试按钮 | 无 | `analysisClient` onClose 单测 |
| 解析中用户点停止 | 任务继续 | 「已停止等待…自动更新」 | 后台完成照常落库 | 前端 `useExecutionPoll` 单测 |

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/main.py` | `_chat_requirement_analysis` 拆分：图前短路内联 + `registry.start_confirmed_task`；新增 `_run_requirement_graph`（parsing 相位落库、scoped MCP seam、超时、事件组装、收尾） |
| `frontend/src/api/analysisClient.ts` | `openChat` 增 `onClose({sawTerminal})`；409 专项文案 |
| `frontend/src/pages/WorkbenchPage.tsx` | `handleSend` 传 `onClose` 并兜底；`useExecutionPoll` 完成判定改 `!isBusyPhase` + 完成后 `loadSessionSnapshot`；`parsing` 停止入口 + 文案 |
| `frontend/src/components/workbench/ParsingCard.tsx` | 可选 `onStop` + 停止按钮 |
| `frontend/src/stores/analysisReducer.ts` | 新增纯函数 `isSettledPhase(phase)`（轮询完成判定单一来源） |
| `backend/tests/api/test_chat_requirement_background.py`（新） | 后台化 3 例：断连仍落库/仍发 requirement、超时落 error、BusyError→409 |
| `frontend/src/api/__tests__/analysisClient.test.ts`（新） | `onClose` 终态/静默结束两例 + 409 文案 |
| `frontend/src/stores/__tests__/analysisReducer.test.ts` | `isSettledPhase` 例 + 「不同 id 低版本也替换」钉子 |
| `docs/plans/README.md` | 本 plan 登记进「进行中」 |
| `docs/hand-off.md` | P0 结项记录（真因 + 修复 + 验证） |

## Reused existing utilities（复用工具）

- `backend/app/infra/execution/registry.py` — `start_confirmed_task` / `publish` / `complete` / `BusyError`
  （**不新建后台任务机制**；requirement 只是第三种 `kind`）
- `backend/app/main.py::_subscribe_events` — 后台任务 → SSE 订阅器（断连即取消订阅，任务不受影响）
- `backend/app/reliability/timeout.py::run_with_timeout`、`MAX_TASK_DURATION`
- `backend/app/reliability/errors.py::classify_exception` / `user_message`（P9 出口，文案不改）
- `backend/app/reliability/mcp_down.py::scoped`（P15 e2e seam）
- `backend/app/infra/checkpoint/session.py::update_phase`
- `frontend/src/stores/analysisReducer.ts::isBusyPhase`（轮询「忙」判定单一来源）
- `frontend/src/stores/sessionEvents.ts::loadSessionSnapshot`（快照回填 requirement/版本/phase）
- `frontend/src/api/streamAbort.ts::abortStream`（停止按钮既有单例）

## Verification（验证）

1. **后端离线**：`pytest backend/tests/api/test_chat_requirement_background.py -q`（mock graph，无 LLM）
   + 受影响面 `pytest backend/tests/api backend/tests/contracts -q`。
2. **前端**：`cd frontend && npx vitest --run src/api/__tests__/analysisClient.test.ts src/stores/__tests__/analysisReducer.test.ts`
   + `npm run test:run`、`npx tsc -b`、`npm run lint`。
3. **真链路（手动门）**：
   - `.e2elogs/probe_supplement_hang.py`（服务在跑时）补充轮仍 `requirement` 事件 + 落库一致；
   - 浏览器脚本 `node frontend/_probe_supplement.mjs`：补充后卡片 chip 出现 `华东`（修复前也会绿，
     用于防回归）；
   - **中断验收**：补充轮发起的第 2 秒 `page.reload()` → 后端日志出现完整一轮（parse → persist），
     DB `current_requirement` 变成新卡，页面重进后卡片显示新卡（修复前：DB 里仍是旧卡）。
4. **回归红线**：主干 3 条 + 多轮 e2e（`REPORTAGENT_E2E=1`）通过。

## Explicitly NOT doing（不做事项）

- 不改 `analysisReducer` 的 `requirement/received` 去重语义（同 id 旧版本才忽略，现语义正确且有单测）；
- 不改需求卡渲染 / 字段 / 中文化，不动 `parse_requirement` 的解析规则；
- 不给 requirement 轮加 progress（trace）帧——本次只做「不丢结果 + 不锁死」；
- 不引入新的事件类型（`STREAM_INTERRUPTED` 走既有 `error` 事件通道，SSE 七事件基线不动）；
- 不做跨进程后台任务（registry 仍是进程内，重启即丢，与 confirm 现状一致）；
- 不顺手修「确认后卡片 pill 仍显示『信息完整 · 待确认』」——单独记账，另开 plan。

## 落地记录（2026-11-03）

### 实际改动（与计划的偏差）

1. **相位写入全部走 `_safe_update_phase`**（计划里只写了「起手落 parsing」）：`update_phase` 是
   观测性簿记，DB 抖动不该把一个已经能返回的结果打成 error。附带好处——现有 api 单测
   （无 DB）不用额外 mock 就能跑。
2. **显式偏好短路的事件序列收窄**：`phase(parsing) → phase(idle) → report → done` 改为
   `phase(idle) → report → done`。短路本来就没有「解析」这一步，第一条 parsing 帧是假信号；
   主链的 parsing 帧改由后台任务订阅器在推进时发出（chitchat 路径仍有 parsing 首帧）。
   同步更新 `backend/tests/api/test_explicit_preference.py` 的两条断言。
3. **`isSettledPhase` 收 `AnalysisPhase | string`**：`SessionSnapshot.session.phase` 是裸 string，
   页面层不必为了类型再 cast 一次。
4. **轮询完成回调改为拉全量快照**：`useExecutionPoll` 的 onDone 由 `refreshVersionsAndSelectLatest`
   换成 `loadSessionSnapshot`——解析轮后台化后，要补回来的不只是版本列表，还有**需求卡**。
5. **中止原因区分**：`onClose({ sawTerminal, aborted })`——用户点「停止等待」走 aborted，
   不再额外弹一条「连接中断」。

### 验证证据（真链路）

| 场景 | 结果 |
|---|---|
| A 补充轮刷新（回归） | 卡片 chips 出现「华东」，变更提示「↻ 已按你的补充更新：数据范围 → 华东」；DB `summary=只看华东区域的业务数据 / scope=['华东'] / version=2` |
| B **解析中刷新页面**（本 plan 核心） | 刷新瞬间 DB `phase=parsing` 且仍是旧卡 v1（= 修复前的死法）；后台跑完 DB 变 `scope=['华南'] version=2`；点回会话卡片渲染出「华南」 |
| C 连续 4 轮 补充→确认（API 直调） | 每轮 `phase, phase, requirement, done`，逐轮落库 v2/v4/v5 |
| 主干 smoke（真 LLM） | 明确问法 → 6 行 SUCCESS；模糊问法 → 回 analysis_intent 澄清 |
| 离线 | `backend/tests/api + smoke + contracts` **697 passed**（新增 9 例）；`backend/tests/graphs` **113 passed**；frontend **337 passed**、`tsc -b` 干净、`oxlint` 0 error |

验收脚本：`.e2elogs/probe_card_refresh.mjs`（浏览器 A/B）、`.e2elogs/probe_supplement_hang.py`（多轮落库）。

### 顺带修掉的既有红灯（与本 plan 无关但会掩盖真问题）

- `backend/tests/graphs/test_requirement_dictionary_clarify.py::test_prompt_forbids_fabricating_missing_business_entity_tables`：
  prompt 瘦身（`f20bd8b`）把段标题改成「点名对象缺失（禁止静默替换）」、占位符改成
  `requested_object:<对象名>`，测试仍钉旧文案 → master 常红。改钉现行文案，并补一条
  「未命中线索不注入」的反向断言（否则附录退回无条件注入、瘦身静默失效）。

### 发现但**不在本 plan 范围**的既有红灯（转 hand-off）

- `backend/tests/e2e/test_multiturn_followup.py` 首轮断言 `card.granularity == "month"`
  （问法「2024年华东销售趋势和异常」）现在拿不到：`_deterministic_intent` 命中「异常」→
  anomaly 分支只补 `analysis_methods/dimensions`，**不补粒度澄清**，卡片以
  `granularity=None / status=complete` 返回（API 直调同样复现，与后台化无关）。
  这正是 hand-off P1 第一条「异常类必须日/周粒度」的产品缺口，修它等于做 P1，故不动。
- 「确认后卡片 pill 仍显示『信息完整 · 待确认』」：confirm 流结束后 store 里的卡没有被
  locked 版本替换，视觉与 `readOnly` 状态不一致。单独记账。
