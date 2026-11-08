> 状态: 已完成

# 分析质量收尾：对比基线 / 重复列 / 确认后状态（C 项）

## Context（背景）

`docs/hand-off.md` §3 的 P1/P2 里还挂着四条「分析质量」问题。逐条判断后分成「修」和「不做」：

| # | 问题 | 判断 |
|---|---|---|
| 1 | 对比类不自动带出「和谁比」（环比/同比） | **修**。基础设施（`comparison_options` / `_dim_kind` / `patch_requirement` 的 `comparison` 分支 / prompt 槽位）都在，缺的是「用户只说『对比』没说基线」时**问出来**这一环 |
| 2 | 图表/SQL 偶尔重复输出同一列（`AS "区域", AS "region"`） | **修**。表格层已有 `drop_duplicate_columns` 去重，但 `rows` 与图表 config 里的键还在——图表会出现两个同名系列 |
| 3 | 确认后 pill 仍显示「信息完整 · 待确认」 | **修**。confirm 后草稿已 locked，但前端卡与 `snapshot.current_requirement.payload.status` 都还是 `complete` |
| 4 | 按日报告的 findings 偏少 | **不做**。`hand-off` 已定论：锚定规则本身正确（「第 N 周合计」在日帧里不是单元格），要补必须先定义「合法的区间聚合来源」——那是新语义，不是 bug，另开 plan |

## Design（设计）

### D1 对比类必须问出基线

「对比」是二元的：**和谁比**没说清，SQL 只能按维度分组，出的是「各区域各多少」而不是对比。

- 需求层：`analysis_methods` 含 `group_compare`（或 `_AGGREGATE_PATTERNS` 命中「对比/比较」）
  且 `dimensions` 里**没有** `对比:*` 标签、原话也没有「同比/环比/和…比」时，
  产一个 `comparison` 待确认项（选项复用 `requirement_options.comparison_options()`：
  同比 / 环比 / 不对比只要分布）；
- 已有 `comparison` 槽位语义与 `patch_requirement` 的 `comparison` 分支，不新造字段；
- 用户原话明确说了基线（「和华南比」「同比」）→ 不问，直接落 dimensions 标签。

### D2 重复列从行键与图表里也清掉

`build_display_table` 已经裁掉了重复的**列定义**，但：

- `answer.table.rows` 里仍留着重复键（同一表达式两个别名）；
- `chart_config.config.data` 的行键同样有两个，`localize_chart` 会把它们改成同一个中文名 →
  图表上出现两个同名系列 / 轴重复。

修法（确定性，不动前端）：`build_display_table` 返回 `columns` 的同时，把**被去掉的键**
从每一行里删掉（保留先出现的那个），`localize_chart` 因此只看到一份键。

### D3 确认后卡就是 locked

两条来源都修，缺一不可：

- **前端**：`phase` 进入 `generating`/`adjusting` 时派发 `requirement/locked`
  （只有当前卡不是 `locked` 才改），pill 立刻变「✓ 已确认」，不用等 F5；
- **后端**：`snapshot_service._decode_requirement` 让 `payload.status` 跟随草稿行状态
  （draft locked → payload.status = locked），否则 F5 回来又退回「待确认」。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/agent/requirement_analysis_graph.py`（或 `requirement_slots.py`） | 对比基线缺失 → `comparison` 待确认项 |
| `backend/app/report/labels.py` | `build_display_table` 同步裁剪行键 |
| `backend/app/services/snapshot_service.py` | `payload.status` 跟随 draft 行 |
| `frontend/src/stores/analysisReducer.ts` + `sessionEvents.ts` | `requirement/locked` action |
| 测试（各自独立） | `test_compare_baseline_slot.py` / `test_display_table_dedupe.py` / `snapshotStatus.test.ts` |

## Verification（验证）

只跑针对性用例 + 真机探针，不跑全量：

```bash
pytest backend/tests/contracts/test_compare_baseline_slot.py \
       backend/tests/contracts/test_display_table_dedupe.py -q
