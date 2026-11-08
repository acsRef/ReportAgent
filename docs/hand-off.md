# ReportAgent 当前状态 / Hand-off

> 最后更新：**2026-11-05 21:05**
> **新对话请先读这一份，再读 `docs/plans/README.md`。**
> 维护原则：本文只回答「现在在哪 / 下一步做什么 / 怎么验证」，不复刻会过期的数字。

## 0. 30 秒摘要

| 项 | 状态 |
|---|---|
| 主链路 | 需求理解 → 确认 → SQL → 报告 → 落库，**可用**（真机两轮验证过） |
| 需求层 | **Turn Understanding Agent 已上**（强模型产 `TurnPlan` + 确定性 Reducer + 可报告性裁决，`TURN_UNDERSTANDING=1`）；`RequirementCard` 契约不变 |
| 需求模型 | `LLM_TURN_MODEL=deepseek-ai/DeepSeek-V4-Flash`（有 CoT，中位 15s，JSON 8/8） |
| 可观测 | 模型**原文思维链**进 trace；「已思考（用时 N 秒）」块当轮展开、历史收起；**链路已落库**（`turn_trace` 行），F5 后历史轮次仍能点开读；思考被预算截断时**有标注** |
| 图表 | 决策顺序 = **用户当轮原话**（"画饼图/柱状图/表格"）> 记忆偏好 > `chart_advisor` 默认 |
| 对话记录 | `app.conversations` 是**真对话记录**（user / assistant_text / 报告指针 / turn_trace 四类行），历史不整表覆盖 |
| 消息模型 | **append-only 聊天账本**（`stores/chatTranscript.ts`）：发送即 append，按 `serverId` 合并 |
| 视觉形态 | 一轮 = 一条用户消息 + **一条**助手消息（过程/状态/动作/进度都在里面）；卡片只在「需求详情」按需展开 |
| 测试策略 | **主干优先**：真机脚本 + 命中测试；全量单测平时不跑（用户明确要求） |
| 服务 | PG 5432 / ragent-py 8000 / backend 8100 / 前端 3000 / MCP schema 8765 |

## 1. 怎么验证（按这个顺序，别跑全量单测）

```bash
# 主链路（真 LLM）
python .e2elogs/probe_transcript.py          # 两轮：问 + 答是否都落库（transcript）
node   .e2elogs/probe_reasoning_ui.mjs       # 「已思考（用时 N 秒）」+ 模型原文链路 + 执行步骤（8 项判据）
node   .e2elogs/probe_trace_persistence.mjs  # 思考链路落库：两行 turn_trace + F5 后仍能展开读原文（12 项判据）
node   .e2elogs/probe_live_issues.mjs        # 「画饼图」出 pie + schema 步骤文案 + 等待秒数 + 截断标注（5 项判据）
node   .e2elogs/probe_reasoning_stream.mjs   # 思考链路流式可见：边跑边长 + 「· Ns」+ 首个帧早于终局（8 项判据）

# 中断场景（各自独立，别混在一起跑）
node   .e2elogs/probe_interrupt_midstream.mjs   # 流式途中掐断连接：后台仍跑完、库里内容齐全（8 项判据）
node   .e2elogs/probe_refresh_midturn.mjs       # 解析中 F5：重载后靠轮询恢复出确认条（7 项判据）
node   .e2elogs/probe_close_page_midturn.mjs    # 跑到一半关浏览器：无前端在场也能跑完（7 项判据）

# 中断相关聚焦用例（只跑这两个文件，别跑整套）
pytest backend/tests/contracts/test_interruption_resilience.py -q
cd frontend && npx vitest --run src/stores/__tests__/interruption.test.ts
node   .e2elogs/probe_clean_two_turns.mjs    # 两轮：一份报告 / 一条助手消息 / 历史仍在
node   .e2elogs/probe_hit_test.mjs           # 全页按钮命中测试（被遮挡元素应为 0）
node   .e2elogs/probe_reasoning_models.py    # 推理模型直连探针（CoT / 延迟）

# 消息模型（2026-11-05 P0，约 4 分钟，13 项判据）
node   .e2elogs/probe_p0_final.mjs           # 一轮一条助手消息 / 历史不丢 / 无整卡 / v1+v2 / F5 仍在 / DB 对照
node   .e2elogs/probe_p0_chitchat.mjs        # 两轮闲聊各留一条回复（不串轮）
node   .e2elogs/probe_p0_reload.mjs          # 刷新后 2 问 2 答 + 版本条 + DB 行序

# 需求理解离线（不调模型，秒级）
pytest backend/tests/graphs/test_turn_understanding.py -q
pytest backend/tests/graphs/test_requirement_parser.py -q

# Turn Understanding 模型对比（8 条真实多轮用例）
python -m evaluation.turn_understanding_runner --models deepseek-ai/DeepSeek-V4-Flash

# 维护：清理探针会话（消息 / 草稿 / 版本 / 审计 / 会话级记忆 / trace / checkpoint 一起删）
Get-Content backend/scripts/cleanup_test_sessions.sql -Raw |
  docker exec -i ragent-postgres psql -U ragent -d ragent -v before="'2026-11-05 00:00'" -f -
```

