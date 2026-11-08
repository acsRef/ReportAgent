> 状态: 已完成

# 澄清选项必须带正确前缀（否则指标被写坏）+ 明细投影要有列清单

## Context（背景）

2026-11-08 真机 `probe_detail_columns.py` 抓到两个用户可见的错：

### 一、点任何澄清选项都会把「指标」写坏（真 bug，优先级最高）

问「把订单列出来看看」，卡上 `target_metrics = ['总体情况']`、`detail_level = None`，
SQL 是 `SELECT SUM(fact_orders.order_amount) FROM fact_orders` → **1 行一个总额**。

根因在 `turn_understanding.project_card`：**所有** blocker 的选项 value 一律拼成
`metric:<中文 label>`：

```python
options=[RequirementOption(label=opt, value=f"metric:{opt}") for opt in blocker.options]
```

而 `analysis_intent` 这个 blocker 的选项是「总体情况 / 按月趋势 / 最高与最低 / 逐条明细」。
用户点「逐条明细」→ 前端回传 `selected_value="metric:逐条明细"` → 后端
`requirement_service.patch_requirement` 按前缀分派 → 指标被写成「逐条明细」，
`detail_level` 一个字段都没设。

后端其实**早就支持** `intent:` 前缀（`_apply_intent` 能把 trend/detail/overview 翻成
granularity + analysis_methods + detail_level），只是生成侧从没发过 `intent:`。
同病：`granularity` blocker 的选项是「按月/按周/按天」，没有前缀 →
`card_dict["granularity"] = "按月"` 会撞 `Literal` 校验。

### 二、明细投影的列太少

「2023年11月的订单明细给我看看」→ 200 行、`detail_level=detail` ✓，但只有
`日期 / 区域 / 品类 / 订单金额` 四列，没有 订单号 / 数量 / 单价 / 客户。
`_detail_contract_violation` 只要求「不许聚合 + 至少一个日期/维度列」，
`日期 + 金额` 两列就算合规——「一条订单的明细」显然不止这些。

### 三、未知指标没人拦（次要）

`metric='总体情况'` 这种不在注册表的字符串能一路走到 SQL。`assess_reportability`
只有在调用方传 `known_metrics` 时才判 `UNSUPPORTED`，而链路里没传。

## Design（设计）

### D1 选项 value 按 blocker.key 分派前缀

```python
_PREFIX_BY_FIELD = {
    "metric": "metric",
    "analysis_intent": "intent",
    "granularity": "granularity",
    "time_range": "time",
    "scope": "scope",
}
```

- `analysis_intent` 的 label 先翻成确定性 code（`_apply_intent` 认得的词）：
  总体情况→`overview`、按月趋势→`trend`、最高与最低→`extreme`、逐条明细→`detail`；
- `granularity` 的 label（按月/按周/按天/按季度/按年）先翻成 `month/week/day/quarter/year`；
- 其余保持 `<prefix>:<label>`。

接收侧补两件事：

1. `requirement_service.patch_requirement` 增 `granularity:` 前缀分支，
   接受中文或英文单位（两种都归一成英文，脏值丢弃而不是写坏卡）；
2. `_apply_intent` 增 `extreme` 分支：`top_period = {direction: both, unit: month}`，
   否则「最高与最低」会被 else 兜成 overview。

### D2 明细投影的列清单

- **生成侧**（`sql_graph._generate_sql` 的明细段）：把「一条业务明细该有哪些列」写清楚，
  并从 schema 里挑真实列名：时间、门店/区域、品类、数量、金额，有订单号就带上；
- **契约侧**（`_detail_contract_violation`）：在「至少一个日期/维度列」之上加一条
  **最少列数**（≥3：日期 + 维度 + 指标）——两列（日期+金额）不再算明细；
- 违规文案直接列出缺什么，交给既有 repair 环。

### D3 未知指标拦截

