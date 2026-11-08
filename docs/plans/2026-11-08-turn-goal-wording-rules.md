> 状态: 已完成

# Turn Understanding 收口：目标分类从 2/4 到 4/4（代码侧裁决，不改 prompt 猜）

## Context（背景）

`evaluation/turn_understanding.yaml` 的 8 例评测（2026-11-08 基线，真 `DeepSeek-V4-Flash`）：

```text
json 7/8 | act 6/6 | goals 2/4 | extreme 0/1 | inherit 4/4 | updated 7/7 | median 15.6s
```

`goals 2/4` 的两例 + `extreme 0/1` 的一例，单例复跑后根因是三类，**都不是 prompt 没写**：

| 例 | 期望 | 模型实际 | 真因 |
|---|---|---|---|
| `t4_add_dimension`「再按产品细分」 | `[compare]` | `[trend(dimension=产品)]` | 「按 X 细分」被读成「趋势」 |
| `t7_inherit_and_detail`「把刚才最高的那个月详细列出来」 | `[detail_rows]` | `[distribution(dimension=区域)]` + 反问维度 | 「详细列出来」不在明细词表 → 模型给了分布，代码也不认明细 |
| `t2_both_extremes`「销售额最多和最小的具体情况」 | 方向 `{max,min}` | 单跑对、评测那次只给了部分方向 | 采样波动，没有确定性兜底 |

关键在第三行：`_VETO_GOALS` 只**删**不**补**。明细词表 `_DETAIL_WORDS` 不含「列出来」，
所以「把…详细列出来」这类最直白的明细诉求，代码侧直接判成「不是明细」。
prompt 里那句「『详细说说』不等于逐条明细」本来是为了挡住 `t2` 的「具体情况」，
结果把「详细**列出来**」也一起挡了。

## Design（设计）

**模型负责理解，代码负责裁决**（既有分工，`reduce_task` 的 docstring 就是这么写的）。
新增 `_refine_goals_by_wording(goals, user_query)`，纯函数，在 `_veto_goals` 之后执行：

### R1 维度细分 → compare

原话命中 `按?<维度词>{0,2}(细分|拆分|分组|分类|分别|各自|对比|比较)`：

- 维度词来自既有 `requirement_parser._TOP_PERIOD_DIMENSION_WORDS`（品类/产品/品牌/区域/…）；
- 目标**收敛成单个 `compare`**，`dimension` 取原话里的维度、`metric` 沿用原目标或任务；
- 替换掉 `trend` / `overview` 等（「再按产品细分」不是趋势）。

### R2 明细到位 → 只留 detail_rows

明细词表扩成「明细 / 逐条 / 逐笔 / 原始数据 / 每一笔 / 每笔 / 详细数据 / 数据明细 /
**列出来 / 列出 / 列一下 / 列给我 / 逐条列出**」，并且：

- 命中 → goals **收敛成单个 `detail_rows`**（明细是终态：用户说「列出来」时，
  「按区域分布」这类聚合目标已经过时）；
- `dimension` / `metric` 从被替换的目标里继承，避免「按品类明细列出来」丢掉品类。

### R3 极值双向兜底