启动服务（顺序）：`ragent-py :8000` → `backend :8100` → `frontend :3000`。
`mcp_schema_server` 用 `MCP_SCHEMA_TRANSPORT=streamable-http MCP_SCHEMA_PORT=8765` 常驻（SDK 2.x 已适配）。

## 2. 2026-11-05 做完的事

| # | 内容 | commit |
|---|---|---|
| 1 | MCP schema server 迁到 SDK 2.x（stdio + streamable-http），解析器对齐 ragent-py 新字典格式 | `3b9e8a0` |
| 2 | 会话时间差 8 小时：写入改 `NOW()`、读出 `utc_iso()` 带时区，迁移 1176 行 | `d75c5c8` |
| 3 | 「最高的那个月的明细」：选期由系统算（注册表口径取极值 → 确定性 WHERE）+ 明细契约只看 SELECT 投影 | `a614823` |
| 4 | 澄清策略：**有默认就不问**（口径缺省按注册表）+ 置信度闸（0.75，`REQUIREMENT_CLARIFY_CONFIDENCE`） | `5337f3a` `61defeb` |
| 5 | 「最多和最小」两个方向：`top_period.direction=both`，SQL `month IN (11,2)` → 2 行 | `e8493f8` |
| 6 | 确认变成**一句人话**（去掉 diff 行 / chip / 需求详情；后于 P0 以「按需详情」形式回归） | `6ed48b9` |
| 7 | 撤回自动确认（不再替用户跑 SQL）；没有图就不渲染空可视化；单值结果改「关键指标」块 | `1578c5d` |
| 8 | **Turn Understanding Agent**：`TurnPlan` + 确定性 `Task State Reducer` + `Reportability Validator` + `ActiveAnalysisTask`/`TaskRevision` 审计（`agent.analysis_task_revision`） | `30100a1` `bf93d22` |
| 9 | 选型 `DeepSeek-V4-Flash`（JSON 8/8、CoT 8/8、中位 15.2s）；新增直连 `reasoning_client` | `07a1fb3` |
| 10 | **模型原文思维链**进 trace `reasoning` 字段，前端「模型推理」块展示 | `0f9a8fb` |
| 11 | **对话记录**：assistant 文本落库，历史轮次渲染「问 + 答 + 版本条」 | `4650f69` |
| 12 | **P0 消息模型**（见 §3）：append-only 账本 + 一条助手消息 + 去只读整卡 + 详情按需；修合并错序真 bug | `a1d3596` |
| 13 | **思考链路重塑**（plan `2026-11-05-reasoning-chain-display.md`）：头部「已思考（用时 N 秒）」+ 展开读模型原文（纯文本 `pre-wrap` / markdown 分别渲染）+ 执行步骤结论；当轮展开、历史收起；删掉步骤内嵌的等宽原文块 | 见 plan 落地记录 |
| 14 | **思考链路落库**（plan `2026-11-05-trace-persistence.md`）：`app.conversations` 加 `turn_trace` 行（entries + 时长 + 原文），两个 runner 顺手收集落库；前端按行序挂到该轮助手消息；顺带补闲聊 `assistant_text` | 见 plan 落地记录 |
| 15 | **真机 5 问收口**（plan `2026-11-05-live-session-issues.md`）：思考截断标注 / 超时快速重试 + 等待秒数 / schema 步骤文案拆四种 / 显式图表要求（「画饼图」→ pie，优先于记忆偏好） | 见 plan 落地记录 |
| 16 | **思考链路实时可见**（plan `2026-11-05-streaming-reasoning.md`）：provider 流式收 `reasoning_content` → 节流 350ms 发既有 `trace` 帧 → 前端链路「边跑边长」+「正在输出…」；预算与总额成对可调（`TURN_MAX_TOKENS`）；图节点走 async 客户端（同步会堵事件循环，帧会挤在一起吐） | 见 plan 落地记录 |
| 17 | **中断场景收口**（plan `2026-11-08-interruption-resilience.md`）：断流 / 停止 / F5 / 关浏览器各写**独立**用例（2 个测试文件 + 3 个探针）；`live` 缓冲同 step 只留最新帧 + 流式帧上限 600；**修 F5 卡死真 bug**（解析中刷新后不起轮询 → 永远停在「正在解析」） | 三探针全绿 |
| 18 | **LLM 预算与记账收敛**（plan `2026-11-08-llm-budget-and-accounting.md`，用户拍板顺序 2→1→3）：①turn 直连调用补 usage 记账（`ReasoningReply.prompt_tokens/completion_tokens` + 三出口 `tracer.add_llm_call`，真机已验证 `observability.llm_call` 出现 `DeepSeek-V4-Flash` 那条：928/610/5469ms）②新建 `app/llm/budgets.py` 单一预算表，6 个 caller 的 7 处字面量全部改走 `budgets.max_tokens("<task>")`③`TURN_REQUEST_BUDGET=120` 贯穿重试（首轮 `min(90,剩余)`，剩余 <20s 不重试并标 `retry_skipped`），最坏耗时从 135s 收到 ≈120s | 48 例聚焦用例 + 真机探针 5/5 |
| 19 | **「标注」不再被当成「只看」+ 模型超时不再说成查询超时**（plan `2026-11-08-top-period-annotate-and-timeout-copy.md`）：①`RequirementTopPeriod.purpose: select\|annotate`（默认 select 零迁移），词表判定 + 产卡后原话归一（模型漏给时按原话补建）；annotate = 全区间 + `CASE WHEN ... AS 是否最高期`，契约校验反向（必须有标记列、不许退化）②`user_message(kind, code)` 加 code 覆盖层，`LLM_TIMEOUT` 说「模型响应超时」不再叫用户去缩时间范围 | 后端 122 passed / 前端 24 + tsc；真机 `probe_top_period_annotate.py` ALL PASS（5 列 200 行、SQL 全区间带标记） |

