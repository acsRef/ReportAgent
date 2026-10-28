> 状态: 进行中

# 需求卡"不要迷惑用户" + 发送超时假警报修复

## Context

浏览器实操暴露三个**用户可感知、但都不是"链路坏了"**的问题：

1. **`范围待补充` 是假告警**。用户问「2026年9月各品类销量」——没限定区域本来就等于全部，后端 `scope: []` 的语义是「未限定（默认 ALL）」（`requirement_prompts.py` 维度规则明写"可缺省"），`missing_fields` 也是空的。但前端把空数组一律渲染成"范围待补充"，读起来像强制要求填。

2. **同一个问题问两遍**。修完第 1 点后实测，卡片同时出现 `missing_fields: metric`（要你选指标）和 `field_meaning:销量` 的 assumption（要你确认口径）——指标和口径是同一件事。而且 assumption 的 `alternatives` 后端解析了、前端从不渲染，用户没法一键定口径，只能点"接受"后让 SQL 生成器自己猜。

3. **「请求超时」是假警报，且与"继续回答"矛盾**。`WorkbenchPage.handleSend` 用裸 `setTimeout(60_000)`：timer 从不清理，60s 后会把**正在进行中的下一轮**判成超时；超时只 `setSending(false)` 而**不 abort 流**，于是 UI 说超时、SSE 继续推、4 秒后结果照常出现；解锁发送框后还能重复提交。trace 实证：19:06:17 那轮 5.7s、19:07:09 那轮 11.9s，**没有任何一次超过 60 秒**。

顺带发现一个真缺陷：`REQUIREMENT_PARSE_V1["output_schema"]` 的 JSON 示例写的是 `{{` `}}`（转义残留），而该段**不参与 `.format()`**，双大括号原样进 prompt。模型若跟输出 `{{...}}` → `safe_json_parse` 失败 → `_fallback_missing_card`（5 个字段全待补充、confidence 0），与"卡片全空"症状同形。

## Design

### 1. 需求卡文案：区分「未限定」与「真缺失」

`RequirementCardView` 渲染 `scope` 时按 `missing_fields` 判定：

- `scope` 在 `missing_fields` 里 → `范围待补充`（真的缺）
- `scope` 为空且不在 → `未限定（全部区域）`

不改成"全部区域"，是因为 `scope: []` 也可能是解析漏了；"未限定"描述事实，不替用户断言。

### 2. 口径一键选（方案 A）

三层配合，不新增契约字段：

| 层 | 改动 |
|---|---|
| prompt 维度规则 | 指标名已在 `target_metrics` 里时**禁止**再把 `metric` 列进 `missing_fields`——口径歧义统一走 assumption，禁止一问两遍 |
| prompt 字段释义规则 | `field_meaning:*` 的 `alternatives` **必须**给 2-3 个候选（`label` 人话 + `value` 可落 SQL 的口径），想不出候选才允许空数组 |
| prompt output_schema | 示例补 `alternatives` 形状；`{{ }}` → `{ }` 修正 |
| 前端 | 假设下渲染可点 chip「选：{label}」；点一下 = `accepted=true` + 正文追加「→ 已选：{label}」 |

**为什么写进 text 而不新增字段**：`_format_confirmed_requirement` 只把「已接受假设的 text」拼进 SQL plan prompt；写 text 才能真正到达下游。新增字段要同步改后端模型、格式化器、`_mask_card_pii` / `card_guard_text` 两个安全面、以及前后端镜像测试——为一个 UI 动作扩大契约面不划算。重复点击先剥掉旧的 `→ 已选：` 后缀，避免叠加。

### 3. 发送超时：只提示，不判死

新增 `src/utils/sendTimeout.ts` 的 `createSendTimeoutGuard(ms)`：

- `arm(cb)`：重设计时，作废上一轮（序号守卫，stale 回调被静默丢弃）
- `clear()`：取消计时并递增序号

`WorkbenchPage` 用法：

