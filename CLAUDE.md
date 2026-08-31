# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 沟通语言

始终用**中文**回复用户。代码、文件路径、类型名、函数名、错误码、命令、SQL 片段保持原文，不要翻译。

## 配套文档

- 详细的 Python / TypeScript 代码风格规范、SSE 事件协议、Git 提交约定、已知坑（Known Quirks）见 [AGENTS.md](AGENTS.md)。本文件与其冲突时以 AGENTS.md 的代码风格部分为准，避免两处重复维护。
- API 端点、SSE v2 事件载荷、工作台界面约定见 [README.md](README.md)。
- **架构契约（P1 冻结，任何改动不得违反）**：[docs/architecture/agent-flow.md](docs/architecture/agent-flow.md) / [state-contract.md](docs/architecture/state-contract.md) / [memory-architecture.md](docs/architecture/memory-architecture.md) / [frontend-contract.md](docs/architecture/frontend-contract.md) / [report-runtime.md](docs/architecture/report-runtime.md)。本文件只写 invariant；实现细节一律以这五份为准。
- 重构总基线（15 Phase 路线图与逐 Phase 验收）：[docs/plans/2026-08-25-refactor-master-freeze.md](docs/plans/2026-08-25-refactor-master-freeze.md)。

## 开发前必读（plan 驱动）

任何非平凡改动（≥2 文件或 ≥1 设计决策）动手前，必须先：

1. 读**固定入口** [docs/plans/README.md](docs/plans/README.md)（永久索引，唯一入口——**不要按日期找 plan**）。
2. 在索引「进行中」区定位本次任务对应的 plan；找不到就 `grep -rl "^> 状态: 进行中" docs/plans/`（锚定行首的 `> 状态:` 标记行，避免命中索引正文）。
3. 以该 plan 的「设计 / 复用工具 / 明确不做」为硬约束：复用优先、不越界、错误路径按 plan 枚举实现。
4. 开新 plan：命名 `docs/plans/YYYY-MM-DD-<slug>.md`，顶部写 `> 状态: 进行中`，并登记进 README.md 索引。
5. plan 落地后状态改 `已完成`（带 commit），被合并/取代的改 `已归档` 并注明并入哪份。

状态机：`进行中 → 已完成 → 已归档`；另有 `暂缓`（已批准但搁置）与 `只读评审`（review/grill 类）。

---

# 宪法区（P1 冻结）

以下章节是架构不变量。每章末尾的「现状」行标注落地阶段——**现状不是契约**，施工时按契约方向走，不按现状将就。

## 1. Project Identity

ReportAgent 是 **Stateful Agentic Data Analysis Workbench**：中文自然语言 → 需求理解 → 补全/确认 → Schema/Knowledge Retrieval → Query Planning → SQL Generation/Validation/Execution → Repair → ReportSpec → ReportVersion → 前端工作台。

项目性质：**个人面试项目**，展示 Agent Engineering 能力。一切「生产系统还可以做 X」的想法先过「明确不做」清单（各 plan 的 Explicitly NOT doing 节 + 本文件 Forbidden Patterns）。

> 现状：两图链路 + 三态落库 + 后台执行已在位（P0 前）。

## 2. Architecture Principles

核心原则：

```text
Agentic where uncertainty exists.
Deterministic where correctness matters.
```

**Agent ≠ Workflow**：Workflow 管确定性骨架（流程、状态传递、生命周期、边界、错误传播）；Agent 只在岔路口做决策（是否澄清？调哪个 Tool？repair 还是停？）。骨架是 Workflow，决策是 Agent。

系统边界（强制）：RAG 项目定位 Retrieval Runtime，经 MCP Server 对外；ReportAgent 定位 Agent/Application Runtime。ReportAgent **不得重新实现** Embedding / Chunking / Vector Search / Reranking / RAG Indexing，只通过 `MCP Client → RAG MCP Server` 使用。

### Forbidden Patterns（冻结，违反即打回）