### 2026-11-04 做完的事

| # | 内容 | commit |
|---|---|---|
| 1 | plan + `CLAUDE.md` §4.1（口径预览例外契约） | `a4fd3f3` |
| 2 | **槽位三态 + 口径确定性**：`requirement_slots.py`，用户原话最长优先 > 上轮已确认 > 注册表 default > 模型猜；澄清选项改注册表派生 | `d62550a` |
| 3 | **带数据澄清**：`metric_preview_service`（注册表 expr 拼总量查询 + fail-open）+ 图节点 `requirement_preview` | `9b7672f` |
| 4 | 没点名口径**一律问**（模型猜等于默认时也问）+ 候选含同单位备选 + supplement 沿用已确认口径 | `3382d3c` |
| 5 | **多轮隔离**：REQUIREMENT 档只吃用户原话、drop query memory；`metrics_mentioned_in` 最长命中 | `a14be5d` |
| 6 | BIRD 式交互回归（15 条 GT 夹具 + `GroundTruthUser` + PRQ/FU/turns）+ 前端「一次一问」QuestionBar / ConfirmBar | `aed44a9` |
| 7 | 中文化列名 + 去重列（表格/图表同源）；卡内方法名可读 | `a1ac06d` |
| 8 | 对话形态收口：上一轮报告默认展开、确认/提问条只在 `awaiting_*` 出现 | `72dc2b6` |
| 9 | **执行过程面板**（Doubao 式，不做 CoT）：需求图也发 trace 帧，异常/停止/切会话/离开都有落点 | `6e8fae5` |
| 10 | 明细类列加确定性投影；`type=table` 不再强画图；每步 trace 带结论 | `a7435bc` `421793d` |
| 11 | 澄清/确认改**一条普通对话消息**（思考一行 + 提问/确认，同一个气泡），卡片退出主流程 | `d24ffdd` |
| 12 | 历史轮次条「未生成报告」去噪 + 版本按时间归属（`historyTurns.ts`，plan `2026-11-04-history-turn-pairing.md`） | `6c00893` |
| 13 | 「需求详情」就地展开（`RequirementCardView variant="inline"`）：修掉展开后的空白气泡与浮空「收起详情」，确认按钮原地不动 | `ac9f1d5` |
| 14 | 确认条补可见的「补充或修改」第二动作（supplement 通道后端已支持、界面从没露出） | `8ce551b` |
| 15 | 「最高的那个月的明细」听懂 + 真出明细：需求层 top_period/detail 确定性识别；选期由系统算（注册表 GROUP BY 期取极值 → 确定性 WHERE）；明细契约改看 SELECT 投影 | 见 plan |

### 2026-11-03 做完的事

