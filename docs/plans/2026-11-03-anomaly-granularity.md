> 状态: 已完成

# 异常类问法必须按日/周出水：粒度确定性判定 + 时间序列/横截面分流

## Context（背景）

`docs/hand-off.md` §3 P1 第一条：**异常类必须日/周粒度**（月粒度 12 个点检不出异常）。
2026-11-03 用 7 种问法 + 1 组多轮实测（`.e2elogs/probe_anomaly.py`，真 LLM + 真 PG），
拿到的是比原描述更具体的三类病灶：

| # | 问法 | 卡片 | 实际 SQL | 结果 |
|---|---|---|---|---|
| 1 | 2024年华东销售趋势和异常 | granularity=None，dims=[时间,区域] | `GROUP BY year, month, region` | 12 行（月）→ 异常检测只有 12 个点 |
| 2 | 2024年销售额有没有异常波动 | granularity=month | `GROUP BY month` | 12 行（月） |
| 3 | 最近三个月销售有没有异常 | granularity=None | `GROUP BY year, month` | 3 行 |
| 4 | **2024年哪几天销售额异常** | granularity=None | `GROUP BY year` | **1 行**（用户点名「哪几天」彻底丢失） |
| 5 | 2024年各月销售额有没有异常 | granularity=month | `GROUP BY month` | 12 行（用户显式要月 → 正确） |
| 6 | 分析一下销售的异常点 | granularity=None，time=今年 | `GROUP BY year` | **1 行** |
| 7 | 2024年各品类销售额有没有异常 | granularity=None，dims=[品类,时间] | `GROUP BY category, year` | 6 行（横截面，但混着 year） |
| 多轮 | 区域→「看看有没有异常」 | dims=[区域,时间] | `GROUP BY region, year` | 6 行（横截面，不是时间序列） |
| 多轮 | 区域→「那按周看看波动」 | granularity=week | `GROUP BY week_of_year, region` | 318 行 → **真的检测出 5 个异常值** |

三条根因：

1. **粒度不落地**：`granularity` 只在「用户/模型明确给出」时才进卡；「哪几天」「有没有异常」
   这类话既没被确定性识别、模型也常常返回 None → `_apply_confirmed_granularity` 空转 →
   dimensions 里的宽时间词「时间」由模型自由发挥，多半收敛成 `GROUP BY year` → 1 行。
2. **异常类没有默认粒度**：趋势有「粒度不明就追问」的兜底（`detect_intent == trend` 补 granularity 澄清），
   异常类什么都没有——`_apply_deterministic_intent(kind="anomaly")` 只加 `analysis_methods` +
   维度「时间」，粒度留空。
3. **时间序列 / 横截面没分流**：`detect_anomaly` 是**对单列做 2σ 检测**（`report_tools.detect_anomaly`）。
   只有单条时间序列才有意义；把「品类 × 时间」或「区域 × 时间」一起喂进去，等于拿品类总量
   跟周销量比大小（实测第 7 例报告因此写成「手机比平板高 26%，未发现异常」——答非所问）。
   反过来，用户**点名**「各品类/各区域」时他要的就是横截面比较，此时该按维度分组、时间只做过滤。

## Design（设计）

### D1 粒度确定性判定（需求层，`requirement_parser`）

新增 `_granularity_from_query(user_query)`：从**用户原话**判粒度，命中即覆盖模型输出。

| 粒度 | 线索 |
|---|---|
| day | 每天/每日/按日/逐日/日粒度/单日/按天/哪天/哪几天/哪一日 |
| week | 每周/按周/逐周/周粒度/哪周/哪一周/周同比 |
| month | 每月/按月/各月/月度/哪个月 |
| quarter | 每季度/按季度/季度 |
| year | 每年/按年/年度 |

规则与 `_deterministic_intent` 同一条哲学：**用户明说了就按用户说**，不赌采样
（实测第 4 例模型把「哪几天」丢了）。判定顺序 day → week → month → quarter → year，
避免「按日」被 month 的「月」误伤（线索词都是多字词，无单字「月」）。

### D2 异常类：时间序列 vs 横截面（`_apply_deterministic_intent(kind="anomaly")`）