- 不直接 import RAG 项目代码——只经 `MCP Client → RAG MCP Server`
- 不让 Agent 自拼 Context——Context Runtime 是唯一入口（P3 落地前沿用 build_session_context）
- 不让 Agent 直接访问 Memory DB——读写一律经 Memory Manager
- 不让 Agent 直接调用 provider SDK——只依赖 LLM Adapter（P6 前：app/llm.py call_llm）
- 不让 Tool 没有 description——Tool Description 是 Agent Contract
- 不让 Report Agent 编造数据——一切数值来自 Query Result
- 不无限 retry——预算固定 SQL 2 / MCP 2 / LLM 2
- 不绕过 MCP 直连 RAG 内部机制（embedding/vector_search/chunk/rerank 不进 ReportAgent）
- 不新增 legacy import——LEGACY BRIDGE 锚点区外零豁免（tests/contracts/test_legacy_import_freeze.py 钉住）
- 不新建 utils2/ managers/ runtime/ helpers/ common2/ 类 generic 文件夹——代码放最窄既有域边界

目标目录结构（迁移唯一依据）见伞形 plan §二·二；`backend/app/legacy/` 与 `frontend/src/legacy/` 是已归置旧链路，规则见 Legacy Policy 章。

> 现状：原则已生效；目录向目标结构迁移自 P3 起。

## 3. Canonical Flow

```text
User → Security Guard → Context Runtime
  → Requirement Agent (Memory + MCP + Decision) → RequirementCard → Confirmation
  → Execution Agent (Plan → Generate → Validate → Execute → Evaluate ↘ Failure → Repair)
  → Report Agent (Query Result → ReportSpec) → ReportVersion → Frontend
  → Langfuse + Eval
```

唯一主链路。新功能必须挂在这条链上，不允许开旁路。详见 [agent-flow.md](docs/architecture/agent-flow.md)。

> 现状：requirement_analysis_graph → confirmed_execution_graph 两图链路即此形态；Execution Loop 的动态决策环 P8 成形。

## 4. Agent Responsibilities

三个智能阶段，职责互斥：

| Agent | 负责 | 禁止 |
|---|---|---|
| Requirement | 理解意图、识别维度/指标、决定 Retrieval 与 Tool、判断缺失、Clarification、产出 RequirementCard | 生成 SQL、执行、渲染、持久化 |
| Execution | 主循环 Plan→Generate→Validate→Execute→Evaluate；失败走 Diagnose→Repair（六要素上下文回灌），预算 `MAX_SQL_REPAIR_RETRIES` | blind retry、无限循环 |
| Report | Execution Result → ReportSpec → ReportVersion | 编造数据 |

详见 [agent-flow.md](docs/architecture/agent-flow.md) §五。

> 现状：Requirement/Report 边界在位；Execution 动态决策环 P8。

## 5. State Contract

State 五块拆分与字段所有权：RequestState / RequirementState / ExecutionState / ReportState / RuntimeState。字段定义、immutable 规则、单一写者、生命周期见 [state-contract.md](docs/architecture/state-contract.md)。

> 现状：P3 已落（State 5 块 TypedDict + checkpoint adapter v1↔v2 + MigrationError + graph `(γ)` 单点注入，p3 分支 614 passed / 0 failed）。后续按 State 拆分执行属工程化任务（无新设计决策），归在 P11 frontend 收尾前的小重构。

## 6. Memory Architecture

四类记忆（Session / Conversation / Semantic / Query）职责分离；读取时机 Recall Before Agent + Selective Recall 四触发条件；写入时机 Write After Reliable Event；V1 简化三条（无 promotion pipeline、confidence 规则固定、temporary_preference 绑 session）；Lifecycle 状态机 candidate→active→superseded/expired；Conflict Priority 固定序——**Schema 永远不能被 Memory 覆盖**。

全部细则见 [memory-architecture.md](docs/architecture/memory-architecture.md)。

