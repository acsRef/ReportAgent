> 状态: 已完成

# Execution Accuracy 闭环：结果对不对，而不是「SQL 跑没跑通」

## Context

用户对 BEAVER（arXiv 2409.02038）的结论表示认同并明确了判断标准：
**最终目标是 Execution Accuracy，不是 Retrieval Relevance**——Schema RAG / SQL Case RAG /
Metric RAG 都只是上游手段，召回准不代表最终 SQL 正确，必须用 SQL 执行结果闭环。

对照我们的评测，现状是：

| 现状 | 问题 |
|---|---|
| `evaluation/runner.py` 的头号指标是 `sql_success_rate` | 它衡量「执行成功 + 有行」= **execution success**，不是 **execution accuracy** |
| 期望里只有 `verdict` / `sql_nonempty` / `rows_gt` | 没有任何一条 case 断言「结果与金标一致」 |
| 20 条 baseline 的 `status=pass` | 一条 SQL 只要跑通且返回了行就算过——**算错了也过** |

所以本 plan 只做一件事：把 Execution Accuracy 建成可执行、可归因的判据。

## Design

### D1 · 结果比较器是纯函数，判错要能归因

`evaluation/execution_accuracy.py::compare_result_sets`：

- **默认只看值**（BEAVER 口径）：列别名不同不算错，但**列数必须一致**；要钉列名时显式 `match_columns=True`；
- 行按**多重集**比较（SQL 没写 ORDER BY 时顺序无意义），重复行按数量对；
- 数值统一 `Decimal` 后按容差比（默认 0.01），`1` 与 `1.5` 必须判错；
- **NULL ≠ 空串 ≠ 0**（三者是不同结果，不做暗中等价）；
- `reason ∈ {column-count, column-mismatch, row-count, values}`，失败附缺失/多出的样例行（各 3 条）。

### D2 · 金标用 SQL 表达，而不是写死数字

`GoldenResult`：`sql`（优先，评测时用**只读角色**现跑→跟着种子数据走）/ `columns`+`rows`（内联，适合小结果）。
金标 SQL 走 `app.tools.sql_tools.execute_sql`——与生产同一套安全闸 + 只读角色，不另开数据库访问路径。

### D3 · 两个指标并列，绝不混淆

`summary` 里同时给出：

- `sql_success_rate`：跑通率（原来的）；
- `execution_accuracy` + `cases_with_golden` + `cases_matched` + `execution_failure_reasons`：
  **只统计有金标的 case**（没标注的既不算过也不算错）。

## Files to change

| 文件 | 改动 |
|---|---|
| `evaluation/execution_accuracy.py` | **新增**：`compare_result_sets` / `accuracy_summary` / 归一化工具 |
| `evaluation/schema.py` | `GoldenResult` + `ExecutionExpectation.golden` |
| `evaluation/checker.py` | `ObservedTurn` 增 `columns` / `rows`（结果行） |
| `evaluation/runner.py` | 抓取真实结果行、执行时比金标、`summary` 增 accuracy 四件套 |
| `evaluation/baseline_cases.json` | 首个金标 case：`explicit-region-sales-ranking`（golden.sql） |
| `evaluation/tests/test_execution_accuracy.py` | **新增**：11 例比较器契约 |

## Verification

```bash
python -m pytest evaluation/tests/test_execution_accuracy.py -q     # 纯函数，秒级
# 真机（PG + backend :8100）：
python -m evaluation.runner --only-id explicit-region-sales-ranking --out /tmp/ea.json
python -c "import json;d=json.load(open('/tmp/ea.json'));print(d['summary'])"
```

## 落地记录（2026-11-10）

**这条指标接上第一次跑，就抓到一个真 bug。**

```
case: explicit-region-sales-ranking（2024 各区域销售额排名）
status: fail      execution.result_matches: fail:values
gold: 6 行  华东 96,318,061.74 / 华北 66,883,715.64
pred: 6 行  华东 326,102,457.72 / 华北 230,552,361.42   ← 约 3.4 倍
```

被测 SQL（reports 里取出来的真 SQL）是：

