> 状态: 进行中

# 「需求详情」就地展开：去掉空气泡与浮空「收起详情」，详情折进那条助手消息

## Context（背景）

用户截图（2026-11-04，工作台·等待确认态点开「需求详情」）：

```
[我]  分析 2024 年华东销售趋势和异常
[A]   ▢ ← 一个空白气泡
                            [ 收起详情 ↑ ]
┌ AGENT REQUIREMENT BRIEF ────────────────┐
│ 需求解析与执行确认        [信息完整·待确认] │   ← 另起的一张整卡
│ …                                       │
│ 预计报告内容                             │   ← 标题下面一片空白
└─────────────────────────────────────────┘
```

用户原话：**「你这个收起详情这里 为什么展开是空白的呢 很丑，尽可能做的人性化一点」**。

四处具体缺陷（全部可在 `.e2elogs/probe_details_inline.mjs` 快照里量化）：

| # | 现象 | 成因 |
|---|---|---|
| 1 | 展开后多出一个**空白气泡** | `requirementUiMode(..., detailsOpen)` 把形态切成 `card`，于是确认条消失，而「只有思考」那条消息的渲染条件（`!questionField && !showConfirmBar`）成立；`TracePanel` 在没有 trace 时返回 `null`，**外层 `.wb-bubble` 照旧渲染** → 空气泡 |
| 2 | 确认按钮在展开时**消失** | 同上：整卡替代了确认条，唯一的动作从原位消失 |
| 3 | 「收起详情 ↑」是**浮空按钮** | 独立的 `wb-detail-back` 行，跟它控制的内容不在一个视觉整体里 |
| 4 | 详情里「预计报告内容」**只有标题、没有内容** | 卡片对每个分组无条件渲染，`expected_blocks`/`dimensions` 为空时就剩一个空壳 |

## Design（设计）

纯前端，不动后端契约与 SSE。

### D1 「详情」不是第四种形态，而是确认条内部的一块内容

`requirementUiMode(phase, card, cardLocked)` 去掉 `detailsOpen` 入参：形态只有
`question / confirm / card / none`，其中 `card` **只剩「执行中只读整卡」一种来源**。
`detailsOpen` 只控制确认条内部是否渲染 `<div class="wb-ask-details">`。

好处：展开/收起时确认按钮、参数小结、思考行全部原地不动，用户不会「展开一下就把动作弄丢」。

### D2 详情用 `RequirementCardView` 的 `inline` 变体

新增 `variant?: 'full' | 'inline'`：

| | `full`（执行中只读） | `inline`（详情） |
|---|---|---|
| 卡片外壳 / 左侧状态条 / 阴影 | 有 | **无**（`.wb-requirement-card.inline` 全部覆盖掉） |
| `AGENT REQUIREMENT BRIEF` + 标题 | 有 | 无（换成一行小字「需求详情」+ 状态 pill） |
| 参数网格 | 2 列 | 2 列，标签 10px |
| 缺失项编辑区 | 有 | 无（问答在对话里问，不在这里填表） |
| 底部动作 | 有 | 无（确认按钮在消息的 foot 里，避免两个同样的主按钮） |

### D3 空分组不渲染

`target_metrics` / `dimensions` / `analysis_methods` / `expected_blocks` 为空时整组不渲染，
不再出现「只有标题、下面空白」的分组。

### D4 没有 trace 就不渲染那条消息

`hasTrace = timeline.length > 0 || timelineNote`：首帧还没到时不再留一个空气泡。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `frontend/src/components/workbench/ConfirmBar.tsx` | `onExpand` → `onToggleDetails` + `expanded` + `details` 插槽；链接文案随展开切换 |
| `frontend/src/components/workbench/RequirementCardView.tsx` | 新增 `variant="inline"`：无外壳/标题/底部动作/缺失区；空分组不渲染 |
| `frontend/src/components/workbench/slotQuestion.ts` | `requirementUiMode` 去掉 `detailsOpen`（`card` 只剩 locked 一种来源） |
| `frontend/src/pages/WorkbenchPage.tsx` | 确认条传入 `expanded/onToggleDetails/details`；删除 `wb-detail-back` 块；空气泡加 `hasTrace` 闸 |
| `frontend/src/styles/workbench.css` | `.wb-ask-details` / `.wb-inline-*` / `.wb-requirement-card.inline` 一套样式；`.wb-detail-back` 退场 |
| `frontend/src/components/workbench/__tests__/{ConfirmBar,RequirementCardView,slotQuestion}.test.*` | 展开/收起、inline 变体、形态判定三组断言更新 |

## Reused existing utilities（复用工具）

- `RequirementCardView` 的参数网格与 `confirmChips` / `methodLabel` 中文标签直接复用（只是换了皮肤）；
- `.wb-message` / `.wb-bubble` / `.wb-ask-*` 既有对话样式体系，不新造视觉语言；
- `TracePanel` 保持不变（仍是气泡里的一行摘要）。

## Verification（验证）

1. `cd frontend && npm run test:run`（全量 vitest）+ `npx tsc -b` + `npm run lint`。
2. 浏览器真链路 `node .e2elogs/probe_details_inline.mjs`：10 条判据——
   展开后无空气泡 / 消息条数不变 / 确认按钮仍在 / 详情在同一条气泡内 / 无浮空收起按钮 /
   没有另起整卡 / 详情读得到参数 / 无空分组 / 收起后回到紧凑态。
3. `node .e2elogs/probe_chat_flow.mjs` 两轮对话四条判据不回退。

## Explicitly NOT doing（不做事项）

- 不改后端 `RequirementCard` 契约（`expected_blocks` 空是数据现状，前端不再画空壳而已）；
- 不给详情加二级 tab / 折叠分组（用户要的是「别占地方、别丑」，不是更多结构）；
- 不改执行中的只读整卡形态（那是「这一轮已经在跑」的状态牌）；
- 不动 `QuestionBar`（一次一问的形态用户已认可）。
