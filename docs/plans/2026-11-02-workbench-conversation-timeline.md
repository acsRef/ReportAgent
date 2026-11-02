# 工作台改成真实对话时间线（历史轮次不再消失）

> 状态: 已完成

## Context

用户实测反馈三连：「新提交的对话直接到开头了，以前的报告和对话都不见了」「第一个报告应该保留，你这样相当于直接更新了」。

排查结论：**数据没丢**。`agent.report_version` 是 append-only，实测同一会话 v1/v2/v3 三行、`parent_version` 链完整（v2→1、v3→2），`app.conversations` 里 6 条消息也都在。

问题在渲染层：`WorkbenchPage` 的画布是**单轮视图**——

```tsx
{lastQuestion && phase !== 'idle' && <UserBubble text={lastQuestion} />}   // 只画最后一次提问
{requirement && <RequirementCardView … />}                                 // 只画一张卡
{selectedReportVersion != null && <ReportPaper … />}                       // 只画一个版本
```

新提问覆盖 `lastQuestion`，于是新问题出现在滚动区**最顶端**、旧问题气泡与旧报告块消失。更根本的是：`/sessions/{sid}` 返回的 `messages`（完整对话）**前端从来没渲染过**——全仓只有 legacy 的 `ChatView` 渲染消息列表，v2 工作台拿到后直接丢弃。

所以这不是"修个 bug"，是 v2 工作台缺了对话流：它叫 conversation workbench，但没有 conversation。

## Design

### 1) 把 messages 放进 store

`AnalysisState` 增加 `messages: ConversationMessage[]` + action `messages/received`。
`loadSessionSnapshot()` 在恢复 requirement/version 的同时 dispatch 消息；`session/selected` 与 `analysis/reset` 按既有语义清空（它们本来就重置 entire state）。

### 2) 历史轮次 = 问题 ↔ 版本按下标配对

新组件 `ConversationTimeline`：
- 输入：`turns: { key, question, version: ReportVersion | null }[]`
- 每轮渲染：用户气泡（复用 `UserBubble`）+ 紧凑报告条（`v{n} · 标题 · 状态`）+「展开报告 / 收起」
- 展开时才挂 `<ReportPaper version={n} />`（每份 ReportPaper 自己按版本拉数据；默认收起，避免一屏挂十个报告）

配对规则：`messages` 里的 user 文本按顺序 = 第 1..n 轮；`reportVersions` 也是 1..n。按下标配对，缺版本的那轮显示「未生成报告」（chitchat / 失败轮）。

### 3) 当前轮仍用现有 live 组件，但排在历史之后

- 历史 = 除最后一轮之外的所有轮次。
- 最后一轮继续用现有 `UserBubble + RequirementCardView + ProgressCard/ErrorCard + ReportPaper`，保证确认、重试、进度这些交互零改动。
- 关键：发送新问题时记录 `turnBaseVersion = selectedReportVersion`，**当前轮的报告只显示"这次新产生的版本"**，否则新问题下面会吊着上一轮的旧报告。

### 4) 一轮结束后刷新 messages

`phase` 变成 `report_ready` / `error` 时调一次 `refreshMessages(sessionId)`，让刚结束的这轮进入历史。用 ref 记录已刷新的 (sessionId, 版本数) 组合，避免重复请求。

### 5) 顺手修一个真 bug

`handleSelectSession` 把 `lastQuestion` 设成 `first_message`（会话第一条），但 live 气泡应该显示**最后一次提问**。重进会话时会显示错的问题 → 改成取最后一条 user 消息。

## Files to change

