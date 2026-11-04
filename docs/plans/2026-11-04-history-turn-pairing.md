> 状态: 已完成

# 历史轮次条「未生成报告」压在当轮消息上方：按时间配对轮次 + 去掉空报告条

## Context（背景）

用户截图（2026-11-04，工作台·等待确认态）：

```
      分析 2024 年华东销售趋势和异常        ← 用户气泡
┌ 未生成报告 ─────────────────────────┐   ← 一条带边框的小条，紧贴下面的助手消息
A 需求清楚了，我按下面这些执行：2024年 按周 销售额 华东 异常检测 [确认并生成报告]
```

用户原话：**「这个未生成报告看着就很奇怪啊 你不觉得的」**。

`.e2elogs/probe_history_note3.mjs` 在真链路复现到两种形态（同一个根因家族）：

| 形态 | DOM | 触发条件 |
|---|---|---|
| A（截图） | `[用户气泡 Q1] [未生成报告条] [助手确认条]` | `messages` 里已有 Q1，但 `lastQuestion` 仍是 `null` → 当轮没被从历史里剔掉，反而以「无版本的历史轮次」渲染 |
| B（多轮） | `[Q1] [未生成报告条] [Q2] [助手确认条]` | 上一轮没产出报告（改了需求但没确认），`version` 配对缺失 → 空条 |

两处独立缺陷：

1. **当轮泄漏进历史**：`WorkbenchPage.historyTurns` 只用 `lastQuestion` 的**字符串相等**判定「哪一轮是当轮」。
   `lastQuestion` 为 `null`（会话从左侧列表点回来、快照 `.then` 尚未回填）时，最后一轮提问被当成历史轮次渲染，
   于是用户既看到历史条、又看不到「当前轮」的语义。
2. **空报告条本来就是噪声**：`ConversationTimeline` 对 `version === null` 的轮次渲染一条
   `未生成报告` 边框条——没有报告就没有可展开的内容，这条小条只贡献视觉干扰，
   而且它长在时间线里，视觉上直接粘在**下一条**助手消息上方，看起来像助手消息的一部分（截图即此）。

附带缺陷 3：版本与轮次是**按下标硬配对**（`version === index + 1`）。只要有一轮没产出报告，
后面所有轮次全部错位 → 有报告的轮次显示 `未生成报告`、没报告的轮次反而抢到别人的版本。

## Design（设计）

纯前端，不动后端契约与 SSE。

### D1 轮次配对改「按时间归属」

新增纯函数模块 `frontend/src/components/workbench/historyTurns.ts`：

- `liveQuestionOf(messages, lastQuestion, phase)` —— 当前轮的问题：
  显式 `lastQuestion` 优先；为空且 `phase !== 'idle'` 时**退回最后一个用户提问**
  （会话恢复窗口期就是这个状态）。这个值同时供给「当前轮用户气泡」与「历史切分点」，
  两者从此不可能打架。
- `buildHistoryTurns(messages, versions, liveQuestion)` ——
  ①用户提问按出现顺序成轮；②**尾部**与 `liveQuestion` 相同的那一轮被剔除（当轮）；
  ③每个版本按 `created_at` 归给「最后一个早于它的用户提问」，时间不可解析时退回下标配对。

时间归属的依据是真实的：`app.conversations.created_at` 与 `agent.report_version.created_at`
同一时钟（服务端 `TimeZone=Etc/UTC`，同一事务内写入，`probe` 已核对 v1 与 assistant 行时间戳完全一致）。

### D2 没有报告的轮次不画空条

`ConversationTimeline` 对 `version === null` 的轮次只渲染用户气泡，不再渲染
`wb-history-report` 边框条（`未生成报告` 文案随之移除）。`status=error` 的版本仍标红。

### D3 当轮气泡与历史切分共用同一个问题

`WorkbenchPage` 用 `liveQuestion` 渲染当轮用户气泡（原来用 `lastQuestion`），
历史时间线用同一个值切分——消除「气泡在历史里、当轮位置空着」的撕裂。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `frontend/src/components/workbench/historyTurns.ts` | 新增：`liveQuestionOf` / `buildHistoryTurns` 纯函数 + 类型 |
| `frontend/src/components/workbench/ConversationTimeline.tsx` | 删除 `version === null` 的「未生成报告」条 |
| `frontend/src/pages/WorkbenchPage.tsx` | `historyTurns` 改用新模块；当轮气泡改用 `liveQuestion` |
| `frontend/src/components/workbench/__tests__/historyTurns.test.ts` | 新增：当轮剔除 / 空报告轮不抢版本 / 时间不可用退回下标 / `lastQuestion=null` 兜底 |
| `frontend/src/components/workbench/__tests__/ConversationTimeline.test.tsx` | 改掉「显示未生成报告」的旧断言，改为「不渲染空条 + 失败仍标红」 |

## Reused existing utilities（复用工具）

- `UserBubble`（`MessageBubbles.tsx`）、`ReportPaper`、`wb-history-*` 样式全部沿用，不改视觉体系；
- `store.messages`（`ConversationMessage`）与 `store.reportVersions`（含 `created_at`）已是现成数据源；
- 复用 `analysisReducer` 的既有 `messages/received` 权限——本改动不新增 action。

## Verification（验证）

1. `cd frontend && npm run test:run`（全量 vitest，含新增 5~6 例）。
2. `npx tsc -b` + `npm run lint` 零错。
3. 浏览器真链路（backend :8100 + frontend :3000）：
   - `node .e2elogs/probe_history_note.mjs`（新会话首轮 → 确认条，DOM 里应无 `未生成报告`）；
   - `node .e2elogs/probe_history_note3.mjs`（逐个点进旧会话，`historyTurns` 里不允许出现 `未生成报告` 条）；
   - `node .e2elogs/probe_chat_flow.mjs`（两轮对话四条判据不回退）。
4. 截图对照：`.e2elogs/hist_fix_*.png`。

## Explicitly NOT doing（不做事项）

- 不动后端 `list_sessions` / 快照接口（`last_message` 语义混乱是后端遗留，但本轮不修）；
- 不做「历史轮次折叠策略」的重新设计（仍是最近一轮展开、更早收起）；
- 不为「无报告轮次」补解释文案（用户明确嫌它奇怪，直接去掉）；
- 不改 SSE 事件面与 phase 状态机。

## 落地记录

- 前端全量 **408 passed**（新增 10 例 `historyTurns.test.ts`，`ConversationTimeline` 旧断言改为「不画空条」），`tsc -b` 0 错、`oxlint` 0 error（5 条既有 warning）。
- 真链路浏览器验收（backend :8100 + frontend :3000）：
  - `probe_hist_fix_shot.mjs`：单轮「等待确认」会话 `historyTurns=0`、`emptyStrips=0`（截图 `.e2elogs/hist_fix_session_0.png`）；
    两轮会话只剩两个提问气泡 + 确认条，`emptyStrips=0`（`.e2elogs/hist_fix_session_2.png`）；
  - `probe_chat_flow.mjs` 四条判据全 true（上一轮展开 / 无只读大卡 / 提问确认条形态 / 两轮报告都在）。
- 过程中发现并绕开的坑：版本归属必须在**含当轮**的全量提问上算，再剔除尾部当轮——
  先剔后配会让当轮拥有的 v2 被算到上一轮头上（`historyTurns.test.ts` 第三例即此回归钉子）。
