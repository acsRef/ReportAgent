> 状态: 已完成（2026-11-18）

# flaky 治理（P1）：定时任务只重跑一次，重跑通过必须留痕

## Context

同一天的两件事催生了这条：

1. 夜间 `e2e-contract` **连红 7 天没人发现**（plan `2026-11-18-e2e-contract-fixture-realign`）——
   原因是"红"太多、太重，人看到红的反应退化成"再点一次"或干脆不看；
2. prompt 行为集里存在**真·概率性**用例（PB-04 占比偶尔判成 ranking、PB-06 打招呼偶尔判成分析任务），
   单次采样做门禁必然抖动——已在用例层用「3 采样取多数」缓解，但仍需要流程层的兜底。

`docs/testing-playbook.md` §二 把「flaky 治理」判为 ❌（无隔离名单/无重跑策略/无失败率统计），本条把它补上。

## Design

三条纪律，缺一条都会退化成"重跑掩盖真回归"：

1. **push 门禁永不重跑**：重跑会掩盖真回归；只有 `GITHUB_EVENT_NAME=schedule` 才允许；
2. **只重跑失败用例一次**：`pytest --last-failed` / `playwright --last-failed`，不整组重跑（省时间也省 LLM 额度）；
3. **重跑通过 ≠ 绿**：job 保持绿色（避免噪音），但必须
   - 打 `::warning title=flaky`、
   - 写 `reports/flaky-<name>.json`（首跑失败用例、重跑结果、event/sha/耗时）、
   - 写 Job Summary（人一眼能看到"这次绿是重跑来的"）、
   - artifact 留存。
   两次都失败 → job 红 → 走 `notify-failure` 开 Issue（这才是真缺陷）。

落成 `scripts/ci/run_with_flaky_retry.py`（Python，跨平台，本地也能验证）：

```
python scripts/ci/run_with_flaky_retry.py --name prompt-behavior -- \
    evaluation/tests/test_prompt_behavior_live.py -q -s --junitxml=reports/prompt-behavior.xml
```

顺带把「失败日志尾部 → `::error` 注解」也放进这个脚本：Actions 的 job log 需要登录才能下载，
而 annotations 匿名可读；2026-11-18 排 ragent 两次红就是吃了这个亏（只能本地 Docker 复现）。

## Files to change

| 文件 | 变更 |
|---|---|
| `scripts/ci/run_with_flaky_retry.py` | 新增：首跑 → 定时重跑一次 → 留痕 + 注解；退出码语义（0 过 / 0 flaky / 1 真失败） |
| `.github/workflows/live-eval.yml` | `prompt-behavior` / `compaction-real-flow` / `offline-regression` / `full-chain` 四个 job 改用它 |
| `.github/workflows/e2e-contract.yml` | Playwright 同样策略（`--last-failed`，仅定时），失败 spec 名写 Job Summary |
| `docs/ci-cd.md` | 新增「flaky 治理」小节 |
| `docs/testing-playbook.md` | 维度 #10（flaky）❌→✅；P1 行标记已落地 |
| `docs/plans/README.md` | 登记本 plan |

## Reused existing utilities

- `notify-failure` job（上一轮加的）负责"两次都失败"的报警，本方案不重复造；
- `actions/upload-artifact` 收集 `reports/`（已存在）；
- GitHub 原生 `::warning` / `GITHUB_STEP_SUMMARY` / `--last-failed`，不引入新依赖。

## Verification

1. **行为 A（push 场景不重跑）**：临时探针 `首跑失败/重跑通过`，不设 `GITHUB_EVENT_NAME` →
   脚本打印"不是定时任务 → 不重跑"，exit 1（已跑 ✅）；
2. **行为 B（定时场景重跑一次）**：同一探针 + `GITHUB_EVENT_NAME=schedule` →
   首跑失败、重跑通过、exit 0、`reports/flaky-*.json` 里 `flaky=true` 且列出首跑失败用例（已跑 ✅）；
3. **接线检查**：用真用例 `evaluation/tests/test_prompt_behavior_live.py -k PB-06`（3 采样调成 1）
   跑脚本 → `首跑通过（13.2s）`、JSON `first_rc=0`（已跑 ✅）；
4. **Playwright 参数存在性**：`npx playwright test --help | grep last-failed`（已确认）；
5. 推送后看 `ci.yml` 三 job 仍绿 + 下一次定时任务的 Job Summary 是否出现 flaky 段。

## Explicitly NOT doing

- 不自动维护隔离名单（quarantine）：先人工月度复盘，等真有反复 flaky 再加；
- 不做跨运行失败率聚合（需要持久化存储/缓存，P2；当前用 artifact + 月度人看）；
- 不在 push 门禁上用 `--rerun-failed`（会掩盖回归）；
- 不重跑整组用例（只重跑失败用例）。