npx vitest --run src/stores/__tests__/snapshotStatus.test.ts
python .e2elogs/probe_c_quality.py
```

## Explicitly NOT doing（不做事项）

- 不做「按日 findings 偏少」（需要先定义区间聚合语义，另开 plan）；
- 不新增 comparison 卡字段（沿用 `dimensions` 的 `对比:*` 标签，既有契约不变）；
- 不改前端渲染组件（只在 reducer 里补状态）。

## 落地记录（2026-11-08）

三项修完，第四项按计划不做。真机对比：

| 问法 | 改前 | 改后 |
|---|---|---|
| 「对比一下各区域的销售额」 | `missing: []`，直接出各区域求和（无基线） | **问出基线**（同比/环比/指定基线/不对比），选同比后 SQL 出 `当期销售额 / 去年同期销售额 / 销售额同比变化率` |
| 「销售额同比怎么样」 | `dims=['时间']`，SQL 只有 12 个月的量 | `dims=['时间','对比:同比']`，SQL 出同比四列（含变化率） |
| 「2023年各区域销售额排名」 | — | 不追问、不加标签（对照组零误伤） |

### D1 对比基线（核心）

分三层落地，缺一层都会失效——这是本轮最花时间的地方：

1. **需求层判定**（`requirement_parser._ensure_comparison_slot`）：同比/环比 → 直接落
   `dimensions` 标签；只说了「对比」且原话没有基线线索 → 补 `comparison` 待确认（选项含
   「不对比」，不强迫用户选）。**基线正则不能把裸「比」当线索**——「对**比**一下各区域」
   会把自己判成「已说基线」，这个 bug 写测试时当场抓到。
2. **TURN_UNDERSTANDING 路径也要调**：`_requirement_project` 里补一次（该路径不走
   `parse_requirement`）。第一版只加在 `parse_requirement` 里，真机一点变化都没有。
3. **执行层透传**：`comparison_baseline` 进 `ConfirmedExecutionState` → sql 子图
   （`SQLAgentState.comparison_baseline`）→ 生成 prompt 追加「必须含当期值/基准值/变化率」。

**prompt 还不够**：同一条 prompt 下「区域对比 + 同比」模型照做，而「按月趋势 + 同比」
连续两次都只给月度序列。所以在 `_validate` 里加 `_comparison_contract_violation`
（认「同比/环比/last_year/LAG/ROW_NUMBER」任一线索），违规走既有 repair 环——
加上之后一次就出对了。

### D2 重复列的行键

`build_display_table` 裁 `columns` 的同时把重复**行键**也删掉，`localize_chart` 因此
只会看到一份键。真机三次都没复现出重复列（低频采样），所以这层是预防性的——
用单测构造「同值不同别名」验证（表格列、行键、图表 data 键三处都干净）。

### D3 确认后卡就是 locked

- 前端：`phase` 进入 `generating`/`adjusting` 时派发 `requirement/locked`，
  reducer 只在「有卡且未锁」时改（闲聊轮不能凭空造卡）；
- 后端：`snapshot_service._decode_requirement` 让 `payload.status` 跟随草稿行状态，
  F5 回来也是 `locked`。

**副作用**：`2023年各区域销售额排名` 的 SQL 从 `dim_date.year = 2023` 变成了
`order_date >= '2023-01-01' AND order_date < '2024-01-01'`——同语义，不修。

### 验证结果

- 后端聚焦 **26 passed**（`test_compare_baseline_slot.py` 17 + `test_display_table_dedupe.py` 9）；
- 受影响面 7 个文件 **133 passed**（含 `test_report_labels` / `test_detail_contract` /
  `test_top_period_detail_intent` / `test_requirement_card_mirror` / `test_clarification_option_prefixes`）；
- 前端 **8 passed**（`requirementLocked.test.ts`）+ 受影响 76 passed（`analysisReducer` /
  `handleSSEEvent` / `RequirementCardView`），`tsc -b` 干净；
- 真机 `probe_c_quality.py` 见上表；探针会话（`cqual-*` 9 个）已按前缀清理，库中会话回到 13。

