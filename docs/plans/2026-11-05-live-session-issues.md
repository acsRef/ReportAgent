> 状态: 已完成

# 真机一轮暴露的 5 个问题：思考没说完 / 卡住 / MCP 文案 / 理解失败 / 饼图没生效

## Context（背景）

2026-11-05 21:42 用户手动跑了一轮（会话 `189d33ce…`）：`分析 2024 年华东销售趋势和异常`
→ 确认 → v1 报告；再发 `按照月份来划分吧，然后画饼图` → v1 被覆盖成按月柱状图。
用户提了五个问题，逐条核对代码/日志后结论如下（**都不是"界面错觉"，四条是真问题、一条是文案误导**）：

| # | 用户问 | 代码/日志事实 | 定性 |
|---|---|---|---|
| 1 | 思考链路没说完 | `thinking_budget=512` 把模型思维链截断（实测 `reasoning_tokens=508`、`finish_reason=stop`、原文 1616 字以半句结尾），而界面没有任何「被截断」提示 | 真问题（**缺提示**，不是显示 bug） |
| 2 | 第二张图是不是卡了 | 日志 `21:44:58 app.llm.reasoning_client: turn model http failed: The read operation timed out`——`LLM_TURN_TIMEOUT=90`，那一轮真的等了 90s 才回落；期间界面只有「正在思考（理解意图）」一个静态词，没有秒数 | 真问题（**等待无反馈**） |
| 3 | MCP 为什么显示未联通 | MCP 没断：同一轮更早的步骤写着「检索到 3 张表：dim_store、dim_customer、dim_date」，直连探针也是 1.9s 返回 3 张表。补充轮那句话没有新的表/字段线索 → `data_graph._detect_intent` 关键词门短路（沿用上一轮 schema），步骤文案却写成「schema 未连通（MCP 不可达或无需检索）」 | 真问题（**文案把三件事混成一句**） |
| 4 | 为什么说理解失败 | 同 #2 的 90s 超时：`understand_turn` 拿不到 TurnPlan → `_turn_understand` 回落通用解析（步骤 detail「理解失败，回落通用解析」），后续卡片仍生成 | 真问题（**超时后没有更快的一次兜底尝试**） |
| 5 | 我说了要饼图为什么还是柱状图 | `chart_advisor` 只看 QueryResult 的列形态；`apply_chart_preference` 只吃**召回的记忆偏好**，用户这一轮原话「画饼图」从未进入图表决策 | 真问题（**显式图表要求没有通道**） |

## Design（设计）

### D1 思考被预算截断 → 明确标注，不假装完整

`reasoning_client.chat_with_reasoning` 读 `choices[0].finish_reason` 与
`usage.completion_tokens_details.reasoning_tokens`，在 `ReasoningReply` 上补
`reasoning_tokens` / `truncated`（`reasoning_tokens >= budget - 4` 或 `finish_reason == "length"`）。

`understand_turn` 拿到 `truncated=True` 时在原文末尾追加一行
`（思考预算用尽，原文在此截断）`——用户看到的是「模型想不完」，而不是「界面坏了」；
步骤 detail 同步写成 `模型原文 N 字（思考预算用尽，已截断）`。
预算本身**不下调也不上调**（512 是延迟与完整性的折中，env `TURN_THINKING_BUDGET` 可调）。

### D2 90s 等不到 → 禁用思考快速重试一次 + 界面出秒数

- `understand_turn`：第一次失败（`None` = 超时/HTTP 错）且默认开着思考时，
  **立刻再发一次 `enable_thinking=False`（timeout 45s）**——多数情况下能拿回 TurnPlan，
  比直接回落通用解析好；重试成功后在步骤 detail 标「超时 → 快速模式重试成功」。
  两次都失败才回落（保持现状语义：失败不猜）。
- 前端 `TracePanel`：执行中的头部显示**已经等了多久**（`正在思考（理解意图 · 12s）`），
  每秒刷新一次——让「还在跑」和「卡死」在视觉上区分开。

### D3 MCP 文案拆成三种事实

`rag_schema` 记录最近一次检索失败（`last_schema_error()`：code + detail）。
`requirement_analysis_graph._data_agent` 按事实分支：

| 情况 | detail |
|---|---|
| 本轮无检索意图（关键词门） | `本轮沿用上一轮 schema（问题里没有新的表/字段线索）` |
| 检索到表 | `检索到 N 张表：…`（现状） |
| 检索失败 | `schema 检索失败（<code>）：<detail 前 60 字> → 用本地 schema 兜底` |
| 检索成功但零命中 | `未检索到匹配的表 → 用本地 schema 兜底` |

