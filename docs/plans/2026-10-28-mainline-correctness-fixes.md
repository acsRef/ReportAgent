> 状态: 进行中

# 主线正确性修复：粒度 / 多轮继承 / 时间图表 / 口径接地 / 错误类型贯通

## Context

按用户要求先把**主线**（提问 → 需求卡 → 补全 → 确认 → SQL → 报告 → 调整）跑到"正常操作、结果正确"，再谈删测试。用 `evaluation.runner` 全量 20 例跑基线，结果 **13 pass / 3 fail / 4 skip**（`sql_success_rate 0.67`）。

逐个定位后发现 6 个真缺陷，且**其中 3 个一直显示"通过"**——因为评测断言太弱（`rows_gt: 0` 之类），bug 被假绿盖住了。

| # | 现象 | 根因 |
|---|---|---|
| 1 | 「每个月」的查询只回 1 行 | `RequirementCard` 没有粒度字段，`time_range` 只表达范围；粒度只作为 missing 选项存在，用户选完被塞进 `dimensions` 的「粒度:month」字符串，`_format_confirmed_requirement` 读不到 |
| 2 | 多轮补充把上一轮的「2024年 / 华东」丢了 | `_requirement_parse` 只在 `prior.status == "complete"` 时才继承；用户拿到卡**没点确认**就接着说「再看月度趋势」→ 无 prior → 时间被解析成"本月" → SQL 过滤当月 |
| 3 | 「各月趋势」画不出图，退化成表格 | `chart_advisor` 用"首行值是否数字"判维度；`月份` 是 int → 被当度量 → 无分类列 → `type=table` |
| 4 | 「销售额和退货金额对比」时灵时不灵 | prompt 没有「业务口径 → 物理字段」映射，模型会写 `fact_orders.sales_amount`（不存在）→ `object_not_found` → 烧完 schema 重检索预算 → FAILED；换次采样又蒙对 |
| 5 | 「各供应商的采购金额」凭空造表 | 业务实体（供应商）在 schema 里没有对应维度表，但需求层没拦 → 下游写出 `JOIN dim_supplier` |
| 6 | 失败原因说不清 | SQL 子图 clarify/fail 直接 `END`、不产 `query_result`，父图只从 qr 取 error → 上游 kind 丢；即使保住，`_build_sse_error` 只认 dict，收到 Pydantic 模型又当空 dict → 用户永远看到「查询执行失败,请稍后重试」 |

## Design

### 1. 粒度成为一等字段（问题 1）

- `RequirementCard.granularity: Literal["day","week","month","quarter","year"] | None`（+ TS 镜像 + 镜像契约测试）。
- parser 归一化中文/英文（月→month），认不出返回 `None`（**不猜**）。
- `_format_confirmed_requirement` 输出 `时间粒度 = 月（时间维度必须按 dim_date.month 分组，不要用 year）`。
- **确定性兜底**：`sql_graph._apply_confirmed_granularity` 在 plan 之后直接改写 `dimensions`，把「时间」替换成「月份」。理由：实测把指令写进 plan prompt 后，模型依旧输出 `dimensions=["时间"]` 并 `GROUP BY year` —— 已确认的粒度不该再赌采样。

### 2. 多轮继承以"表达过"为准（问题 2）

继承条件从 `status == "complete"` 改为 `status != "locked"`。用户已说过的约束不该因为"没点确认"而消失；`locked` 表示执行链正在用，仍排除。
配套：`_drop_satisfied_missing_fields` —— 卡体已有答案的维度不再追问（LLM 会自相矛盾：既给 `granularity: 月` 又把它列进 `missing_fields`）。

### 3. 时间列按列名识别（问题 3）

`chart_advisor` 增加 `_is_time_dimension(name)`（时间/日期/年/月/季度/周/星期/date/time/month/…）：
- 时间列**无论值是 int 还是文本**都当作维度；
- 时间序列**一律柱状图**（行数 ≤ 8 也不画饼图——饼图表达不了趋势）；
- 真分类列优先于时间列做维度（`品类 × 月份` 仍用品类）。

### 4. 业务口径接地（问题 4）

新增 `_SCHEMA_TERM_HINTS`（销售额→`fact_orders.order_amount`、退货→`payment_amount WHERE status='REFUNDED'`、销量→`SUM(quantity)`、客单价、区域/品类/时间/促销折扣…），同时注入 **plan** 与 **generate** 两个 prompt（这两处是模型选列的地方）。

NOTE：这是本星型模型的固定口径，schema 变更时必须同步，否则 prompt 会指向不存在的列。

### 5. 业务实体缺失转 requested_object（问题 5）

