> 状态: 已完成

# 明细类列太薄（P1-②）+ 过程帧带结论

## Context（背景）

用户截图（2026-11-04）指出两件事：

1. **「思考的内容也要输出」**：执行过程面板只有步骤名（`解析需求`），看不到这一步
   得到了什么；用户要的是「里面思考的内容是什么」。
2. **「明细怎么成这个样子了」**：`帮我分析一下销量` 的报告里，
   - 关键数据明细只有一列「销量」；
   - 可视化画出一排无意义柱子（x 轴是 1/8/2/3/5/…，y 轴最高 20）。

## 诊断（代码取证）

| 现象 | 真因 |
|---|---|
| 明细只有一列 | 明细模式只把「要原始列」写进 prompt，**没有确定性投影**；模型只 `SELECT quantity`。P1-② 记的正是这条 |
| 图表一排噪声柱 | 后端 `chart_advisor` 对单列数值**已经**返回 `{"type":"table"}`，但前端 `prepareChart` 把「非 line/pie」一律强转 `bar`（`type='table'` 也被转）→ x/y 都退化成同一列，画出一排柱子 |
| 过程面板没内容 | `trace` 帧的 `detail` 恒为空字符串；节点侧的结论（意图/口径/待确认/预览数值）没有出口 |

## Design（设计）

### D1 明细确定性投影（后端，`confirmed_execution_graph._detail_projection_hint`）

明细模式的 prompt 里追加一份**照抄清单**：`日期 + 维度列 + 原值指标列`，列名/别名全部来自知识库：

- 日期：`knowledge.time_bucket_column("day")`；
- 维度：卡片声明的维度经 `dimension_column()` 映射；卡片没声明就取**不同维度表**的前两个（区域 + 品类）；
- 指标：只接 `SUM(<列>)` 形态（能安全脱掉聚合）；`COUNT`/比率/`FILTER` 口径在明细里没有单行原值 → 跳过（宁缺勿造列）。

### D2 图表不许强画（前端，`chartPrepare` + `ChartBlock`）

- `type === 'table'` 直接返回 `chartType: 'table'`，不再被强制转成柱状图；
- 推断后若 `xField === yField`（没有真正的维度）→ 同样退回 table；
- `ChartBlock` 在 `table` 时**不渲染**（下方「数据明细」照常展示），避免一张噪声图。

### D3 过程帧带结论（后端 `progress_detail` + 前端展示）

- 新增 `app/infra/execution/progress_detail.py`：contextvar 收集「节点 → 这一步的结论」，
  `ProgressTraceHandler` 在**收尾帧**把 detail 拼进 `trace.detail`（running 帧不带）；
- 已在需求链写入结论：`安全检查`（通过/拦截 + 风险分）、`识别意图`（意图 + 理由）、
  `准备分析数据`（检索到几张表）、`解析需求`（时间/口径/范围/待确认项）、
  `口径预览`（三个候选的真实总量）、`保存需求卡`（草稿号 + 状态）；
- 前端 `TracePanel`：步骤名下面一行灰色小字显示 detail（换行自适应）。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/agent/confirmed_execution_graph.py` | `_SIMPLE_SUM_RE` + `_detail_projection_hint`，明细模式追加投影 |
| `backend/app/infra/execution/progress_detail.py`（新） | detail contextvar 通道 |
| `backend/app/infra/execution/progress.py` | `format_progress_frame` 支持 detail；收尾帧读取 |
| `backend/app/agent/requirement_analysis_graph.py` | 6 处节点写结论 |
| `backend/app/main.py` | 安装/清理 detail sink |
| `frontend/src/components/report/blocks/chartPrepare.ts` / `ChartBlock.tsx` | table 不再强画 |
| `frontend/src/components/workbench/TracePanel.tsx` + `workbench.css` | 步骤下显示 detail |
| 测试 | `chartPrepare.test.ts`（3 例）、`TracePanel.test.tsx`（detail 例）、后端 api 帧断言已有 |

## Verification（验证）

- `pytest backend/tests/{api,graphs,contracts,smoke} -q` → 全绿
- `npm run test:run` / `npx tsc -b` / `npm run lint`
- 真链路：`2024年各区域表现` 的 trace 帧应带结论（实测）：
  `识别意图 | 意图=report（…）`、`解析需求 | 时间 2024年｜口径 销售额｜待确认 1 项`、
  `口径预览 | 销售额 2.63 亿元、净销售额 2.51 亿元、实收金额 2.47 亿元`；
  明细类问法（`帮我分析一下销量` → 选明细）表格必须出现 日期/区域/品类 + 销量，且不出噪声柱状图。

## Explicitly NOT doing（不做事项）

- 不做 CoT（不接 `reasoning_content`）；
- 不把 prompt/SQL 全文塞进 detail（只放确定性结论，≤160 字）；
- 不重写 `chart_advisor` 的判定逻辑（它本来就对；错在前端强转）；
- 明细投影不做「任意指标都能取原值」——`COUNT`/比率/`FILTER` 口径仍不进明细列。
