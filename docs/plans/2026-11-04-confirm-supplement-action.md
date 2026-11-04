> 状态: 已完成

# 确认条给出「补充或修改」：这一屏不是只有一个签字的按钮

## Context（背景）

用户截图 + 原话（2026-11-04）：

> **「难道我就只能确认并生成报告吗 没有补充吗？」**

截图里确认消息只有两个可点项：`确认并生成报告`（主按钮）和 `需求详情`（链接）。
而「直接说一句补充、让它重新解析」这条路**在实现上是存在的**
（`chatModeForPhase('awaiting_confirm') === 'supplement'`，输入框 placeholder 也写着
「继续补充或修改需求，确认后再生成」），但**界面上看不见**——用户只能靠猜。

这是呈现层的可发现性缺陷，不是能力缺失。

## Design（设计）

纯前端，不动后端契约、不加新事件。

在 `ConfirmBar` 的 foot 里补**第二个动作**：

```
[ 确认并生成报告 ]   ( 补充或修改 )   需求详情
     主按钮            次要按钮        链接
```

- 「补充或修改」=`onSupplement()`，把焦点交回输入框（与 `QuestionBar` 里
  「都不是，我补充说明」同一套语义，保持一致）；
- 附 `title` 提示：「直接在下面输入，例如「按日看」「再加上华南」」——一句话说明**怎么补**；
- `busy`（PATCH/确认进行中）时禁用，避免一边确认一边开新补轮；
- 链接顺序固定：主按钮 → 次要按钮 → 详情链接，视线从左到右「先决定、再改、最后看细节」。

样式新增 `.wb-ask-secondary`（描边胶囊，hover 变 teal），复用现有 token，不引新视觉语言。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `frontend/src/components/workbench/ConfirmBar.tsx` | 新增 `onSupplement?` 与「补充或修改」次要按钮（busy 时禁用） |
| `frontend/src/pages/WorkbenchPage.tsx` | 传 `onSupplement={() => composerRef.current?.focus()}` |
| `frontend/src/styles/workbench.css` | `.wb-ask-secondary` 样式；`.wb-ask-foot` 允许换行（窄屏三个动作不挤） |
| `frontend/src/components/workbench/__tests__/ConfirmBar.test.tsx` | 「补充或修改」回调 + busy 禁用两条断言 |

## Reused existing utilities（复用工具）

- 复用 `chatModeForPhase` 的 supplement 语义（后端早已支持，本次只是把它显性化）；
- 复用 `composerRef` 已有焦点通道（`QuestionBar.onSupplement` 同款）；
- 复用 `.wb-primary` / `.wb-ask-link` 样式与 `--teal*` token。

## Verification（验证）

1. `npm run test:run`（全量）+ `npx tsc -b` + `npm run lint`。
2. `node .e2elogs/probe_details_inline.mjs` 增两条判据：
   - 「确认条有可见的补充入口」；
   - 「补充入口把焦点交给输入框」（`document.activeElement` 的 `aria-label === 输入分析问题`）。
3. 截图 `.e2elogs/details_inline_collapsed.png` 可见三动作一行排布。

## Explicitly NOT doing（不做事项）

- 不做「点 chip 就地改某一项」——改动入口是「用一句话说」，多一条表单级编辑路径会与
  「一次一问」的澄清流程打架，且后端 supplement 已经能处理自由表述；
- 不给 `QuestionBar` 换文案（「都不是，我补充说明」已验证有效，保持）；
- 不在确认后新增「返回修改」按钮（确认即执行，改需求走同一输入框，语义已覆盖）。