prompt 新增规则：供应商/仓库/员工/渠道/合同/成本中心等实体在可用表结构里没有对应 `dim_*/fact_*` 表时，必须生成 `requested_object:<实体名>` assumption 并列出可用维度；**严禁臆造一张 dim_ 前缀的自造表**。
（注意：rules 里不能写旧 schema 字面 token，`test_schema_grounding_alignment` 会拦——第一版就踩到，已改成不含 token 的表述。）

### 6. 错误类型贯通两跳（问题 6）

- 父图 `_confirmed_report_agent`：`qr is None` 时回落到 `state["error"]`（子图已透传），不再丢掉 `kind`。
- SSE 出口 `main._build_sse_error`：同时接受 dict 与 Pydantic 模型（`model_dump()`）。这是第二跳，只修第一跳时**真链路仍是 `kind=other`**。

### 7. 评测断言收紧（防止再假绿）

- `explicit-monthly-trend-east`：`report.rows_gt 0 → 6`（12 行月趋势，1 行即失败）。
- `empty-result-future-year`：查询年份 `2025 → 2027`（种子数据已扩到 2026-10，2025 不再是零行）。

## Files to change

| 文件 | 变更 |
|---|---|
| `backend/app/models/requirement.py` | `RequirementGranularity` + `RequirementCard.granularity` |
| `backend/app/agent/requirement_parser.py` | 粒度归一化、prior 继承、`_drop_satisfied_missing_fields` |
| `backend/app/agent/requirement_analysis_graph.py` | 继承条件 `!= locked` |
| `backend/app/agent/prompts/requirement_prompts.py` | 粒度规则/输出字段、业务实体缺失规则 |
| `backend/app/services/requirement_service.py` | 选粒度写卡体字段（不再塞 dimensions 标签） |
| `backend/app/agent/confirmed_execution_graph.py` | 粒度指令、上游 error 回落 |
| `backend/app/agent/sql_graph.py` | `_SCHEMA_TERM_HINTS`、`_apply_confirmed_granularity`、state `granularity` |
| `backend/app/tools/sql_tools.py` | `chart_advisor` 时间维度识别 |
| `backend/app/main.py` | `_build_sse_error` 接受模型 |
| `backend/tests/**`（9 个文件） | 新增 24 例覆盖上述每一条 |
| `evaluation/baseline_cases.json` | 两条断言收紧 |
| `frontend/src/types/requirement.ts` + 6 处 fixture | `granularity` 镜像字段 |

## Reused existing utilities

- `RequirementMissingField` / `RequirementOption` 受控选项机制（粒度仍可作缺失项追问）。
- `_merge_prior_card` 的 presence/absence 语义（只补粒度一条，不重写）。
- `fault_inject` seam（`REPORTAGENT_E2E=1` + `X-E2E-Fault`）做错误类型的**真链路**验证，不新增测试设施。
- `evaluation.runner` 既有 20 例主基线 + `_fill_missing_fields` fill-all 策略。
- `chart_advisor` 既有 pie/bar/table 三型，仅增加"时间维度"判定分支。

## Verification

1. **单测**：backend `pytest -q` → **1218 passed / 1 skipped**（+24 例，零回退）。
2. **前端**：`npm run test:run` 309 passed；`npx tsc -b` 0 error。
3. **主线 20 例**（真 LLM + 真 PG）：**16 pass / 0 fail / 4 skip**，`sql_success_rate 1.0`，退出码 0。
   - 逐项回归过的 crash 用例：`explicit-monthly-trend-east`（12 行）、`session-scope-carryover`（继承 2024年）、`report-chart-trend`（柱状图）、`complex-sql-sales-vs-returns`（口径接地）、`empty-result-future-year`（2027 零行）。
4. **错误类型真链路**（fault 注入）：
   ```
   error.code = QUERY_OBJECT
   error.kind = object_not_found
   error.message = 查询引用的表或字段不存在,请检查维度后重试
   ```
   （修复前：`QUERY_FAILED` / `other` / 「查询执行失败,请稍后重试」）

## Explicitly NOT doing

- 不动 SQL 安全边界（白名单/EXPLAIN/只读角色）；`dim_supplier` 能过前缀白名单是**有意的分层**，存在性由 EXPLAIN 兜。
- 不做澄清表单 UI（`requested_object` 的 alternatives 常为空、前端无结构化澄清）——记为下一步。
- 不改 `DiagnosePolicy` 的预算与分流表。
- 不为本次修复新增评测维度（归 B12 评估补全）。
- 不清理冗余测试（用户要求先补后删，删除批次另开）。
