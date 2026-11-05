> 状态: 已完成

# 思考链路显示：把「思考了 N 步」换成可展开的「已思考（用时 N 秒）」+ 模型原文

## Context（背景）

2026-11-05 用户贴了一张参考截图（另一个聊天产品的思考块）：折叠头是「已思考（用时 4 秒）」，
展开后是**模型自己的推理原文**（markdown 段落、编号、加粗），而不是「第几步做了什么」。

本项目现状（`TracePanel`）：

- 摘要行是 `思考了 21 步 · 16.5s`（讲的是**步骤数**，不是「思考了多久」→ 用户读不出「这轮想了多久」）；
- 模型原文思维链（`reasoning_content`，2026-11-05 已进 trace 的 `reasoning` 字段）被塞在
  **每一步内部**，用等宽 `<pre>` 渲染、限高 260px —— 既不在主视野里，也读起来像日志；
- 真机实测该字段每个需求轮只有一条（节点 `理解意图 · 思考`，约 1200 字纯文本），
  所以「链路」实际上就是这一段原文，没必要按步骤散落。

用户结论：**要参考截图那种「已思考（用时 N 秒）」+ 展开看推理链路**。

## Design（设计）

### D1 头部改成「时间」而不是「步骤数」

| 状态 | 文案 |
|---|---|
| 执行中 | `正在思考（<当前步骤>）` |
| 正常结束 | `已思考（用时 16.5 秒）` |
| 失败 | `思考中断于「解析需求」（用时 12 秒）` |
| 用户停止 | `已停止于「撰写报告内容」（后台继续）` |

时长 = `entries[last].timestamp - entries[0].timestamp`：<1s → `不到 1 秒`，<60s → `N.N 秒`，
≥60s → `M 分 S 秒`。纯函数 `formatThoughtDuration`（可单测）。

### D2 展开后：先「思考链路」原文，再「执行步骤」结论

```
▾ 已思考（用时 16.5 秒）
  我们只需要输出 JSON。用户原话：「2024年各区域销售额」……     ← 思考链路（模型原文）
  ── 执行步骤 ──
  ✓ 安全检查   通过（LOW，风险分 0）
  ✓ 理解意图 · 思考   模型原文 1214 字
  ✓ 解析需求   时间 2024年｜口径 销售额｜待确认 0 项
```

- **思考链路**：把所有 `entry.reasoning` 按到达顺序聚合。多个节点都有原文时，每段前加
  节点名小标题；只有一个节点时不加（避免「一个标题 + 一段话」的噪声）。
- 渲染策略（纯函数 `looksLikeMarkdown`）：原文含 markdown 痕迹（`**` / `# ` / 列表 /
  空行分段）→ `react-markdown` 渲染；纯文本 → `white-space: pre-wrap` 的段落渲染
  （真机抓到的 1214 字原文是纯文本，用 markdown 会把换行吃掉）。
- **执行步骤**保留（每步一行 + 结论），但**移除步骤内嵌的等宽原文块**——同一段原文不出现两次。

### D3 默认展开策略：当轮展开、历史收起、用户优先

- 当轮（`WorkbenchPage` 传 `autoOpen`）默认展开——用户刚问完就能看见「它在想什么」；
- 历史轮次（`ConversationTimeline`）默认收起——只留一行「已思考（用时 N 秒）」；
- 用户点过折叠头之后以用户的为准（`touched` 标记），不再被 `autoOpen` 抢回去。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `frontend/src/components/workbench/reasoningText.ts`（新） | `formatThoughtDuration` / `looksLikeMarkdown` / `thoughtSummary`（纯函数，便于穷举） |
| `frontend/src/components/workbench/TracePanel.tsx` | 头部文案换 D1；展开体改成「思考链路 + 执行步骤」；删除每步内嵌原文；`autoOpen` prop |
| `frontend/src/pages/WorkbenchPage.tsx` | 当轮 `TracePanel` 传 `autoOpen` |
| `frontend/src/components/workbench/ConversationTimeline.tsx` | 历史轮次不传 `autoOpen`（默认收起） |
| `frontend/src/styles/workbench.css` | 新增 `.wb-chain-*`（原文档位、行高、限高 320px 滚动、段落间距）；删掉 `.wb-think-reasoning*` |
| `frontend/src/components/workbench/__tests__/TracePanel.test.tsx` | 文案断言更新 + 新增链路渲染/默认展开用例 |
| `frontend/src/components/workbench/__tests__/reasoningText.test.ts`（新） | 时长格式 / markdown 判定 / 摘要文案 |
| `.e2elogs/probe_reasoning_ui.mjs` | 改为验证「已思考（用时…）+ 链路正文 + 步骤结论」 |

