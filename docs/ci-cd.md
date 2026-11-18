# CI/CD 现状：三层，各自回答不同问题

> 方法论与「怎么把这套搬到别的 agent 项目」见 [testing-playbook.md](testing-playbook.md)；可整包复制的脚手架在 [docs/templates/agent-testing/](templates/agent-testing/README.md)。

> 设计动机与取舍见 [plan 2026-11-18-live-ci-pipeline](plans/2026-11-18-live-ci-pipeline.md)。
> 一句话：**L1 证明「代码没被改坏」，L2 证明「改完之后系统真的对」。** CD（镜像/发布/部署）本轮未做。

## 一、三层一览

| 层 | workflow | 何时跑 | 跑什么 | 要 secrets | 典型耗时 |
|---|---|---|---|---|---|
| **L1 门禁** | `ci.yml` | 每次 push / PR | 后端全量 pytest（真 PG）+ 前端 vitest + tsc + oxlint + `vite build` + 评测平台自测 | 无 | ~2 min |
| **L2 core** | `live-eval.yml`（`prompt-behavior`） | 每晚 18:30 UTC（=02:30 CST）+ 手动 | prompt 行为集（13 例 × 3 采样，**真 LLM**） | `LLM_API_KEY` | ~10 min |
| **L2 core** | `live-eval.yml`（`compaction-real-flow`） | 同上 | 压缩摘要保真（真数仓 + 真 LLM） | `LLM_API_KEY` | ~4 min |
| **L2 普通回归** | `live-eval.yml`（`offline-regression`） | 同上，**排在 core 之后** | 后端全量 pytest 再跑一遍（确认真链路绿了、离线套件也没回退） | 无 | ~3 min |
| **L2 full** | `live-eval.yml`（`full-chain`） | 每周日 19:30 UTC（=周一 03:30 CST）+ 手动 `mode=full` | clone ragent → 起 :8000 → 灌业务表字典 KB → 起 backend → 真 e2e（多轮/语义 SQL/真实问法） | `LLM_API_KEY` + `SILICONFLOW_API_KEY` | 15–30 min |
| **L2 contract** | `e2e-contract.yml` | 每晚 + 手动 | Playwright Contract E2E（mock LLM + 真 PG） | 无 | ~6 min |

为什么 L2 要分 core / full：full 需要 clone ragent 并安装它的依赖（含 `docling`，重），
每晚跑不划算；而 prompt 行为集只打真 LLM、几分钟就能跑，适合每天。

## 二、需要配置的 secrets

仓库 → Settings → Secrets and variables → Actions：

| Secret | 用途 | 没配会怎样 |
|---|---|---|
| `LLM_API_KEY` | 真 LLM（prompt 行为集、full 链路的回答/规划） | L2 的 `prompt-behavior` / `full-chain` 打印 `::warning::` 并**跳过**，job 仍是绿的（不会假装通过） |
| `SILICONFLOW_API_KEY` | embedding（full 链路灌字典 KB、ragent 检索） | `full-chain` 跳过 |

**没配 secret 时不会红，但也没有证据**——`live-eval` 的 job 日志里会看到「跳过」，别把绿当通过。

## 三、怎么加「有价值的用例」

### 3.1 prompt 行为用例（`evaluation/prompt_behavior_cases.json`）

这是本仓唯一「把 prompt 当被测对象」的地方：断言 prompt **驱动出来的行为**，
不是「prompt 文案里有没有这句话」（那种断言有 78 处，改一个字就假红）。

一条用例的字段：

```json
{
  "id": "PB-07-followup-subdivide",
  "area": "turn_understanding",
  "why": "「再按产品细分」是追问，不是新任务；判错会丢掉已确认的时间/区域。",
  "input": "再按产品细分一下",
  "task": {"time_range": "2024年", "metrics": ["销售额"], "goals": [{"type": "ranking", "dimension": "区域"}]},
  "history": ["2024年各区域销售额排名"],
  "criteria": [
    {"type": "enum_exact", "path": "conversation_act", "expected": "follow_up"},
    {"type": "goal_dimension_contains", "expected": ["产品"]}
  ],
  "breaks_when": "prompt 对多轮上下文描述变弱 → 模型判成 new_task，或把「按产品细分」当成新的时间范围改写"
}
```

**准入纪律**：`breaks_when` 必填，写不清「什么改动会让它红」的用例不收——否则它只是噪音。

**为什么会「多采样取多数」**：prompt 行为是概率性的。同一批用例连跑两次，`PB-04`（占比）
偶尔被归成 ranking、`PB-06`（打招呼）偶尔被归成分析任务——单次采样进了 CI 只会变成噪音，
于是和「改了文案就红」的假红一个下场（被无视）。现在每例采样 3 次、**多数通过**才算过
（`RUN_PROMPT_BEHAVIOR_SAMPLES` 可调，本地调试设 1 更快）。真正的退化是「多数采样都不对」，它能被抓住。

支持的判据（`evaluation/tests/test_prompt_behavior_live.py` 里实现，全部确定性、无 LLM 裁判）：

| 判据 | 含义 |
|---|---|
| `enum_exact` | `plan.<path>` 必须等于期望值（闭集枚举） |
| `goals_empty` | 必须没有分析目标（闲聊） |
| `goal_types_include` / `goal_types_exclude` | 目标类型集合必须含 / 不含 |
| `goal_any` | 存在某目标满足 `where`，且其字段等于 `expect` |
| `goal_dimension_contains` / `metric_mentioned` | 目标里的维度 / 指标提到某词 |
| `update_fields_contains` / `update_fields_absent` | 某字段被改写成含某词 / 不该出现在改写里 |
| `plan_json_contains_any` / `plan_json_excludes` | 整个 plan 必须出现 / 不得出现某些串（用于「年份不能被算成 2026」这类回归） |
| `no_blocking_uncertainty` | 不该有 blocking 的不确定项（当前产品里 `uncertainties` 尚未被消费，保留判据备用） |

