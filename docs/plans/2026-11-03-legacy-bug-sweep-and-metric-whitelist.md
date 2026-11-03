# 旧 bug 清理 + 指标白名单（A–E）

> 状态: 已完成

## Context

用户 2026-11-03：「先把之前的 bug 解决了再弄这个，一步一步来」。

之前遗留的问题里，属于 bug 的五条（新功能另开）：

| # | 问题 | 证据 |
|---|---|---|
| A | 报告标题恒为「报告」/「报告（调整）」 | 左栏十条会话十个「报告」，翻历史分不清 |
| B | 表格显示常量列（scope 过滤后的「地区=华东」） | 明细模式表格出现 `订单日期/销售额/地区`，地区恒为华东 |
| C | `/api/v1/sessions` 的 `report_versions` 恒为 `[]` | `repository.py` 注释写着「lazily populated」，但没有任何代码去填 |
| D | `_PLAN_FEWSHOT` / `_SQL_GENERATION_RULES` 硬编码表名 | 换 schema 要改代码 |
| E | 模型把注册表里 6 个指标一起 SELECT | 销售额/净销售额/订单数/销量/客单价/退款率全上，答案不聚焦 |

## Design

### A. 标题来自需求

`_report_title(state)`：取 `requirement_card.summary`，压空白、超 24 字加省略号，无卡退回「报告」。
四个落库分支（confirmed / adjust / empty / error）统一用它。

### B. 隐藏常量列

`_drop_constant_columns(columns, rows)`：所有行取值相同的列隐藏；三个保护——
单行结果不动、单列结果不动、全列都是常量时不删空。

### C. 批量补齐会话版本

`list_sessions` 在拿到当页 session 后，用**一次** `WHERE session_id = ANY($2)` 查版本再分组，避免 N+1。

### D. few-shot 与生成规则迁进 YAML

`schema_hints.yaml` 新增 `plan_few_shot`（结构化 user/output）与 `sql_generation_rules`（文本块），
`registry.render_plan_few_shot()` / `render_sql_generation_rules()` 渲染。
至此 `sql_graph.py` 里不再有任何表名/列名/口径字面量。

### E. 指标白名单（两级解析）

`render_metric_hints(only=[...])` 支持白名单；`_metric_hints_for(card)`：

1. `target_metrics` 直接命中注册表 → 只渲染这些；
2. 命不中（`sales_trend` 这类语义名）→ 从**用户已接受的假设**文本里做词面匹配；
3. 都没有 → 返回 `None`，退回全量（无卡片场景）。

命中时，`_format_confirmed_requirement` 追加「只输出这些指标列，不要额外新增指标」，
`_plan` / `_generate_sql` 用白名单版提示替换全量注册表。

## Files to change

| 文件 | 变更 |
|---|---|
| `backend/app/agent/confirmed_execution_graph.py` | `_report_title` / `_drop_constant_columns` / `_metric_hints_for` + 透传 `metric_hints` |
| `backend/app/infra/conversation/repository.py` | 会话列表批量补 `report_versions` |
| `backend/app/agent/sql_graph.py` | `metric_hints` channel；plan/generate 改用白名单提示；few-shot/rules 改 registry 渲染 |
| `backend/app/knowledge/registry.py` | `render_metric_hints(only)` / `metrics_mentioned_in` / `render_plan_few_shot` / `render_sql_generation_rules` |
| `backend/app/knowledge/schema_hints.yaml` | `plan_few_shot` / `sql_generation_rules` |
| `backend/tests/contracts/test_report_title.py` | 5 例 |
| `backend/tests/contracts/test_report_table_columns.py` | 5 例 |
| `backend/tests/contracts/test_knowledge_registry.py` | 白名单 / 假设兜底 / 换 schema 证明 |

## Verification

```bash
cd backend && pytest -q
```

真链路：

| 场景 | 结果 |
|---|---|
| 「分析 2024 年华东销售趋势和异常」 | 标题 = `分析2024年华东地区的销售趋势和异常情况`；SQL 指标从 6 个降到 **2 个**（销售额/订单数）；表格 `月份/销售额/订单数` |
| 「2024年各品类销售额和订单数对比」 | SQL 只有 `销售额`+`订单数`；6 行；饼图 |
| `GET /api/v1/sessions` | 每个会话都能拿到 `report_versions`（4 条） |
| 多轮 e2e | `1 passed in 126.70s`（A/B/C 修复后复跑） |

## Explicitly NOT doing

- 不做「需求-结果一致性」校验（F，下一轮）
- 不改 `ReportPaper` 的渲染结构（常量列是后端过滤）
- 不把 `attention` / KPI 等其它硬编码迁走（本轮只清表名口径）

## 落地记录（2026-11-03）

- A 真机：新报告标题为需求摘要；旧数据仍是「报告」（不回填历史）。
- B 单元覆盖 5 例；真机 trend 查询表格已无「地区」列。
- C 真机：`versions=4` 的会话列表可见。
- D 新增「换 schema」测试：把 YAML 里 `fact_orders→sales_fact`、`dim_store→store_dim` 后
  few-shot / 规则 / 表清单全部跟着变，代码零改动。
- E 真机：趋势查询指标从 6 个降到 2 个；未注册语义名走「已接受假设」兜底。

回归：backend **1084 passed / 2 skipped**、frontend **324 passed**。