关键词门本身不动（它是成本控制，不是 bug）。

### D4 显式图表要求 → 确定性通道，优先于记忆偏好

`app/report/preference.py` 新增 `explicit_chart_type(text)` /
`apply_explicit_chart_request(chart_config, text)`：
- 词表与 `chart_advisor` 交集：`饼图/饼状图→pie`、`柱状图/条形图→bar`、`表格→table`；
- 否定前缀（「不」「别」「无需」「非」）跳过——「不要表格」不得被读成要表格；
- 复用既有 `apply_chart_preference` 的类型覆盖 + dimensions 键归一（bar 的 x/y ↔ pie 的 category/value），
  数据不支持可视化时照旧让位（保持 table）。

`report_graph._build_output`：
```
用户原话命中显式图表要求 → 只按它走（记忆偏好不再二次覆盖）
否则 → 现有 apply_chart_preference(report_preferences)
```

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/llm/reasoning_client.py` | `ReasoningReply` 补 `reasoning_tokens` / `truncated`；解析 finish_reason + usage |
| `backend/app/agent/turn_understanding.py` | 截断标注；超时后一次「不思考」快速重试；`meta` 出参记录走的是哪条路径 |
| `backend/app/agent/requirement_analysis_graph.py` | `_turn_understand` 的 detail 带截断/重试说明；`_data_agent` 三/四分支文案 |
| `backend/app/tools/rag_schema.py` | `last_schema_error()` + 失败记录 |
| `backend/app/report/preference.py` | `explicit_chart_type` / `apply_explicit_chart_request` |
| `backend/app/agent/report_graph.py` | 显式图表要求优先于记忆偏好 |
| `frontend/src/components/workbench/TracePanel.tsx` | 执行中头部带等待秒数（1s 心跳） |
| `frontend/src/components/workbench/reasoningText.ts` | `thoughtSummary` 支持 `elapsedSec` |
| 测试 | `test_trace_store` 同目录补 preference/thinking 用例；前端 `reasoningText`/`TracePanel` 用例 |

## Reused existing utilities（复用工具）

- `app/report/preference.py::apply_chart_preference`（类型覆盖 + dims 键归一，已覆盖 {pie,bar,table} 词表）；
- `reasoning_client` 的直连通道（只加字段，不改调用方式）；
- `rag_schema` 现有 MCP 通道 + `MCPBoundaryError` 分类（只把 code/detail 暴露出来）；
- 前端 `TracePanel` / `reasoningText`（本轮刚建的思考块）。

## Verification（验证）

1. 离线：`pytest backend/tests/smoke backend/tests/contracts -q`；前端受影响单测；
2. 真机 A（饼图）：`分析 2024 年华东销售趋势和异常` → 确认 → `按月份看，用饼图` → 确认 →
   报告图表类型必须是 `pie`（查 `agent.report_version.report_payload`）；
3. 真机 B（MCP 文案）：补充轮步骤 detail 不再是「MCP 不可达」；
4. 真机 C（秒数）：执行中头部出现 `· Ns`；
5. 真机 D（截断标注）：思考块末尾出现「思考预算用尽」提示（若该轮被截断）。

## Explicitly NOT doing（不做事项）

- 不调大 `thinking_budget` / `LLM_TURN_TIMEOUT`（延迟与完整性是取舍，交 env 调）；
- 不改 `chart_advisor` 词表、不新增折线图（前端 renderer 未覆盖）；
- 不重写 `data_graph` 关键词门（成本控制，且补充轮沿用上一轮 schema 是对的）；
- 不动 SSE 事件面。

---

## 落地记录（2026-11-05 22:20）

### 实际改动

| 文件 | 改动 |
|---|---|
| `backend/app/llm/reasoning_client.py` | `ReasoningReply` 补 `reasoning_tokens` / `truncated`（读 `finish_reason` + `usage.completion_tokens_details.reasoning_tokens`，并对照 `thinking_budget`） |
| `backend/app/agent/turn_understanding.py` | `REASONING_TRUNCATED_MARKER` + 截断时追加标注；首轮失败自动**不思考快速重试一次**（`LLM_TURN_FAST_TIMEOUT` 默认 45s）；`meta` 出参记录路径；`fast_retry_timeout()` |
| `backend/app/agent/requirement_analysis_graph.py` | 理解步骤 detail 带「首轮超时 → 不思考重试成功」「思考预算用尽（思维链被截断）」；`_data_agent` 步骤文案拆四种事实；state 裁剪时保住截断标注 |
| `backend/app/tools/rag_schema.py` | `last_schema_error()` + 每个失败分支记录 code/detail（成功时清空） |
| `backend/app/tools/data_tools.py` | 转发 `last_schema_error`（agent 不能直接 import rag_schema——P2 边界钉子） |
| `backend/app/report/preference.py` | `explicit_chart_type` / `apply_explicit_chart_request`（否定词跳过；复用 `_apply_type`：类型覆盖 + dims 键归一 + 数据不支持时让位） |
| `backend/app/agent/report_graph.py` | `_user_words(state)` 汇总「user_query + 需求卡 summary + adjustment_text」；命中显式要求则**不再让记忆偏好覆盖** |
| `frontend/src/components/workbench/reasoningText.ts` | `thoughtSummary(..., elapsedSeconds)` + `thoughtElapsedSeconds`（>600s 或时钟反向不显示） |
| `frontend/src/components/workbench/TracePanel.tsx` | 执行中每 1s 刷新，头部显示「正在思考（<步骤> · Ns）」 |
| 测试 | 新增 `backend/tests/contracts/test_live_session_issue_fixes.py`（17 例）；`reasoningText.test.ts` +3、`TracePanel.test.tsx` +1 |

### 验证（真机 + 离线）

`node .e2elogs/probe_live_issues.mjs` **PASS 5/5**（两轮真 LLM）：

```
v1 chart: bar（未点名图表类型 → advisor 默认，符合预期）
v2 chart: pie，dims {category: 月份, value: 净销售额}   ← 用户「帮我画饼图」生效
schema 步骤: 「检索到 3 张表：dim_store、dim_customer、dim_date」/「本轮沿用上一轮 schema（问题里没有新的表/字段线索，跳过检索）」
             —— 再没有「MCP 不可达」
