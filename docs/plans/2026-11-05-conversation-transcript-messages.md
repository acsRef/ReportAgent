> 状态: 已完成

# 收敛消息渲染：让它像聊天产品，而不是每轮一张业务卡片

## Context（背景）

用户（2026-11-05，长评）指出两个必须一起改的问题：

1. **发一条消息后，之前 Agent 回复的消息自动减少甚至消失**，对话历史不连续；
2. **每一轮交互都生成一个卡片**，普通聊天也被做成卡片，非常机械。

并给出交互原则：一条消息就是一条消息；历史永久连续；结构化展示按需出现；过程融进同一条回复。
明确要求「不要继续修补卡片样式，而是重新收敛 `ConversationTimeline` 的消息渲染规则」，
保留 SSE、TracePanel、ReportPaper、图表能力。

## 根因（代码核对结论）

| 现象 | 真因 |
|---|---|
| Agent 回复「消失」 | **assistant 文本从来没落库**：`app.conversations` 只有 user 文本行 + `report_version` 指针行（content 为空）。历史轮次只能渲染「用户问 + vN 版本条」，没有助手说的话 → 新的一轮开始，上一轮助手那句话就没地方显示了 |
| 每轮都像卡片 | 渲染层把「助手一句话 + 参数 + 动作」拼成了带边框的确认条/卡；且 `generating` 时还会渲染整张只读 `RequirementCard` |

## Design（设计）

### D1 助手消息落库（补上缺失的一环）

- 需求轮结束（`persist_draft` 之后）：写一条 `role='assistant'`、`message_type='assistant_text'`，
  content = 面向用户的那句话（`好，我理解为：<summary>。确认后我就开始查数据。`）；
- 报告落库时：把已有的 `report_version` 指针行的 content 补成 `报告 vN 已生成：<title>`。

这样 `app.conversations` 才是一份**真实对话记录**，历史轮次能渲染出助手说过的话。

### D2 历史轮次按「问 → 答」渲染

`ConversationTimeline` 每个 turn 渲染：

```
[用户气泡]
[助手气泡]  ← 新增：该轮 assistant_text
[vN 报告 · 收起]  ← 报告仍在，默认收起，点开才挂 ReportPaper
```

助手气泡里可以内嵌过程摘要（`◐ 思考了 N 步`），与当前轮的形态一致。

### D3 去掉「每轮一张卡」

- `generating` 阶段不再渲染整张只读 `RequirementCard`（它只是「已锁定」的状态牌，信息量为零）；
- 需求确认仍是**一句话 + 一个动作**（已完成）；
- 卡片只作为「需求详情」的按需视图保留（查看用，不占主流程）。

### D4 消息生命周期（append-only）

目标模型（后续阶段落地）：

```
发送 → 立即 append 用户消息 + append 空的助手占位（streaming）
SSE  → 更新**最后一条助手消息**（trace / 文本 / 动作 / 报告块）
done → 标记完成（保留 id），合并服务端历史时按 id 去重，不整表覆盖
```

