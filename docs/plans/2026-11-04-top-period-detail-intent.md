> 状态: 已完成

# 「最高的那个月的明细」这类问法：需求层听懂 + 执行层真的只取那一期

## Context（背景）

用户在第一轮（2024 年华东销售趋势和异常）之后说：

> **「我只要2024年份中最多月份的那个月数据详细图」**

然后 Agent 回了一句：

> 「还需要你补充一点：**你想看2024年销售情况的哪种视角？**」+ 三个通用选项（全年总览 / 按月趋势 / 区域对比）

用户原话：

> **「我觉得我说的够清楚了，为什么还是这种补充？你觉得合理吗？上下文连串在一起就废掉了？？？」**

### 取证（真库 `agent.requirement_draft` id=4388，就是这一轮）

| 字段 | 实际值 | 应该是什么 | 问题 |
|---|---|---|---|
| `summary` | 「分析2024年华东销售额最高的月份数据」 | —— | 模型**其实听懂了**「销售额最高的月份」 |
| `detail_level` | `aggregate` | `detail` | 「数据详细图」没被识别成明细 → 会去聚合 |
| `analysis_methods` | `["anomaly_detection"]` | `[]` | 上一轮的“异常检测”被继承了，与新诉求无关 |
| `missing_fields` | `analysis_intent`「哪种视角？」 | 无 | **用户已经说清楚**，却退回通用提问 |
| `slot_states.analysis_intent` | `missing` | `filled` | 同上 |

再往前追一层（会话 7613c963 的「那最多那一周为什么销售额这么多」→ v2 SQL）：
SQL 是 `GROUP BY year, region` 的**一行汇总**——「最高那周」在执行层也完全没落地。

两个独立缺陷：

1. **需求层听不懂「最高/最多的那一期 + 明细」**：`_INTENT_PHRASES` 里有「最高/最多」，
   但 `_deterministic_intent` 对「只有 最多/最高、没有维度词」的问法返回 `None`（注释写着
   「不知道比什么 → 交给澄清」）。于是模型接管，问了「哪种视角」。
   `_DETAIL_PATTERNS`（明细|逐笔|逐条|原始数据|流水|每笔）也不含「详细图/详细数据」。
2. **执行层没有「选最高期」这个概念**：卡片里没有任何字段承载“取销售额最高的那一期”，
   SQL 只能做成全区间聚合。

## Design（设计）

### D1 需求层：确定性识别「最高期 / 明细」

`requirement_parser.py`：

- 扩展 `_DETAIL_PATTERNS`：`详细图|明细图|详细数据|明细数据|数据详细|每一条|全部字段|所有字段|清单`
  （不动裸「详细」——「详细讲一下」是叙述诉求，不是明细）。
- 新增 `_top_period_from_query(query) -> TopPeriod | None`：正则
  `(最多|最高|最大|峰值)` / `(最低|最少|最差)` + `(月份?|周|日|天|季度|年)`，
  允许中间夹「的 / 那个 / 这个 / 一」→ 返回 `{direction, unit}`。
- 识别到就按 `top_period` 走确定性落地（**优先于模型**）：
  `card.top_period`、`granularity = unit`、有明细词 → `detail_level=detail` 且
  `analysis_methods=[]`、`dimensions=["时间"]`；**清掉** analysis_intent/metric 类澄清项。
  → 用户不会再被问「哪种视角」。

### D2 卡片契约：`top_period` 字段

`models/requirement.py` 新增可选字段（old card 兼容，缺省 None）：

```python
class RequirementTopPeriod(BaseModel):
    direction: Literal["max", "min"]
    unit: RequirementGranularity      # 按哪一期取极值（月 / 周 / 日 / 季 / 年）
    metric: str | None = None         # 用哪个指标取极值（默认注册表口径）
```

前端同步：`types/requirement.ts` 加同构类型；确认条 chip 与需求详情多一条
「销售额最高月份」——**让用户看见自己被听懂了**（这正是这次最刺痛的地方）。

### D3 槽位：有 `top_period` 就不再问「分析角度」

`requirement_slots.slot_states`：`analysis_intent` 在 `is_detail or top_period` 时算已填。

### D4 执行层：把「最高期」变成确定性 SQL 契约

- `confirmed_execution_graph._top_period_hint(card)`：写死两段式指令（先 CTE 选期，再取该期数据），
  列名全部来自知识库（`time_bucket_column(unit)` / 指标注册表 expr），**不硬编码表名**；