| 文件 | 变更 |
|---|---|
| `frontend/src/types/analysis.ts` | 新增 `ConversationMessage` |
| `frontend/src/stores/analysisReducer.ts` | `messages` 字段 + `messages/received` |
| `frontend/src/stores/sessionEvents.ts` | 快照恢复时 dispatch messages；新增 `refreshMessages()` |
| `frontend/src/components/workbench/ConversationTimeline.tsx` | 新增：历史轮次时间线 |
| `frontend/src/styles/workbench.css` | `.wb-history*` 少量样式（复用 tokens） |
| `frontend/src/pages/WorkbenchPage.tsx` | 渲染时间线；turnBaseVersion；终态刷新 messages；修正 lastQuestion |
| `frontend/src/stores/__tests__/analysisReducer.test.ts` | messages 增删 + reset 语义 |
| `frontend/src/components/workbench/__tests__/ConversationTimeline.test.tsx` | 新组件 3 例 |
| `frontend/src/pages/__tests__/WorkbenchPage.test.tsx` | 不动（只测 useExecutionPoll） |

## Reused existing utilities

- `UserBubble`（`components/workbench/MessageBubbles.tsx`）——不新写气泡
- `ReportPaper`（按 `sessionId + version` 自取数，可多实例）——展开时直接挂载
- `sessionEvents.loadSessionSnapshot` / `fetchSession`——沿用快照来源
- 既有设计 token 与 `wb-quiet-btn` / `wb-req-status` 风格

## Verification

```bash
cd frontend && npx vitest --run src/components/workbench/__tests__/ConversationTimeline.test.tsx \
  src/stores/__tests__/analysisReducer.test.ts
cd frontend && npm run test:run && npx tsc -b && npm run lint
```

真机验证（真 LLM + PG + 前端）：
1. 新建会话问「2024年各区域销售额」→ 确认 → v1；
2. 输入「增加华东对比」→ 确认 → v2；
3. **期望**：v1 的问答仍在画布上方（问题气泡 + 「v1 · 报告」条），新提问出现在下方，v2 的报告在最新位置；
4. 点「展开报告」能回看 v1 内容；刷新页面重进会话，顺序与内容不变；
5. 截图留档。

## Explicitly NOT doing

- 不做聊天式 AI 回复气泡（assistant 文本类消息目前只有 requirement_patch / report_version，没有自由文本）
- 不改 SSE 协议、不改后端任何接口
- 不做虚拟滚动 / 分页加载历史（单会话历史量级几十轮，直接渲染）
- 不改 `ReportPaper` 内部渲染
- 不动 legacy 的 `ChatView`

## 落地记录（2026-11-02）

### 一个差点让改动静默失效的坑

`analysisStore.dispatch` **不是**把 reducer 结果整体合并，而是**逐字段枚举**写回 immer draft：

```ts
draft.phase = next.phase
draft.reportVersions = next.reportVersions
…
```

新增 `messages` 后我只改了 reducer，没改这处枚举 → 真实浏览器里 `messages` 恒为 `[]`、时间线空白，而单测（直接调 reducer）全绿。补上 `draft.messages = next.messages` 才通。**教训：新增 state 字段必须同时改 store 的写回枚举，否则测试与真实行为脱节。**

### 真机验证（chromium + 真后端）

打开一个有 3 个版本的会话：

```
历史轮次数量: 2
历史问题: ["2024年各区域销售额", "增加华东区域对比，看看为什么这么多"]
历史版本: ["v1", "v2"]
展开按钮数量: 2
展开后报告含 REPORT = true | 长度 = 625
```

截图：`.e2elogs/timeline-top.png`（顶部：v1/v2 两轮历史 + 当前轮）、`.e2elogs/timeline-expanded.png`（展开历史报告）。

### 回归

- frontend **323 passed**（+4 时间线组件 + 2 reducer 用例），`tsc -b` 0 error，`oxlint` 0 error
- 后端未改动

### 残留（下一轮）

- 当前轮的需求卡仍是**会话里唯一那张 draft**（调整文本不重新解析），所以切到 v3 时卡面还是首轮的需求描述——这是「问题 2：答非所问」的一部分，未在本轮修。
- 画布在 `report_ready` 时仍自动滚到底部，想看历史需要往上滚（可考虑加"跳到最新/最早"）。
