# ReportAgent 当前状态 / Hand-off

> 最后更新：2026-11-02
> **维护原则**：本文只回答「现在在哪 + 去哪看」，**不复刻会过期的快照数字**。数字以命令输出为准（见 §1）。
> 2026-07-24 版本是「现场快照」（当时写着 31 个测试、阶段清单与工作树状态），已整体失效，本版重写为索引式 hand-off。

## 0. 30 秒摘要

| 项 | 状态 |
|---|---|
| 后端测试 | **1050 收集 / 1049 passed / 1 skipped**（`cd backend && pytest -q`，约 60s；时间大头是 persistence 真库） |
| 前端测试 | **317 passed** + `tsc -b` 0 error + `oxlint` 0 error |
| 评测基线 | 20 例：16 pass / 0 fail / 4 条故障用例**真跑**（fault seam 已接通） |
| CI 门禁 | backend pytest + frontend vitest/tsc（任意分支 push 与 PR）；Playwright 在独立 workflow（手动/夜间，不阻塞） |
| 服务 | PG 5432 / backend 8100 / ragent-py(MCP) 8000 / 前端 3000，按需启动 |

**结论**：主链路（需求卡 → 确认 → SQL → 报告 → 落库）完整可用；剩余问题集中在**边界外的产品级能力**（行级权限、成本核算、行为指标），不在正确性上。

## 1. 怎么跑

```bash
# 后端（pytest 配置在仓库根 pytest.ini，不在 backend/ 下）
cd backend && pytest -q

# 前端
cd frontend && npm run test:run && npx tsc -b

# 评测基线（需 PG + ragent-py MCP :8000 + backend :8100 + 真 LLM key）
python -m evaluation.runner --out evaluation/results/baseline.json
```

完整启动顺序、环境变量与命令表见 [CLAUDE.md](CLAUDE.md) 与 [AGENTS.md](AGENTS.md)。

## 2. 现在有什么（一行一条）

- **两图链路**：`requirement_analysis_graph`（只暴露 schema 工具，产出 RequirementCard）→ `confirmed_execution_graph`（security_guard → load → **idempotency_guard** → sql_gate → data → sql → report → persist）。
- **三态落库**：SUCCESS / EMPTY / FAILED 全部 append-only 写 `agent.report_version`，永不覆盖、永不伪造成功。
- **幂等**：同一 draft 重复 confirm 直接回已 done 版本；FAILED 允许重试。
- **SQL 子图**：plan → generate → validate(EXPLAIN) → execute → evaluate → diagnose（确定性 DiagnosePolicy）→ build_output。
- **故障注入 seam**：`REPORTAGENT_E2E=1` + `X-E2E-Fault` / `X-E2E-McpDown` 两个 header，生产零行为变化。
- **Memory**：四类记忆 + Selective Recall + 金标召回（Recall@3 = 0.96）。
- **报告真实性**：ReportSpec + 三层 Validator（结构 / 数值重算 / 行存在性）。
- **观测**：PG trace + Langfuse 双 sink，含真实耗时回放。

## 3. 已知缺口（按边界编号，详见 [testing-boundaries.md](testing-boundaries.md) §四）

| 边界 | 缺什么 |
|---|---|
| B1 | 行级权限（目前只有 owner check + 只读 DB 角色） |
| B9 | `llm_call.cost` 恒为 0，没有 token×价核算 |
| B10 | `ErrorCard` 对 `recoverable=false` 也显示「重试」；错误会话恢复未验 |
| B12 | 行为指标（澄清轮数 / 参数完成率）与 LLM-as-Judge 未做 |
| B5 | `original_query(immutable)` 契约只适用于 legacy 图，现役 v2 主链不携带该字段 |

## 4. 文档地图（先看这里，别按日期找）

| 想知道什么 | 去哪 |
|---|---|
| 架构不变量（宪法区，改动不得违反） | [CLAUDE.md](CLAUDE.md) |
| 正在进行 / 已完成的工作与决策记录 | [docs/plans/README.md](docs/plans/README.md)（**唯一入口**） |
| 五份架构契约（flow / state / memory / frontend / report） | [docs/architecture/](architecture/) |
| 测试该写什么、什么算边界外 | [docs/testing-boundaries.md](testing-boundaries.md) |
| API 端点、SSE v2 事件载荷 | [README.md](../README.md) / [docs/sse-v2.md](sse-v2.md) |
| 代码风格与已知坑 | [AGENTS.md](../AGENTS.md) |
