> 状态: 已完成

# 复杂度分档 + 子任务诊断：把「SQL 错了」拆成「哪一步错了」

## Context

`execution_accuracy` 只说「结果与金标不一致」，**不说差在哪**。这轮铺金标的经历证明了这个缺口：
三个真缺陷（多轮丢年份、丢区域过滤、双口径少一半）在指标上全都只是「`fail:values` / `fail:column-count`」，
要靠人肉翻 `requirement_draft` / `analysis_task_revision` / `query_snapshot` 才定位到根因。

BEAVER（arXiv 2409.02038）的做法是把 Text-to-SQL 拆成可分别评分的子任务，并按复杂度分档切片。
本 plan 把这两件事接上，只做**诊断**，不改判分口径：

- 结果对不对：仍由 `compare_result_sets` 唯一裁定（不因诊断改变 pass/fail）；
- 差在哪：新增「金标 SQL vs 被测 SQL」的结构件比对，把失败归因到具体子任务；
- 有多难：由**金标结构**推导复杂度档位，按档看准确率。

## Design

### D1 · 结构件从 SQL 反推（`evaluation/task_decomposition.py`）

`decompose(sql) -> SqlParts`，用 sqlglot（postgres dialect）抽五类物理事实：

| 件 | 归一规则 | 为什么这样归一 |
|---|---|---|
| `tables` | 去 schema/别名小写；**排除 CTE 名** | CTE 是自己造的名字，不是物理表 |
| `join_keys` | 等值 join 的 `表.列=表.列`，**别名解析成真实表名**，对内排序 | 别名是写法，物理关联才是事实 |
| `columns` | 聚合：**整条 SQL**（含 CTE 内）的 `sum(表.列)`；维度：顶层非聚合纯列引用的**列名** | 预聚合形态下聚合不在顶层投影；维度列的表前缀/别名属写法 |
| `predicates` | WHERE/HAVING 的 AND 原子，**去表限定** | 过滤条件比的是「还在不在」，不是「用哪个别名写的」 |
| `structure` | 有无 CTE、聚合种类集合 | 分解方式差异（该预聚合却在同层聚合） |

### D2 · 判定是**覆盖率**语义（recall），不是相等

金标的表/join 键/列/谓词必须在被测里出现；**多出来的只记录不判错**——
多带一个无害维表、多一个冗余谓词不该算失败。`failed` 按 `SUBTASK_ORDER`
（tables → join_keys → columns → predicates → structure）排列。

### D3 · 复杂度分档由金标结构推导，不人标

- **T1**：单表、无 join；
- **T2**：星型直连（事实表 join 维表），单来源聚合；
- **T3**：预聚合（CTE/子查询）、**多口径**（两个来源各自聚合），或明细（无聚合）。

人标会随「谁写的这条 case」漂移；结构推导可复现。分档只用于按难度切片，不给 case 打质量分。

### D4 · 汇总口径：分档准确率 + 首个失败子任务

`summary.complexity_tiers`（每档 cases/matched/accuracy）与
`summary.subtask_failures`（失败 case 的**首个**失败子任务计数）。
只记首个，是因为表没找对时谓词必然也不同——全记会把每个子任务都算成高频。
每个 case 的完整 `diagnosis.failed` / `details` 仍逐条落盘，供人翻。

### D5 · 诊断器本身要能被证伪

单元测试里的坏 SQL **全部来自本轮真机事故**（四年净额、丢区域过滤、只出退款金额一列），
断言它们各自被指到对的子任务上。只测「全绿时不报错」没有意义。

## Files to change

| 文件 | 改动 |
|---|---|
| `evaluation/task_decomposition.py` | **新增**：`decompose` / `diagnose` / `complexity_tier` / `tier_accuracy` / `subtask_failure_counts` |
| `evaluation/runner.py` | 有金标且金标 SQL 可解析时计算 `complexity_tier` / `diagnosis` 并落进结果；`summary` 增两字段 |
| `evaluation/tests/test_task_decomposition.py` | **新增**：结构件归一、三个真机事故的归因、分档、聚合 |

