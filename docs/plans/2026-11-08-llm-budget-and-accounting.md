> 状态: 已完成

# LLM 预算与记账收敛：先能看见（turn 记账）→ 再收敛（统一预算表）→ 最后收口最坏耗时

## Context（背景）

上一轮讨论「thinking 预算要不要拉满」时暴露了三件事（2026-11-08，真机 + 6 次采样）：

1. **turn 这条直连调用完全没记账**：`observability.llm_call` / Langfuse 里只有 adapter 路径的记录，
   `reasoning_client` 拿到的 `usage`（prompt/completion/reasoning tokens、延迟）用完就丢 →
   我们永远在"猜"思考花了多少。企业级做法是先记账，再谈预算。
2. **预算散在 6 处**：`intent.py 200`、`sql_graph 1500×3`、`report_graph 1500/900`、
   `conversation 1000`、`requirement_parser 1500`、turn `budget+900` —— 改一处影响一条链，
   也没法做"按任务类型的预算表"。
3. **单轮最坏耗时不可预测**：思考 90s 超时后**再给**「不思考」45s 重试 → 最坏 135s，
   而用户感知的"卡住"就是它。行业做法是**一个 request budget 贯穿重试**。

## Design（设计）

### D1（先做）turn 调用记账

- `ReasoningReply` 补 `prompt_tokens` / `completion_tokens`（`usage` 里就有）；
- 三个出口（非流式 / 流式 / async）都在拿到回复后调
  `current_tracer().add_llm_call(model, pt, ct, elapsed_ms, input=redact(prompt), output=redact(content))`；
- **观测失败绝不影响主链**：try/except 包住，只 debug 日志；
- 记账字段与 adapter 路径完全一致，所以 `observability.llm_call`、Langfuse generation 两处都能对账。

### D2 预算收敛成一张表

新 `backend/app/llm/budgets.py`（**唯一**预算入口）：

```python
TASK_BUDGETS = {
    "intent":                Budget(200,  30, "requirement"),
    "requirement_parse":     Budget(1500, 90, "requirement"),
    "sql_plan":              Budget(1500, 90, "execution"),
    "sql_generate":          Budget(1500, 90, "execution"),
    "sql_repair":            Budget(1500, 90, "execution"),
    "report_plan":           Budget(1500, 90, "report"),
    "report_narrative":      Budget(900,  60, "report"),
    "memory_summary":        Budget(1000, 60, "memory"),
}
max_tokens(task) -> int          # 六个 caller 全部改成它
thinking_budget() -> int         # TURN_THINKING_BUDGET（默认 512）
turn_max_tokens(budget) -> int   # TURN_MAX_TOKENS，默认 budget+900，配错兜底 budget+300
turn_timeout() -> float          # LLM_TURN_TIMEOUT（默认 90）
turn_fast_timeout() -> float     # LLM_TURN_FAST_TIMEOUT（默认 30）
turn_request_budget() -> float   # TURN_REQUEST_BUDGET（默认 120）
```

`reasoning_client` / `turn_understanding` 改成引用本模块（原函数保留为薄委托，避免动测试面）；
六个 caller 的 `max_tokens` 字面量换成 `budgets.max_tokens("<task>")`。
表里同时登记 `stage`（requirement/execution/report/memory），便于按域统计与后续告警。

### D3 单轮 request budget（最坏耗时可控）

`understand_turn` / `understand_turn_async` 入口记 `deadline = now + turn_request_budget()`：

- 首次调用 `timeout = min(turn_timeout(), 剩余)`；
- 失败后判断剩余：`剩余 < 20s` → **不再重试**（`meta["retry_skipped"] = "budget"`），
  直接回落通用解析；否则用「不思考」重试，`timeout = min(turn_fast_timeout(), 剩余-5)`；
- 于是最坏 ≈ 120s（90 + 30），不再是 135s，也不会出现「第二次也超时又白等」。

步骤 detail 同步：跳过重试时写「理解超时且预算用尽，回落通用解析」；
重试成功仍写「首轮超时 → 不思考模式重试成功」。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/llm/budgets.py`（新） | 预算表 + turn 旋钮（env 解析集中于此） |
| `backend/app/llm/reasoning_client.py` | `ReasoningReply` 补 usage；三出口记账；预算旋钮改委托 `budgets` |
| `backend/app/agent/turn_understanding.py` | request budget deadline + 跳过重试语义 + meta |
| `backend/app/agent/intent.py`、`sql_graph.py`（3 处）、`report_graph.py`（2 处）、`conversation.py`、`requirement_parser.py` | `max_tokens` 改 `budgets.max_tokens("<task>")` |
| `backend/app/agent/requirement_analysis_graph.py` | 步骤 detail 补「预算用尽 → 回落」 |
| 测试（各自独立） | `test_turn_token_accounting.py` / `test_llm_budgets.py` / `test_turn_request_budget.py` |

## Reused existing utilities（复用工具）

- `tracer.add_llm_call` + `observability.llm_call` + Langfuse flush（P13 已建，不新增通道）；
- `app/observability/redaction.redact`（PII 掩码，adapter 同款）；
- `reliability.timeout.run_with_timeout`（任务级超时已有，不改）；
- 既有 `test_reasoning_stream.py` 的 `_FakeStream/_FakeResponse` 夹具（记账测试复用同一套假 transport）。

## Verification（验证）

只跑新增的三个文件 + 既有流式用例（不跑整套）：

```bash
pytest backend/tests/contracts/test_turn_token_accounting.py \
       backend/tests/contracts/test_llm_budgets.py \
       backend/tests/contracts/test_turn_request_budget.py \
       backend/tests/contracts/test_reasoning_stream.py -q
