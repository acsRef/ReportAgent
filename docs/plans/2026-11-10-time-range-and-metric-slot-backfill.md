> 状态: 已完成

# 时间范围与口径在投影层确定性回填（3.4 倍偏差的真因）

## Context

`2026-11-10-execution-accuracy-closed-loop` 把 Execution Accuracy 接上后，第一次真机跑
`explicit-region-sales-ranking` 就红了（金标 6 行、被测 6 行、数值约 3.4 倍）。当时的记录
把原因判成「`LEFT JOIN fact_payments` 的 1:N 扇出把 `SUM(order_amount)` 放大 N 份」。

**该判定经复核不成立**，本 plan 先把事实钉住，再修真因。

### 复核证据（真库 `ragent`，2026-11-10）

| 检查 | 结果 | 结论 |
|---|---|---|
| 每单支付条数分布 | 全部 = 1（179,947 单） | 该 seed 下 `LEFT JOIN fact_payments` 是 **1:1** |
| 每单退款条数分布 | 全部 = 1（10,600 单） | 同上 |
| 扇出写法 vs 派生表写法逐区域比净额 | 六区域**完全相同**（华东 326,102,457.72） | 扇出在当前数据里**不发生** |
| `fact_orders.order_date` 范围 | 2023-01-01 ~ 2026-10-31 | 数据是**四年**，不是 AGENTS.md 记载的只有 2024 |
| 金标 `WHERE d.year = 2024` 毛额 | 华东 96,318,061.74 | 金标本身**正确** |

3.4 倍 = 「四年 ÷ 一年」× 「毛额 vs 净额」，与 join 基数无关。

### 真因（两张卡片的实测数据）

`agent.requirement_draft` / `agent.analysis_task_revision` 里同一轮的三条事实：

```
task : {"time_range": null, "metrics": [], "goals":[{"type":"ranking","metric":"销售额","dimension":"区域"}]}
card : {"time_range": null, "target_metrics": ["净销售额"], "missing_fields": [{"key":"metric", ...}]}
sql  : SELECT dim_store.region, SUM(order_amount) - COALESCE(SUM(payment_amount),0) ...  ← 无任何时间谓词
```

1. **时间范围丢失**：模型这一轮没给 `time_range`（`null`），而 `TURN_UNDERSTANDING=1` 这条路径
   **没有任何确定性兜底**——旧路径 `requirement_parser` 里那套 `_YEAR_RE` / `_YEAR_MONTH` 规则
   只在 `parse_requirement` 内生效。于是「2024年」从未进入 SQL，四年数据一起算了。
2. **口径被改写**：模型把口径正确放在 `goals[0].metric = "销售额"`，但 `task.metrics` 是空数组
   → `assess_reportability` 判「缺口径」，候选来自
   `options=sorted(known_metrics)[:3]`——**按字符串排序取前三个**。中文按 Unicode 码点排，
   `净`(U+51C0) < `销`(U+9500)，于是候选成了 `净销售额 / 实收金额 / 客单价`，
   **用户明说的「销售额」被挤出选项**；评测夹具再盲选第一个 → 毛额被换成净额。

两个缺陷同一类：**模型负责理解，但代码该裁决的地方没有裁决**。

## Design

### D1 · 时间范围确定性回填（只在空的时）

`requirement_parser.time_range_from_query(user_query, *, today=None) -> str | None`：

- 纯函数，只认**绝对时间**写法，按优先级：`YYYY年M月-M月` → `YYYY年M月`（含中文月份
  「十一月」）→ `YYYY年`；
- 复用既有 `_YEAR_RE` / `_YEAR_MONTH` / `_CN_MONTHS`，不新造词表；
- **相对时间词（今年/上月/最近N天）不在这里换算**：它们已有既有链路（prompt 注入 today +
  `_inherit_year`），改口径会波及既有卡片文案与测试，不在本次范围（见「明确不做」）；
- 只在 `card.time_range` 为空时使用，模型给的一律尊重。

### D2 · 口径确定性回填（只在空的时）

复用 `requirement_slots.match_metric_terms()`（最长命中优先，已经是既有真源）：
原话**唯一命中**一个注册表口径 → 直接落位，不再问「用哪个指标？」；
命中 0 个（真的没说）或 >1 个（一词多义）→ 保持澄清。

### D3 · 澄清候选不许再按字符串排序

`assess_reportability` 增加 `preferred_metrics: Sequence[str] = ()`：候选顺序为
「原话命中的口径 → 注册表其余（排序后）」，截断到 3。`sorted(known_metrics)[:3]` 删除。
调用侧 `_reportability` 用 `match_metric_terms(user_query)` 提供 preferred。

### D4 · 评测夹具不许替被测系统做决定

`evaluation/runner._fill_missing_fields` 选口径时，当前规则是「label 里含『销售』的第一个」
——`净销售额` 也含「销售」且排在前，于是选错。改为：**优先取 label 在用户原话里逐字出现的
选项**，其次才回退。这把「夹具替我选了口径」降级为「只在原话没提口径时才发生」。

### 落地位置

`requirement_analysis_graph._requirement_project` 已经是「投影后确定性归一」的既定位置
（`_normalize_card_scope` / `_normalize_card_top_period` / `_ensure_comparison_slot` 都在这儿），
D1/D2 作为同族的两步接进去，末尾复用 `requirement_parser._drop_satisfied_missing_fields`
把已被卡体回答的槽位从 `missing_fields` 移除，并按结果重算 `status`。