| # | 内容 | commit |
|---|---|---|
| 1 | 报告标题取需求摘要；隐藏常量列；会话列表补 report_versions | `5f49d9e` |
| 2 | few-shot / SQL 生成规则迁进 YAML（`sql_graph` 再无表名字面量） | `9f28b23` |
| 3 | 指标白名单：卡片解析出的指标才进 prompt（真机 6→2 个） | `c2865ef` |
| 4 | **需求层重构**：schema 字段进 prompt、模型按字段生成澄清、确定性意图闸、注入 today | `48a461a` |
| 5 | **14 条真实问法矩阵** + 反抄袭断言（禁止用 prompt few-shot 当测试输入） | `4974553` |
| 6 | prompt 7 段化（输入优先级 + 自检）+ `docs/architecture/prompt-contract.md` | `8b20c2d` |
| 7 | 只说了月份时继承上一轮年份（修「七月份」→2026-07） | `719f496` |
| 8 | prompt 瘦身：requirement 5592→3435 字符，附录按需注入 | `f20bd8b` |
| 9 | 卡片显示中文化（payment_amount→实收金额）、`只看华东` 确定性抽 scope、叙述层 | `8bcc4a6` |
| 10 | 叙述按分析角度分流 + 数字锚定校验 | `e72ab38` |
| 11 | **P0 收口**：补充轮结果不再随连接消失（解析轮后台化 + 前端断流兜底），见 §3 | `11cf3c4` |
| 12 | **P1-① 收口**：异常类必须按日/周出水（粒度确定性 + 形态分流 + 报告层两处） | `ace1017` |

## 3. 下一步（按优先级，用户已确认顺序）

### P0 ✅ 已关闭：消息渲染收敛成 append-only 聊天模型（plan `2026-11-05-conversation-transcript-messages.md`）

做完的两件（用户拍板的两项都在内）：

1. **append-only 消息生命周期**：`stores/chatTranscript.ts` 定义 `ChatMessage`（本地 id + `serverId`），
   发送即 `chat/turn-appended`（用户消息 + 空助手占位）、SSE 只更新**最后一条**、`done` 标状态、
   服务端历史按 id **合并**（`mergeServerHistory`，不整表覆盖）；过程（trace）挂到消息上，
   全局 `timeline` 删除；`ConversationTimeline` 与当轮统一成 `ChatTurn[]`。
2. **`generating` 去掉只读整卡**：`requirementUiMode` 不再有 `card` 形态；参数改在确认那条消息里
   「需求详情」按需展开；过程/状态/动作/进度全部收进**同一条**助手气泡（`AssistantTurn`）。

**验收**（真机，全绿）：

```bash
node .e2elogs/probe_p0_final.mjs     # 13 项判据：一轮一条助手消息 / 历史不丢 / 无整卡 / v1+v2 / F5 仍在 / DB 对照
node .e2elogs/probe_p0_chitchat.mjs  # 两轮闲聊各留一条回复（不串轮）
node .e2elogs/probe_p0_reload.mjs    # 刷新后 2 问 2 答 + 版本条；DB 行序 user/assistant 交替
```

**下一阶段要补的两个缺口**（都不大，但要新契约；别当成 bug 重查）：

- 闲聊回复**没落库**（`app.conversations` 只有 user 行）→ F5 后那一轮的助手文本消失；
  补法：闲聊出口也写一条 `message_type='assistant_text'`；
- 过程步骤（trace）只在内存里 → 刷新后历史轮次只保留正文，没有「思考了 N 步」；
  要留就得把 trace 落库（`agent.report_version.metadata` 或独立表），另开 plan。

约束（用户明确，已固化进实现）：一条消息就是一条消息、不要白卡；历史永久连续、绝不整表覆盖；
结构化组件按需出现；过程融进同一条回复。

### P1（Turn Understanding 收口）

- 评测里还差三例目标分类：`再按产品细分`（应 compare）、`把刚才最高的那个月详细列出来`
  （应 detail_rows）、`现在看看退货情况`（应 new_task）——`goals 2/4、act 6/7`；
  修 prompt 示例或加确定性后置规则，目标是 `goals 4/4`；
- **Qwen3.8-27B 未公平比较**：短 prompt 7.4s 且有 CoT，但本项目长 prompt 会 >90s 超时。
  要重测就得先裁 prompt / 改流式 / 加大预算；
- 把 `TURN_UNDERSTANDING` 从实验开关变成默认（现在 `=1` 已在 `.env` 开着，但代码默认仍是关，
  回退只需删这一行）；