`_task_reduce` 调 `assess_reportability(task, known_metrics=...)` 时传注册表指标集合
（`knowledge.all_metrics()`），未知指标 → `UNSUPPORTED` → 卡上出「当前数据不支持」的待确认项，
不再让它走到 SQL。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/agent/turn_understanding.py` | `_PREFIX_BY_FIELD` + `_option_value`（label→code 映射）；`project_card` 用它生成选项 |
| `backend/app/services/requirement_service.py` | `granularity:` 前缀分支；`_apply_intent` 增 `extreme` |
| `backend/app/agent/sql_graph.py` | 明细 prompt 列清单；`_detail_contract_violation` 加最少列数 |
| `backend/app/agent/requirement_analysis_graph.py` | `_task_reduce` 传 `known_metrics` |
| 测试（各自独立） | `test_clarification_option_prefixes.py` / `test_detail_projection_columns.py` |

## Reused existing utilities（复用工具）

- `_apply_intent`（已有 intent→卡字段的确定性翻译，只补 extreme 分支）；
- `patch_requirement` 的「前缀:值」分派表（不新造协议，只补齐前缀）；
- `_select_list` / `_has_dimension_column`（已有投影解析，直接数候选列）；
- `knowledge.all_metrics()`（注册表唯一来源）。

## Verification（验证）

```bash
pytest backend/tests/contracts/test_clarification_option_prefixes.py \
       backend/tests/contracts/test_detail_projection_columns.py -q
```

真机（同一组问法，判据是卡字段 + 列数，不是「有没有数据」）：

```bash
python .e2elogs/probe_detail_columns.py
# 期望：选「逐条明细」→ detail_level=detail、target_metrics 不被污染；
#       明细列 ≥ 3 且含日期 + 维度 + 指标
```

## Explicitly NOT doing（不做事项）

- 不改前端选项交互（前端只回传 `selected_value`，协议不变）；
- 不新增 blocker 类型（只修前缀映射）；
- 不把明细列清单做成硬编码表名（用 schema 里真实存在的列名）；
- 不为「总体情况」这种词做同义词扩展（未知指标直接判 UNSUPPORTED）。

## 落地记录（2026-11-08）

三项全落，真机对比直接可读：

| 问法 | 改前 | 改后 |
|---|---|---|
| 「把订单列出来看看」 | `detail=None`、`metric=['总体情况']`、**1 行**、`SELECT SUM(order_amount)` | `detail=detail`、`metric=['净销售额']`、**200 行**、7 列（含**订单编号**） |
| 「2023年11月的订单明细」 | 4 列（日期/区域/品类/金额） | 5 列（多一个**订单编号**） |

### D1 选项前缀 ✅

`turn_understanding._PREFIX_BY_FIELD` + `_option_value(key, label)`：
`analysis_intent` → `intent:<code>`（总览/趋势/极值/明细 四个 code），
`granularity` → `granularity:<month|week|day>`，`metric` / `time_range` / `scope` 保持原前缀。
接收侧补 `granularity:` 分支（中文/英文都归一，认不出返回 None 而不是写坏卡）与
`_apply_intent("extreme")`（落 `top_period={direction: both, unit: month}`，此前会掉进 overview）。

### D2 明细列数下限 ✅

`_count_select_columns`（忽略函数括号里的逗号）+ `_MIN_DETAIL_COLUMNS = 3`；
契约顺序是「聚合 → GROUP BY → 列数 → 维度列」，所以 `SELECT SUM(...)` 仍先报「禁止聚合」。
生成侧 prompt 同时把列清单写死：日期 / 门店或区域 / 品类 / 数量 / 金额（有订单号也带上），
并要求「至少 4 列」。

**连带修改**：`test_detail_contract.py::test_validate_detail_ok_passes` 原来拿
`full_date + order_amount`（两列）当「合格明细」——那正是真机里「一列数」的形态，
按新契约必须改成三列。这不是为过测试而改，是契约收紧的正确连带。

### D3 假指标守卫 ✅

D3 的前提判断错了一半：`_reportability` 其实**早就**传了 `known_metrics`。
真正漏的是 **PATCH 之后**——`patch_requirement` 合入 `selected_value` 时不校验注册表，
所以「总体情况」在被写进卡后就再没人管。改成 `metric:` 前缀只在
`_is_known_metric(name)` 为真时写入，否则保留原口径并 warning（知识库不可用时放行）。

### 验证结果

- `test_clarification_option_prefixes.py` **33 passed**（编码 10 + 产卡 3 + 接收 4 + 守卫 6 …）、
  `test_detail_projection_columns.py` **9 passed**；
- 受影响面 8 个文件一起 **150 passed**（含 `test_requirement_card_mirror` /
  `test_metric_preview_service` / `test_user_sim` / `test_detail_contract`）；
- 真机 `probe_detail_columns.py` 见上表；探针里顺手修了「一律选第一个选项」的盲点
  （原来永远选「总体情况」，正好把「点了明细拿不到明细」这个 bug 掩盖住）。