## Reused existing utilities（复用工具）

- `TimelineEntry.reasoning`（后端 `infra/execution/reasoning.py` 通道 → `progress.py` 收尾帧）——
  **数据链路零改动**，本次只改呈现；
- `react-markdown` + `remark-gfm`（`MessageBubbles` / `AssistantTurn` 已在用）；
- `TracePanel` 现有的折叠头/步骤列表结构与 `wb-think-*` 类名；
- 真机探针脚手架 `.e2elogs/probe_reasoning_ui.mjs`。

## Verification（验证）

1. `npx tsc -b`、`npm run lint`；
2. `npx vitest --run src/components/workbench/__tests__/TracePanel.test.tsx src/components/workbench/__tests__/reasoningText.test.ts`；
3. 真机 `node .e2elogs/probe_reasoning_ui.mjs`：当轮默认展开、头部是「已思考（用时 N 秒）」、
   正文是模型原文（与 SSE `reasoning` 字段前 40 字一致）、步骤区仍有结论；
4. 双轮真机：第一轮结束后（变成历史）思考块收起，只剩一行头；点开仍能读到原文。

## Explicitly NOT doing（不做事项）

- **不做逐字流式 CoT**：后端按节点一次性返回 `reasoning_content`，要流式得改 provider 调用方式
  （新契约），不在本次范围；
- **不落库 CoT**：刷新后历史轮次的思考块仍只有头部（正文只在内存），与 hand-off 已知缺口一致；
- 不改后端 trace 契约、不动 SSE 事件面、不新增事件类型。

---

## 落地记录（2026-11-05 21:10）

### 实际改动

| 文件 | 改动 |
|---|---|
| `frontend/src/components/workbench/reasoningText.ts`（新） | `formatThoughtDuration` / `thoughtDurationMs` / `thoughtSummary` / `reasoningBlocks` / `looksLikeMarkdown`（全纯函数） |
| `frontend/src/components/workbench/TracePanel.tsx` | 头部文案改「已思考（用时 N 秒）」等四种；展开体拆成 **思考链路**（模型原文）+ **执行步骤**（结论）；删除每步内嵌的等宽原文块；新增 `autoOpen` + `touched` 交互（用户点过以用户为准） |
| `frontend/src/pages/WorkbenchPage.tsx` | 当轮 `TracePanel` 传 `autoOpen` |
| `frontend/src/components/workbench/ConversationTimeline.tsx` | 历史轮次不传 `autoOpen`（默认收起） |
| `frontend/src/styles/workbench.css` | 新增 `.wb-think-body` / `.wb-chain-*`（正文排版、限高 480px、`pre-wrap` 纯文本）；删除 `.wb-think-reasoning*`；头部改 teal-deep 可点态 |
| 单测 | `TracePanel.test.tsx` 文案与链路用例（12 例）、新增 `reasoningText.test.ts`（14 例） |
| `.e2elogs/probe_reasoning_ui.mjs` | 改为验证 8 项判据（默认展开 / 头部是时间 / 链路是原文 / 步骤结论 / 折叠交互 / 下一轮历史收起） |

### 验证（真机）

`node .e2elogs/probe_reasoning_ui.mjs` **PASS 8/8**：

```
turn1 default: 头部「已思考（用时 9.2 秒）」，aria-expanded=true
               链路首句「我们只需要输出JSON。用户原话："2024年各区域销售额"……」（= SSE reasoning 原文）
               步骤 10 条、结论 10 条（含「模型原文 1130 字」）
collapse/reopen: false → true（用户可收起再打开）
turn2 heads: 上一轮「▸ 已思考（用时 9.2 秒）」(收起) / 本轮「▾ 正在思考（理解意图）」(展开)
```

截图：`.e2elogs/reasoning_chain_turn1.png`（展开态）、`reasoning_chain_turn2.png`（历史收起）。

其他门禁：`npx tsc -b` 干净、`npm run lint` 0 error（5 个既有 warning）、`npm run build` 通过、
`src/{components,pages,stores}` 单测 **277 passed**。

### 落地偏差 2 项

1. **纯文本 CoT 不走 markdown**：真机抓到的 `deepseek-v4-flash` 原文是 1130 字**无换行纯段落**，
   交给 markdown 渲染会把句子挤成一坨 → 用 `looksLikeMarkdown` 判定，纯文本走
   `<p class="wb-chain-text">` + `pre-wrap`；markdown 原文（参考截图那种）才用 `react-markdown`。
2. **链路上限 480px 内部滚动**（原计划 320px）：320px 会把常见 CoT 截成半句，480px 让
   ≈1200 字整段可见，只有接近后端 4000 字上限时才滚动。