- 前端「任务变更日志」UI（数据已在 `agent.analysis_task_revision`，接口 `list_revisions` 已有）。

### 2026-11-04 新增：需求层槽位契约（已落地，plan `2026-11-04-requirement-slot-contract.md`）

「答案乱 / 追问越走越偏」的三条真实通道已堵住，**验证入口**：

```bash
python .e2elogs/probe_slots.py                     # 7 问法 + 多轮：口径落位/带数字澄清/沿用/覆盖
node .e2elogs/probe_slots_ui.mjs                   # 浏览器：一次一问 + 三候选带数值 + 确认条 + 展开详情
REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_requirement_slots.py -q -s   # 15 例交互质量（~11 分钟）
```

要点：
- 口径**确定性**落位——`2024年各区域销售额` 不再被模型改成「实收金额」；「到账」→ 实收金额；
  「净销售额」不会被「销售额」抢；
- 没点名口径**一定会问**，选项带真实总量（`销售额 2.63 亿元 / 净销售额 2.51 亿元 / 实收金额 2.47 亿元`）；
- supplement 轮沿用已确认口径，用户改写才覆盖；需求解析不再吃 L2 摘要与历史 SQL 参考帧；
- 前端改成「一次只问一个槽位」+ 一行确认条，整卡降级为「展开详情」。

**已知边界（P2 记账）**：口径候选只覆盖默认口径所在**单位族**（元），数量类（订单数/销量）不在
候选里——刻意守住「识别题 3 选上限」；用户真要数量需自己说出来。

### P0 ✅ 已关闭：补充需求后卡片不刷新
**真因不是前端**（hand-off 21:30 版猜的三个方向都不成立）：刷新链路本身是好的，两个独立复现
（API 直调 + Playwright）都能看到新卡；失败会话 `80b8c302…` 的 DB 里 `current_requirement` 仍是
**第 1 轮旧卡**，日志停在 `parse_requirement parsed` 之后什么都没有 —— 那一轮**结果根本没落库**。
原因：`graph.ainvoke` 跑在 SSE 响应任务里，用户刷新页面（或断网）一断连，图被取消 → `persist_draft`
事务回滚 → 新卡既不落库也不送达。两个前端缺陷把它放大成永久卡死：`openChat` 对流静默结束没有任何
回调（发送框永久禁用），parsing 阶段没有停止入口（唯一出路是 F5 = 触发上面那条）。

**已修**（plan `2026-11-03-supplement-turn-durability.md`）：
- 后端解析轮**后台化**（复用 ExecutionRegistry 第三 kind），断连只取消订阅；起手落 `current_phase=parsing`；
  `run_with_timeout(MAX_TASK_DURATION)` 兜住挂死；同会话重入 409；落库失败/无卡明确报错。
- 前端 `openChat` 增 `onClose({sawTerminal, aborted})`；`parsing` 增「停止等待」；`useExecutionPoll`
  完成判定改 `isSettledPhase` 并在完成时拉全量快照（把后台落库的新卡刷回来）。

**再验证用**：`node .e2elogs/probe_card_refresh.mjs`（A 补充刷新 / B 解析中刷新页面，两条都要 PASS）、
`.e2elogs/probe_supplement_hang.py`（连续 4 轮补充+确认，逐轮落库）。

### P1 分析质量继续提升

#### P1-① ✅ 已关闭：异常类必须日/周粒度
**改前实测**（`.e2elogs/probe_anomaly.py`，7 种问法）：纯异常类问法普遍落在 `GROUP BY year/month`；
「2024年哪几天销售额异常」用户点名的「哪几天」被模型丢掉 → `granularity=None` → `GROUP BY year` → **1 行**；
「2024年各品类销售额有没有异常」把品类和时间一起分组 → 2σ 检测拿品类总量跟周销量比大小，叙述答非所问。

**已修**（plan `2026-11-03-anomaly-granularity.md`）：
- 粒度确定性判定：用户原话（哪几天/按周/各月/按季度…）优先，其次异常类默认（≤45 天→日，否则→周），
  模型自己的猜测不再压过默认值；
- 异常类形态分流：点名「各/每个/哪个+维度」→ 横截面（只按该维度分组），否则 → 时间序列
  （丢掉 scope 造成的常量维度，方法收窄成 `anomaly_detection`）；
- 报告层两处：确定性结论不再把「周」当度量求和（「周合计 1,484.00」），数字锚定补上度量列的
  合计/均值且年份不参与（此前派生真值被误判成编造，长序列 findings 几乎全灭）；
- 前端：需求卡补粒度 chip（按日/按周/按月…）、明细表展开上限 50 条 + 页脚说明。

