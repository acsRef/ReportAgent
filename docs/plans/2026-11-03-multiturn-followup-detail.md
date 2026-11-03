# 多轮追问要真的继续查出来：明细模式 + 月份区间 + 指代解析 + 多轮 e2e

> 状态: 已完成

## Context

用户 2026-11-03 的要求：「继续干，测试的时候记得要新开一个东西，用来测试多轮对话的具体项，
能不能够继续查出来」。

于是新开一条多轮探针会话（`probe-multiturn-*`）连问四轮，把「能不能继续查」变成可复现的用例：

```
1) 2024年华东销售趋势和异常     → 期望 12 行（按月）
2) 详细讲一下七月份             → 期望 1 行，2024 年 7 月
3) 那八月份呢                   → 期望 1 行，2024 年 8 月
4) 给我这两个月的明细数据       → 期望 7-8 月、逐条明细、不聚合
```

第一次跑就抓到四个真缺陷（全部修掉）：

| # | 现象 | 根因 |
|---|---|---|
| 1 | 第 2 轮 FAILED、SQL 空 | 模型把单月写成 `dim_date.month = '2024-07'`（month 是整数列） |
| 2 | 净销售额一 GROUP BY 就报错 `subquery uses ungrouped column` | `metrics.yaml` 里用相关子查询写口径，聚合查询下非法 |
| 3 | 「明细数据」仍然按 month 聚合出 2 行 | 卡片没有承载「不聚合」的字段 |
| 4 | 「这两个月」被当成「最近两个月」→ 查 9/10 月 | 指代没上下文，模型按 today 重算 |

## Design

### 1) 卡片加 `detail_level`（aggregate / detail）

`RequirementCard.detail_level` + 前端镜像字段。由 `_normalize_detail_level(user_query)` 确定性识别
（明细/逐笔/逐条/原始数据/流水 → detail；汇总/趋势/同比 → aggregate；都没说 → 继承上一轮）。

下游两处生效：

- `_format_confirmed_requirement` 输出「明细模式（优先级最高，覆盖上面的聚合/分组要求）」，并且**不再输出**趋势分组规则（否则两条指令打架）；
- `_plan` 确定性改写：`plan.dimensions = []`、`plan.aggregation = "none"`（实测只写 prompt，模型仍会 GROUP BY）。

### 2) 时间筛选与区间（确定性）

`_period_filter_hint(time_range)` 把卡片时间翻译成筛选条件，并声明列类型：

- `2024年7月` → `year = 2024 AND month = 7`（明确 month 是整数，禁止 `'2024-7'`）
- `2024年7月-8月` → `month BETWEEN 7 AND 8`

### 3) 指代解析看用户原话，不看模型输出

`_resolve_relative_month_span(card, prior, user_query)`：用户话说「这两个月/最近两个月/这俩月」且上一轮卡是单月 → 用上一轮月份往前推一个月。

第一版只检查模型输出的 `time_range`，结果这一轮模型没产出该词 → 规则没触发（多轮 e2e 直接照出来）。改为检查 `user_query`。

### 4) 指标口径带 JOIN 依赖（数据驱动）

`metrics.yaml` 的 `净销售额` 由相关子查询改成显式 JOIN，并把 JOIN 声明在 `joins:` 字段；
`MetricDef.joins` → `_format_confirmed_requirement` 输出「指标依赖的 JOIN（必须一起带上）」。

## Files to change

| 文件 | 变更 |
|---|---|
| `backend/app/models/requirement.py` | 新增 `RequirementDetailLevel` + `detail_level` 字段 |
| `backend/app/agent/requirement_parser.py` | `_normalize_detail_level` / `_resolve_relative_month_span` / `_merge_prior_card(user_query=...)` |
| `backend/app/agent/confirmed_execution_graph.py` | 明细模式块（优先级最高）+ `_period_filter_hint` + 指标 JOIN 输出 |
| `backend/app/agent/sql_graph.py` | `SQLAgentState.detail_level` + `_plan` 清空维度/聚合 |
| `backend/app/knowledge/metrics.yaml` / `registry.py` | `joins` 字段 + 净销售额改写 |
| `backend/tests/contracts/test_supplement_time_inheritance.py` | +3 例（指代 / 显式月份 / 区间提示） |
| `backend/tests/e2e/test_multiturn_followup.py` | **新增**：四轮真链路 e2e（自建会话，env-gated） |
| `frontend/src/types/requirement.ts` | 镜像 `detail_level`（可选，兼容旧会话） |
| `backend/tests/contracts/test_requirement_card_mirror.py` | TS 字段清单同步 |

## Reused existing utilities

- `_merge_prior_card`（P15 T2 supplement 继承）
- `_apply_confirmed_granularity`（粒度确定性改写的同款手法，用于 `detail_level`）
- `knowledge.registry`（时间列名 / 指标表达式都从 YAML 取）
- `evaluation.runner` 的 SSE 解析模式（e2e 用例自包含一份）

## Verification

```bash
# 离线
cd backend && pytest -q

# 真链路多轮（需 PG + ragent-py:8000 + backend:8100 + 真 LLM）
REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_multiturn_followup.py -s
```

实测（2026-11-03）：

```
第1轮 12 行  GROUP BY dim_date.month
第2轮 1 行   dim_date.year = 2024 AND dim_date.month = 7
第3轮 1 行   dim_date.year = 2024 AND dim_date.month = 8
第4轮 200 行 无 GROUP BY、LIMIT 200，明细字段含订单日期/门店
多轮 e2e：1 passed in 130.93s
```

## Explicitly NOT doing

- 不做「追问自动确认」（需求变了要让用户看到新卡）
- 不处理「上个月/去年同期」等相对时间表达
- 不做指标白名单（模型仍可能多选指标，下一轮）
- 不改 `adjust` 的落库语义

## 落地记录（2026-11-03）

四个缺陷都是**先跑真链路才暴露**的，单测一条都发现不了：

1. `month = '2024-07'` → 加 `_period_filter_hint`，明示整数月份。
2. 净销售额相关子查询非法 → 口径改成 JOIN + `joins` 声明。
3. 明细不生效 → 先补 prompt（无效），再加 `_plan` 确定性改写（生效）。
4. 指代失效 → 把判定源从「模型输出的 time_range」改成「用户原话」，多轮 e2e 复跑通过。

回归：backend **1069 passed / 2 skipped**（e2e 两个默认 skip）、frontend **324 passed**、tsc 零错。