> 现状（2026-08-29 P4c 后）：P3/P4a/P4b 骨架已落（p3 分支 614 passed / 0 failed）；P4c 在 p4c 分支（5 commit: 9fefa44 graph caller 翻转 / 8b16835 主链 smoke / 7d58eb0 selective 收益 / 23331aa assembler real Filter & Budget / 78bd6b7 golden before/after）——4 graph caller（requirement_analysis_graph._requirement_parse + confirmed_execution_graph._confirmed_sql_agent + requirement_parser.parse_requirement + sql_graph._plan/_generate_sql）真接 `ContextRuntime.build()`；assembler 真实装 dedup by `(source, ref_id)` + §七 kind 排序（query > semantic > preference）+ Token Budget 截断（`P4C_ASSEMBLER_TOKEN_BUDGET` env，默认 4000 tokens ≈ 12000 chars）；SelectiveRecallPolicy §二四触发 + §三分流 在主链真触发；facade `build_session_context` 兼容路径保留（外部 import 仍可用）。**离线 proxy**：新增契约层 41/41 PASS + 既有 contract suite 84/84 PASS + `evaluation/tests/test_schema.py` 40/40 PASS。真端到端 baseline runner（`evaluation/runner.py`）留 P12 手动门（CLAUDE.md §15）。对比结论见 [docs/p4c-golden-before-after.md](docs/p4c-golden-before-after.md)。

## 7. Tool & MCP Contract

Tool Metadata 统一面：`name / purpose / when_to_use / when_not_to_use / input_schema / output_schema / preconditions / postconditions / failure_policy / side_effects / examples / risk_level / permission / source`。Tool Description 是 Agent Contract——写得模糊，Agent 必乱调用。每个 Tool 必须能回答「什么时候调用 / 什么时候不调用 / 调用前需要什么 / 调用后得到什么」。

MCP 失败不许默默返回空数组伪装"没结果"——必须显式 unavailable/timeout，由上层决定 retry/clarify/fallback/fail。

> 现状：工具已有 description（test_tool_descriptions.py 钉最小面）；统一 Metadata 与 Registry 收敛 P5。迁移期 `PHASE2_MCP_ONLY` flag + contract 一致的 local fallback 允许存在，Phase 5 起停止本地 fallback。

## 8. LLM Policy

全项目统一使用一个 reasoning-capable chat model，provider 无关。Provider / Model / Base URL / Auth / Generation Config / Structured Output / Reasoning Normalization / Retry / Timeout 八件事于 P6 收敛为 `backend/app/llm/` Adapter（generate / generate_structured）；Agent 代码零 provider 硬编码、不自解析 JSON、不带模型兼容逻辑。Reasoning 归一化（think 标签剥离）集中在 Adapter 层。

配置届时从 MINIMAX_* 收敛为 `LLM_*` settings（Configuration 表注记）。

> 现状：app/llm.py call_llm + llm_resilience.py 分散承担；P6 迁移并跑 Golden Set Before/After 对比。

## 9. Frontend Contract

AnalysisPhase 状态机 + `analysisReducer` 单一 phase 写者 + discriminated-union dispatch + 单向数据流。Canonical Events 七事件基线（phase / requirement / trace / thinking / report / error / done，锚定 [docs/sse-v2.md](docs/sse-v2.md)）；P11 在此基线上扩展 progress 事件族，扩展不替换。ReportVersion 一级 Domain Object（GENERATING/DONE/ERROR 与 SUCCESS/EMPTY/FAILED 两组状态区分）。报告严格渲染真实 payload。

详见 [frontend-contract.md](docs/architecture/frontend-contract.md)。

