> 状态: 已完成

# Requirement 槽位契约：口径确定性填充 + 带数据澄清 + 多轮隔离 + 单问式呈现

## Context（背景）

用户反馈「答案会乱、追问也奇奇怪怪」，并给了一份外部诊断（意图没槽位化 / 澄清不带数据 /
多轮被上一轮叙述污染）。逐条对代码核实后：**两条成立、一条基本不成立，且机理与外部描述不同**。

| 外部诊断 | 本仓库是否成立 | 证据 |
|---|---|---|
| 意图没槽位化 | **部分成立**：槽位已有（`time_range/granularity/detail_level/scope/dimensions/analysis_methods/target_metrics`），缺**三态**与**口径确定性填充** | `backend/app/models/requirement.py` |
| 澄清带问题不带数据 | **成立** | `RequirementOption` 只有 `label/value`；`requirement_options.metric_options()` 是静态表（含注册表里没有的「客户数」，却缺 `净销售额/实收金额`），与 `metrics.yaml` 脱节 |
| 多轮被上一轮「叙述」污染 | **基本不成立** | `save_message` 落的 assistant 消息内容是**空串**（实测 `[user '2024年各区域销售额', system '', assistant '', user '只看华东']`）；Requirement 档会话上下文只在含「继续/刚才/上次/再按/那个」等历史引用词时注入（`SelectiveRecallPolicy` §二触发1） |
| `paid_amount/gmv/sales_amount` 字段 | 不存在 | 现役 `fact_orders.order_amount` / `fact_payments.payment_amount`；口径在 `app/knowledge/metrics.yaml` |
| 「是否含退款」是独立槽位 | 不是 | 由口径候选承载（销售额 / 净销售额 / 实收金额 / 退款金额） |
| 「L3 已存口径」是前提 | 不成立 | `memory.semantic_entry` 只有 preference/semantic/query；已确认槽位的权威记录是 `agent.requirement_draft`（需求卡） |
| 追问越走越偏 | **成立，但通道不是对话文本** | 见下三条 |

**真实的口径漂移三通道（本 plan 的打击目标）**
1. **模型自由选口径**：实测「2024年各区域销售额」卡片 `target_metrics=['实收金额']`——注册表里
   `销售额` 明明 `default: true`（`.e2elogs/probe_scope.py`、`smoke_trunk.py` 均可复现）。
2. **`_merge_prior_card` 无条件继承**：supplement 轮只要模型没给值就继承上一轮卡的口径，
   而上一轮口径本身可能是猜的 → 猜测被逐轮固化成「事实」。
3. **query memory 参考帧 + 子串匹配**：Requirement 档在 `data_similar && history_ref` 时注入 1 条
   历史 SQL（含旧口径表达式）；`metrics_mentioned_in` 用子串匹配（「净销售额」里含「销售额」）。

## Design（设计）

### Phase 1｜槽位三态 + 口径确定性填充（纯确定性，不动架构）

新增 `backend/app/agent/requirement_slots.py`（纯函数，可单测）：

- `SlotResolution{key, state: filled|missing|ambiguous, value, candidates, source}`
  （`source ∈ explicit|registry_default|model|inherited`）。
- `resolve_metric_slot(user_query, model_metrics)`，顺序即优先级：
  1. 用户原话命中注册表 → `filled`，覆盖模型输出。匹配必须**最长优先 + 精确优先**
     （先等值匹配 name/synonym，再按「原话包含该词」取最长者），避免「净销售额」被「销售额」抢；
  2. 用户原话没提指标（或提了但注册表认不出）→ 候选 = {模型猜的指标 ∪ 注册表 `default`} 去重：
     ≥2 → `ambiguous`（交 Phase 2 补数字），=1 → `filled`；
  3. 都没有 → `missing`（澄清项选项由注册表派生）。
- `slot_states(card)` → 七个槽位的状态字典；`next_slot(states)` → **确定性提问顺序**
  （口径 > 时间范围 > 粒度（仅趋势/异常）> 范围 > 分析角度），供 Phase 5 呈现层使用。

接线：
- `requirement_parser.parse_requirement` 以 slots 结果为准（用户词 > 注册表 default > 模型猜）；
  `ambiguous` → `RequirementMissingField(key="metric")`，选项由注册表构造（`value=metric:<name>`）。
