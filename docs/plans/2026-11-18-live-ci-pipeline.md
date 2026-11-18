> 状态: 进行中

# 让 CI 可信：L1 门禁 + L2 真链路流水线（两个项目）

## Context

用户 2026-11-18 的判断：「目前我们的 CI 用处不大——如果我调整了部分代码，测试的例子应该不是实时的吧」。
核对后**成立**，证据如下：

| 事实 | 数字 |
|---|---|
| 每次 push 跑的后端测试文件 | 189 个 / 约 1900 例 |
| 其中替身/mock LLM 引用 | 91 处 |
| 断言 prompt 文案的位置 | 78 处（prompt 一改就假红） |
| 真端到端（`REPORTAGENT_E2E=1`） | 5 条，CI 里直接 skip |
| 夜间 Playwright contract（真 PG） | **自 2026-11-10 起连续 7 次失败**，无人发现 |
| 真实检索/真 LLM 评测（65 题 + 5 场景） | 完全不在 CI 里，只手动跑 |
| ragent-py 仓库 | **没有任何 CI**（90 个测试文件、eval 资产齐全） |

结论：现有 CI 只能证明「以前修过的坑没复发」，不能证明「改完之后系统真的对」。用户选择方案 **A**：
先把 CI 做可信（修死掉的 nightly + 建真链路层），CD（镜像/GHCR/Release/部署）**推迟**。
用户补充要求：**只挑有价值的用例**（prompt 行为、真链路、各种边界情况），**普通回归放最后跑**，**两个项目都要**。

## Design

### 一、三层，各自回答不同问题