> 现状（2026-08-30 P11 后，2026-08-31 P12 增补）：事件面统一为 transport→schema→dispatch 三层——`api/sse.ts parseSSEFrameRaw`（拆帧）→ `api/analysisEvents.ts parseAnalysisSSEEvent`（七事件 + trace/thinking + report wire 形态，唯一 schema 层，两流共用）→ `stores/sessionEvents.ts handleSSEEvent`（单一写者 dispatch）；confirm/adjust 后台流执行中实时推 `trace` progress 帧（`infra/execution/progress.py` 节点→kind×status 映射，progress 族不新增顶层事件类型）；泛化异常 SSE 文案走 `user_message()`（P9-5 接线）；chitchat 终态 idle + 前端闲聊泡；session resume 恢复真实 phase + busy 会话接后台轮询；ProgressCard 由真 trace 信号驱动（移除 650ms 假定时器）。Report 渲染 / EMPTY / FAILED 保持 P10 验收零改动（KPI block 无生产者，P10-3 subset 前置）。**P12**：浏览器端到端 `frontend/e2e/` Playwright 工程——10 Contract specs（mock LLM + real PG，CI per-PR）+ 2 Full specs（env `REPORTAGENT_E2E=1` gate，nightly/manual）；mock keying = 语义 kind（prompt 固定 system_contract 首句分类）+ 调用序，不受日期/schema 漂移影响。

## 10. Report Contract

Report Agent 输出结构化 ReportSpec（非自由 Markdown）；三层 Validator 校验 `ReportSpec → QueryResult` 映射（结构 / 数值来源 / 禁止自由生成），不做 HTML 正则审计。ReportVersion append-only，SUCCESS/EMPTY/FAILED 三态全部落库，永不伪造成功。

详见 [report-runtime.md](docs/architecture/report-runtime.md)。

> 现状（2026-08-30 P10 后）：`app/report/` 域包（spec / validator / versioning）已落——ReportSpec v2（KpiSpec / TableSpec / DataBinding provenance，旧 payload 兼容 + `models/contracts.py` shim）+ 三层 Validator（结构字段存在性 / KPI 聚合重算 / 行存在性 fabrication；insight 文本不入正则审计）+ 父图接线 violations→FAILED + `REPORT_VALIDATION_ERROR`（SSE 用户码 QUERY_FAILED 兜底，前端 answer 契约零改动）。九类 block 按 P11 渲染需求扩展；`agents/report/` 目录迁移未做；真端到端回归留 P12 手动门。

## 11. Timeout & Failure Policy

统一 Failure Pipeline：`Error → Classify → Record Trace → Determine Recoverability → Retry/Resume/Fail → Persist State → User-visible Error`。Frontend 不自己猜错误含义。

- Retry 固定预算：SQL repair 2 / MCP 2 / LLM transient 2；Permanent 不 retry；Agent-recoverable 走 repair。
- DB Timeout ≠ SQL 错——区分 Query Timeout / Connection Failure / Permission / Object Not Found / Syntax，只有可恢复错误进有限 retry。
- SSE Disconnect ≠ Backend Failed——断连后任务继续跑完并持久化，前端轮询通知。
- Background Task 超 `MAX_TASK_DURATION` → Persist FAILED → ReportVersion(error)，不允许永远停在 generating。

> 现状（2026-08-30 P9 后）：`backend/app/reliability/`（errors / retry / backoff / timeout）已落——ErrorEnvelope + 10 码统一分类，AGENT（DiagnosePolicy）与 USER（SSE canRetry）两张 recoverable 表显式分离；`llm_resilience.py` 整体收编 `reliability/retry.py`（算法未重写，原文件为兼容 shim）；RetryPolicy 固定预算 SQL 2 / MCP 2 / LLM 2（`LLM_MAX_RETRIES` 默认自 5 收敛为契约值 2）；`MAX_TASK_DURATION`（默认 600s）背景任务超时全链：Persist FAILED → ReportVersion(error) → TASK_TIMEOUT 事件。SSE 用户码 `QUERY_*` 与 runtime 码双轨有意保留（前端契约稳定）；Langfuse 落库 P13、adaptive retry 留 Evaluation。

## 12. Observability

架构：`Agent Runtime → Observability Adapter → { PostgreSQL Trace, Langfuse }`。Span 字段明细（trace metadata / LLM generation / tool / MCP / SQL / repair）、PII Redaction 统一做在 Adapter 前一层、所有 timeout 必须产生 span。Metrics 记 Langfuse + 本地；**不引入 Prometheus/Grafana**。