- `handleSend` 里 `arm(...)` → 超时**只弹提示**「响应时间较长，任务仍在处理中」，**不改 sending、不 abort 流**
- SSE 收到 `done` / `error` → `clear()`
- 「停止」按钮 → `clear()`

这样「说超时了但结果又出来」不再存在：提示与结果可以并存，且不会解锁发送框造成重复提交。

## Files to change

| 文件 | 变更 |
|---|---|
| `backend/app/agent/prompts/requirement_prompts.py` | 维度规则加"禁止一问两遍"；字段释义强制 alternatives；output_schema 单大括号 + 示例 |
| `frontend/src/components/workbench/RequirementCardView.tsx` | scope chip 语义化；assumption 渲染 alternatives；`chooseAssumptionAlternative` 写入 text |
| `frontend/src/components/workbench/__tests__/RequirementCardView.test.tsx` | 新增 2 例（未限定 / 真缺失）+ 1 例（一键选口径） |
| `frontend/src/utils/sendTimeout.ts`（新） | 超时提示闸（序号守卫） |
| `frontend/src/utils/__tests__/sendTimeout.test.ts`（新） | 4 例（到点 / clear / 重设不串台 / 过期回调失效） |
| `frontend/src/pages/WorkbenchPage.tsx` | 接入 guard；终态 clear；停止 clear；删掉裸 setTimeout |

## Reused existing utilities

- `frontend/src/components/workbench/RequirementCardView.tsx` 已有 `setAssumptionAccepted` / `onChange` 单向数据流，alternatives 复用同一通道，不引入新状态。
- `RequirementAssumption.alternatives`（`RequirementOption{label,value}`）**早已在契约里**（含 `test_requirement_card_mirror.py` 字段钉），本次只是终于渲染它。
- `isDraftReadyForReview` 已经要求 `accepted !== null`，一键选天然满足，无需改判定。
- `toast`（atelier ToastProvider）承载提示，不新增 UI 组件。
- 后端 `_format_confirmed_requirement` 的「用户已接受的假设」通路直接消费改写后的 text，SQL prompt 零改动。

## Verification

1. **prompt 契约**：`pytest tests/contracts/test_prompt_equivalence.py tests/contracts/test_prompt_layering.py` → 30 passed（`field_meaning:<字段名>` marker 保留，改文案后一度失败已修回）。
2. **真卡片**（重启后端后实测两条 query）：
   - 「2026年9月各品类销量」→ `missing_fields` 空；assumption `field_meaning:销量` 带 2 个候选（`COUNT(*)` / `SUM(quantity)`）
   - 「2024年各区域销售额排名」→ `missing_fields` 空；候选为 `SUM(payment_amount)` / `SUM(order_amount)`
3. **前端**：`npm run test:run` → 308 passed；`npx tsc -b` 干净；`npm run lint` 0 error。
4. **后端全量离线**：`pytest -m "smoke or contracts"` → 744 passed。
5. **手工**：刷新页面 → 问「2026年9月各品类销量」→ 假设下出现两条「选：…」→ 点一条 → 文本变「… → 已选：商品件数（按销量求和）」→ 右侧「✓ 已接受」→「补充完成，查看确认」可点。

## Explicitly NOT doing

- 不新增 `RequirementAssumption.selected_value` 字段（避免扩散到模型/格式化器/两个安全面/镜像测试）。
- 不把 `scope: []` 渲染成"全部区域"（解析漏判时会是假安心）。
- 不改 `DONE_TIMEOUT_MS`（60s 保持不变，只改语义）。
- 超时后**不** abort 流：`mode=new|supplement` 是同步 SSE，abort 会真丢结果。
- 不动 `target_metrics` 的"业务术语 vs 物理列名"二义（`evaluation` 的 `target_metrics_contains` 仍可能抖动）——留作独立议题。
- 不碰 `model_phase` / `ProgressTraceHandler` 的 `on_chat_model_start` 噪音（另开）。