新增 `_anomaly_cross_dimension(user_query)`：本轮原话命中「各/每个/哪个/分 + 维度词
（品类/区域/门店/品牌/客户）」→ 返回该维度中文标签，否则 None。

- **命中（横截面）**：`dimensions = [该维度]`，`granularity = None`
  （时间只作 WHERE 过滤）。理由：用户要的是「哪个品类不正常」。
- **未命中（时间序列）**：`dimensions` 只保留时间维度（丢掉 scope 造成的常量维度，
  例如「2024年华东销售趋势和异常」里模型塞的「区域」——华东是过滤条件，分组后是常量列），
  `granularity = 用户显式粒度 or 默认粒度`。
- **默认粒度**（`_default_anomaly_granularity`）：按时间跨度确定——
  `≤ 45 天 → day`，否则 `week`（整年 = 52 点，正好落在「能查出异常」的密度上）。
  跨度由 `_range_days(time_range)` 解析：`YYYY年M月` → 31；`YYYY年M月-M月` → 月数×31；
  `YYYY年` → 366；`最近N天/近N周/最近N个月` → N/N×7/N×31；`今年/去年` → 366；解析不出 → None。

> 为什么不让 prompt 兜：与 `_apply_confirmed_granularity` 同一条理由（见 `sql_graph` 注释）——
> 粒度是确定性事实，prompt 里写了模型照样按 year 分组（实测第 4/6 例）。

### D3 下游零改动（复用既有确定性通道）

粒度进了卡体后，三处既有通道自动生效，本次**不加新机制**：
- `sql_graph._apply_confirmed_granularity`：plan.dimensions 的宽时间词替换成「周/日期…」；
- `confirmed_execution_graph._format_confirmed_requirement`：把「时间粒度 = 周（必须按
  `dim_date.week_of_year` 分组，不要用 year）」写进 SQL prompt；
- `chart_advisor` 按列名识别时间维度（含「周」）+ `report_prompts` 的 anomaly 叙述档。

### D4 前端（后端验证通过后再做）

