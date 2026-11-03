# ReportAgent 当前状态 / Hand-off

> 最后更新：**2026-11-03 21:30**
> **新对话请先读这一份，再读 `docs/plans/README.md`。**
> 维护原则：本文只回答「现在在哪 / 下一步做什么 / 怎么验证」，不复刻会过期的数字。

## 0. 30 秒摘要

| 项 | 状态 |
|---|---|
| 主链路 | 需求卡 → 确认 → SQL → 报告 → 落库，**可用** |
| 需求层 | 意图三层（确定性 → 模型 → 服务端校验）；模糊问法会先澄清，不再瞎猜 |
| 报告层 | 核心发现是**叙述**（结论/证据/原因/建议），按分析角度分流，数字有锚定校验 |
| 测试策略 | **主干优先**：14 条真实问法 + 多轮 e2e，不跑上千个单测（用户明确要求） |
| CI | backend pytest + frontend vitest/tsc（任意分支 push / PR） |
| 服务 | PG 5432 / ragent-py 8000 / backend 8100 / 前端 3000 |

## 1. 怎么验证（按这个顺序，别跑全量单测）

```bash
# 主干 3 条（迭代期，~2 分钟）
REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_trunk_smoke.py -q      # 若不存在则用下面的
python .e2elogs/smoke_trunk.py "2024年各区域销售额"                     # 明确问法
python .e2elogs/smoke_trunk.py "2024年的销售情况" --no-confirm          # 模糊问法（应出澄清）

# 14 条真实问法（阶段完成 / 提交前，~5.5 分钟）
REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_real_user_questions.py -s

# 多轮追问（~2.5 分钟）
REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_multiturn_followup.py -q

# 只在改了某模块时，跑它自己的单测文件（秒级）
pytest backend/tests/graphs/test_requirement_parser.py -q
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

## 3. 下一步（按优先级，用户已确认顺序）

### P0 前端 bug：补充需求后卡片不刷新
**现象**：浏览器里发 `只看华东`（`mode=supplement`，请求体已确认正确），后端日志显示解析出新卡
（`summary='华东区域销售分析'`、`scope=['华东']`），但界面仍显示旧卡（`未限定（全部区域）`）。
**已排除**：请求体、后端解析、scope 抽取（API 直调 `probe_scope.py` 返回 `scope=['华东']`）。
**怀疑方向**：
1. 前端 `requirement/received` 被后续快照刷新覆盖（`loadSessionSnapshot` / `refreshMessages`）；
2. 或 reducer 的去重条件（同 id + 旧 version）误判；
3. 或 `handleSSEEvent` 只有在 `phase=awaiting_confirm` 时才 dispatch。
**复现脚本**：曾用 `frontend/_supplement-check.mjs`（已删），可用 Playwright 重写：
登录 → 问 `2024年各区域销售额` → 确认 → 等发送键可用 → 发 `只看华东` → 检查卡片文本。

### P1 分析质量继续提升
- 异常类必须**日/周粒度**（月粒度 12 个点检不出异常；现在会返回 1 行年级数据）；
- 明细类列太薄（`把订单列出来看看` 只返回「订单金额」一列）；
- 对比类自动带出「和谁比」（环比/同比）。

### P2 遗留
- 图表/SQL 偶尔重复输出同一列（`AS "区域", AS "region"`）；
- `docs/testing-boundaries.md` 里 B1 行级权限、B9 成本、B12 行为指标仍是缺口；
- 全量单测很久没跑（用户要求不跑），发版前建议跑一次。

## 4. 今天踩过的坑（新对话别再踩）

1. **`app/llm/mock.py` 的 `KIND_MARKERS` 依赖各 prompt 的首句**：改 `system_contract` 首句会让
   Playwright Contract E2E 的 mock 分类静默失配。`test_knowledge_registry.py` 里已有 guard 会红。
2. **`analysisStore.dispatch` 逐字段枚举写回**：新增 state 字段必须同时改 store，否则单测绿、界面空。
3. **明细模式要在 plan 和 generate 两处都声明**：只在 prompt 里写一次，模型仍会 SUM。
4. **`git add docs` 会把 `docs/superpowers/specs/` 带进去**：已加到 `.git/info/exclude`，别再用手写 `git add docs`。
5. **测试填充策略**：盲选第一个选项会选到「逐条明细」，多轮测试里已改成 overview/trend 优先。

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