```

真机：跑一轮 → `select model, prompt_tokens, completion_tokens, latency_ms from observability.llm_call`
能看到 `DeepSeek-V4-Flash` 那条（此前没有）；并确认最坏耗时口径 = 120s。

## Explicitly NOT doing（不做事项）

- 不引入 LLM gateway / 模型路由（第 3 级降级）；
- 不改 adapter 路径的 timeout 行为（只把 `max_tokens` 换成表里的值，语义不变）；
- 不做按用户/租户配额与成本看板（个人项目容器里没有多租户）；
- 不把预算表做成可热更新的配置（YAML/DB），env + 常量足够。

## 落地记录（2026-11-08）

按用户拍板的顺序 2 → 1 → 3 全部落地，**没有一项走偏**（下面的偏差都记了原因）。

### D1 turn 记账 ✅

- `ReasoningReply` 新增 `prompt_tokens` / `completion_tokens`；`_parse_tokens(usage)` 单独解析
  （脏值归 0），`_parse_usage` 签名未动（既有用例仍钉 `(reasoning_tokens, truncated)`）；
- `_record_llm_call(reply, prompt)` 三个出口统一调用：非流式 / 流式（`_stream_chat` 新增 `prompt`
  形参）/ async 两态；`input=redact(prompt)`、`output=redact(reply.content)`，与 adapter 完全同款；
- 记账失败只打 debug：`test_记账抛错时照常返回` 钉住。

### D2 预算表 ✅

- `backend/app/llm/budgets.py`：`Budget(max_tokens, timeout_seconds, stage)` + `TASK_BUDGETS`（8 项）
  + `budget_for/max_tokens/stage_of` + 五个 turn 旋钮；未登记任务回落 `1500/90/unknown` 并 debug 日志。
- 接线 7 处（算术核对过，与旧值一一对应）：
  `intent→intent`、`sql_graph._intent_analyze→sql_plan`、`sql_graph._plan→sql_plan`、
  `sql_graph._generate_sql→sql_repair if repair_ctx else sql_generate`、
  `report_graph._plan_analysis→report_plan`、`report_graph._narrate→report_narrative`、
  `requirement_parser→requirement_parse`、`memory/conversation→memory_summary`。
- **偏差**：plan 原写「`_plan` 也区分 repair」，实现后判定 `_plan` 的 repair_ctx 属于**重规划**
  （`MAX_PLAN_RETRIES`）而非 SQL repair，故 `_plan` 固定 `sql_plan`，只有 `_generate_sql` 分流。
- `reasoning_client.turn_timeout / resolve_thinking_budget / resolve_max_tokens` 改成薄委托，
  旧函数名与语义保留（既有 5 个预算用例零改动通过）。

### D3 单轮 request budget ✅

- `turn_understanding._MIN_RETRY_SECONDS = 20.0` + `_remaining / _first_timeout / _retry_timeout`
  三个纯函数（可单测，不依赖睡眠）；sync 与 async 版本共用同一套判定。
- `fast_retry_timeout()` 默认 45 → **30**（与 120s 预算对齐，最坏 90+30）；薄委托 `budgets`。
- `meta["retry_skipped"] = "budget"` 时 `requirement_analysis_graph._turn_understand` 的 detail 写
  「理解超时且本轮预算用尽（未重试），回落通用解析」——不再和「重试也失败」共用一句话。

### 验证结果

- 聚焦用例 **48 passed**：`test_turn_token_accounting.py`(8) + `test_llm_budgets.py`(14) +
  `test_turn_request_budget.py`(13) + `test_reasoning_stream.py`(13)；受影响的既有
  `test_live_session_issue_fixes.py` + `test_failure_attribution_fixes.py` +
  `test_interruption_resilience.py` 共 **37 passed**。
- 真机 `python .e2elogs/probe_turn_accounting.py` **5/5 PASS**：一轮 chat 后
  `observability.llm_call` 新增 `deepseek-ai/DeepSeek-V4-Flash` 一条，
  `prompt_tokens=928 / completion_tokens=610 / latency_ms=5469`（改前是 0 行）。
- 探针会话已按前缀清理（`probe-turn-acct-*` 两条，含 trace/span/llm_call/conversations）。
- **踩坑记账**：第一次跑探针 1/3 PASS，根本不是代码问题——uvicorn 没开 `--reload`，
  进程还是 14:50 的旧模块。重启后立刻 5/5。已写进 hand-off quirk 30。

