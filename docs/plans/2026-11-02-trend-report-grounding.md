# 趋势/异常类问题答非所问：图表维度选错 + 报告规划不知道需求

> 状态: 已完成

## Context

用户实测：「分析 2024 年华东销售趋势和异常」，得到的是**一根柱子的柱状图 + 月份/区域/支付金额三列表格**，既没有趋势结论也没有异常检测，问「人看得懂吗」。

排查（真库真链路）：

- 需求卡其实是**对的**：`target_metrics=['sales_trend','anomalies']`、`granularity=month`、`scope=['华东']`、`analysis_methods=['trend_analysis','anomaly_detection']`。
- SQL 也对：`GROUP BY dim_date.month, dim_store.region`，12 行。
- 断在两处**确定性**缺陷上：

**缺陷 A（图表）** `sql_tools.chart_advisor` 的维度候选是「文本分类列在前、时间列在后」。`区域` 被 scope 过滤成常量（只有「华东」），但它仍是「文本列」→ 被选成 x 轴 → `len(rows)=12 > 8` → bar with x=区域 → 一根柱子。

**缺陷 B（报告规划）** `report_graph._plan_analysis` 只把「列名 + 行数 + 工具菜单」喂给规划模型，**用户问题与分析要求完全没进 prompt**。卡片上的 `analysis_methods` 从没有被任何代码读过，于是 `trend_analysis` / `detect_anomaly` 永远排不进 steps。

两者叠加，用户看到的就是「一张错图 + 一张表」。

## Design

### A. 图表维度：只让**会变化**的文本列当分类维度

`chart_advisor` 内计算每列的 distinct 值数量：

```python
varying_text = [c for c in text_cols if _distinct(c) > 1]
categorical_cols = varying_text + time_cols
```

常量文本列不再吃掉维度名额；全部是常量时回落到时间列（时间序列仍然出 bar，不退化 pie/table）。这是确定性修复，不依赖模型。

### B. 报告规划拿到需求 + 需求→工具的确定性映射

- `build_report_plan_prompt(...)` 增加可选参数 `requirement_summary / analysis_methods / dimensions`，在 task_contract 之后插入「【用户需求】」段：原始诉求、分析维度、已确认的分析方法，以及**必须**生成的 step 清单。
- 映射表 `_METHOD_TO_TOOL`：`trend_analysis→trend_analysis`、`anomaly_detection→detect_anomaly`、`comparison→group_compare` 等；白名单外的方法名不产生任何要求（不凭空造工具）。
- 明确排序：分析类工具在前，`chart_advisor` 放最后。
- 透传链：`confirmed_execution_graph._confirmed_report_agent` 把 `requirement_card` 传进 report 子图 → `ReportAgentState` 新增 `requirement_card` channel → `_plan_analysis` 读取。

### 为什么不用「让模型自己判断」

`analysis_methods` 是需求确认阶段**用户已经点头**的结论，属于确定性输入。让报告层再"猜"一次，就是把已确认的契约丢掉重掷骰子——与「Agentic where uncertainty exists, deterministic where correctness matters」相反。

## Files to change

| 文件 | 变更 |
|---|---|
| `backend/app/tools/sql_tools.py` | `chart_advisor`：常量文本列不参与维度选择 |
| `backend/app/agent/prompts/report_prompts.py` | `build_report_plan_prompt` 新增需求段 + 方法→工具映射 |
| `backend/app/agent/report_graph.py` | `ReportAgentState` 加 `requirement_card`；`_plan_analysis` 传需求 |
| `backend/app/agent/confirmed_execution_graph.py` | report 子图调用透传需求卡 |
| `backend/tests/smoke/test_report_tools_numeric_strings.py` | 新增常量维度回落时间列用例 |
| `backend/tests/contracts/test_report_plan_requirement.py` | 新增 3 例：需求段 / 无需求不插段 / 未知方法不造工具 |

## Reused existing utilities

- `chart_advisor` 既有 `_is_time_dimension` / `is_numeric_value` 判定
- `build_report_plan_prompt` 既有六段结构（新段插在 task_contract 之后，不改 registry 结构）
- `_METHOD_TO_TOOL` 里的工具名与 `app/tools/__init__.py` 注册的 5 个报告工具一一对应
- 既有 `test_report_tools_numeric_strings.py` 的时间维度用例组

## Verification

```bash
cd backend && pytest tests/contracts/test_report_plan_requirement.py \
  tests/smoke/test_report_tools_numeric_strings.py -q
cd backend && pytest -q          # 全量
```

真链路（真 LLM + PG + backend :8100）：`.e2elogs/probe_trend.py` 复跑同一条问题，断言：

```
card.analysis_methods = ['trend_analysis', 'anomaly_detection']   （未变）
chart dims            = {'x': '月份', 'y': '销售额'}                （原为 区域）
insight               = 整体呈上升趋势，后半段增长 171.4% 未发现明显异常值
```

## Explicitly NOT doing

- 不改 `adjustment_text` 的语义（调整请求仍是「重跑同一张卡」，那需要卡片层重解析，另开 plan）
- 不新增图表类型（仍是 pie/bar/table；前端 ChartBlock 无 line）
- 不删表格里的常量列（`区域` 仍会显示，属展示冗余，不是错误数据）
- 不做异常阈值可配置化（沿用 2σ）

## 落地记录（2026-11-02）

真链路实测（`probe-trend-*` 会话）：

```
chart type: bar | dims: {'x': '月份', 'y': '销售额'}
insight: 整体呈上升趋势，后半段增长 171.4% 未发现明显异常值
SQL: 月份/区域/销售额/订单数，GROUP BY dim_date.month, dim_store.region
```

对比修复前：`dims={'x': '区域', 'y': '支付金额'}`、insight 只有统计摘要、无趋势无异常。

顺带观察：同一张卡这次生成的 SQL 同时取了「销售额 + 订单数」（assumptions 里两个口径），比只取支付金额更贴合「销售趋势」。