> 现状：PG trace（infra/trace/repository.py 经共享 asyncpg pool）+ observability API 在位；Langfuse 接入与 redaction 层 P13。

## 13. Legacy Policy

- `backend/app/legacy/`（agents/parent_graph 等）与 `frontend/src/legacy/`（旧页面/旧 store/旧 SSE client/chat 组件）已归置；`main.py` 内 LEGACY BRIDGE BEGIN/END 锚点区是 mode=legacy 引用的唯一豁免区。
- **新代码禁止 import legacy**（后端 tests/contracts/test_legacy_import_freeze.py + 前端 legacyImportFreeze.test.ts 双侧钉死；桥接区快照禁扩容）。
- Phase 2~14 对 legacy 只允许 bug fix；Phase 15 删除。
- 名字像旧代码但属现役共用、**不在 legacy/** 的：data_graph / intent.py / requirement_parser / requirement_options / sql_graph._intent_analyze 入口节点——不要因名字旧而移动或删除。

> 现状：归置与断言已完成（P1，2026-08-26）。

## 14. Change Discipline

### Planning Discipline

> **Every multi-step change leaves a plan behind.** No "I'll just quickly do X" — anything that touches ≥2 files or ≥1 design decision is planned first, written to a file, and tracked.

Plans live in two mirrored locations during a session, but the **canonical, traceable copy** is always in the repo:

| Location | Purpose |
|---|---|
| `~/.claude/plans/<token>.md` | Live plan edited while in plan mode (Claude Code internal). Throwaway. |
| `docs/plans/YYYY-MM-DD-<topic-slug>.md` | Canonical plan committed to git. The only one that survives the session. |

When plan mode is invoked, after writing the initial plan to `~/.claude/plans/`, copy it to `docs/plans/`. When the plan evolves during execution, edit the in-repo copy. On commit, the in-repo plan goes in alongside the code change — same commit, message `feat|fix(<scope>): <title> + plan: <topic-slug>`.

Naming convention:

```
docs/plans/YYYY-MM-DD-<topic-slug>.md
```

Rules (do not relax these):

1. **Date prefix is `YYYY-MM-DD`**, ISO 8601, zero-padded, today's date in the user's local timezone. One plan per day per topic — if a topic rolls past midnight, add `-v2` (and `-v3`, …) instead of changing the date.
2. **`<topic-slug>` is kebab-case**, 2–6 words, derived from the feature or fix area.
3. **No random suffixes.** Token-style scratchpad names are not reused as canonical names.
4. **One topic per file.**
5. **Same slug in the commit and the PR title** so `git log --grep "<slug>"` retrieves every artifact of the change.

Required structure (skipping a section is a defect): **Context**（为什么做，含原始诉求）→ **Design**（做什么、模块怎么拼，文件路径不写行号）→ **Files to change**（变更模式 + 代表路径）→ **Reused existing utilities**（引用现有工具路径，复用是设计）→ **Verification**(端到端验证命令与冒烟矩阵) → **Explicitly NOT doing**（反向 scope）。

Design quality bar（一条不满足即打回重写）：Single responsibility per change / High cohesion / Low coupling / No drive-by edits / Reuse over reinvention / Interface is the contract / Errors are first-class / Naming carries intent。

Workflow：Trigger → Brainstorm before planning（fuzzy 时走 superpowers:brainstorming）→ Write in-repo plan first → Validate quality bar → ExitPlanMode hand to user → After approval the plan stays.

### Phase 门纪律

15 个 Phase 严格按序（P0 Baseline → P1 Freeze → P2 MCP → P3 Context → P4 Memory → P5 Tool → P6 LLM → P7 Prompt → P8 Agent Loop → P9 Reliability → P10 Report → P11 Frontend/SSE → P12 Playwright → P13 Langfuse → P14 Evaluation → P15 Docs/Demo）。每个 Phase 完成必须走 `代码 → Unit Test → Integration Test → Golden Case → Git Commit → 更新 CLAUDE.md 对应章节` 再进下一个；每个 Phase 另开实施 plan 并登记索引。

全程回归红线：任一 Phase 落地后全量 offline suite 不回退；P12 后 Contract E2E 入 CI per-PR 自动跑，Full E2E env-gated（`REPORTAGENT_E2E=1`）nightly/manual。

---

# 操作区（保留）

## Project Layout（速览）

```text
User ←SSE→ React + Vite (:3000) → /api proxy → FastAPI + LangGraph (:8100)
                                                    │
                                                    ├─MCP→ Schema Server (auto-discovered port)
                                                    └────→ PostgreSQL
                                                          public: analytical star schema
                                                          app/agent/memory/observability: persistence
```

前端、后端、MCP schema server 是独立进程；PostgreSQL 是唯一活跃数据库。分析 SQL 走 psycopg2（`tools/sql_tools.py`，ANALYSIS_DSN 独立角色），应用持久化与 trace 共享 asyncpg pool（`infra/db/postgres.py`）。MCP 不可用时本地 schema 工具兜底（Phase 5 收口）。

**TSD 加密提示**：许多 Python 文件在工作树中是 TSD 加密的（文件头 `%TSD-Header-###%`）。检查已提交明文用 `git show HEAD:<path>`，不要把工作树字节当源码。空的 `__init__.py` 是有意的。

## Setup and Commands

Requirements: Python 3.11, Node.js 18+, Docker, a MiniMax API key, and a SiliconFlow API key. There is no `.env.example` for the root; create `.env` at the repository root (a backend-focused example lives at [backend/.env.example](backend/.env.example)).

```bash
# Python environment and dependencies
conda create -n agent python=3.11
conda activate agent
pip install -r backend/requirements.txt
pip install -r backend/requirements-dev.txt   # test deps (pytest, pytest-asyncio, httpx, pytest-cov)
pip install -r mcp_schema_server/requirements.txt
npm --prefix frontend install

# PostgreSQL with pgvector
docker run -d --name ragent-postgres \
  -e POSTGRES_USER=ragent \
  -e POSTGRES_PASSWORD=ragent \
  -e POSTGRES_DB=ragent \
  -p 5432:5432 \
  pgvector/pgvector:0.7.0-pg15

docker exec -i ragent-postgres psql -U ragent -d ragent < backend/scripts/init_pg.sql
docker exec -i ragent-postgres psql -U ragent -d ragent < backend/scripts/seed_pg.sql
```

Start services in this order; the MCP server has no fixed port and the backend discovers it through MCP (local schema tools provide the fallback if MCP is down):

```bash
# Terminal 1
python -m mcp_schema_server.server

# Terminal 2
cd backend && uvicorn app.main:app --port 8100 --reload

# Terminal 3
cd frontend && npm run dev
```

Frontend commands:

```bash
cd frontend && npm run dev       # Vite on :3000; /api proxies to :8100
cd frontend && npm run build     # tsc -b && vite build
cd frontend && npm run lint      # oxlint, not ESLint
cd frontend && npm run test:run  # vitest one-shot (`npm run test` for watch mode)
```

### Testing

Backend tests use pytest ([backend/pytest.ini](backend/pytest.ini): `asyncio_mode = auto`, `testpaths = tests`, `--strict-markers`); suites live in `backend/tests/{smoke,contracts,persistence,graphs,e2e}`. Frontend tests use vitest (jsdom, `src/**/__tests__/*.{test,spec}.{ts,tsx}`).

```bash
# Backend (run from backend/)
cd backend && pytest                     # offline suite; persistence auto-skips without DATABASE_URL,
                                         # e2e auto-skips without REPORTAGENT_E2E
cd backend && pytest -m graphs           # markers: smoke | contracts | graphs | persistence | api | e2e
cd backend && pytest tests/graphs/test_sql_generation.py -k "keyword"   # single file / test

# Frontend
cd frontend && npm run test:run
cd frontend && npx vitest --run src/stores/__tests__/analysisReducer.test.ts -t "test name"

# Real end-to-end (needs PG + backend on :8100 + real LLM keys), from repo root:
REPORTAGENT_E2E=1 python -m pytest backend/tests/e2e/test_full_flow.py -s
```

`e2e/test_full_flow.py` drives the live API (login → chat → PATCH requirement → confirm → report with real rows → template CRUD) and asserts `query_snapshot.sql` non-empty and `answer.table` populated. Manual API checks:

```bash
curl http://localhost:8100/health
curl -X POST http://localhost:8100/api/v1/auth/login \
  -H "Content-Type: application/json" -d '{"username":"admin","password":"admin123"}'
curl -N -X POST http://localhost:8100/api/v1/chat \
  -H "Content-Type: application/json" -H "Authorization: Bearer <token>" \
  -d '{"user_query":"2024年各区域销售额排名","session_id":"test-1","mode":"new"}'
```

## Configuration

Important root `.env` variables:

| Variable | Default / behavior |
| --- | --- |
| `MINIMAX_API_KEY` | Primary LLM key |
| `LLM_API_KEY` | Alternative key; falls back to `MINIMAX_API_KEY` |
| `LLM_MODEL` | `MiniMax-M3` in code; configurable |
| `LLM_BASE_URL` | `https://api.minimax.chat/v1` |
| `SILICONFLOW_API_KEY` | Embedding provider key |
| `EMBEDDING_MODEL` | `Qwen/Qwen2.5-7B-Instruct` in code |
| `EMBEDDING_DIM` | `1536`; must match `VECTOR(1536)` in `init_pg.sql` |
| `DATABASE_URL` | `postgresql://ragent:ragent@localhost:5432/ragent` |
| `JWT_SECRET` | Development fallback exists; set explicitly outside local development |
| `DEFAULT_USERNAME` / `DEFAULT_PASSWORD` | `admin` / `admin123` |
| `APP_ENV` | `development` / `staging` / `production`; **fail-closed: unset means `production`** |
| `ALLOW_INSECURE_DEFAULT_AUTH` | `1` bypasses the auth gate, **only honored when `APP_ENV=development`** |
| `MEM0_ENABLED` | Optional mem0-based L3 fact extraction (default `false` → pure LLM extraction) |
| `MAX_TASK_DURATION` | `600`; background confirm/adjust task total budget (P9); on expiry → FAILED persist + `TASK_TIMEOUT` SSE error |
| `MAX_SQL_REPAIR_RETRIES` / `MAX_PLAN_RETRIES` | `2` / `1`; SQL repair & replan budgets (P8), pinned against `reliability/retry.RETRY_BUDGETS` |

> **P6 注记**：上表 `MINIMAX_API_KEY` / `LLM_MODEL` / `LLM_BASE_URL` 将随 Unified LLM Migration 收敛为 `LLM_*` settings（宪法 §8）；届时当天更新本表。

Startup runs the **fail-closed auth gate** ([backend/app/infra/auth/startup_guard.py](backend/app/infra/auth/startup_guard.py)) before anything else: in non-dev environments it refuses to boot when `JWT_SECRET` is missing / equals the public dev default / is shorter than 32 chars, or when `DEFAULT_PASSWORD` is still `admin123` (even for an already-existing admin row). Local development therefore needs `APP_ENV=development` + `ALLOW_INSECURE_DEFAULT_AUTH=1` in `.env`. After the gate, startup initializes the async PostgreSQL pool, creates the default user if missing, checks embedding dimensions, and compiles the graphs. Embedding failures degrade memory search to keyword matching rather than blocking startup.

Checkpointer 按 APP_ENV 选择（[backend/app/infra/checkpoint/factory.py](backend/app/infra/checkpoint/factory.py)）：development 用 MemorySaver；其余（含未设置，fail-closed）用进程级 AsyncPostgresSaver 单例。