原话同时出现「最多/最高/最大」与「最少/最低/最小」，且只有一个 `extreme_period` 方向 →
补另一个（`unit` 沿用已有、缺省 `month`），`metric` 沿用。这是采样波动，不该靠运气。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/agent/turn_understanding.py` | `_DETAIL_WORDS` 扩词；新增 `_refine_goals_by_wording`（R1/R2/R3）并在 `reduce_task` 调用 |
| `backend/tests/graphs/test_turn_goal_wording.py`（新） | 纯函数用例：三类措辞 × 收敛结果 × 不误伤对照组 |
| `evaluation/turn_understanding.yaml` | 不改（评测集是标准，改它就是作弊） |

## Reused existing utilities（复用工具）

- `requirement_parser._TOP_PERIOD_DIMENSION_WORDS`（维度词表，已有单一来源）；
- `requirement_parser._TOP_PERIOD_MAX_WORDS` / `_TOP_PERIOD_MIN_WORDS`（极值词表）；
- `AnalysisGoal` / `ActiveAnalysisTask`（契约不变）；
- 既有 `_veto_goals`（删）与新 `_refine`（补/改）串联，顺序固定：先删后改。

## Verification（验证）

```bash
# 纯函数（秒级，不调模型）
pytest backend/tests/graphs/test_turn_goal_wording.py -q
# 评测（真 LLM，8 例约 2~3 分钟）：目标 goals 4/4、extreme 至少不因采样掉
python -m evaluation.turn_understanding_runner --models deepseek-ai/DeepSeek-V4-Flash
```

## Explicitly NOT doing（不做事项）

- 不因为评测而改 `turn_understanding.yaml`（期望是标准，不是可调参数）；
- 不提高思考预算换准确率（已测过：预算买延迟不买正确性）；
- 不做「语义相似度」匹配（词表 + 确定性规则足够，且可解释）；
- 不改 `conversation_act` 的判定（`act` 已 6/6→7/7）。

## 落地记录（2026-11-08）

结果：`goals 2/4 → 4/4`，顺带 `act 7/7`、`extreme 1/1`、`inherit 5/5`、`upd 8/8`、`json 8/8`。

```text
model                            json   act     goals   extreme  inherit  upd    median
改前  DeepSeek-V4-Flash           7/8    6/6     2/4     0/1      4/4      7/7    15.6
改后  DeepSeek-V4-Flash           8/8    7/7     4/4     1/1      5/5      8/8    20.3
```

### 过程中发现的关键一点（原 plan 漏了）

第一版把 `_refine_goals_by_wording` 只接在 `reduce_task` 里，评测**毫无改善**（仍 2/4）。
原因：`runner._score_case` 判的是 `plan.analysis_goals`——**模型原始输出**——而裁决发生在
更下游的 reduce。也就是说「系统行为已经对了，但计划本身还是错的」，两处对不上。

修法：把裁决前移到 `_finish_reply`（`understand_turn` / `understand_turn_async` 的共同出口），
`reduce_task` 里那次保留为**幂等兜底**（任何直接构造 `TurnPlan` 的调用方都不会漏）。
这样「模型负责理解 → 代码负责裁决」的结果才是**返回出去的那份计划**。

### 三条规则的最终形态

| 规则 | 触发 | 结果 |
|---|---|---|
| R2 明细到位 | `_DETAIL_WORDS` 命中（新增「列出来 / 列出 / 列一下 / 列给我 / 逐条列 / 都列」） | goals 收敛成**单个** `detail_rows`，`dimension`/`metric` 从被替换目标继承 |
| R1 维度细分 | 维度词 + `_SPLIT_WORDS`（细分/拆分/**拆开**/分组/分类/分别/各自/对比/比较） | 收敛成单个 `compare` + `dimension` |
| R3 极值双向 | 原话同时有「最多/最高」与「最少/最低」，但只有单方向 | 补齐另一个方向（`unit` 沿用，缺省 month），保留非极值目标 |

`_DETAIL_WORDS` 仍**不含**「详细说说 / 详细讲一下」——那是要解释不要行（`t2` 的
「具体情况」靠这条挡着），所以只加「列出」类**动词**。

### 验证结果

- `pytest backend/tests/graphs/test_turn_goal_wording.py` **28 passed**（含「返回前已裁决」两例）；
- `test_turn_understanding.py` + `test_live_session_issue_fixes.py` +
  `test_turn_request_budget.py` 一起 **66 passed**（零回归）；
- 真 LLM 评测（8 例 × 3 次采样）见上表；`t1` 那次 100.7s 是「首轮超时 → 不思考快速重试成功」，
  正好验证了 `TURN_REQUEST_BUDGET` 那条链（改前这种 case 会 135s 且大概率判失败）。

