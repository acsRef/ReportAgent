> 状态: 进行中

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
