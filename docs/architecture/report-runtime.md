# Report Runtime 与 Fact Checker 分层

> 状态: 冻结（P1，2026-08-26）— 目标契约。当前实现差距见「现状映射」节与各 Phase plan。
> 上游: [2026-08-25-refactor-master-freeze.md](../plans/2026-08-25-refactor-master-freeze.md) §十二 + §四 Report Agent 段。

## 一、ReportSpec

Report Agent 输入 `Execution Result`（+ RequirementCard / Memory preference），输出 **ReportSpec**（结构化对象，不是自由 Markdown）。至少支持：

```text
title / summary / insight / kpi / chart / table / section / recommendation / alert
```

**数据真实性原则**：所有数值、排名、统计、图表数据必须来自实际 Query Result；禁止编造数字/指标/字段/修改 SQL 结果。

## 二、三层 Validator

1. **结构校验**：chart field 来自 query result、table column 存在、KPI field 存在。
2. **数值校验**：结构化 ReportSpec 中数值必须来源于 Query Result 或明确允许的 aggregation/arithmetic。
3. **禁止自由生成**：Report Agent 输出的是 ReportSpec 而非自由 Markdown；校验对象是 **`ReportSpec → QueryResult` 的映射**，不是渲染产物。

> 明确不做对最终 HTML 文本的正则数字审计——`12345 / 12,345 / 1.23万 / 12.35%` 会大量误报。

## 三、ReportVersion append-only 语义

三态全部落 append-only 行（`agent.report_version`），失败/空结果保留作 traceback：

| 函数 | 场景 |
|---|---|
| `persist_confirmed_run` / `persist_adjust_run` | SUCCESS |
| `persist_empty_run` | EMPTY（合法零行） |
| `persist_error_run` | FAILED |

FAILED/EMPTY 经 SSE `report`/`error` 事件带 `error_kind` + 尝试过的 SQL 下发；前端 ErrorCard 按 kind 分支、ReportPaper 渲染「未找到匹配记录」带——三态 verdict 永不伪造成功。

## 四、边界区分

- `agents/report/` = **Agent 怎么决定报告结构**（思考）；
- `report/` = **报告这个 Domain Object 怎么定义、校验、版本化**（契约）。
两个边界不合并。

## 五、现状映射（截至 P1）

| 契约要素 | 现状 | 差距归属 Phase |
|---|---|---|
| 三态落库 | `backend/app/services/report_version_service.py` 四个 persist_* 在位（append-only） | — |
| ReportSpec 结构 | `models/contracts.py` 已有 ReportSpec 雏形（version/insight/chart_config 等）；九类 block 未齐 | P10 扩展 schema |
| 三层 Validator | 未成形——现靠 report_graph prompt 约束 | P10 |
| Report Agent 决策面 | `agent/report_graph.py` 固定三节点（plan_analysis → run_step → build_output） | P10 迁 `agents/report/` 并强化决策 |
