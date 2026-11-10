> 状态: 已完成

# 基线金标覆盖：让 20 条 case 里的 10 条真的判「算得对不对」

## Context

`2026-11-10-execution-accuracy-closed-loop` 建好了判据（`compare_result_sets` + `GoldenResult`），
但只给 1 条 case 写了金标（`explicit-region-sales-ranking`）。其余 19 条仍然只断言
「跑通 + 有行」，即 `sql_success_rate` 意义上的过——**算错了也过**。

`2026-11-10-time-range-and-metric-slot-backfill` 修掉了「四年当一年算 + 口径被换」，
现在第一反应是「把剩下 19 条都补上金标」。逐条看下来**不能也不该全补**：

| 类别 | case | 能不能有金标 |
|---|---|---|
| 有真实结果集（有 SQL） | 1, 2, 6, 7, 11, 12, 13(turn2), 18, 19, 20 | **能**（10 条） |
| 澄清类（不该执行 SQL） | 3, 4 | 不能：没有结果可比 |
| 安全拦截 / 闲聊 / schema 检索 | 9, 14, 15, 16, 17 | 不能：没有 SQL |
| 故障注入（FAILED / EMPTY 语义） | 10 | 不能：判据是 verdict + 错误码 |
| 问题本身有两种合理读法 | 5 | 不该：写死等于替产品做决定 |
| 明细（LIMIT 200 + 排序） | 8 | 不该：行集随实现抖动，金标会变成「复读机」 |

所以本 plan 的目标是**覆盖 10/20，并给另外 10 条写明「为什么没有」**——覆盖率低但每条都有理由，
比 20/20 全绿而理由是「照抄被测 SQL」有价值得多。

## Design

### D1 · 金标用**最直白**的写法表达业务口径，不照抄被测 SQL

金标是判据，不是复读机。抄被测 SQL 等于用同一把尺子量自己。所以：

- 跨 `fact_orders` / `fact_payments` 的双口径（case 7）用 **CTE 各聚合一次再 join**，
  避免任何 1:N 扇出写成金标；
- 时间一律走 `dim_date.year`（数据是 2023–2026 **四年**，`WHERE year=2024` 不可省）；
- 数值统一 `ROUND(..., 2)`，与比较器 `float_tol=0.01` 对齐；
- 列顺序 = 问题里提到的顺序（「销售额和退货金额」→ sales, refund）。

### D2 · 三条共享金标，鼓励「同问必同答」

`2024年各区域销售额`（毛额，6 行）被 case 1 / 13 / 18 / 19 / 20 共用；
`2024 每月销售额`（12 行）被 case 2 / 12 共用（case 2 多一个华东过滤，单独写）。

这一条同时是**一致性判据**：同一个问题在不同注入下（MCP 挂 / 修复重试 / 记忆偏好）
必须给出同一个数——这正是执行准确率该守住的东西。

### D3 · `time_range_equals` 改归一化比较

现在 `checker` 用 `observed.time_range == expected` 精确比字符串。卡片承载的是人读的文本
（`2024年` / `2024` / `2024年7月`），格式差异不是语义差异。改为：两侧各抽
`(年, 月?)` 元组比较；任一侧抽不出则回退精确比（不让归一化变成放水）。

## Files to change

| 文件 | 改动 |
|---|---|
| `evaluation/baseline_cases.json` | 9 条 case 追加 `execution.golden.sql`（case 1 已有） |
| `evaluation/checker.py` | `time_range_equals` 归一化比较（`_canonical_period`） |
| `evaluation/tests/test_checker.py` | 新增归一化正反例 |
| `docs/plans/2026-11-10-baseline-golden-coverage.md` | 本文件 |

（`runner.py` / `execution_accuracy.py` 不动——上一轮刚落地，本轮只喂数据。）

## Reused existing utilities

| 复用 | 位置 |
|---|---|
| `compare_result_sets`（多重集 + 容差 + NULL≠空串≠0） | `evaluation/execution_accuracy.py` |
| `GoldenResult.sql`（只读角色现跑） | `evaluation/schema.py` + `runner._resolve_golden` |
| `app.tools.sql_tools.execute_sql`（与生产同一套安全闸 + 只读角色） | `backend/app/tools/sql_tools.py` |

## Verification

```bash
# 1) 纯函数/契约（秒级）
python -m pytest evaluation/tests/test_checker.py evaluation/tests/test_execution_accuracy.py -q

# 2) 真机全量（真 LLM + 真 PG，约 13 分钟）——一次跑完，逐条分类
python -m evaluation.runner --out eval_golden.json
python -c "import json;d=json.load(open('eval_golden.json'));print(d['summary'])"
```

分类纪律（每条 fail 必须落到一类，不许含糊）：