**再验证用**：`python .e2elogs/probe_anomaly.py`（判据是 SQL 的 GROUP BY 与行数，不是卡片字段）、
`node .e2elogs/probe_anomaly_ui.mjs`（卡片 chip + 表格上限）。

**残留**（转 P2）：按日报告的 findings 偏少（叙述引用的「第 N 周合计」在日帧里不是单元格 →
被锚定丢掉，规则本身正确；要补得先定义「合法的区间聚合」来源）；`_default_intent_clarification`
不提供「异常波动」选项（只在原话命中「异常/波动/异动」时走确定性闸）。

#### 其余 P1
- **明细类列太薄 + 聚合没被拦住**：明细模式现在有确定性契约（`sql_graph._validate` 之后，
  出现 SUM/COUNT/GROUP BY 即判 validation 失败 → repair 重生成），「明细返回 1 行汇总」不再静默成功；
  但「`把订单列出来看看` 只返回 订单金额 一列」仍在——明细该回日期/门店/品类/数量等原始列。
  验证：`REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_multiturn_followup.py -q` 第 4 轮。
- 对比类自动带出「和谁比」（环比/同比）。

### P2 遗留
- 图表/SQL 偶尔重复输出同一列（`AS "区域", AS "region"`）；
- 「确认后卡片 pill 仍显示『信息完整 · 待确认』」：confirm 流结束后 store 里的卡没有被 locked 版本替换，
  视觉与 `readOnly` 状态不一致；
- `docs/testing-boundaries.md` 里 B1 行级权限、B9 成本、B12 行为指标仍是缺口；
- 全量单测很久没跑（用户要求不跑），发版前建议跑一次。

## 4. 今天踩过的坑（新对话别再踩）

1. **`app/llm/mock.py` 的 `KIND_MARKERS` 依赖各 prompt 的首句**：改 `system_contract` 首句会让
   Playwright Contract E2E 的 mock 分类静默失配。`test_knowledge_registry.py` 里已有 guard 会红。
2. **`analysisStore.dispatch` 逐字段枚举写回**：新增 state 字段必须同时改 store，否则单测绿、界面空。
3. **明细模式要在 plan 和 generate 两处都声明**：只在 prompt 里写一次，模型仍会 SUM。
4. **`git add docs` 会把 `docs/superpowers/specs/` 带进去**：已加到 `.git/info/exclude`，别再用手写 `git add docs`。
5. **测试填充策略**：盲选第一个选项会选到「逐条明细」，多轮测试里已改成 overview/trend 优先。
6. **「界面没刷新」先查 DB，别先查前端**：这次旧卡能一路活下来，是因为那一轮**没落库**。
   一步到位的判据 = `GET /api/v1/sessions/{sid}` 的 `current_requirement.payload` 是不是新内容；
   不是 → 后端那一轮没跑完（断连/异常）；是 → 才是前端渲染问题。
7. **长任务（>30s）的轮次不要用「看截图」判断生死**：本轮截图 `.e2elogs/ui-supplement.png` 正好拍在
   parsing 中途，误判成「卡片不刷新」。长任务的判据是终态事件或 DB 行。
8. **Vite 里 `WorkbenchPage.tsx` 的 `useExecutionPoll` 导出会触发 `hmr invalidate` → 整页重载**，
   重载会掐掉正在跑的 SSE（这正是本次 bug 的手动触发方式）。调试 UI 时别在有任务跑的时候改这个文件。
9. **推理模型不要走 LangChain 的 `ChatOpenAI + extra_body`**（2026-11-05 实测）：60s 超时、
   拿不到 `reasoning_content`、重试后整轮失败。用 `app/llm/reasoning_client.py` 直连
   `/chat/completions`；超时用 `LLM_TURN_TIMEOUT` 单独配。
10. **`agent.analysis_task_revision` 的读写必须在 async 节点里**：asyncpg 池绑定主事件循环，
    在同步节点里 `asyncio.run` 会拿到另一个 loop → 一条 revision 都落不了库、第二轮继承全丢。
11. **历史里「助手说了什么」原本不存在**：`app.conversations` 只存 user 文本 + report_version 指针，
    assistant 的 content 是空的。任何「历史消息消失」判断先看这张表有没有 assistant 行。
12. **新增图节点要同时登记 `progress.PROGRESS_NODES`**，否则该节点的 trace 帧被静默丢弃，
    界面上看不到这一步（本次 Turn Understanding 的四个节点就踩过）。
13. **前端跑 420 个单测没有价值**（用户明确要求不跑）：用真机脚本 + 命中测试代替；
    改组件时最多跑该组件自己的单测文件。