本阶段先保证「历史不丢」（D1/D2）与「不再每轮一张卡」（D3）；
把 `ConversationTimeline` 的渲染数据源换成统一的 `ChatTurn[]`（问、答、报告、过程）留到下一阶段，
因为它要同时改 store 的 `messages` 语义与 reducer，风险较大。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/agent/requirement_analysis_graph.py`（或 `_persist_draft`） | 需求轮成功后写 `assistant_text` 消息 |
| `backend/app/services/report_version_service.py` | `report_version` 行的 content 写「报告 vN 已生成：title」 |
| `frontend/src/components/workbench/ConversationTimeline.tsx` | 每轮渲染助手气泡（新 prop `assistantText`），报告默认收起 |
| `frontend/src/components/workbench/historyTurns.ts` | 把 assistant 文本配对到对应轮次 |
| `frontend/src/pages/WorkbenchPage.tsx` | 不再渲染 `generating` 时的只读整卡 |

## Verification（验证）

1. 真机两轮：第一轮报告后，第二轮开始时**第一轮的助手那句话仍在时间线里**；
2. `app.conversations` 里能看到 assistant 文本行（`select role, message_type, left(content,40) ...`）；
3. 截图对比：`generating` 阶段不再出现整张只读卡。

## Explicitly NOT doing（不做事项）

- 不动 SSE 事件面与后端 Agent 架构；
- 不重写 ReportPaper / 图表；
- 本阶段不做「流式占位消息」的完整重构（见 D4，下一阶段）。

---

## 修订 A（2026-11-05 20:40）：D4 落地——append-only 消息生命周期

第一阶段（D1/D2/D3 前半）已落：助手文本落库、历史轮次「问 + 答 + 版本条」。
本修订把 **D4（流式占位消息）** 与 **D3 剩余项（generating 去只读整卡）** 一次做完。

### A1 数据模型：`messages` 从「服务端快照」变成「本地对话账本」

`stores/chatTranscript.ts`（新）定义唯一消息类型：

```ts
interface ChatMessage {
  id: string                 // 本地稳定 id（React key；合并历史时不变）
  serverId: number | null    // app.conversations.id；落地后回填，用于去重
  role: 'user' | 'assistant'
  content: string
  createdAt: string
  status: 'streaming' | 'done' | 'error'
  version: number | null     // 该轮产出的报告版本（assistant）
  trace: TimelineEntry[]     // 过程步骤——**属于这条消息**，不再是全局 timeline
  traceNote: string | null   // 中断/停止/重连说明
}
```

要点：
- `timeline` / `timelineNote` 从 `AnalysisState` **移除**，改为挂在最后一条 assistant 消息上：
  「过程融进同一条回复」由数据结构保证，而不是靠渲染时拼装；
- `timeline/received` / `timeline/closed` / `timeline/note` 动作名保留（调用面不变），
  语义改为「写最后一条 assistant 消息」；`timeline/cleared` 删除——新消息天然没有旧过程，
  不需要「开新一轮前清空」。

### A2 生命周期（发送 → 流式 → 完成 → 合并）

```
chat/turn-appended  → 一次 action 原子追加 [user(content), assistant('', streaming)]
trace/requirement/report → 写最后一条 assistant（过程 / 文本 / 版本）
done / error       → chat/status（done / error），保留 id
chat/merged        → 服务端历史按 id 合并（见 A3），永不整表覆盖
```

本地 id 由页面在发送时生成（`crypto.randomUUID()`），reducer 保持纯函数（id 由 action 携带）。

### A3 合并规则（按 id 去重，不整表覆盖）

`mergeServerHistory(local, serverRows)` 纯函数：

1. 服务端行按到达顺序遍历：
   - `serverId` 已存在本地 → 用服务端内容刷新（服务端是真值），保留本地 id；
   - 否则在**未落地的本地消息**里找同 role 且内容相同的一条 → 认领该服务端 id
     （`serverId` 回填，本地 id 不变 → 不重挂 React 节点）；
   - 都对不上 → 作为新消息插入；
2. 仍未落地的本地消息（正在流的占位、chitchat 回复）**追加在末尾保留**——
   历史刷新永远不会删掉屏幕上正在生成的那条。
3. `role='system'`（`requirement_patch` 指针）与 `message_type='report_version'`
   （版本指针行，版本条已表达）不进对话账本，避免一轮冒出两条助手气泡。
4. 无 serverId 的 assistant 占位与「本地 content 为空」的助手消息允许被
   **占位认领**（服务端 `assistant_text` 到达时把句子回填到同一条气泡）。

### A4 渲染：一个轮次 = 一条用户消息 + 一条助手消息

`historyTurns.ts` 收敛成 `buildChatTurns(messages, versions): ChatTurn[]` +
`currentTurnKey(messages, phase)`：

```
turns          = 每个 user 消息开一轮，其后的 assistant 并入该轮（答 + 过程 + 版本）
currentTurnKey = phase === 'idle' ? null : 最后一个 user 轮次
history        = 除当轮以外的所有轮次（ConversationTimeline 数据源，统一 ChatTurn[]）
```

- 当轮由页面渲染成**一条**助手气泡：`TracePanel` + 一个 body
  （追问 / 确认 / 解析中 / 进行中进度 / 错误 / 状态文案，五选一），
  不再出现「思考一条 + 文案一条 + 进度卡一张」；
- `QuestionBar` / `ConfirmBar` / `ParsingCard` / `ProgressCard` / `ErrorCard`
  新增 `bare` 形态：只出内容，外壳由 `AssistantTurn` 提供（默认形态不变，既有单测不受影响）；
- `generating` / `adjusting` 不再渲染只读整卡（`requirementUiMode` 去掉 `'card'` 分支）；
  详情仍在确认那条消息里按需展开；
- `lastQuestion` / `casualReply` 两个页面级 state 删除——提问与闲聊文本都来自对话账本，
  不会再出现「界面上那句话和库里不是同一条」。

### A5 后端（最小改动，一行）

`get_messages` 排序改 `ORDER BY created_at, id`：同一事务内多行 `created_at`
相同（`NOW()` 是事务开始时间），只按时间排序会让并发插入的先后不确定，
合并时可能把「问 + 答」顺序抖开。

### A6 验证

1. `npx tsc -b`、`npm run lint`；
2. 受影响单测（只跑这几个文件，不跑 420 例全量）：
   `chatTranscript.test.ts`（新）/ `analysisReducer.test.ts` / `historyTurns.test.ts` /
   `handleSSEEvent.test.ts` / `sessionEvents.test.ts` / `ConversationTimeline.test.tsx`；
3. 真机两轮 `.e2elogs/probe_clean_two_turns.mjs`：第二轮开始时第一轮的「问 + 答 + 版本条」
   全部还在，且界面上助手气泡总数 = 轮次数（不是 2 倍）；
4. 真机 `.e2elogs/probe_transcript_ui.mjs`：历史会话打开后气泡与 `app.conversations` 一致；
5. `.e2elogs/probe_hit_test.mjs`：被遮挡元素 0（bare 化后按钮仍在可点层）。

---

## 落地记录（2026-11-05 21:00，修订 A 全部完成）

> commit `a1d3596`（feat(ui): collapse message rendering into an append-only transcript）。
> 同一提交里同步了 Playwright 选择器：`e2e/helpers/page-objects.ts` 的
> `expectRequirementCard` 改等 `[aria-label="需求确认|需求澄清"]`、`expectBusy` 改等
> `.wb-turn-progress`；`11/12/screenshots` 三个 spec 的旧选择器（`.wb-requirement-card` /
> `.wb-progress-card`）一并改掉。**Contract E2E 本次没跑**（要 mock backend + 真 PG，
> 独立 nightly/manual workflow，且此前已有 known-red），下次夜间跑时以本提交为准。

### 实际改动

| 文件 | 改动 |
|---|---|
| `frontend/src/stores/chatTranscript.ts`（新） | `ChatMessage` 模型 + `appendTurn` / `withAssistant*` / `withTraceEntry` / `closeTrace` / `mergeServerHistory`（全部纯函数） |
| `frontend/src/stores/analysisReducer.ts` | `messages` 语义换成对话账本；`timeline` / `timelineNote` / `timeline/cleared` 删除；新增 `chat/turn-appended`、`chat/text`、`chat/status`、`chat/version`、`chat/merged`；`timeline/*` 改写最后一条助手消息 |
| `frontend/src/stores/analysisStore.ts` | 去掉 timeline 两字段的逐字段写回 |
| `frontend/src/stores/sessionEvents.ts` | 历史合并改 `chat/merged`；chitchat 文本写进当轮助手消息（删除 `setCasualReply`）；`done`/`error` 标记消息状态 |
| `frontend/src/components/workbench/historyTurns.ts` | 收敛成 `buildChatTurns` + `currentTurnKey`（`ChatTurn` 成为唯一轮次类型；删除 `liveQuestionOf`） |
| `frontend/src/components/workbench/ConversationTimeline.tsx` | 数据源 `ChatTurn[]`，一轮 = 问 + 答（内含 `TracePanel` 过程）+ 版本条 |
| `frontend/src/components/workbench/AssistantTurn.tsx`（新） | 当轮**唯一**助手气泡外壳（过程 + 文案 + body） |
| `QuestionBar` / `ConfirmBar` / `ParsingCard` / `ProgressCard` / `ErrorCard` | 新增 `bare` 形态；`ConfirmBar` 补「需求详情」按需就地展开 |
| `frontend/src/components/workbench/slotQuestion.ts` | `requirementUiMode` 去掉 `'card'`（执行中不再出只读整卡） |
| `frontend/src/pages/WorkbenchPage.tsx` | 发送即 append；删除 `lastQuestion` / `casualReply` / `changeNote`；当轮渲染成一条助手消息 |
| `frontend/src/styles/workbench.css` | 新增 `.wb-turn-body` 系列；历史版本条从白底卡片降级成助手气泡下方一行字 |
| `backend/app/infra/conversation/repository.py` | `get_messages` 排序 `ORDER BY created_at, id` |
| `frontend/src/api/confirmStream.ts` | confirm 流的 trace 也写进当轮助手消息（此前只喂进度卡，过程列表在确认后是空的） |

### 验证（真机）

- `npx tsc -b` 干净；`npm run lint` 0 error（5 个 warning 均为既有：e2e 文件 + `useExecutionPoll` 导出）；
- 受影响前端单测 **161 passed**（`src/stores/__tests__` + workbench 组件 + `handleSSEEvent` + `confirmStream`）；
- `.e2elogs/probe_p0_final.mjs` **PASS**（13 项判据全绿：一轮一条助手消息 / 发下一句历史仍在 /
  generating 只有进度无整卡 / 详情就地展开 / 两轮出 v1+v2 / F5 后历史仍在 / DB 两条 assistant_text / 0 console error / 0 404）；
- `.e2elogs/probe_p0_chitchat.mjs` **PASS**（闲聊两轮各留一条回复，且不串轮）；
- `.e2elogs/probe_p0_reload.mjs` **PASS**（刷新后 2 问 2 答 + v1 条 + v2 报告；DB 行序 user/assistant 交替）；
- `.e2elogs/probe_hit_test.mjs`：24/25 ok，0 COVERED（唯一非 ok 是表格「展开更多明细」在视口外）；
- `.e2elogs/probe_transcript_ui.mjs`：从左侧点回会话，2 个提问 + 2 个助手气泡 + v1 版本行。

### 落地偏差 3 项

1. **`mergeServerHistory` 第一版有真 bug（实测发现并修复）**：把「服务端这次没返回的本地消息」
   统一追加到末尾 → 闲聊轮的助手消息（服务端不落库）被挪到后面、错挂到下一轮头上
   （两轮闲聊只剩一条回复）。改为**以本地账本为主序**遍历、认领后位置不动，
   服务端多出来的行按 `created_at` 插回正确位置；补两条回归钉子
   （`服务端缺助手行时，本地助手消息留在自己那一轮的位置` / `服务端新行按时间插回正确位置`）。
2. **「需求详情」入口回到了确认那条消息里**（按需展开，默认收起）。2026-11-05 的
   `6ed48b9` 曾把它整个删掉；hand-off P0 又写明「卡片仅作需求详情按需视图」，
   故按后者重建，形态改成一行小链接 + 气泡内就地展开（不再有整卡、不再有 chip/diff）。
3. **发送瞬间不再拉会话消息**：本地已 append，新会话的服务端行还没创建，那次刷新每次都打一个 404
   （真机探针抓到）。历史改由 requirement / 终态两次刷新按 id 合并回来。

### 明确留下的缺口（下一阶段）

- **闲聊回复没有落库**（`app.conversations` 只有 user 行）：F5 之后闲聊那一轮的助手文本会消失
  （报告轮不受影响，assistant_text 已落库）。补法同 D1：`main.py` 闲聊出口写一条
  `message_type='assistant_text'`；
- **过程步骤（trace）只在内存里**：刷新后历史轮次的「思考了 N 步」消失（正文仍在）。
  要留下就得把 trace 落库（`agent.report_version.metadata` 或独立表），属于新契约，另开 plan。
