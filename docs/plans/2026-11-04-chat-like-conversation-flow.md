> 状态: 已完成

# 把工作台改成「正常对话」：上一轮答案不折叠、需求卡退到详情、新草稿不锁

## Context（背景）

用户看自己的截图后提了两点（2026-11-04）：

1. **「第一个版本发出来之后，第二轮对话发了之后就立马收缩」**：上一轮报告在下一轮一开始就被
   折成一行 `v1 … 已完成 / 展开报告`，刚看到的内容直接消失；
2. **「希望这个操作是正常的，类似于豆包的那种正常的对话」**：一张大卡片（还带
   「需求已确认并锁定，报表生成中」）夹在对话中间，不像聊天。

## 现状取证（代码 + 截图）

| 现象 | 成因 |
|---|---|
| 上一轮报告被折成一行 | `ConversationTimeline` 的历史轮次**默认全部收起**（2026-11-02 为省渲染而设计：一屏挂十个 `ReportPaper` 会狂拉接口），实现里 `openKey` 初值 `null` → 每个历史轮都是「展开报告」按钮 |
| 新草稿被当成「已锁定」 | `WorkbenchPage.cardLocked` 把 `phase === 'report_ready'` 也算成锁定。可 `report_ready` 是**上一轮**的终态，重发追问后新草稿 (`status=complete`) 也被判 locked → 整卡渲染成只读、没有「确认并生成报告」按钮，只剩一句「已锁定」 |
| 大卡片夹在对话里 | `showCard = cardLocked \|\| detailsOpen`；一旦 locked 为真就渲染整卡。Phase 5 的「一次一问 + 确认条」因此被整卡盖住 |

## Design（设计）

纯前端改动，**不动后端契约与 SSE**。

### D1 上一轮答案默认展开（对齐「正常对话」）

`ConversationTimeline` 增加 `expandLatest?: boolean`（默认 true）：**最后一轮历史**直接挂
`ReportPaper`（等价于用户点了「展开报告」），更早的轮次仍可折叠。
这样「提问 → 答案」在时间线上连续可读；只有更早的历史才收起（渲染预算仍受控：
默认最多多挂 1 个 `ReportPaper`）。

### D2 新草稿不再被上一轮的 `report_ready` 锁住

`cardLocked` 去掉 `phase === 'report_ready'`：
- 锁定的唯一真实来源是**卡片自身** `requirement.status === 'locked'`；
- 执行中（`generating` / `adjusting`）也锁，避免执行期间编辑草稿；
- `report_ready` 只表示「上一轮报告已出」，与当前草稿是否可编辑无关。

配套：`showCard = cardLocked || detailsOpen`。改完之后正常流程里**不再渲染整卡**——
用户在对话里看到的是「一次一问」或一行确认条；要看完整卡点「展开详情」。

### D3 折叠态文案与位置

历史轮次折叠行保留（早期轮次仍需要），但把标签从「展开报告」改成
「查看 v{n} 报告」，并给最后一轮加 `展开` 状态标记，避免「点都点不到」的错觉。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `frontend/src/components/workbench/ConversationTimeline.tsx` | `expandLatest` 支持（最后一轮默认展开）+ 折叠文案 |
| `frontend/src/pages/WorkbenchPage.tsx` | `cardLocked` 去掉 `report_ready`；给 Timeline 传 `expandLatest` |
| `frontend/src/components/workbench/__tests__/ConversationTimeline.test.tsx` | 最后一轮默认展开 / 更早轮次仍可折叠 |
| `frontend/src/pages/__tests__/WorkbenchPage.test.tsx` | 「report_ready 下新草稿可编辑」 |
| `docs/plans/README.md`、`docs/hand-off.md` | 索引 + 交接 |

## Reused existing utilities（复用工具）

`ConversationTimeline`（已有折叠/展开）、`ReportPaper`（按版本拉数）、
`isDraftReadyForReview` / `pickNextQuestion` / `ConfirmBar`（Phase 5 已落）、
`useExecutionPoll` / `loadSessionSnapshot`（后台刷新）。

## Verification（验证）

- `cd frontend && npx vitest --run src/components/workbench/__tests__/ConversationTimeline.test.tsx src/pages/__tests__/WorkbenchPage.test.tsx`
- `npm run test:run`、`npx tsc -b`、`npm run lint`
- 浏览器（两轮真实对话，`.e2elogs/probe_chat_flow.mjs`）：
  1. 第一轮出报告后，**报告仍在页面上且完整可读**；
  2. 发第二轮追问，**上一轮报告不消失**（不折成一行），新草稿以「一次一问 / 确认条」出现，
     且**有「确认并生成报告」按钮**（不被误锁）；
  3. 第二轮出报告后，页面顺序为「问 → 答 → 问 → 答」。

## Explicitly NOT doing（不做事项）

- 不把需求卡删掉（仍可「展开详情」查看/编辑）；
- 不改 `ReportPaper` 的取数逻辑、不改后端 SSE / 卡片契约；
- 不做「聊天式流式打字机」等纯装饰动画；
- 不重构整个历史时间线（只在最后 N=1 轮默认展开）。

## 落地记录（2026-11-04）

实际改了三件事（第 3 件是实测撞出来的真 bug，不在原计划里）：

1. **`ConversationTimeline.expandLatest`（默认 true）**：最后一轮历史直接挂 `ReportPaper`；
   更早的轮次仍折叠，且折叠文案从「展开报告」改成「查看 v{n} 报告」。
   用户显式收起过的轮次用 `closedKey` 记住，默认展开的最后一轮也能收起。
2. **`cardLocked` 去掉 `report_ready`**：锁定的唯一真实来源是卡片自身 `status === 'locked'`
   （+ 执行中 `generating/adjusting`）。此前上一轮的 `report_ready` 会把**新草稿**判成锁定，
   渲染成只读大卡（只剩一句「已锁定」、没有确认按钮）。
3. **需求区形态判定收敛成纯函数 `requirementUiMode(phase, card, locked, detailsOpen)`**
   （`slotQuestion.ts`）→ `question | confirm | card | none`。
   只有 `awaiting_confirm` / `awaiting_missing` 才出提问条/确认条；`parsing` 一律 `none`。
   **起因**：实测两轮对话里，上一轮的「确认条」在发新问题后仍留在屏幕上，一点就
   `POST /confirm → 409 Conflict`（后端日志实证 SESSION_BUSY，因为新一轮解析任务占着会话槽）。

### 验证

| 层 | 命令 | 结果 |
|---|---|---|
| 前端单测 | `npm run test:run` | **376 passed**（新增 8 例：默认展开 / 手动收起 / ui mode 含 409 回归钉子） |
| 类型/风格 | `npx tsc -b` / `npm run lint` | 0 error |
| 浏览器两轮验收 | `node .e2elogs/probe_chat_flow.mjs` | 上一轮报告保持展开 ✔ 无只读大卡 ✔ 解析后出「一次一问」✔ 两轮都有报告（v1+v2）✔ |

页面顺序实测：`问 → 答(v1，展开) → 问 → 解析中(只有解析卡) → 一次一问/确认条 → 答(v2)`。