| 层 | 何时跑 | 内容 | 回答什么 |
|---|---|---|---|
| **L1 门禁** | 每次 push / PR（≤6 min） | 后端离线套件 + 前端 vitest/tsc/lint/**build** + `eval_platform` 自测 | 「代码/契约/类型有没有被改坏」 |
| **L2 真链路** | 每晚 + 手动 | 真 LLM + 真 PG + 真 ragent：核心行为套件 + prompt 行为集 + 检索/场景 | 「改完之后系统是不是真的对」 |
| **L3 发布卡口** | 打 tag 前（本轮只做文档约束） | 要求 L2 最近一次绿 | 「这个版本敢不敢发」 |

CD（镜像/GHCR/Release/SSH 部署）**本轮不做**，理由写在「不做事项」：测不准的绿灯上装自动发布更危险。

### 二、L2 用例分级：只挑有价值的

`nightly-core`（每晚必跑，控制在 ~15 min、LLM 调用 ~40 次）：

| 用例 | 覆盖 | 为什么值得 |
|---|---|---|
| `evaluation/tests/test_prompt_behavior_live.py`（**新建**） | prompt 行为：目标分类 / 多轮继承与改写 / 否决词 / 不过度澄清（13 例） | 现有 78 处 prompt 断言只验**文案**，不验**行为** |
| `backend/tests/persistence/test_compaction_real_flow.py` | 真数仓数据 + 生产入口压缩：摘要有没有丢数字 | 压缩 prompt 是最「默默影响答案」的一处 |
| `evaluation/tests/test_real_rag_mcp_e2e.py` | 6 场景（happy/repair/永久失败/点名澄清/检索/多轮） | 唯一验证「整条链路 + 真 RAG + 真 LLM」 |
| `evaluation/tests/test_semantic_sql_accuracy.py` | SQL **语义**等于答案（数值比对 canonical reference） | 抓 double_fact / 错表 / 漏年 |
| `backend/tests/e2e/test_multiturn_followup.py` | 4 轮追问逐年/逐月继承 | 多轮是历史事故高发区 |
| `evaluation/tests/test_memory_llm_sql_behavior.py` | 记忆不污染 SQL | 记忆接线后的真实边界 |

`nightly-core` = 上表前两行（prompt 行为 + 压缩保真，**不需要 ragent**，~6 min）；
其余需要 ragent 服务的放进 `weekly-extended`（full 档，含 clone + 起服务 + 灌 KB，15–30 min）。

`weekly-extended`（每周日，慢且贵）：`test_chain_matrix.py`（问法矩阵）、`test_requirement_slots.py`（用户模拟器三度量）、
`test_real_user_questions.py`（真实问法矩阵）、`backend/tests/persistence/test_compaction_real_flow.py`。

`ragent 侧`（在 ragent-py 仓自己的 workflow）：`tests/unit` + `tests/integration`（真 PG）作 L1；
`eval` 全量 65 题检索 + 5 个场景（增量更新/删除/跨库隔离/权限×2）作 L2 —— 复用我已跑通的执行逻辑，
不再造第二套。

**普通回归放最后**：L2 workflow 里 `offline-regression` job 排在真链路之后（`needs`），
它只做「真链路绿了之后，再确认离线套件也没回退」，失败不掩盖前面的信号。

### 三、Prompt 行为用例集（新建，回答「prompt 好不好」）

现有 78 处断言是「prompt 文案里有没有这句话」——这是**冻结历史**，不是行为。
新建 `evaluation/prompt_behavior_cases.json` + `evaluation/tests/test_prompt_behavior_live.py`（env-gated `RUN_PROMPT_BEHAVIOR=1`）：

| 判据类型 | 含义 | 例子 |
|---|---|---|
| `enum_exact` | 管道输出必须是闭集里的某个值 | turn understanding 的 `goal` ∈ {analysis, chitchat, schema_lookup} |
| `set_contains` | 结构化字段必须包含/等于集合 | `target_metrics` 含「销售额」且不含「净销售额」 |
| `normalized_eq` | 归一后相等 | `time_range` 归一后 =「2024年」 |
| `slot_asked` | 澄清必须问到指定槽位 | 缺时间范围 → 问句里出现时间槽 |
| `numeric_grounded` | 输出里的数字必须来自结果集 | 报告 insight 的数字 ⊆ 查询结果值域（既有 validator 复用） |

每例 = `{id, prompt_area, input, criteria:[...], why}`，跑完输出 pass/fail + LLM 原文（失败可复盘）。
**纪律**：每例至少要能「因 prompt 退化而红」——所以每例都在用例里写明 `breaks_when`（什么改动会让它红），
没有 `breaks_when` 的用例不收。

### 四、失败要能吵醒人（否则又是连红 7 天）

L2 的定时触发失败时，自动开/更新一个 GitHub Issue：
- 标题固定 `[live-eval] nightly 失败：<job>`，按标题去重（已开则评论最新结论，不重复刷）；
- 权限：`permissions: issues: write`；
- 手动触发（`workflow_dispatch`）失败**不开** Issue（避免调试噪音）。

### 五、修掉那条死掉的 nightly

`e2e-contract` 自 11-10 连红。已知根因类别（workflow 注释里自己写过）：mock LLM 按「语义 kind + 调用序」
匹配 fixture，prompt/调用序一偏，canned 响应落到错步骤上。处理原则：

1. 先复现拿到**具体失败 spec 与断言**（本地 `npm run e2e:contract`）；
2. 若确认是 mock 错位 → 改 mock keying 为「kind 优先 + 该 kind 剩余 fixture 轮转」，不再依赖全局调用序；
3. 若是真缺陷 → 修代码，并把该 spec 提为 core 用例；
4. 无论哪种，**失败要能定位**：artifact 里保留 trace + 失败 spec 名。

### 六、两个仓库的分工

| 仓库 | 新增 | 说明 |
|---|---|---|
| `ReportAgent` | `live-eval.yml`、`prompt_behavior` 用例与 runner、`ci.yml` 加严 | L2 会 `git clone` 公开的 `acsRef/Rag` 起 ragent 服务（真检索） |
| `ragent-py` | `.github/workflows/ci.yml`（L1）、`eval.yml`（L2 检索+场景） | 此前零 CI；复用 `eval/` 与 `tests/` 既有资产 |

## Files to change

| 文件 | 变更 |
|---|---|
| `.github/workflows/live-eval.yml` | 新增：L2 真链路（nightly-core / weekly-extended / offline-regression / issue-on-failure） |
| `.github/workflows/ci.yml` | 加 `frontend-build`、`eval-platform` 两个 job |
| `.github/workflows/e2e-contract.yml` | mock keying 修复 + 失败定位信息 |
| `evaluation/prompt_behavior_cases.json` | 新增：行为用例（含 `breaks_when`） |
| `evaluation/tests/test_prompt_behavior_live.py` | 新增：env-gated 行为 runner（判据五类） |
| `frontend/e2e/helpers/llm-mock.ts` | 改：kind 优先 + 同 kind 轮转，去掉对全局调用序的强依赖 |
| `ragent-py/.github/workflows/ci.yml` | 新增（另一仓库）：unit + integration（真 PG） |
| `ragent-py/.github/workflows/eval.yml` | 新增（另一仓库）：65 题检索 + 5 场景（nightly/手动） |
| `docs/deployment.md` → `docs/ci-cd.md` | 新增：三层职责、如何加用例、如何加 secret、失败怎么看 |
| `docs/plans/README.md` | 登记本 plan |

## Reused existing utilities

- 既有真链路用例（`evaluation/tests/*`、`backend/tests/e2e/*`）**不重写**，L2 只负责编排与挑选；
- 既有判据助手：`runners._term_eq` / 数值归一（`_norm`）、`evaluation/deterministic_numeric.py`、ReportSpec validator；
- 既有评测平台（`eval_platform/`）的检索/场景执行逻辑 → ragent 仓 `eval.yml` 复用同一批断言口径；
- 既有 `LLM` adapter / `call_llm`（不新增 provider 代码）；
- 既有 `pytest` marker 体系（`e2e` / `persistence` 的 env-gate 已有，直接沿用 `REPORTAGENT_E2E`、`RUN_REAL_COMPACTION`）。

## Verification

1. **本地**：`RUN_PROMPT_BEHAVIOR=1 pytest evaluation/tests/test_prompt_behavior_live.py -q`（真 key）→ 全绿；
   逐个改一版 prompt 文案（临时），确认用例**会红**（证明它不是假绿）。
2. **本地**：`cd frontend && npm run e2e:contract` → 复现 nightly 失败并修到绿。
3. **远端**：push 后看 `ci.yml` 新 job 绿；`workflow_dispatch` 触发 `live-eval` 跑通；
   故意让一条用例失败一次，确认自动开 Issue（随后关闭）。
4. **ragent 仓**：push 后两个 workflow 绿；`eval.yml` dispatch 跑完输出 65 题与 5 场景结论。
5. 两个仓库 CI 全绿且 L2 有 artifact（JSON 快照）可查。

## Explicitly NOT doing

- **不做 CD**（镜像/GHCR/Release/SSH 部署）——用户确认本轮只做 A；测不准的绿灯上装自动发布更危险；
- 不引入 LLM 裁判打分（会引入第二层不确定性；真链路用确定性判据）；
- 不把 Playwright Contract E2E 塞进 push 门禁（假红会让人重新无视 CI）；
- 不做多机/多环境（staging）部署；
- 不改两个项目的业务代码（除修 nightly 定位到的真缺陷）；
- 不追求「全用例都进 CI」——按用户要求只挑有价值的，普通回归排最后。
