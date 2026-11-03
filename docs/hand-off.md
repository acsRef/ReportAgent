# ReportAgent 当前状态 / Hand-off

> 最后更新：**2026-11-03 22:00**
> **新对话请先读这一份，再读 `docs/plans/README.md`。**
> 维护原则：本文只回答「现在在哪 / 下一步做什么 / 怎么验证」，不复刻会过期的数字。

## 0. 30 秒摘要

| 项 | 状态 |
|---|---|
| 主链路 | 需求卡 → 确认 → SQL → 报告 → 落库，**可用** |
| 需求层 | 意图三层（确定性 → 模型 → 服务端校验）；模糊问法会先澄清，不再瞎猜 |
| 报告层 | 核心发现是**叙述**（结论/证据/原因/建议），按分析角度分流，数字有锚定校验 |
| 测试策略 | **主干优先**：端到端真实问法 + 多轮 e2e；全量单测平时不跑（用户明确要求） |
| CI | backend pytest + frontend vitest/tsc（任意分支 push / PR） |
| 服务 | PG 5432 / ragent-py 8000 / backend 8100 / 前端 3000 |

## 1. 怎么验证（按这个顺序，别跑全量单测）

```bash
# 主干 3 条（迭代期，~2 分钟）
python .e2elogs/smoke_trunk.py "2024年各区域销售额"                     # 明确问法
python .e2elogs/smoke_trunk.py "2024年的销售情况" --no-confirm          # 模糊问法（应出澄清）

# 补充轮 / 断连不丢结果（改了解析轮或前端事件面时必跑，~4 分钟）
node .e2elogs/probe_card_refresh.mjs                                   # A 补充刷新 + B 解析中刷新页面
python .e2elogs/probe_supplement_hang.py                               # 连续 4 轮 补充→确认，逐轮落库

# 14 条真实问法（阶段完成 / 提交前，~5.5 分钟）
REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_real_user_questions.py -s

# 多轮追问（~2.5 分钟；当前首轮断言是红的，见 §3 P1）
REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_multiturn_followup.py -q

# 只在改了某模块时，跑它自己的单测文件（秒级）
pytest backend/tests/graphs/test_requirement_parser.py -q
pytest backend/tests/api -q
```

启动服务（顺序）：`ragent-py :8000` → `backend :8100` → `frontend :3000`。命令见 `CLAUDE.md`。

## 2. 2026-11-03 做完的事（按时间）

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
| 11 | **P0 收口**：补充轮结果不再随连接消失（解析轮后台化 + 前端断流兜底），见 §3 | 本次 commit |

## 3. 下一步（按优先级，用户已确认顺序）

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
- **异常类必须日/周粒度**（月粒度 12 个点检不出异常；现在会返回 1 行年级数据）。
  注意这条已把 `backend/tests/e2e/test_multiturn_followup.py` 钉红：`_deterministic_intent` 命中「异常」
  后只补 `analysis_methods/dimensions`，**不补粒度澄清**，卡片以 `granularity=None / status=complete`
  返回（「2024年华东销售趋势和异常」API 直调同样复现）。修这条时一并修那个 e2e 的首轮断言。
- 明细类列太薄（`把订单列出来看看` 只返回「订单金额」一列）；
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