```sql
SELECT dim_store.region AS 区域,
       SUM(fact_orders.order_amount) - COALESCE(SUM(fact_payments.payment_amount), 0) AS 净销售额
FROM fact_orders
LEFT JOIN dim_store   ON fact_orders.store_id = dim_store.store_id
LEFT JOIN fact_payments ON fact_orders.order_id = fact_payments.order_id
                       AND fact_payments.status = 'REFUNDED'
GROUP BY dim_store.region
```

**经典的 1:N join 扇出**：一个订单对应多条退款/支付记录时，`LEFT JOIN` 把订单行**复制**了 N 份，
`SUM(order_amount)` 于是被放大 N 倍（这里约 3.4 倍）。此前 `rows_gt > 0` + `verdict=SUCCESS`
完全测不出来——**这正是「只测跑通率」和「测结果对不对」的差别**。

顺带记录两个不是 bug 但会让人误判的点：

1. 同一条 case 的 `requirement.time_range_equals` 断言写的是 `"2024年"`，而卡片归一化后是 `"2024"`
   → 这条断言一直在 fail，与 SQL 对错无关（属于「期望写得太贴字面」的问题，另修）。
2. `query_snapshot.columns` 是 `[{name, type}]`、`rows` 是 `[{列名: 值}]`——首版按字符串/列表处理，
   把「表头」当成了一行数据，导致一开始报 `unhashable type: dict`。抓取代码里留了注释。

## 勘误（同日复核，2026-11-10 晚）

上面「落地记录」里把 3.4 倍偏差判成 **1:N 扇出，该结论错误**，原文保留不改（设计文档是记录），
以本节为准：

| 检查（真库） | 结果 |
|---|---|
| 每单支付条数 / 每单退款条数 | 全部 = 1（179,947 单 / 10,600 单）→ 该 seed 下 `LEFT JOIN fact_payments` 是 **1:1** |
| 扇出写法 vs 派生表写法（逐区域净额） | 六区域**完全相同**（华东均 326,102,457.72） |
| `fact_orders.order_date` 范围 | 2023-01-01 ~ 2026-10-31 → 数据是**四年**，不是只有 2024 |
| 金标 `WHERE d.year=2024` 毛额 | 华东 96,318,061.74 → 金标**正确** |

3.4 倍 = 四年 ÷ 一年 × 毛额 vs 净额。真因两条，都在需求层：

1. `task.time_range = null`（模型没给，且 `TURN_UNDERSTANDING` 路径没有确定性兜底）
   → SQL 无任何时间谓词 → 四年一起算；
2. `task.metrics = []` 但 `goals[0].metric = "销售额"` → `assess_reportability` 用
   `sorted(known_metrics)[:3]` 出候选（中文按码点排，`净` < `销`）→ 「销售额」不在选项里，
   夹具盲选第一个 → 毛额被换成净额。

修复见 [2026-11-10-time-range-and-metric-slot-backfill.md](2026-11-10-time-range-and-metric-slot-backfill.md)。
**结论仍然成立且更有力**：这一层确实第一次跑就抓到了真 bug，只是当时的归因是错的——
错在被测系统之外（金标其实对、数据其实是四年），这也正是「必须用执行结果闭环」的价值：
没有它，我们连「4 年当 1 年算」都不知道。

### 未做（下一步）

- **扇出修复**：`metrics.yaml` 里 `净销售额` 的表达式需要改成「先按订单聚合再减」或
  `SUM(o.order_amount) - (SELECT SUM(...) ...)` 的形式，并补一条「同订单多退款」的夹具——
  这是数据正确性问题，值得单独一轮。
- **给其余 19 条 case 补金标**：老案例要逐条写 golden SQL（每条都要先人肉确认口径），
  外加把 `time_range_equals` 这类字面断言改成归一化比较。
- **按复杂度分档 + 子任务诊断**（BEAVER 的三档切片与 5 个子任务）：用本轮的比较器做底座，
  下一步接「表召回 / join 键 / 列映射 / 谓词 / 分解」。