## Files to change

| 文件 | 改动 |
|---|---|
| `backend/app/agent/requirement_parser.py` | 新增 `time_range_from_query()`（纯函数，复用既有正则） |
| `backend/app/agent/requirement_analysis_graph.py` | `_requirement_project` 增 `_normalize_card_time_range` / `_normalize_card_metric` + 复用 `_drop_satisfied_missing_fields` + 重算 `status`；`_reportability` 传 preferred 口径 |
| `backend/app/agent/turn_understanding.py` | `assess_reportability(..., preferred_metrics=())`：候选排序不再用 `sorted()[:3]` |
| `evaluation/runner.py` | `_fill_missing_fields` 选口径优先取「原话里逐字出现」的选项 |
| `backend/tests/contracts/test_requirement_projection_backfill.py` | **新增**：时间/口径回填契约 + 候选顺序 + 不越界（模型给了就尊重） |
| `docs/plans/2026-11-10-execution-accuracy-closed-loop.md` | 追加「勘误」节（不改原文） |

## Reused existing utilities

| 复用 | 位置 |
|---|---|
| `_YEAR_RE` / `_YEAR_MONTH` / `_CN_MONTHS` | `app/agent/requirement_parser.py` |
| `match_metric_terms`（最长命中优先，口径真源） | `app/agent/requirement_slots.py` |
| `_display_metric`（口径名归一） | `app/agent/requirement_parser.py` |
| `_drop_satisfied_missing_fields`（卡体已有答案的槽位不再追问） | `app/agent/requirement_parser.py` |
| `_period_filter_hint`（「2024年」→ `dim_date.year = 2024` 确定性谓词） | `app/agent/confirmed_execution_graph.py` |

## Verification

```bash
# 纯函数契约（秒级，不连库）
python -m pytest backend/tests/contracts/test_requirement_projection_backfill.py -q
# 受影响的既有用例（只跑这些，不跑全量）
python -m pytest backend/tests/graphs/test_turn_understanding.py backend/tests/graphs/test_metric_registration.py backend/tests/contracts/test_clarification_option_prefixes.py -q
python -m pytest evaluation/tests/test_execution_accuracy.py -q
# 真机闭环：卡片应带 time_range=2024年、口径=销售额；SQL 带 year=2024
python -m evaluation.runner --only-id explicit-region-sales-ranking --out /tmp/ea2.json
```

真机验收标准：`execution.result_matches == pass`，`execution_accuracy == 1.0`，
`requirement.time_range_equals` 由 fail 转 pass。

## 落地记录（2026-11-10）

真机闭环（`--only-id explicit-region-sales-ranking`，真 LLM + 真 PG）：

```
改前：execution_accuracy 0.0   execution.result_matches = fail:values
      卡片 time_range=null、target_metrics=["净销售额"]、SQL 无时间谓词
      pred 华东 326,102,457.72
改后：execution_accuracy 1.0   execution.result_matches = pass（gold 6 行 / pred 6 行，无差异）
      卡片 time_range="2024年"、target_metrics=["销售额"]、missing_fields=0、status=complete
      SQL  WHERE dim_date.year = 2024   → pred 华东 96,318,061.74（与金标一致）
```

被测 SQL 也随之变干净了——**不再 join `fact_payments`**（要的是毛额），
`净销售额` 的派生表写法只在该口径被选中时才出现。

测试（只跑受影响文件，未跑全量）：

| 批次 | 结果 |
|---|---|
| 新增 `test_requirement_projection_backfill.py` | 22 passed |
| `test_turn_understanding` / `test_metric_registration` / `test_clarification_option_prefixes` / `test_requirement_slots` | 91 passed |
| `test_execution_accuracy` / `test_runner_integration` / `test_checker` | 27 passed |
| requirement_parser 相关 9 文件（parse_requirement / merge / top_period / anomaly） | 139 passed |

顺带修掉一处**pre-existing red**：`test_runner_integration.test_success_case_passes_end_to_end_mock`
自上一轮给该 case 加 `golden.sql` 起就一直红——它 mock 了 HTTP 却把金标 SQL 真跑了一遍，
拿真数据库的 6 行去和假快照的空行比。现在把金标解析注入掉，并**额外断言
`execution.result_matches == pass`**：这条用例从此证明的是「算对了」，不只是「跑通了」。

## Explicitly NOT doing

- **不改 `metrics.yaml`**：`净销售额` 已是「先按 `order_id` 聚合的派生表再 join」的安全写法，
  改它修不了任何真实故障（当前数据下两种写法同值）。
- **不做扇出静态检测器**：它是**潜在**缺陷（一旦出现同单多退款就会放大），不是当前故障。
  真要做需要「catalog 唯一键 + 1:N 边 + 聚合所在侧」三件套判定，另开 plan；
  本次只把结论写实，不顺手扩一个未验证的闸。
- **不改 seed 造「同单多退款」夹具**：会改动所有金标数值，必须与「重算全部金标」同批进行。
- **不换算相对时间词**（今年/上月/最近N天）：已有链路按 prompt+today 处理，本次只补绝对年份。
- **不动 `evaluation/user_sim.py`**：它是另一套按 GT 作答的模拟器，本次只修 runner 的盲选。