14. **合并服务端历史时「未认领的本地消息」不能一律追加到末尾**（2026-11-05 实测踩到）：
    闲聊轮的助手消息服务端不落库，追加到尾部会让两条回复错挂到下一轮、看起来像「回复串台」。
    正确做法是**以本地账本为主序**遍历（认领后位置不动），服务端多出来的行按 `created_at` 插回。
15. **发送瞬间别拉 `GET /sessions/{sid}`**：新会话的服务端行还没创建（`/chat` 里才建），
    每次发送都打一个 404。历史由 requirement / 终态两次刷新按 id 合并回来就够了。
16. **`app.conversations` 的行序要带 `id`**：同一事务多行 `created_at` 相同（`NOW()` 是事务开始
    时间），只按时间排会让「问 + 答」先后不确定。`get_messages` 已是 `ORDER BY created_at, id`。
17. **`metadata` 从 API 出来是 JSON 字符串**（asyncpg 没注册 jsonb codec）：前端读它必须
    `typeof === 'string' ? JSON.parse : 直读`。`turn_trace` 的 entries 就靠这个读回来。
18. **trace 行归属别用时间判断**：同一事务里 `created_at` 相同，按时间挑「最近一条助手消息」
    会把思考链路挂到下一轮。要沿**服务端行序**往前找最近一条已进入结果集的 assistant 行。
19. **`app.conversations` 里有四类行**：`text`（用户提问）/`assistant_text`（助手那句话）/
    `report_version`（版本指针）/`turn_trace`（思考链路）。**只有前两类是气泡**；后两类由
    版本条与思考块承载，统计口径（`list_sessions` 的 msg_count / 首末句）要排除 `turn_trace`。
20. **confirm 流的 `user_query` 是空串**（安全闸约定）：报告层要读用户原话（图表类型、口径…）
    必须同时看 `requirement_card.summary` 与 `adjustment_text`。而且报告子图里
    `requirement_card` 是 **dict**（父图 model_dump），`getattr(dict, "summary")` 恒为空——
    2026-11-05 饼图第一次没生效就是这个。
21. **思考链路会被 `TURN_THINKING_BUDGET`（默认 512 tokens）截断**，`finish_reason` 仍是 `stop`，
    只能靠 `usage.completion_tokens_details.reasoning_tokens` 判断。现在截断会标注。
    **别指望「拉满预算 = 想完整」**（2026-11-08 实测，同一句输入的 4 次采样）：
    `512 → 508/13s`、`1024 → 1020/23s`、`2048 → 2044/30s`（都顶格截断）、
    `4096 → 839/15s（自然收尾）或 4092/68s（顶格截断）`——**思考长度本身在 839~4092 token 抖动**，
    预算只是「最多让它想多久」的保险丝，抬高它换来的是**最坏耗时变长**，不是保证完整。
    结论：默认 512（快）或 1024–1536（略完整，需把 `LLM_TURN_TIMEOUT` 提到 150）；
    **不建议 ≥4096**（实测有 68s 样本，撞到 90s 超时反而退化成「不思考重试」，连链路都没了）。
    答案质量与预算无关：四档采样里 TurnPlan JSON 全部可解析。
22. **turn 模型超时会回落到通用解析**：现在超时后先自动用「不思考」模式重试一次
    （`LLM_TURN_FAST_TIMEOUT`，默认 45s），两次都失败才回落——排查「理解失败」先看后端日志
    的 `turn model http failed`。
23. **`app/agent/*` 不能 import `app.tools.rag_schema`**（P2 传输层边界钉子）：需要 schema 检索
    的失败原因走 `app.tools.data_tools.last_schema_error` 转发，别直连。
24. **「会话还在忙吗」只有一个判定源**：`isBusyPhase`（parsing / generating / adjusting）。
    `loadSessionSnapshot` 曾只认 generating/adjusting，于是**解析中按 F5 回来不会起轮询**，
   界面永远停在「正在解析」、发送框永久禁用（2026-11-08 探针抓到）。别再手写 phase 列表。
25. **流式帧会让缓冲变大**：订阅者一断（断流/F5/关页面），`registry.publish` 还在往里塞。
   现在 `live` 里同一 step 只留最新一帧、流式帧总量上限 600（超了只停过程帧，终态照发）。
26. **验证中断场景别用「看截图」**：判据是「断开后库里 phase 是否收敛 + 行是否齐」，
   再补一次 UI 复核；三个探针（interrupt / refresh / close-page）各自独立跑，别混。
