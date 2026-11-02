# 指标口径数据化 + 需求驱动的 SQL 约束（去硬编码）

> 状态: 已完成

## Context

用户连着追问了三层：

1. 「分析 2024 年华东销售趋势和异常」生成的 SQL 是 `GROUP BY month, region`，而 region 已被 scope 过滤成常量 → 冗余、图表退化成一根柱子。
2. 「**以后换了几张表难道还要重新写吗？**」——`sql_graph._SCHEMA_TERM_HINTS` 把「销售额 → fact_orders.order_amount」这类映射写死在 Python 字符串里，换 schema 就要改代码，加指标也要改代码。
3. 「需求都生成出来了，你肯定要从需求出发生成 SQL；不然完全不具备应用条件」——卡片上的指标/维度/范围没有真正驱动 SQL。

结论：这不是「某一个问题答错」，是**口径与 schema 知识写死在代码里**，且 SQL 生成没有以需求卡为约束源。

## Design

### 1) 口径与结构变成数据文件

```
backend/app/knowledge/
├── metrics.yaml        # 指标：name / synonyms / expr / unit / note / default
├── schema_hints.yaml   # facts / dimensions / fk_chains / dimension_terms / time_dimension.buckets
└── registry.py         # 只做加载 + 解析 + 渲染
```

- `resolve_metric(term)`：按名字或同义词命中 → `MetricDef`；未命中返回 `None`（**不编造**）。
- `resolve_metrics([...])`：返回 (已解析, 未解析)。
- `render_metric_hints()` / `render_fk_chain_hints()` / `render_table_hints()`：渲染成 prompt 片段。
- `dimension_column(term)` / `time_bucket_column(granularity)`：维度与粒度查询。

`sql_graph` 里三个常量改为 import 时渲染的结果（`_FK_CHAIN_HINTS / _SCHEMA_TERM_HINTS / _PLAN_TABLE_HINTS`），调用点与既有测试不变。**换表 → 改 YAML**。

### 2) 需求卡驱动 SQL 约束（`_format_confirmed_requirement`）

新增三段确定性的约束，不再只给模型一堆自由文本：

| 卡片字段 | 注入 prompt 的约束 |
|---|---|
| `target_metrics` | 解析出的**指标表达式**，逐字使用：「销售额 = SUM(fact_orders.order_amount)」；解析不到 → 「未注册指标（禁止臆造列名）」 |
| `scope` | 「只作 WHERE 过滤，**禁止**把范围里的实体放进 GROUP BY 或 SELECT」 |
| `dimensions` + `analysis_methods` | 含 trend 时**代码层收窄**成只有时间维度（不靠模型自觉，实测光写规则它仍会 GROUP BY region） |
| `analysis_methods` | 「趋势分析：只按时间维度分组…」 |

### 3) 前端两处反人类修正（用户点名）

- **确认并生成报告后卡片只读**：`phase ∈ {generating, adjusting, report_ready}` 或 `status=locked` → `readOnly`，隐藏选项/确认/修改按钮，改需求走下方输入框。
- **状态区挪到卡片下方**：此前 `ProgressCard` / 解析卡渲染在需求卡**上方**（进度 → 卡 → 报告）。现在顺序是 问题 → 需求卡 → 状态（进度/错误/助手气泡）→ 报告。

## Files to change

| 文件 | 变更 |
|---|---|
| `backend/app/knowledge/metrics.yaml` / `schema_hints.yaml` / `registry.py` | 新增（数据 + 解析器） |
| `backend/app/agent/sql_graph.py` | 三个硬编码提示常量改为 registry 渲染 |
| `backend/app/agent/confirmed_execution_graph.py` | 需求块：指标表达式 / scope 过滤规则 / 趋势维度收窄 |
| `backend/tests/contracts/test_knowledge_registry.py` | 新增 8 例：同义词解析 / 未知指标不编造 / 维度与粒度查询 / 换卡片自动适配 |
| `frontend/src/components/workbench/RequirementCardView.tsx` | `readOnly` prop + 锁定提示 |
| `frontend/src/pages/WorkbenchPage.tsx` | `cardLocked` 判定；状态区移到卡片下方 |
| `frontend/src/components/workbench/__tests__/RequirementCardView.test.tsx` | 新增 readOnly 用例 |

## Reused existing utilities

- `app/agent/sql_graph._apply_confirmed_granularity`（粒度确定性改写）与 `knowledge.time_bucket_column` 对齐
- `RequirementCard` Pydantic 模型（未新增字段，本轮回退到「注册表解析」而非改卡片契约）
- 既有 `_format_confirmed_requirement` 的长度上限与拼接方式

## Verification

```bash
cd backend && pytest -q
cd frontend && npx vitest --run && npx tsc -b
```

真链路（真 LLM + PG），两个**不同**需求各跑一遍（通用性证据）：

| 需求 | 卡片 | 实际 SQL 形态 | 结果 |
|---|---|---|---|
| 分析 2024 年华东销售趋势和异常 | methods=[trend_analysis, anomaly_detection]，scope=[华东]，granularity=month | `GROUP BY dim_date.month`（**无 region**） | 12 行；chart `{x:月份, y:销售额}`；insight「整体呈上升趋势，后半段增长 171.4% 未发现明显异常值」 |
| 2024年各品类的销售额和订单数对比 | methods=[group_compare]，dims=[品类] | `GROUP BY dim_product.category` | 6 行；chart `pie`；insight 各品类合计 |

## Explicitly NOT doing

- 不改 `RequirementCard` 模型加 `metric_definition` 字段（本轮走注册表解析；卡片契约留到下一步）
- 不把 `_PLAN_FEWSHOT` 示例里的表名迁走（few-shot 仍是静态示例，属残余硬编码）
- 不做指标权限/血缘
- 不做多 SQL 拆分（趋势 + 异常仍共用一条查询）

## 落地记录（2026-11-02）

两轮真链路验证：

1. 第一轮 SQL **仍带 region** —— 因为 prompt 里同时有「分析维度 = [时间, 区域]」，模型照抄。说明**规则必须落到代码**：改成 trend 时在 Python 侧把非时间维度从格式化串里剔掉，第二轮 `GROUP BY dim_date.month` 才干净。
2. 第二轮 SQL 只按月分组，图表 `{x:月份, y:销售额}`，洞察「整体呈上升趋势，后半段增长 171.4% 未发现明显异常值」。
3. 换成「各品类销售额和订单数对比」：自动变成 `GROUP BY dim_product.category` + 饼图，**没有改任何代码**——这就是通用性的证据。

残余：模型本轮把注册表里多个指标一起 SELECT 了（销售额/净销售额/订单数/销量/客单价/退款率），指标选择仍偏宽；下一步应把「卡片已解析指标」作为白名单强制只选这些。

回归：backend **1061 passed / 1 skipped**（+8 契约）、frontend **324 passed**、tsc/oxlint 零错。
