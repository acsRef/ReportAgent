# frontend/e2e Playwright E2E（P12）

ReportAgent 浏览器端到端。测试对象 = React 工作台（:3000，vite），经 `/api` proxy 到 backend（:8100）。

## 两层

| 层 | 后端 LLM | env | 谁跑 |
|---|---|---|---|
| **Contract** | mock（`LLM_PROVIDER=mock` + fixture 驱动；不连真实 MCP） | 无额外要求 | `npm run e2e:contract` — 本地/CI per-PR |
| **Full** | 真实 LLM（MiniMax）+ 真实 MCP（ragent-py） | `REPORTAGENT_E2E=1` | `npm run e2e:full` — nightly/manual |

Contract 后端由每个 spec 的 `beforeAll` 独占启动（fixture key = 语义 kind + 调用序，
与日期/schema 漂移无关）。Full spec 顶部 `test.skip(!process.env.REPORTAGENT_E2E)` 守门。

## 依赖

- PostgreSQL :5432（`ragent-postgres` 容器 + `init_pg.sql` + `seed_pg.sql`）
- Playwright chromium（`npx playwright install chromium`）
- Full 层还需：`.env` 里真实 `MINIMAX_API_KEY` + `D:/PyProject/ragent-py`（MCP 子进程）

## 命令

```bash
cd frontend
npm run e2e            # 全部（Contract specs 正常跑，Full specs 无 REPORTAGENT_E2E 自动 skip）
npm run e2e:contract   # specs/01..10（mock LLM，CI per-PR）
REPORTAGENT_E2E=1 npm run e2e:full   # specs/11..12（真实 LLM，nightly/manual）
```

## 目录

```text
frontend/e2e/
├── playwright.config.ts   # baseURL :3000，chromium，workers=1（serial backend per spec）
├── helpers/
│   ├── env.ts             # repo root / .env 读取 / URL 常量
│   ├── llm-mock.ts        # startContractBackend(caseId)：uvicorn :8100 + LLM_PROVIDER=mock
│   ├── global-setup.ts    # 验证 PG + 起 vite :3000
│   ├── global-teardown.ts # 关 backend + vite
│   ├── auth.ts            # login → token → localStorage 注入（ragent_auth）
│   ├── page-objects.ts    # WorkbenchPage（sendQuery/confirm/expectReport...）
│   └── wait-for-sse.ts    # 轮询动态条件（phase / trace 文本）
├── specs/
│   ├── 00-smoke.spec.ts   # 工程冒烟（未登录跳 /login）
│   ├── 01-happy-path.spec.ts ... 10-trace-progress.spec.ts   # Contract
│   └── 11-chitchat-bubble.spec.ts ... 12-empty-error-report.spec.ts  # Full
└── fixtures/llm/          # mock fixture（{kind:seq: response}，由 llm-mock 读）
```

## mock 语义 key

fixture 文件 `{case}.json` 的 key 是 `kind:seq`（`backend/tests/fixtures/llm_responses/`）：
- `kind` 由 prompt 固定 system_contract 首句分类（intent_classify / requirement_parse /
  sql_plan / sql_generate / report_plan …），不受「当前日期 / schema_text / memory 上下文」漂移影响
- `seq` 是该 kind 在本 backend 进程内被调用的次数（repair：`sql_generate:1` 坏 → `sql_generate:2` 好）
- 未命中抛 `MockLLMMiss`，绝不让 mock 静默兜底污染断言