# P12 Playwright E2E

## Contract E2E（CI per-PR，10 specs）

```bash
npm run e2e:contract
# 或带 JSON 归档：
npm run e2e:contract:report
# 产物：frontend/e2e/artifacts/playwright-contract-report.json
```

**栈组成**：real browser（Playwright bundled Chromium）+ real FastAPI +
real LangGraph + real PG（localhost:5432）+ mock LLM（fixture 驱动）+
**intentionally disabled MCP**（`RAGENT_MCP_PYTHON=D:/non-existent/...`）。

**证明**：MCP 不可用时系统 fallback 后能工作 + 各业务路径 spec 行为契约
（happy / clarification / empty / failed / retry / background / version /
recovery / memory / trace progress）。

**期望**：10/10 passed（CI 无 env 依赖）。

## Full E2E（nightly / manual gate，2 specs）

```bash
REPORTAGENT_E2E=1 npm run e2e:full
# 或带 JSON 归档：
REPORTAGENT_E2E=1 npm run e2e:full:report
# 产物：frontend/e2e/artifacts/playwright-full-report.json
```

**栈组成**：real browser + real FastAPI + real LangGraph + real PG +
real LLM（MiniMax，需 `LLM_API_KEY`）+ real MCP（ragent-py，需
`RAGENT_MCP_PYTHON` 指向真实解释器 + ragent-py 服务运行中）。

**证明**：frontend → backend → MCP → DB 全链路 + 真实 LLM 决策。

**期望**：2/2 passed（spec 11 chitchat + spec 12 empty-result with real data）。

未设 `REPORTAGENT_E2E`：`npm run e2e:full` 自动 `test.skip` 2 个 spec →
输出 `2 skipped / 0 failed`（不是 bug，是 env gate）。

## 报告解读

- 默认 `npm run e2e` 跑全 12 个 spec（10 Contract + 2 Full），未设
  `REPORTAGENT_E2E` 时 2 个 Full 自动 skip。
- CI 调用 `e2e:contract` 期望 10/10 passed；调用 `e2e:full` 前必须设 env。
- JSON report 含每个 spec 的 timing + 失败 trace 路径，便于 CI 归档对比。

## Contract E2E 边界说明（review-prep-r2 Fix 2）

Contract E2E 故意禁用 MCP（`RAGENT_MCP_PYTHON` 指向不存在的解释器）。
这不是缺陷——是设计选择：

- **Contract**：证明业务契约 + fallback 路径（不需要真 MCP）。
- **Full**：证明端到端全栈（需要真 MCP + 真 LLM）。

二者互补，不重叠。Full spec 默认 env-gated 是为了 CI 不依赖外部 key。

## MockLLM session scope（review-prep-r2 Fix 1）

`backend/app/llm/mock.py` 在 review-prep-r2 后支持 per-session cursor
scope（`set_mock_session_scope(f"{user_id}:{session_id}")` 在 graph entry
调用）。这保证：

- 同一 backend process 内多个 session 各自 cursor 从 `:1` 起；
- 业务流程多调一次 / 少调一次不会让后续 session fixture 漂移。

详见 [docs/plans/2026-08-31-p12-review-prep-r2.md](../../docs/plans/2026-08-31-p12-review-prep-r2.md)。