- 透传 `top_period` 进 `sql_graph`（新 state channel），生成阶段再强调一次；
- 校验契约（`sql_validate`，与既有 detail 契约同一处）：
  - `_top_period_contract_violation`：SQL 里必须有 `LIMIT 1`（选期），否则 repair；
  - 明细 + 最高期并存时，**允许 CTE 内聚合**（选期必须 SUM+ORDER BY），
    但 CTE 之外的最终查询仍不许聚合/分组 → `_strip_cte_blocks()` 后判定。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/models/requirement.py` | `RequirementTopPeriod` + `RequirementCard.top_period` |
| `backend/app/agent/requirement_parser.py` | 明细词扩展、`_top_period_from_query`、`_apply_deterministic_intent` 支持 `top_period` |
| `backend/app/agent/requirement_slots.py` | `analysis_intent` 在 detail / top_period 下算已填 |
| `backend/app/agent/confirmed_execution_graph.py` | `_top_period_hint` 注入 `_format_confirmed_requirement`；透传 `top_period` |
| `backend/app/agent/sql_graph.py` | state channel + 生成提示 + `_strip_cte_blocks` / `_top_period_contract_violation` |
| `frontend/src/types/requirement.ts` | `top_period` 类型 |
| `frontend/src/components/workbench/slotQuestion.ts` | `confirmChips` 增加最高期 chip |
| `frontend/src/components/workbench/RequirementCardView.tsx` | 详情里显示最高期 chip |
| 测试 | parser / slots / hint / sql 契约 / 前端 chips |

## Reused existing utilities（复用工具）

- `knowledge.time_bucket_column` / `resolve_metrics`：期列与指标表达式全部来自注册表；
- 既有 repair 环与 `_detail_contract_violation` 形态（违反 → 按 validation 失败重生成）；
- 卡片既有 `slot_states/slot_order` 与前端「一次一问」通道；
- 既有 `_period_filter_hint`（年份/月份筛选）。

## Verification（验证）

1. `pytest backend/tests/graphs/test_requirement_parser*.py backend/tests/graphs/test_sql_generation*.py`（新增用例）。
2. 真链路（backend :8100 + 真 LLM）跑用户这一轮：
   1) 新会话问「分析 2024 年华东销售趋势和异常」→ 确认并出报告；
   2) 追问「我只要2024年份中最多月份的那个月数据详细图」→ 断言：
      - 卡片 `detail_level=detail`、`top_period={max, month, 销售额}`、
        **没有** analysis_intent 澄清项、`analysis_methods` 为空；
      - 确认后 SQL 含 `LIMIT 1` 选期且明细无 GROUP BY；执行返回该月的多行明细（不是 1 行）。
3. `pytest backend/tests/{graphs,api}` 回归；`npm run test:run` 回归。

## Explicitly NOT doing（不做事项）

- 不做通用的「Top-N 期」参数化（只支持最高/最低一期；N>1 需要新的交互，先不做）；
- 不改 `analysis_methods` 的既有取值集（不新增工具）；
- 不让需求层执行 SQL（预览例外只覆盖口径总量，见 CLAUDE.md §4.1）；
- 不重构 `metric_preview_service` 来复用选期查询（它不是同一类查询，且契约受限）。

## 落地记录（2026-11-04 深夜，真链路）

### 改了什么（最终形态）

1. **需求层听懂**：`_top_period_from_query`（最高/最低 + 期单位）+ 明细词扩展
   （详细图/明细数据/清单…）。这一轮卡片变成：`detail_level=detail`、
   `top_period={max, month, 销售额}`、`analysis_methods=[]`、**没有** analysis_intent 追问。
2. **选期不由模型算**：`metric_preview_service.resolve_top_period` 先用注册表口径 +
   `schema_hints` 的时间桶列跑一条 `GROUP BY year, 期 ORDER BY <指标> DESC LIMIT 1`
   （Execution 域只读查询），把结果变成确定性 WHERE（`dim_date.year = 2024 AND dim_date.month = 11`）。
   模型只需要「照这个 WHERE 取数」。
   *为什么不再让模型写 CTE*：实测模型会输出 `SELECT ...; SELECT ...` 两条语句，
   sqlglot 直接判语法错，「选出那一期」根本没跑起来。
3. **明细投影真的生效**：`_has_dimension_column` 之前扫全文，`dim_date.full_date`
   出现在 JOIN ON、`dim_store.region` 出现在 WHERE 都算「有维度列」→ 明细表只有一列
   也能过闸。改成只看 **SELECT 投影**（`_select_list`），违规交给既有 repair。
4. **契约兜底**：`_top_period_contract_violation` 双形态——已算出期时要求 SQL 带该期过滤，
   算不出来时要求 `LIMIT 1` 选期结构；明细 + 选期并存时允许 CTE 内聚合
   （`_strip_cte_blocks` 后只看最终查询）。
5. 顺手修一个「重复问口径」：澄清选项把物理列名（`order_amount`）原样写进
   `target_metrics`，下一轮继承时认不出中文口径 → 又问一次「用哪个口径？」。
   `requirement_service._normalize_metric_name` + `resolve_metric_slot` 继承路径都做了归一。

### 真链路证据（用户原话那一轮，session `topperiod-f1afce`）

需求卡：

```json
{"detail_level": "detail", "top_period": {"direction": "max", "unit": "month", "metric": "销售额"},
 "granularity": "month", "time_range": "2024年", "scope": ["华东"], "analysis_methods": []}
```

用户点了确认后生成的 SQL（真实落库 `agent.report_version.query_snapshot`）：

```sql
SELECT dim_date.full_date AS 日期, dim_store.region AS 区域,
       dim_product.category AS 品类, fact_orders.order_amount AS 销售额
FROM fact_orders
LEFT JOIN dim_date ON dim_date.full_date = fact_orders.order_date
LEFT JOIN dim_store ON fact_orders.store_id = dim_store.store_id
LEFT JOIN dim_product ON fact_orders.product_id = dim_product.product_id
WHERE dim_date.year = 2024 AND dim_date.month = 11 AND dim_store.region = '华东'
ORDER BY fact_orders.order_date DESC
LIMIT 200
```

结果：**200 行 × 4 列**（日期/区域/品类/销售额），`row_count=200`，无聚合、无 GROUP BY。
2024 年 11 月正是销售额最高的月份（9,437,879.30 元，由系统预先算出）。

### 没做 / 未验证的部分（诚实记录）

- 新写了 `evaluation/chain_matrix.yaml` + `backend/tests/e2e/test_chain_matrix.py`
  （12 条寻常问法的整链矩阵，env-gated）；用户明确要求「不要再一直跑测试」，
  因此**本轮没有跑完**，留作一次性回归工具，不作为本次验收依据。
- `detail_after_trend`（「把最差的那个月的明细给我看看」）这类说法在 `_TOP_PERIOD_MIN_RE`
  之外（说的是「最差」+ 「那个月」而非「最低的月」），**未验证**是否命中，属已知边界。
- 报告层对「单月明细」的叙述模板未调整（仍走通用明细分支）。