后端验完再决定。已知候选：需求卡上**没有粒度的展示位**（用户看不到「异常类默认按周」），
`RequirementCardView` 只显示 指标 / 时间与范围 / 维度 / 方法。阶段 2 再评估是否补一个
「粒度」chip（数据已在卡体 `granularity` 字段里，前端零成本）。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/agent/requirement_parser.py` | `_granularity_from_query`（原话线索）/ `_range_days` / `_default_anomaly_granularity` / `_anomaly_cross_dimension`；`_apply_deterministic_intent` 的 anomaly 分支分流；`parse_requirement` 应用确定性粒度覆盖 |
| `backend/tests/graphs/test_anomaly_granularity.py`（新） | 12-16 例：原话粒度 5 种 / 默认粒度（整年→week、单月→day、最近7天→day）/ 横截面 vs 时间序列 / 显式粒度优先 / 「哪几天」端到端卡片 |
| `frontend/src/components/workbench/RequirementCardView.tsx` | 阶段 2：粒度 chip（先看后端） |
| `docs/plans/README.md` | 本 plan 登记 |
| `docs/hand-off.md` | P1 第一条结项 |

## Reused existing utilities（复用工具）

- `sql_graph._apply_confirmed_granularity` + `_GRANULARITY_DIM_LABELS`（粒度 → dimensions 改写）
- `confirmed_execution_graph._GRANULARITY_COLUMNS` / `_format_confirmed_requirement`（粒度 → SQL prompt 列名）
- `app/knowledge/registry.time_dimension_terms()` / `time_bucket_column`（时间维度词汇与列名，YAML 数据驱动）
- `requirement_parser._dim_kind` / `_normalize_granularity` / `_GRANULARITY_ALIASES`
- `report_tools.detect_anomaly`（2σ 检测，不动）+ `report_prompts` 的 anomaly 叙述档
- `.e2elogs/probe_anomaly.py`（本次新建的多角度体检脚本，复用 `evaluation.runner` 的 SSE 工具）

## Verification（验证）

**阶段 1：后端链路（先做，多角度多轮）**

1. `python .e2elogs/probe_anomaly.py`：7 条问法 + 多轮，判据逐条为
   - 第 1/2/3/6 例（纯异常/趋势+异常）：`GROUP BY` 必须落在日或周桶（`dim_date.week_of_year` 或
     `dim_date.full_date`），行数 ≥ 12；`granularity` 不再为空；
   - 第 4 例「哪几天」：`granularity=day`，`GROUP BY dim_date.full_date`，行数 = 365/366；
   - 第 5 例「各月」：仍 month（用户显式优先，不许被默认值覆盖）；
   - 第 7 例「各品类异常」：横截面 `GROUP BY dim_product.category`，行数 = 品类数（6）；
   - 多轮「看看有没有异常」：时间序列（周），不许退化成 `GROUP BY region`；
   - 多轮「那按周看看波动」：week（现状已对，防回归）。
2. `pytest backend/tests/graphs/test_anomaly_granularity.py -q`（新增单测）
   + `pytest backend/tests/graphs -q`（全 graphs 不回归）。

**阶段 2：前端（后端绿了之后）**

3. 浏览器：异常类问法确认后，卡片 / 报告 / 图表都能看出「按周」；如需补 chip 再改
   `RequirementCardView` + 单测。
4. `cd frontend && npm run test:run`、`npx tsc -b`、`npm run lint`。

## Explicitly NOT doing（不做事项）

- 不改 `detect_anomaly` 的算法（仍是 2σ 单列检测）；不做多序列分组检测（横截面 vs 时间序列已在需求层分流）；
- 不动 `week_of_year` 的建模（`dim_date` 已是 `week_of_year` int；跨年周序问题不在本次范围）；
- 不给趋势类加"默认粒度"——趋势已有"粒度不明→追问"的既有兜底，本次只修异常；
- 不改报告叙述 prompt（先用真链路看现有 anomaly 档在周序列上够不够用）；
- 不碰 SQL 安全闸 / MCP / 后台化链路。

## 落地记录（2026-11-03）

### 阶段 1：后端链路（先做，7 问法 + 多轮真链路验证）

改完后 `.e2elogs/probe_anomaly.py` 全绿（真 LLM + 真 PG）：

| 问法 | 卡片粒度 | GROUP BY | 行数 | 异常结论 |
|---|---|---|---|---|
| 2024年华东销售趋势和异常 | week | `week_of_year` | 53 | 检出 1 个异常（第 7 周） |
| 2024年销售额有没有异常波动 | **week**（原 month） | `week_of_year` | 53 | 结论正常 |
| 最近三个月销售有没有异常 | week | `week_of_year` | 13 | 连续 6 周偏低被点出 |
| 2024年哪几天销售额异常 | **day**（原 None→1 行） | `full_date` | 366 | 具体日期异常 |
| 2024年各月销售额有没有异常 | month（用户显式） | `month` | 12 | 未被默认值覆盖 |
| 分析一下销售的异常点 | week | `week_of_year` | 44 | 检出 2 个异常值 |
| 2024年各品类销售额有没有异常 | None（横截面） | `category` | 6 | 「均未发现异常」语义正确 |
| 多轮：区域 → 看看有没有异常 | week | `week_of_year` | 53 | 检出第 7 周 |
| 多轮：→ 那按周看看波动 | week | `week_of_year` | 53 | 防回归 |

### 实际改动与偏差

1. **模型自己的粒度猜测不再压过默认值**（偏差，原设计写成「用户 > 模型 > 默认」）：
   实测「2024年销售额有没有异常波动」模型给 `month` → 12 个点，2σ 检不出东西。
   异常类是「数据密度决定能不能用」的场景，优先级改为 **用户原话 > 异常类默认 > 模型猜测**。
2. **多修了两处报告层缺陷**（实测暴露，属同一「异常类结果能不能读」的链路）：
   - `report_graph._fallback_headline` 取「第一个数值列」→ 按周聚合时那是 `周`（int 1..53），
     结论写成「周合计 1,484.00」（把周序号求和）。改用 `report_tools.measure_keys`（排除时间维度），
     并把标签写成「第41周 / 6月 / 2024年」（此前是「周=41 最高」）。
   - 数字锚定只收**单元格值** → 「全年合计 2.63 亿」「平均每周 …」这类**派生但真实**的结论全被判成
     编造，日/周长序列报告的 findings 几乎全灭。`_collect_data_numbers` 现在补上度量列的
     合计与均值（全量行算）；同时**年份不再进锚定**（`2024 年` 是日历标签，不是数据断言）。
3. **PATCH 路径同步**：`requirement_service._apply_intent` 加 `anomaly` 分支（用户从澄清项选
   「异常」时也要落粒度），懒 import 复用同一个 `_default_anomaly_granularity`。
4. **`_apply_deterministic_intent` 增加 `user_query` 参数**（异常分支要读原话判粒度与横截面维度）。
5. **多轮 e2e 带出的第 5 处修正：明细模式的确定性契约**（`sql_graph._validate` 之后）。
   改完粒度后跑 `test_multiturn_followup.py`，第 4 轮「给我这两个月的明细数据」实测拿到
   `SELECT SUM(order_amount)` 且无 GROUP BY → **1 行汇总**，而卡上 `detail_level=detail`、
   plan 阶段维度已清空、生成 prompt 也明写「禁止聚合」。这是 P1 第 2 条（明细类）的邻居问题：
   光写 prompt 治不住采样。修法：在安全闸/EXPLAIN 通过之后加一道**语义契约**——明细模式下
   出现聚合函数或 GROUP BY 即判 validation 失败（`error_kind=syntax`），交给既有 repair 环
   带 repair_ctx 重生成；非明细模式的聚合不受影响（按周/月汇总是正常的）。

### 阶段 2：前端（后端绿了之后才动）

1. **需求卡补粒度 chip**（`RequirementCardView`）：「时间与范围」组里紧跟时间范围显示
   按日/按周/按月/按季度/按年。此前粒度只活在卡体字段里，用户看不到「异常类默认按周」，
   也不知道结果为什么是 53 行而不是 12 行。
2. **明细表展开加上限**（`TableBlock`）：按日聚合 = 366 行，此前「展开更多明细」一次铺开全部
   DOM；现在展开只铺前 50 条，页脚写「显示 50 / 366 条（长结果仅展开前 50 条，完整数据请用导出）」。
   ≤50 行的表行为不变。

### 验证证据

| 层 | 命令 | 结果 |
|---|---|---|
| 后端单测 | `pytest backend/tests/graphs -q` | **175 passed**（新增 45 + 8 + 9 例） |
| 后端回归 | `pytest backend/tests/{api,contracts,smoke} -q` | **697 passed** |
| 后端真链路 | `python .e2elogs/probe_anomaly.py` | 7 问法 + 多轮全绿（表见上） |
| 多轮 e2e | `REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_multiturn_followup.py -q` | **1 passed / 142s**（首轮断言改钉「按周 ≥50 行」，第 4 轮明细由新契约守住） |
| 主干 smoke | `python .e2elogs/smoke_trunk.py "2024年各区域销售额"` | 6 行 SUCCESS |
| 前端单测 | `npm run test:run` | **343 passed**（新增 4 + 2 例） |
| 前端类型/风格 | `npx tsc -b` / `npm run lint` | 0 error |
| 前端真链路 | `node .e2elogs/probe_anomaly_ui.mjs` | 按日 chip ✔ / 表格 50·366 上限 ✔ / 按周 chip ✔ |

### 已知遗留（转 hand-off P2）

- **按日报告的 findings 偏少**：叙述模型爱引用「第 7 周合计」这类周级派生值，而日帧里没有这一格
  → 被锚定规则丢掉（规则本身正确：不许编）。要让日粒度报告的叙述更饱满，需要给锚定补「同一时间桶
  的区间聚合」来源——那是一个独立设计题（怎么界定「合法的区间聚合」），不在本 plan。
- **`_default_intent_clarification` 不提供「异常波动」选项**：只在原话命中「异常/波动/异动」时走
  确定性闸；模糊问法（「分析一下销售」）不会主动提供异常这一视角。要不要加是产品选择，先记账。