### 3.2 真链路用例

L2 只**编排**既有用例，不重写：`backend/tests/e2e/*`、`evaluation/tests/test_*.py` 里的 live 套件
（都由 `REPORTAGENT_E2E=1` gate）。要加一条，就在 `live-eval.yml` 的对应 job 里加文件路径，
并在 plan 里写清为什么值得每天/每周跑。

## 三·五、flaky 治理（2026-11-18 P1）

规则只有三条，缺一条都会退化成「重跑掩盖真回归」：

| 规则 | 落地 |
|---|---|
| **push 门禁永不重跑** | `scripts/ci/run_with_flaky_retry.py` 只在 `GITHUB_EVENT_NAME=schedule` 时重跑 |
| **只重跑失败用例一次** | `pytest --last-failed` / `playwright --last-failed`（不整组重跑，省时间也省额度） |
| **重跑通过 ≠ 绿** | job 保持绿色，但强制留痕：`::warning title=flaky` + `reports/flaky-<name>.json` + Job Summary + artifact |

两次都失败 → job 红 → `notify-failure` 开 Issue（那才是真缺陷）。

L2 的四个 pytest job（`prompt-behavior` / `compaction-real-flow` / `offline-regression` / `full-chain`）
和 `e2e-contract` 都已接入。本地验证：

```powershell
$env:GITHUB_EVENT_NAME='schedule'   # 模拟定时任务
python scripts/ci/run_with_flaky_retry.py --name demo -- backend/tests/smoke -q
# 想在 push 场景验证"不重跑"：去掉上面那行，脚本会打印「不是定时任务 → 不重跑」并以失败退出
```

## 四、失败怎么看

1. 定时失败会**自动开/更新 Issue**：`[live-eval] nightly 失败`（同标题去重，只加评论）；
2. 进 Actions → `live-eval` → 失败 job：
   - `prompt-behavior`：看模型原文（用例失败时打印 `breaks_when` + 前 600 字 plan 原文），
     先判断是**prompt 退化**还是**用例判据写错**；
   - `full-chain`：下载 `full-chain-report` artifact，里有 `/tmp/backend.log`、`/tmp/ragent.log`、`pytest` 输出；
3. 手动触发的失败**不刷 Issue**（调试噪音）；看 run 页面即可。

排查顺序固定为：**先证明判定器对，再判定产品错**（2026-11-17 的教训：8 条 fail 里 4 条是判定器阈值写错）。

## 五、本地复跑

```powershell
# L1（离线，不需要 key）
cd D:\PyProject\ReportAgent
D:\miniConda\envs\agent\python.exe -m pytest -q

# L2 prompt 行为（需要仓库根 .env 里的真 LLM key；不需要后端进程）
$env:RUN_PROMPT_BEHAVIOR='1'
D:\miniConda\envs\agent\python.exe -m pytest evaluation/tests/test_prompt_behavior_live.py -q -s

# L2 真链路（需要 PG + backend :8100 + ragent :8000 + 真 key，见各测试文件头部的「前置」）
$env:REPORTAGENT_E2E='1'
D:\miniConda\envs\agent\python.exe -m pytest backend/tests/e2e/test_multiturn_followup.py -q -s

# 手动触发 L2
#   Actions → live-eval → Run workflow → mode: core | full | offline
```

## 六、两个仓库的分工

| 仓库 | 流水线 | 内容 |
|---|---|---|
| `ReportAgent`（本仓） | `ci.yml` / `live-eval.yml` / `e2e-contract.yml` | L1 门禁 + L2 真链路（prompt 行为 / 真 e2e） |
| `ragent-py`（`acsRef/Rag`） | `ci.yml` / `eval.yml` | L1（unit + integration，真 PG）+ L2（65 题检索评测 + 场景测试，nightly/dispatch） |

跨仓真链路（ReportAgent 需要 ragent 的字典 KB）由 `full-chain` job 现场 clone 并启动，不依赖线上服务。

## 七、已验证到哪一步（2026-11-18）

| 项 | 状态 |
|---|---|
| L1 门禁（含新增前端 build/lint、评测平台自测） | ✅ 远端 GitHub Actions 实跑绿（run 126） |
| prompt 行为集（13 例 × 3 采样） | ✅ 本地按 CI 配置真跑 13 passed（7m22s） |
| 压缩真数据保真 | ✅ 本地 12 passed（1m34s） |
| Contract E2E（曾连红 7 天） | ✅ 本地全量 10 passed；根因与修复见 plan `2026-11-18-e2e-contract-fixture-realign` |
| L2 远端 dispatch | ⏳ 需要配 secrets 后手动点一次（见第二节） |
| ragent-py 仓 CI | ✅ 远端全绿（run 3, `620cfd9`：unit + integration 都 success）。首跑抓出两处**依赖漂移**（SQLAlchemy 2.1 换默认驱动 / bcrypt 5.0 与 passlib 不兼容），pin 后修好；L2 本地真跑 65 题：hit@5=1.0 / recall@5=0.945 / MRR=0.949，详见其 `docs/ci-eval-pipelines.md` |

## 八、还没做的

- **CD**：镜像构建（backend / frontend / mcp）、GHCR 推送、GitHub Release、SSH 部署 —— 本轮不做。
  理由：先把「测得准」解决掉，再谈「自动上线」；否则等于给不可信的绿灯装自动发布。
- 覆盖率门禁（当前只跑测试，不设覆盖率阈值：仓库里 mock 多，覆盖率数字会误导）。
- LLM 裁判打分（会引入第二层不确定性；真链路用确定性判据）。