- `requirement_options.metric_options()` 静态表**删除**，`options_for("metric")` 改从 registry 派生。
- `models/requirement.py` 新增可选字段：`RequirementCard.slot_states: dict[str,str] = {}`、
  `RequirementCard.slot_order: list[str] = []`、`RequirementOption.hint: str | None = None`
  （默认空 → 旧卡/旧会话/旧前端兼容）。

### Phase 2｜带数据的澄清：口径预览（Execution 域服务，Requirement 只消费）

对应 BIRD-INTERACT 的 **a-Interact**（agent 主动行动消歧）。`CLAUDE.md` §4 冻结
「Requirement Agent 禁止生成/执行 SQL」，因此预览**不是** Requirement 自拼 SQL：

- 新增 `backend/app/services/metric_preview_service.py`（Execution 域侧，注册表驱动、
  **无 LLM、无自由列名**）：用 `MetricDef.expr/joins` + `knowledge.time_bucket_column/dimension_column`
  确定性拼一条**总量**查询（不含 GROUP BY），经 `asyncio.to_thread(execute_sql)` 走
  `ragent_readonly` + 安全闸 + EXPLAIN + statement timeout（仓库既有 async 里跑 sync psycopg2 的惯例）。
- **fail-open**：时间范围解析不出具体年/月、查询报错、超时、`null` → `{ok: False}`，
  调用方退回纯文字选项（**绝不展示 0 元假数**）。
- 图接线：`requirement_analysis_graph` 新增节点 `requirement_preview`，边序
  `requirement_parse → requirement_preview → persist_draft`；仅当 `slot_states.metric == "ambiguous"`
  且候选 ≥2 时启用，最多 3 个候选、2s 级超时。
- 回填：数值写进选项 `hint`（`2.61 亿 · 订单金额未扣退款`）。
- **契约注记**：`CLAUDE.md` §4 加限定例外（见「Files to change」），明确写入而不是偷偷做。

### Phase 3｜多轮隔离：每轮只从「已确认槽位」出发

- `requirement_analysis_graph._requirement_parse` 上下文来源改**白名单**：
  - 保留：`_format_prior_block(prior_card)`（结构化槽位）+ 最近 ≤5 条**用户原话**（≤400 字符，
    仅用于指代消解）+ 本轮原话；
  - 移除：`assembled_context`（召回文本 / L2 摘要 / query memory 参考 SQL）不再进需求解析 prompt。
- 新增 `app/memory/conversation.format_recent_user_utterances(session_id, user_id, limit)`（只取 `role=user`）。
- `_merge_prior_card` 继承时在 `slot_states` 标 `inherited`；用户本轮命中注册表口径 → 覆盖。
- `knowledge.registry.metrics_mentioned_in` 改**最长命中优先**，修「净销售额」命中「销售额」。

### Phase 4｜BIRD-INTERACT 式交互回归（三层）

