> 状态: 已完成

# 需求流水线收敛：同一句话必须产出同一张卡

## Context

项目里实际存在**两条需求理解流水线**：

| 路径 | 何时走 | 实现 |
|---|---|---|
| A（**代码默认**，CI 也走这条） | `TURN_UNDERSTANDING` 未设 | `requirement_parser.parse_requirement` |
| B（本地 `.env` 开着） | `TURN_UNDERSTANDING=1` | `TurnPlan` → `reduce_task` → `assess_reportability` → `_requirement_project` |

这两条路径的**确定性归一规则集不同**，于是同一句原话会得到不同卡片。今天已经因此付过一次代价：
「时间范围确定性回填」只补了 B，A 上同一个洞还在（而 A 才是默认路径）。

本 plan 用**差分探针**先把差异量出来（同一句 + 同一份「模型说了什么」，只比执行契约字段），
再逐条判定谁对、收敛成一份实现，最后用常驻契约测试钉住。

### 差分探针结果（5 个 case，2026-11-10）

| # | case | 差异 | 判定 |
|---|---|---|---|
| ① | 「2024年各区域销售额排名」，模型只给维度 | `time_range`：A=**今年** / B=**2024年** | **A 错**：原话明写 2024，A 却按「今年」=2026 算 → 用户看到「没数据」 |
| ② | 同句，模型全给对 | 仅 `detail_level`：A=aggregate / B=None | 同语义两种取值，需统一 |
| ③ | 「帮我看一下」（什么都没抽出来） | `metrics`：A=**['销售额']**（默认不问）/ B=**[]**（问「用哪个指标」） | **A 对**：`2026-11-04-clarify-default-instead-of-ask` 已定此策略 |
| ④ | 「2024年华东每个月销售额趋势」 | 完全一致 | ✅ |
| ⑤ | 「各品类销售额和退货金额对比」 | A=拦截问「用哪个口径？」（让用户二选一）/ B=保留两口径 | **B 对**：用户要的是对比，不是选择 |

## Design

### D1 · 时间范围：唯一决策点 `resolve_time_range`

`requirement_parser.resolve_time_range(user_query, current, *, default_year)`：
**已有值（模型/状态）> 原话里的绝对时间 > 按意图给「今年」**。

- A：把两处「模型没给就直接填『今年』」替换成它；
- B：`_backfill_from_query` 也调它（此前 B 只做「原话能抽就抽」，抽不到就留空，
  与 A 的「今年」默认不一致）；
- `default_year` 由**意图**决定：趋势/对比/排名/分布/异常/选期 → True；其余 → False。

### D2 · 口径多命中不是歧义（`MetricSlot.values`）

`resolve_metric_slot` 在「原话点名多个**不同**口径」时返回 `state=filled` + `values=(...)`，
不再返回 `ambiguous`。理由见 ⑤：`match_metric_terms` 在 2026-11-10 已改成
「子串被更长命中完整覆盖才消歧」，所以 `len>1` 现在只可能是**真的两个口径**。

### D3 · 口径缺省：一份策略、两条路径都用

`_apply_slot_resolutions` 是旧路径的口径落位实现（多口径保留 / 唯一命中覆盖 / 没点名给
注册表默认 + accepted assumption 且不问）。B 直接复用它，不再各写一套。

### D4 · `detail_level` 内部统一成 `detail` / `None`

`aggregate` 只作为**输入侧**可接受值（模型/旧数据会写），输出不再出现。
A 此前「排名」给 aggregate、「趋势」给 None —— 同一语义两种取值，是 A 自己的内部不一致。

### D5 · 契约测试 = 常驻钉子

`backend/tests/contracts/test_requirement_pipeline_parity.py`：5 个差分 case
逐字段比对两条路径的卡片。**先红后绿**——它存在的意义就是让"同一个缺陷要在两条路径各修一遍"
这件事不可能再发生。

## Files to change

| 文件 | 改动 |
|---|---|
| `backend/app/agent/requirement_parser.py` | 新增 `resolve_time_range`；两处「今年」默认改调它；`aggregate → None` 归一；`_apply_slot_resolutions` 支持 `slot.values` |
| `backend/app/agent/requirement_slots.py` | `MetricSlot.values` + 多命中返回 `filled` |
| `backend/app/agent/turn_understanding.py` | `_backfill_from_query` 改用 `resolve_time_range`（含按意图的 今年 默认）+ `_goal_type` 兼容 dict/对象 |
| `backend/app/agent/requirement_analysis_graph.py` | `_requirement_project` 接入 `_apply_slot_resolutions` |
| `backend/tests/contracts/test_requirement_pipeline_parity.py` | **新增**：时间策略 / 口径槽位 / 5 个 case 的逐步落地比对 |

## Reused existing utilities

| 复用 | 位置 |
|---|---|
| `time_range_from_query` / `scope_from_query` / `match_metric_terms` | `requirement_parser.py` / `requirement_slots.py` |
| `_apply_slot_resolutions`（口径落位唯一实现） | `requirement_parser.py` |
| `_drop_satisfied_missing_fields` | `requirement_parser.py` |

## Verification

```bash
python -m pytest backend/tests/contracts/test_requirement_pipeline_parity.py -q   # 15 passed
python -m pytest <受影响的 14 个需求层文件> -q                                     # 263 passed
python -m pytest -q                                                              # 全量门禁
```

## 落地记录

- 差分探针 4 类差异全部收敛；契约测试 15 条通过。
- 受影响 14 个需求层测试文件 **263 passed**，零回归；全量后端 **1783 passed / 18 skipped**。
- 未做「两次提交」（时间 fix / 收敛）：两者落在同一批函数里
  （`resolve_time_range` 就是共享决策点、`_apply_slot_resolutions` 就是口径策略本身），
  强行拆开会产生一个「改了函数定义但没接线」的中间态，历史好看但不诚实。合为一次提交。

### 连带的夹具后果（真机才发现）

收敛后真机全量从 20/20 变成 **19/20**，红的正是 `clarify-vague-metric`（「帮我看一下」）：

- 收敛前它是 missing，是因为**口径槽位**（以及可能的分析视角）拦了一下；
- 收敛后口径缺省即默认 → 那条卡是否 missing 只剩「模型有没有给分析目标」这一**采样随机因素**。

也就是说这个断言已经不是判据了，而是一个掷硬币。处置：

- 把它改成 **`default-metric-when-unnamed`**，断言语义改为「没点名口径 → 默认口径直接前进」
  （`target_metrics == ['销售额']`）——与采样无关，才是真判据；
- baseline 因此不再钉「必须澄清」。真正**确定性**的澄清触发是「对比基线缺失」与「选期单位缺失」，
  由 `backend/tests/graphs/test_requirement_slots.py` 与 Playwright `02-clarification` 覆盖；
- 顺带印证了一条判断：**能被确定性默认掉的槽位，就不该出现在「必须澄清」的判据里**。

## Explicitly NOT doing

- **不删旧路径**：`TURN_UNDERSTANDING` 仍可关，收敛的是**规则集**不是路径本身；
  删路径是另一个量级的迁移（涉及 graph 节点、env 开关、e2e）。
- **不动置信度闸**：A 有 `_apply_confidence_gate`、B 没有，这是**第五条已知差异**，
  本轮没处理——它会改变"要不要问"，需要单独评估（差分探针的 5 个 case 都未触发）。
- **不统一 `overview` 类的口径默认措辞**：卡面文案不在契约字段内。
- **不改 SQL/报告层**：本轮只收敛需求卡。
