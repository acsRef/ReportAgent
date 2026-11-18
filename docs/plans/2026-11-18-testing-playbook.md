> 状态: 已完成（2026-11-18）

# 测试流程正规性评估 + 可迁移到其他 agent 项目的经验

## Context

用户 2026-11-18：「关注下当前的测试流程是不是正规的，以及可落地；总结出一套快速迭代到其他 agent 项目的经验」。

背景：同一天刚落完 `2026-11-18-live-ci-pipeline`（L1/L2 分层、prompt 行为集）与
`2026-11-18-e2e-contract-fixture-realign`（夜间死红根因）。此时需要停下来做一次**评估与沉淀**，
而不是继续加流水线——否则加的是"更多没被验证的绿灯"。

## Design

1. **先量化再评价**：实测 10 个维度（分层 / 确定性 / 契约 / 门禁分离 / 真链路 / prompt 质量 /
   资产治理 / 依赖纪律 / 可复现性 / flaky 治理），每维度给 ✅ / ⚠️ / ❌；
2. **差距按 ROI 排 P0/P1/P2**，P0 当天修完（可复现性）；P1/P2 写清动作与成本，**明确不做**的也写（覆盖率门禁、变异测试）；
3. **可迁移经验**抽象成「四层 + 六纪律 + 反模式清单 + 7 天节奏 + 成本参考」，
   每条纪律附本仓证据（不是泛泛而谈）；
4. **可落地**：抽一套脚手架 `docs/templates/agent-testing/`（pytest marker 约定、env-gate conftest、
   L2 workflow 模板、prompt 行为用例 schema、一页检查表），新项目可整包复制；
5. 明确「不要照抄」的部分（评测平台形态、Playwright、全量金标、deferred 分层、多采样），避免经验被误用。

## Files to change

| 文件 | 变更 |
|---|---|
| `docs/testing-playbook.md` | 新增：评估表 + 差距 + 四层模型 + 六纪律 + 反模式 + 7 天节奏 + 检查表 + 不要照抄 |
| `docs/templates/agent-testing/README.md` | 新增：落地顺序与三条硬约束 |
| `docs/templates/agent-testing/CHECKLIST.md` | 新增：一页检查表（可复制到新项目 README） |
| `docs/templates/agent-testing/pytest.ini.snippet` | 新增：marker 约定 + 分层跑法 + 三条纪律 |
| `docs/templates/agent-testing/conftest_gate.snippet.py` | 新增：离线哨兵 + e2e env-gate 自动 skip |
| `docs/templates/agent-testing/live-eval.yml.template` | 新增：L2 流水线骨架（core / 普通回归排最后 / full / 失败开 Issue） |
| `docs/templates/agent-testing/prompt_behavior_cases.template.json` | 新增：prompt 行为用例 schema + 4 个示例（含 `breaks_when`） |
| `backend/tests/conftest.py` | 修可复现性 P0：repo root 加入 `sys.path`（3 个文件 `import evaluation` 依赖 cwd） |
| `docs/ci-cd.md` | 顶部交叉链接 playbook 与模板 |
| `docs/plans/README.md` | 登记本 plan |

## Reused existing utilities

- 既有分层与 marker 体系（`pytest.ini` 的 smoke/contracts/graphs/persistence/api/e2e）；
- 既有 `live-eval.yml` / `e2e-contract.yml` 的真实结构（模板是从它们脱敏而来，不是凭空写）；
- 既有 prompt 行为用例集与判据实现（`evaluation/tests/test_prompt_behavior_live.py`）；
- 既有评测平台与证据档位（`docs/evaluation/*`）作为 L3 的样例。

## Verification

1. 模板文件语法：`json.load` / `yaml.safe_load` 均通过（已跑）；
2. 可复现性修复：`backend/` 与 repo root 两个 cwd 都 `--collect-only` 出 **1937 tests、0 error**（已跑，前后对比）；
3. 文档内所有数字都取自当次实测（1937 / 97 / 91 / ≈78 / 13×3=7m22s / 10 specs 4m06s / ragent 505&66）。

## Explicitly NOT doing

- 不引入覆盖率门禁（mock 多，覆盖率会误导；只建议记趋势）；
- 不引入变异测试工具（用「抽掉实现看是否红」的手工抽查替代，本轮 fixture 防漂移闸已做过一次）；
- 不做 CD（镜像/发布/部署）——用户已确认推迟，理由：测不准的绿灯上装自动发布更危险；
- 不给 flaky 加重跑掩盖（重跑只允许放在 L2 定时任务，且要记录频率）。