执行中头部: 正在思考（规划查询 · 3s）                    ← 等待秒数
思考原文:   模型原文 1222 字（思考预算用尽，已截断）      ← 截断有标注（正文末尾同样带标注）
console errors: 0
```

UI 复核：v2 报告渲染为**环形饼图**（12 个月份 + 占比 + 图例），见 `.e2elogs/live_issues_pie_view.png`。

离线门禁：后端 `smoke + contracts` **682 passed**；前端 `tsc -b` 干净、`lint` 0 error、
受影响单测 **288 passed**。

### 落地偏差 / 过程中修掉的两个问题

1. **`requirement_card` 在报告子图里是 dict，不是 Pydantic 模型**：我第一版用
   `getattr(card, "summary")` 恒为空 → 饼图不生效（真机第一次跑就抓到了）。
   现在 `_user_words` 两种形态都吃，并补了三个单测。
2. **`app/agent/*` 不能 import `app.tools.rag_schema`**（P2 传输层边界钉子
   `test_mcp_boundary_freeze`）。改为由 `app.tools.data_tools` 转发 `last_schema_error`，
   边界钉子重新变绿。

### 给用户的答案（对应五个问题）

1. **没说完**：是模型的**思考预算**（`TURN_THINKING_BUDGET=512`）用尽了，不是界面把话吃掉。
   现在原文末尾和步骤结论都会标「思考预算用尽，已截断」；想要更长的链路可调这个 env
   （代价是每轮更慢）。
2. **卡了**：那一轮 turn 模型真的等了 90s 才超时（`LLM_TURN_TIMEOUT=90`）。现在超时后立刻
   用「不思考」模式重试一次，并且执行中的思考块会显示已等待秒数。
3. **MCP 未联通**：**文案错了**。MCP 一直通着（同轮更早一步「检索到 3 张表」就是 MCP 的返回，
   直连探针 1.9s 也能取到表）。真实情况是补充轮没有新的表/字段线索，关键词门短路、沿用上一轮
   schema。文案已拆成四种事实，不再暗示 MCP 挂了。
4. **理解失败**：就是第 2 条的 90s 超时 → `TurnPlan` 拿不到 → 回落通用解析。超时快速重试上线后
   这类回落会明显减少；仍失败时步骤会明说「理解失败（模型超时/输出不合法）」。
5. **饼图**：此前 `chart_advisor` 只看列形态、记忆偏好也只吃召回文本，用户当轮原话根本没有通道。
   现在显式图表要求（饼图/柱状图/条形图/表格，含否定句保护）优先于记忆偏好，真机已验证出 pie。