## Reused existing utilities

| 复用 | 位置 |
|---|---|
| `sqlglot`（postgres dialect，与 SQL 安全闸/`validate_sql` 同一套解析） | `backend/app/tools/sql_tools.py` 已依赖 |
| 金标 SQL 与结果比对（判分口径不变） | `evaluation/execution_accuracy.py` |
| 真机证据（坏 SQL 原文） | `docs/plans/2026-11-10-time-range-and-metric-slot-backfill.md` / `baseline-golden-coverage.md` |

## Verification

```bash
# 纯函数（秒级，无 DB）
python -m pytest evaluation/tests/test_task_decomposition.py -q
python -m pytest evaluation/tests -q
# 真机全量（真 LLM + 真 PG，约 13 分钟）
python -m evaluation.runner --out eval_diag.json --md evaluation/results/baseline-2026-11-10.md
```

验收：`summary` 出现 `complexity_tiers`（至少 T2/T3 两档）与 `subtask_failures`；
10 条有金标的 case 每条都有 `complexity_tier`，且 `diagnosis.ok` 与 `result_matches` 一致
（结果对 ⇒ 结构也覆盖；若出现「结果对但诊断报差异」，说明归一化有过严之处，要修诊断器而不是改判分）。

## 落地记录（2026-11-10）

真机全量（20 条，其中 10 条有金标）：

```
execution_accuracy 1.0   cases_matched 10/10
complexity_tiers   T2 9/9 (1.0)   T3 1/1 (1.0)
subtask_failures   {}            ← 本轮无失败，归因表为空（符合预期）
```

### 诊断器自己也被测过一轮（本轮重点）

下面三处都是「**结果对、但诊断报差异**」——按本 plan 的验收标准，这只说明诊断器过严，
要改诊断器而不是改判分口径：

| 假阳性 | 为什么不该算差异 | 修法 |
|---|---|---|
| `uses_cte` 不同 | 扁平写法与预聚合写法可以给出完全一致的结果 | 从失败判据里移除，`structure` 只比聚合种类 |
| CTE 之间的 join（`refunds.category=sales.category`） | 查询内部的逻辑连接，不是物理关联；而且要先用别名表把 `r`/`s` 还原成 CTE 名才判得出来 | 排除两侧归属为 CTE 的 join 键 |
| 谓词只收顶层 WHERE/HAVING | `year = 2024` 写在 CTE 里还是外层，是写法差异，不是「条件丢了」 | 整条 SQL（含 CTE/子查询）的 WHERE/HAVING 全收 |

修完后 10 条有金标的 case：`diagnosis.ok` 与 `result_matches` **完全一致**（无假阳性、无假阴性）。

另有一处**实现 bug** 是靠真机结果发现的：`subtask_failure_counts` 一开始没按
`result_matches is False` 过滤，于是「结果一致但结构不同」的等价写法被算进了失败归因
（20 条里就出现了 1 条）。已修，并把这条口径写进 docstring 防止复发。

### 附带收益：诊断是纯函数，可离线重算

「两条 SQL → 结构件」没有副作用，所以诊断口径修正后**不需要重跑 13 分钟的真机全量**：
结果产物里已同时落了 `sql` / `golden_sql`，直接用它们重算 `complexity_tier` / `diagnosis`
并重新渲染产物即可。本轮最后一次口径修正就是这么验证的。

## Explicitly NOT doing

- **不引入执行计划/代价**：只比 SQL 结构，不跑 `EXPLAIN` 分析。
- **不做谓词语义等价**（`>` 与 `>=`、`BETWEEN` 与 `AND` 互换）：那是另一个量级的问题，
  眼下先保证「条件在不在」这一层可靠。
- **不用诊断改变 pass/fail**：诊断是归因工具，判分口径只认结果比对。
- **不按档位加权总分**：分档只做切片展示，不发明「难度加权准确率」这类指标。
- **不给 20 条 case 手工标难度**：分档一律从金标结构推导。