外部依据：BIRD-INTERACT（ICLR 2026，[项目页](https://bird-interact.github.io/)）的要素是
**GT SQL + 可执行测试用例**、**function-driven 用户模拟器**、两阶段度量
（**Priority Questions SR** 消歧阶段 / **Follow Ups SR** 追问阶段）+ **Efficiency（轮数）** + patience。
我们只借「任务构造 + 模拟器 + 三阶段度量」，不接官方 runner。

- **4A 任务构造**：`evaluation/requirement_slots.yaml` 15 条，每条含
  `ground_truth{metric_expr_substring, granularity}` / `priority_questions{must_ask, must_not_ask}` /
  `follow_ups[{query, ground_truth}]` / `patience`。
- **4B 脚本化用户模拟器**（零 token）：`evaluation/user_sim.py::GroundTruthUser`——只按 GT 回答
  （GT 里的槽位被问到 → 选对应选项；不在 GT → 「按你默认」；已确认为过的槽位不重复回答；
  `patience` 用尽即结束记 penalty）。`evaluation/runner.py::run_case` 增可选 `user_sim`
  （默认仍 `_fill_missing_fields`，兼容既有用例）。
- 度量：`priority_question_sr` / `turns_used`（Efficiency）/ `follow_up_sr` /
  **`false_green`**（没问到关键槽位却直接出结果 → 记 fail；这正是盲选 fill-all 会漏掉的假绿）。
- **4C LLM 用户模拟**（可选、env-gated、不进 CI）：`SLOT_SIM=llm`，多采样 3 次统计收敛率。
- **4D 明确不做**：BIRD 官方 runner 接入、600 任务规模、a-Interact 的 BIRD COIN 成本模型、
  Stress Mode / ITS 曲线。

### Phase 5（最后做）｜呈现层：一次一问 + 紧凑确认条，卡降级为「详情」

**决策**：后端**保持**结构化槽位与整卡下发（SSE `requirement` 事件不变），只改呈现与提问节奏。
不做「后端对话式逐问 + 每次自然语言再解析」——那会把确定性翻译换回模型推理，与本次修复同源相悖。

- 后端只新增确定性排序 `slot_order`（复用 `next_slot`），随卡下发；不发新事件、不改 wire 结构。
- 前端：
  - 新增 `QuestionBar`：一条 agent 气泡只问 `slot_order` 里第一个非 `filled` 的槽位；选项为可点选
    chip（带 `hint` 数值）+ 「补充说明」入口（复用 composer）；
  - 答完立刻出下一问（由 PATCH 后的新卡推导）；
  - 全部 `filled` → **紧凑确认条**：`时间 2024年 · 按周 · 净销售额（已扣退款） · 华东` +
    「确认并生成报告」+「展开详情」；
  - 现有 `RequirementCardView` 保留为「详情」视图（14 个既有用例继续有效）；
  - 补轮仍显示 `changeNote`，改挂在确认条上。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/agent/requirement_slots.py`（新） | 槽位三态 + 口径确定性解析（最长优先）+ 候选集 + `next_slot` |
| `backend/app/agent/requirement_parser.py` | 接入 slots；`ambiguous` → 注册表派生澄清；标 `inherited`；不接 `assembled_context` |
| `backend/app/agent/requirement_options.py` | 删静态 metric 表 → `registry` 派生 |
| `backend/app/services/metric_preview_service.py`（新） | 口径预览（确定性总量 SQL + `to_thread` + fail-open） |
| `backend/app/agent/requirement_analysis_graph.py` | 新节点 `requirement_preview`；上下文白名单 |
| `backend/app/memory/conversation.py` | `format_recent_user_utterances()` |
| `backend/app/models/requirement.py` | `slot_states` / `slot_order` / `hint`（均可选） |
| `backend/app/knowledge/registry.py` | `metrics_mentioned_in` 最长命中优先 |
| `evaluation/user_sim.py`（新）、`evaluation/runner.py` | `GroundTruthUser` + PRQ/FU/Efficiency/false_green 度量 |
| `evaluation/requirement_slots.yaml`（新） | 15 条 GT 夹具 |
| `backend/tests/graphs/test_requirement_slots.py`、`test_metric_preview_service.py`（新） | 离线契约 |
| `backend/tests/e2e/test_requirement_slots.py`（新） | 4A+4B 真链路（env-gated） |
| `frontend/src/components/workbench/QuestionBar.tsx`（新）、`WorkbenchPage.tsx`、`RequirementCardView.tsx`、`types/requirement.ts`、`styles/workbench.css` | Phase 5 单问 + 确认条 + 详情折叠 |
| `CLAUDE.md` §4 | 口径预览限定注记（唯一契约修订点） |
| `docs/plans/README.md`、`docs/hand-off.md` | 索引登记 + 交接更新 |

## Reused existing utilities（复用工具）

`app/knowledge/registry.py`（`MetricDef/resolve_metric/all_metrics/time_bucket_column/dimension_column`）、
`metrics.yaml`、`app/tools/sql_tools.execute_sql`（安全闸 + EXPLAIN + 只读角色）、
`app/reliability/timeout.run_with_timeout`、`_format_prior_block`、`format_context_block`、
`RequirementMissingField/RequirementOption` wire 形态、`evaluation/runner.py` 的 SSE/`_observe_turn`、
`evaluation/real_user_questions.yaml` 夹具写法、既有 `RequirementCardView` 与其 14 个用例。

## Verification（验证）

**离线**
- `pytest backend/tests/graphs/test_requirement_slots.py test_metric_preview_service.py -q`
- `evaluation/user_sim.py` 单测（GT 应答 / patience / false_green 判定）
- `pytest backend/tests/{graphs,api,contracts} -q` 不回退

**真链路（先后端、多角度多轮）**
- `.e2elogs/probe_slots.py`：12 条问法（明确口径 / 模糊口径 / 相对时间 / 显式月粒度 / 追问改口径）
  打印 `slot_states`、澄清 `label+hint`、确认后 SQL 的实际表达式；
- `REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_requirement_slots.py -q`（输出 PRQ/FU/turns/false_green）；
- 既有 `backend/tests/e2e/test_multiturn_followup.py` + 主干 smoke 两条。

**前端**
- `npm run test:run` / `npx tsc -b` / `npm run lint`；
- 浏览器 `node .e2elogs/probe_slots_ui.mjs`：单问逐条推进 → 紧凑确认条 → 报告；澄清项显示数值。

## Explicitly NOT doing（不做事项）

- **不把后端改成对话式逐问**（每次自然语言回复再解析）——会同时毁掉确定性翻译与可评测性；
- 不做 PleaSQLarify 式「多候选 SQL 采样聚类 + 信息增益最大化」，只对**口径**一个槽位做 ≤3 候选目录式预览；
- 不把「是否含退款」拆成独立槽位（由口径候选承载）；
- 不写 L3 `semantic_entry` 的口径记忆（跨会话口径沿用另开 plan）；
- 不接 BIRD 官方 runner / 600 任务 / BIRD COIN 成本模型 / Stress Mode / ITS 曲线；
- 不新增 SSE 事件或卡片类型；`hint`/`slot_states`/`slot_order` 均为可选字段；
- 不动 Report 层、异常检测、后台任务链路。

## 落地记录（2026-11-04）

### Phase 1｜槽位三态 + 口径确定性（commit `d62550a`）

- 新增 `backend/app/agent/requirement_slots.py`：`match_metric_terms`（**最长命中优先**）、
  `resolve_metric_slot`（三态 + 候选）、`slot_states` / `slot_order` / `next_slot`（确定性提问顺序）。
- `requirement_parser` 接入：口径优先级 **用户原话 > 上一轮已确认 > 注册表 default > 模型猜**；
  歧义时补 `RequirementMissingField(key="metric", state="ambiguous")`，选项 `value=metric:<注册表名>`。
- `requirement_options.metric_options()` **删掉手写静态表**（含注册表没有的「客户数」）→ 从
  `metrics.yaml` 派生；`RequirementCard` 增 `slot_states`/`slot_order`，`RequirementOption` 增 `hint`。
- 契约镜像测试（`test_requirement_card_mirror.py`）与 `test_schema_grounding_alignment.py`
  同步更新（同 commit，按既有规矩）；`test_requirement_merge_supplement` 的「退货率」按注册表
  规范名 `退款率` 断言。

### Phase 2｜口径预览（commit `9b7672f` + `3382d3c`）

- 新增 `backend/app/services/metric_preview_service.py`：`resolve_period_bounds`（年份/年月/月区间/
  ISO 区间/今年/最近 N 天→半开区间）、`build_preview_sql`（注册表 `expr`/`joins` + 时间过滤 +
  可选 `dim_store` 区域过滤，**无 GROUP BY**）、`preview_metrics`（`asyncio.to_thread(execute_sql)`
  + `run_with_timeout` + fail-open）。
- 图节点 `requirement_preview`：`requirement_parse → requirement_preview → persist_draft`，
  仅当口径 ≥2 候选时触发；数值写进 `RequirementOption.hint`。
- **契约**：`CLAUDE.md` §4.1 新增「口径预览例外」限定（唯一契约修订点，实现约束四条）。
- 实测（真链路）：`2024年各区域表现` → 三候选带真实数值
  `销售额 2.63 亿元 / 净销售额 2.51 亿元 / 实收金额 2.47 亿元`；
  `实收金额 + scope` 因无声明外键链路 → fail-open（不显示假数）。

**偏差 1（重要）**：原设计「≥2 候选才问」在实测中不触发——模型恰好也猜默认口径，
于是「用户没点名口径」这件事被静默带过。改为**用户没点名口径一律问**，
候选 = 模型猜 ∪ 注册表 default ∪ 同单位其它指标（≤3，数据驱动不写死名字）。
**偏差 2**：模型自己的粒度/口径猜不再压过默认值（与 2026-11-03 异常粒度同一条优先级原则）。

### Phase 3｜多轮隔离（commit `a14be5d`）

- `ContextRuntime.build()` 增加 **REQUIREMENT 档分流**：会话上下文改走
  `prepare_user_utterances_context`（只取最近 5 条**用户原话**，≤400 字符，无 L2 摘要/L2.5 脉络、
  无记忆写副作用）；召回里 **drop `kind=="query"`**（上一轮 SQL 参考帧）。
  **这样既满足「不让 Agent 自拼 Context」的 Forbidden Pattern，又满足隔离目标**——
  比原计划「graph 节点自己拼用户原话」更合规，故采用。
- `_merge_prior_card` 之上再叠一层：supplement 轮用户没提口径 → 用 `inherited`（不再重复问）。
- `knowledge.metrics_mentioned_in` 改最长命中优先（修「净销售额」命中「销售额」）。
- 契约测试更新：`test_context_runtime_contract` 的通用五步管线改用 EXECUTION 档验证，
  新增 `test_requirement_profile_isolates_conversation_and_query_memory` 钉住隔离语义。

### Phase 4｜BIRD-INTERACT 式交互回归

- `evaluation/requirement_slots.yaml`：**15 条** GT 夹具（显式口径 5 / 模糊口径 3 / 粒度 2 /
  多轮 3 / 口语 2），字段含 `ground_truth.metric|granularity|sql_must`、
  `priority_questions.must_ask|must_not_ask`、`patience`。
- `evaluation/user_sim.py::GroundTruthUser`：**只按 GT 作答**、一次答一个槽位（按 `slot_order`）、
  patience 上限、`must_not_ask` 违规记录、**GT 选项不存在时拒绝作答**（`gt_option_missing`）。
- `backend/tests/e2e/test_requirement_slots.py`（env-gated）：逐 case 打印
  `converged / priority_ok / turns / asked / gt_missing`，汇总 PRQ / FU / turns_avg / 违规数。
- 单测：`backend/tests/graphs/test_user_sim.py`（9 例，含「GT 选项缺失必须拒绝」）。

**两处测试装置缺陷（实测暴露，已修）**：① 模拟器按 `missing_fields` 原始顺序答题，会先答
`analysis_intent`（→ overview 把口径留成模型猜的销售额），改为按 `slot_order`；
② 模型现场生成的 `field_meaning:*` key 模拟器不认 → 直接跳过、卡片卡在 missing（看起来像产品 bug），
改为「未知 key 原样当槽位」。
**一处设计边界（记录不修）**：口径候选只覆盖默认口径所在**单位族**（元：销售额/净销售额/实收金额），
数量类（订单数/销量）不在候选里——为的是守住「识别题不是多选题」的 3 选上限。

### Phase 5｜呈现层「一次一问」（commit `aed44a9` + 后续）

- 新增 `QuestionBar`（一条气泡只问一个槽位，选项带 `hint` 数值 + 「都不是，我补充说明」）、
  `ConfirmBar`（`2024年 · 按周 · 净销售额 · 华东` + 确认并生成 + 展开详情）、
  `slotQuestion.ts`（`pickQuestionField` / `pickPendingAssumption` / `pickNextQuestion` / `confirmChips`，
  纯函数 + 10 例单测）。
- `WorkbenchPage`：答一个槽位立即 `PATCH`（服务端算新卡，下一问来自真实槽位状态）；
  整卡降级为「展开详情」；**未表态的假设也走同一条一次一问通道**（否则用户会被整卡拦住）。
- `types/requirement.ts`：`RequirementFieldKey` 从 5 字面量**放宽为 `string`**（与后端
  `RequirementFieldKey is str` 的契约对齐，也才能承载 `assumption:<key>` 合成 key）。

### 验证证据

| 层 | 命令 | 结果 |
|---|---|---|
| 后端离线 | `pytest backend/tests/{graphs,api,contracts,smoke} -q` | **924 passed** |
| 交互 e2e | `REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_requirement_slots.py -q -s` | **15/15 收敛 · 1 passed / 751s**：`priority_question_sr=1.0`、`follow_up_sr=1.0`、`turns_used_avg=0.53`、`must_not_ask_violations=0` |
| 真链路探针 | `python .e2elogs/probe_slots.py` | 7 问法 + 多轮：口径确定性落位 / 歧义带数字 / 追问沿用 / 改写覆盖 |
| 前端 | `npm run test:run` / `npx tsc -b` / `npm run lint` | **364 passed** / 0 error / 0 error |
| 浏览器 | `node .e2elogs/probe_slots_ui.mjs` | 一次一问 ✔ 三候选带数值 ✔ 下一问 ✔ 确认条 ✔ 展开详情 ✔ |