| 归类 | 处置 |
|---|---|
| 金标 SQL 写错（口径/过滤/列义） | 改金标，并在 plan 记录「我错了什么」 |
| 系统输出格式差异（如月份带成 `2024-01`） | 视为真缺陷：执行准确率就是比**值** |
| 系统残缺（缺列 / 多列 / 少行） | 真缺陷，记入下一步（需求层或 SQL 层） |
| 问题本身有多解 | 把该 case 从金标覆盖里撤掉并写明原因 |

## 落地记录（2026-11-10）

### 第一轮：金标一接上，立刻抓出三个真缺陷

首跑（20 条，金标 10 条）：**15 passed / 5 failed，execution_accuracy 0.8**。
5 条失败里 **4 条是真缺陷**，只有 1 条是夹具立场过期：

| case | 现象 | 归类 |
|---|---|---|
| `session-scope-carryover` | 第 2 轮 `time_range` 丢失 → 四年一起算 | **真缺陷**（多轮继承读任务，而回填只改了卡片） |
| `explicit-monthly-trend-east` | 12 行但值 ≈ 全部区域（全国/华东 2.66 倍） | **真缺陷**（`scope` 无确定性兜底） |
| `complex-sql-sales-vs-returns` | 只有 1 列 `退款金额`，`column-count` 不符 | **真缺陷**（多口径只留最长命中） |
| `multiturn-followup-dimension` | 第 2 轮 `time_range` 断言 fail | **真缺陷**（同上第一条） |
| `clarify-vague-metric` / `clarify-no-time-range` | 不再澄清 | **夹具立场过期**（见下节） |

### 修复（`time-range-and-metric-slot-backfill` 的勘误 + 新增两条）

1. **回填下沉到任务层**（`reduce_task` 内、构造 task 之前）——多轮继承读的是任务，卡层回填
   单轮看着对、多轮全错。卡层两个 `_normalize_card_*` 删除，避免再掩盖任务层缺陷。
2. **`match_metric_terms` 改「子串覆盖才消歧、互不覆盖就全留」**——「销售额和退货金额」两个都要。
3. **新增 `scope_from_query`**（复用 `requirement_options.scope_options` 区域词表）——
   区域是原话里的字面事实，不该靠模型抽样。

### 最终数字（全量复跑，真 LLM + 真 PG）

```
total 20  passed 20  failed 0
sql_success_rate 1.0
execution_accuracy 1.0   cases_with_golden 10   cases_matched 10
p50 38.6s   p95 74.8s
```

证据产物：`evaluation/results/baseline-2026-11-10.json` / `.md`（与 `baseline-2026-08-25` 同格式）。
演进：**15/20 + 准确率 0.8**（首跑）→ 18/20（修多轮继承与多口径）→ **20/20 + 准确率 1.0**（补 `scope` 回填）。

一句话结论：**金标从 1 条铺到 10 条，代价是三个真缺陷浮出水面，收益是「跑通率 1.0」终于不再
掩盖「四年当一年算 / 区域过滤丢失 / 双口径少一半」。**

### 夹具立场对齐（不是放宽）

`clarify-vague-metric` / `clarify-no-time-range` 原本钉的是**已被推翻的立场**：

- `turn_understanding.assess_reportability` 明确把「缺 time_range」记为 **note**，
  不是 blocker（默认按全部数据理解，卡上标注）；
- `requirement_parser._apply_slot_resolutions` 写明口径优先级「**用户原话命中注册表** >
  上一轮已确认 > 注册表 default > 模型猜」，所以「帮我看一下销量」点的是注册表口径「销量」，
  按优先级就该直接作答——旧问法下模型给出的候选是 `净销售额/实收金额/客单价`，
  **用户说的「销量」根本不在选项里**，那种「澄清」本身就是错的。

处置：`clarify-vague-metric` 换成**真·模糊**问法（`帮我看一下`）保住「模糊 → 必须澄清」的覆盖；
`clarify-no-time-range` 改为「口径可定、时间缺省 → 直接作答并标注」。
同一立场变化也影响 `backend/tests/e2e/test_full_flow.py`（其问法依赖「必须 missing」），
一并改成「年份明确 + 口径模糊」，让它继续走「澄清 → PATCH → 确认」这条被测路径。

## Explicitly NOT doing

- **不给 case 5 写金标**：「再按产品细分」既可读成「月×品类」也可读成「换成品类」，
  写死任何一种都是替产品做决定；它继续靠 `rows_gt` / 状态断言。
- **不给 case 8 写金标**：明细 `LIMIT 200` + 排序并列时行集不确定，金标会随实现漂移。
- **不改 seed 数据**：一改所有金标数值全部失效，必须与「重算全部金标」同批。
- **不放宽比较器**（不忽略列顺序、不做行内模糊匹配）：比的就是「结果一样」，
  放宽一次就再也证明不了什么。
- **不做分档 + 子任务诊断**：那是下一步（BEAVER 三段切片），本 plan 只把金标喂饱。