27. **scope 值必须先过数据字典**（2026-11-08 真机）：用户说「华南区域的吧」→ 卡上落成
   `华南区域`，而 `dim_store.region` 里是 `华南` → 选期查询 0 行 → `resolved=false` →
   退回「让模型自己写两段式」→ 校验连续失败 → 预算耗尽 → 报错。现在两个产卡路径都会过
   `knowledge.canonical_scopes`（后缀裁剪 + 精确匹配，归不了原样保留）。
   **判断这类问题的最快方式**：直接调 `resolve_top_period(...)` 看 `reason`，`empty` 基本就是取值没对齐。
28. **失败文案绝不能拼空串**：`f"SQL执行失败: {raw_err}"` 里 `raw_err` 来自「执行结果 JSON」，
   而校验失败时**根本没执行** → 空串 → 界面一行 `SQL执行失败: `。改走 `sql_graph._failure_detail`
   （校验结论 → 执行错误 → 原始返回 → 固定兜底）。排查时优先看 `validation.error`。
29. **`validate_sql` 的日志曾经只打 `sql[:60]`**：SQL 以换行/空格开头时日志里啥也看不到。
   现在压缩空白后打 160 字——以后看「top-period contract violated」时能直接读到 SQL。
30. **改了后端代码先确认 uvicorn 真的重载了**（2026-11-08 踩过）：上一轮探针明明写着 PASS 可
   `observability.llm_call` 一行不增，排查半小时发现进程还是 14:50 启动的——`--reload` 没开，
   改的是磁盘上的文件，跑的是内存里的旧模块。判定方法：`Get-Process -Id <pid> | Select StartTime`
   看启动时间是否晚于最后一次改动；日志里搜 `Started server process`。
31. **`observability.llm_call` 只有 token 数，没有 input/output**：表结构是
   `span_id/model/prompt_tokens/completion_tokens/latency_ms/cost`，prompt 与响应文本只进
   Langfuse（`input`/`output` 字段）。想核对「turn 到底想了什么」得看 `turn_trace` 行或 Langfuse。
32. **「记账」和「预算」是两件事，顺序不能反**：先能看见（每类调用都进 `llm_call`），
   再收敛（`app/llm/budgets.py` 唯一预算表），最后才谈 deadline。反过来的话调预算永远在猜。
   另外 turn 的 `max_tokens` 是**推算值**（思考预算 + 回答余量），不能写进表里当常量。
33. **卡上的 `time_range` 会被规范化成纯年份**（`2023年` → `2023`），而
   `metric_preview_service._YEAR` 正则要求带「年」字 → 解析失败返回 None → 选期按**全库**算。
   2026-11-08 真机表现：用户问「2023年最高的那个月」，SQL 里标出的是 `year = 2024 AND month = 6`。
   已加 `_YEAR_BARE` 兜「整串就是一个四位年份」（`2023-11` 这种半残写法仍不认）。
34. **`_drop_constant_columns` 会删掉「整列恒定」的列**（2026-11-03 为藏掉 scope 过滤后的
   `地区=华东` 加的）。用户**点名要的列**必须豁免：明细按时间倒序只取最近 200 条，
   若全在同一月，「是否最高期」整列恒定 → 被删 → 界面上根本看不到标注。
   改法 `_drop_constant_columns(..., keep=...)`，且 keep 要**双来源**（state + 列名形状 `是否…期`）。
35. **LangGraph 会丢掉 TypedDict 里没声明的 state 键**：`top_period` 原先只写进子图 state，
   `ConfirmedExecutionState` 没这个字段 → 报告节点永远读到 None（豁免静默失效）。
   加字段前先确认它在 TypedDict 里，不然「算出来了」和「传下去了」是两回事。
36. **排序本身也会制造 bug**：想「把标为『是』的行排前面」→ 前 200 行全变「是」→
   标记列又成常量、又被删。标注的正确排序是**照旧按时间倒序**（LIMIT 截断才是客观限制）。

## 5. 文档地图

| 想知道什么 | 去哪 |
|---|---|
| 架构不变量 | `CLAUDE.md` |
| 进行中/已完成的工作 | `docs/plans/README.md`（唯一入口） |
| Prompt 契约（7 段 / 输出契约 / 仲裁表 / 测试原则） | `docs/architecture/prompt-contract.md` |
| 测试边界与缺口 | `docs/testing-boundaries.md` |
| 真实问法集 | `evaluation/real_user_questions.yaml` |
| 指标口径（数据） | `backend/app/knowledge/metrics.yaml` |
| SQL/结构提示（数据） | `backend/app/knowledge/schema_hints.yaml` |
