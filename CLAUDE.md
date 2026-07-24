# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ReportAgent turns Chinese natural-language questions into PostgreSQL queries and renders the results as tables, charts, and insights.

```text
User ←SSE→ React + Vite (:3000) → /api proxy → FastAPI + LangGraph (:8100)
                                                    │
                                                    ├─MCP→ Schema Server (auto-discovered port)
                                                    └────→ PostgreSQL
                                                          public: analytical star schema
                                                          app/agent/memory/observability: persistence
```

The frontend, backend, and MCP schema server are separate processes. PostgreSQL is the active analytical database; [backend/app/db.py](backend/app/db.py) and [backend/seed_data.sql](backend/seed_data.sql) are legacy DuckDB compatibility paths.

## Setup and Commands

Requirements: Python 3.11, Node.js 18+, Docker, a MiniMax API key, and a SiliconFlow API key. There is no `.env.example`; create `.env` at the repository root.

```bash
# Python environment and dependencies
conda create -n agent python=3.11
conda activate agent
pip install -r backend/requirements.txt
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

Start services in this order; the MCP server has no fixed port and the backend discovers it through MCP:

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
cd frontend && npm run preview
```

There is no test runner, test suite, or single-test command in this repository. Neither backend nor frontend has test configuration, and `frontend/package.json` has no `test` script. Verify backend behavior with authenticated curl requests and verify frontend changes with `npm run build`, `npm run lint`, and manual interaction.

```bash
# Health check
curl http://localhost:8100/health

# Obtain a JWT (development default: admin/admin123)
curl -X POST http://localhost:8100/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# Exercise the SSE chat endpoint
curl -N -X POST http://localhost:8100/api/v1/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"user_query":"今年华东销售趋势","session_id":"test-1"}'
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

Startup initializes the async PostgreSQL pool, creates the default user if missing, checks embedding dimensions, and compiles the parent graph. Embedding failures degrade memory search to keyword matching rather than blocking startup.

## Architecture

### Storage and infrastructure

- [backend/scripts/seed_pg.sql](backend/scripts/seed_pg.sql) recreates the 6 dimension and 4 fact tables in PostgreSQL's `public` schema. It contains destructive `DROP TABLE ... CASCADE` statements.
- [backend/scripts/init_pg.sql](backend/scripts/init_pg.sql) creates `app` (users and conversations), `agent` (sessions), `memory` (query templates and semantic entries), and `observability` (traces, spans, LLM calls).
- Analysis SQL uses synchronous `psycopg2` connections in [backend/app/tools/sql_tools.py](backend/app/tools/sql_tools.py). Application persistence uses the `asyncpg` pool in [backend/app/infra/db/postgres.py](backend/app/infra/db/postgres.py); the trace repository still creates its own asyncpg connection.
- The MCP server exposes table discovery tools. [mcp_schema_server/registry.py](mcp_schema_server/registry.py) and [backend/app/tools/data_tools.py](backend/app/tools/data_tools.py) are independent schema-matching implementations; local tools provide the fallback when MCP is unavailable.
- Many Python files are TSD-encrypted in the working tree and begin with `%TSD-Header-###%`. Inspect committed plaintext with `git show HEAD:<path>` instead of treating the working-tree bytes as source. Empty `__init__.py` files are intentional.

### Backend request flow

[backend/app/main.py](backend/app/main.py) owns FastAPI routes, JWT dependencies, SSE formatting, checkpoint resume, conversation persistence, and awaited trace flushing.

Public endpoints are `/health`, `/api/v1/auth/login`, and `/api/v1/auth/register`. `/api/v1/chat`, `/api/v1/sessions`, and `/api/v1/conversations/{session_id}` require `Authorization: Bearer <token>`. JWTs use HS256 with a 24-hour expiry. A supplied `session_id` identifies both a conversation and a LangGraph checkpoint; checkpoints older than the current server process are treated as stale.

SSE events are:

| Event | Payload / role |
| --- | --- |
| `token` | Report-node LLM text chunks only |
| `trace` | `{step, status, detail}` node/tool progress |
| `thinking` | Lightweight pre-SQL planning hint |
| `card` | `{type, version, payload}` interactive card envelope |
| `report` | `{answer: {text, table, chart, insight}}` |
| `clarify` | `{question}` from the interrupted graph |
| `error` | Error text |
| `done` | Stream completion |

### Parent graph and SQL flow

[backend/app/agent/parent_graph.py](backend/app/agent/parent_graph.py) is an eight-node parent graph:

```text
security_guard
  ├─ HIGH → END
  └─ classify
       ├─ 闲聊 → END
       ├─ 看板 → dashboard placeholder → END
       └─ 报表 → data_agent → sql_agent → evaluate
                                      ├─ SUCCESS → report_agent → END
                                      ├─ INTENT_AWAIT → END after emitting a card
                                      ├─ NEED_CLARIFICATION → clarify (interrupt) → data_agent
                                      └─ other failure → retry sql_agent, then clarify
```

The data, SQL, and report graphs are invoked with `.ainvoke()` inside parent nodes; they are not embedded LangGraph subgraphs. Only the parent `clarify` node calls `interrupt()`. Checkpoints therefore persist parent state, while subgraph state is ephemeral. `evaluate` routes only on `execution_status` and retry count.

Important parent-state semantics:

- `original_query` is frozen and used when remembering a successful SQL template.
- `current_query` accumulates clarification answers and drives subsequent schema, SQL, and report work.
- `clarification_history` records question/answer pairs.
- `chosen_tool` carries the frontend's interactive-card selection into SQL planning.

The interactive pre-SQL flow has two implemented stages:

1. With no `chosen_tool`, the SQL node runs a lightweight intent analyzer, stores an `intent_card`, returns `INTENT_AWAIT`, and stops before SQL generation.
2. The frontend repeats `/api/v1/chat` with the same `session_id` and a `chosen_tool`; the backend injects it with `update_state()` and resumes planning. If the plan still lacks dimensions, an `options_group` card is emitted and the graph enters the normal clarification interrupt path.

`preview_card` exists in types/comments but is not emitted by the current parent flow.

[backend/app/agent/sql_graph.py](backend/app/agent/sql_graph.py) runs `plan → generate_sql → validate → execute → evaluate → build_output`. SQL syntax/validation failures regenerate up to three attempts; after that, one schema re-plan is allowed before `NEED_CLARIFICATION`. The parent graph may also re-enter `sql_agent` up to its retry limit.

SQL safety in [backend/app/tools/sql_tools.py](backend/app/tools/sql_tools.py) is layered:

1. Require a statement beginning with `SELECT` and reject DDL/DML keywords.
2. Parse with `sqlglot` and require a `Select` AST.
3. Run PostgreSQL `EXPLAIN <sql>` before execution.

### Memory and tracing

[backend/app/infra/memory/memory_manager.py](backend/app/infra/memory/memory_manager.py) coordinates two pgvector-backed stores:

- Query templates: `semantic_similarity × 0.5 + success_rate × 0.3 + frequency × 0.1 + recency × 0.1`.
- User memory: `semantic_similarity × 0.6 + importance × 0.2 + frequency × 0.1 + recency × 0.1`.

Both use `embed_or_none`; embedding failure falls back to parameterized `ILIKE ANY($1::text[])` matching. Successful SQL is stored against `original_query`; report insights are stored as user memory.

The tracer accumulates spans during a request. Keep `await tracer.flush()` in the SSE generator's `finally` path so disconnects and errors still persist trace data.

### Frontend data flow

- [frontend/src/api/chat.ts](frontend/src/api/chat.ts) sends JWT-authenticated requests, parses buffered SSE frames, aborts after 180 seconds, and logs out on HTTP 401.
- [frontend/src/stores/session.ts](frontend/src/stores/session.ts) owns the session/checkpoint ID, SSE-driven UI state, cards, timeline, reports, and templates. The session ID has a 24-hour local TTL. Auth is persisted separately by [frontend/src/stores/authStore.ts](frontend/src/stores/authStore.ts).
- `card` events are normalized into `ChatCard` values and upserted per report message. Selecting an intent option sends its tool name back as `chosen_tool`.
- [frontend/src/adapter/reportAdapter.ts](frontend/src/adapter/reportAdapter.ts) converts the backend response into flat `ReportBlock[]` values. [frontend/src/components/report/registry.ts](frontend/src/components/report/registry.ts) maps block types to renderers, and `ReportRenderer` delegates rendering.
- Vite proxies `/api` to `http://localhost:8100`; production routing or proxying is not configured in this repository.

## Conversational Workbench (shipped on `feat/conversational-workbench`)

Phases 0–7 of [docs/plans/2026-07-24-conversational-workbench.md](docs/plans/2026-07-24-conversational-workbench.md) are now implemented. The architecture above describes the legacy flow; the workbench flow below is the **active** path.

- **API shape:** `POST /api/v1/chat` accepts `mode: new | supplement | adjust | legacy`. New companions:
  - `PATCH /api/v1/sessions/{sid}/requirement` — server-side recompute of `RequirementCard`
  - `POST /api/v1/sessions/{sid}/confirm` — SSE v2 stream; runs the confirmed-execution graph
  - `POST /api/v1/sessions/{sid}/retry` — resume after `last_failed_action`
  - `GET /api/v1/sessions/{sid}` — full snapshot (session + messages + requirement + latest report)
  - `GET /api/v1/sessions/{sid}/reports/{version}` — pure PG read; no LLM
  - `POST|GET|PATCH|DELETE /api/v1/templates` — PG-backed template CRUD
- **Two-graph split:** [backend/app/agent/requirement_analysis_graph.py](backend/app/agent/requirement_analysis_graph.py) exposes only schema tools (`search_tables`, `get_table_ddl`, `list_tables`); SQL/Report tools are unreachable. [backend/app/agent/confirmed_execution_graph.py](backend/app/agent/confirmed_execution_graph.py) gates on `draft.user_id == jwt.user_id AND draft.status == 'complete' AND missing_fields == [] AND all assumptions accepted`. The legacy `parent_graph` with `interrupt()` + `chosen_tool` is reached only via `mode=legacy`.
- **SSE v2 events:** `phase` (`{phase, reason?}`), `requirement` (full `RequirementCard`), `report` (`{version, parent_version, title, answer, trace}`), `error` (`{code, message, recoverable, failed_action}`), `done` (`{final_phase}`). `card` / `clarify` / `token` only appear on `mode=legacy`.
- **Persistence:** `agent.requirement_draft`, `agent.report_version` (append-only, `parent_version`), `app.report_template`, and 4 new columns on `agent.session` (`current_phase`, `last_failed_action`, `latest_requirement_draft_id`, `latest_report_version`). All writes go through one `asyncpg` transaction per service call. DDL: [backend/scripts/init_pg.sql](backend/scripts/init_pg.sql); design: [docs/persistence.md](docs/persistence.md).
- **Shared contract:** `RequirementCard` Pydantic at [backend/app/models/requirement.py](backend/app/models/requirement.py) + TS mirror at [frontend/src/types/requirement.ts](frontend/src/types/requirement.ts); field parity enforced by `backend/tests/contracts/test_requirement_card_mirror.py`.
- **Frontend state:** `analysisReducer` is the single source of truth for the workbench UI. `useAnalysisStore` ([frontend/src/stores/analysisStore.ts](frontend/src/stores/analysisStore.ts)) dispatches via immer; React components never write `phase` directly. `useTemplateStore` ([frontend/src/stores/templateStore.ts](frontend/src/stores/templateStore.ts)) owns PG-backed templates and one-shot migration from the legacy `ragent_templates` localStorage key.
- **User-id bug fixes:** the three pre-workbench call sites that confused `session_id` for `user_id` are corrected in [backend/app/main.py](backend/app/main.py) (line 159–161) and [backend/app/agent/parent_graph.py](backend/app/agent/parent_graph.py) (lines 139 and 434). `agent.session.user_id` and `memory.semantic_entry.user_id` were soft-migrated from VARCHAR to INT in `init_pg.sql`.
- **Visual baseline:** design tokens at [frontend/src/styles/tokens.css](frontend/src/styles/tokens.css) and [frontend/src/theme/antdTheme.ts](frontend/src/theme/antdTheme.ts). Pages: [frontend/src/pages/WorkbenchPage.tsx](frontend/src/pages/WorkbenchPage.tsx), [TemplateLibraryPage.tsx](frontend/src/pages/TemplateLibraryPage.tsx), [SecureReportPage.tsx](frontend/src/pages/SecureReportPage.tsx), [LoginPage.tsx](frontend/src/pages/LoginPage.tsx). All AntD component tokens overridden; AntD default `#1677ff` is replaced by the teal family. The legacy `ChatPage` / `RunningView` / `ReportView` / `HistoryPage` / `TemplateCenter` / `Navbar` are still reachable under `/legacy/*` for one release; Phase 8 plans to remove them in a follow-up commit.
- **Guardrails for new work:** Pydantic v2 with full type annotations; all PG reads/writes scoped by `(user_id, session_id)` from JWT; reducers stay pure and immutable; `main.py` keeps to HTTP/SSE orchestration only; static diff review (contracts, state transitions, auth, transactions, error recovery, visual tokens) required per batch.

### Status (this branch)

| Concern | State |
| --- | --- |
| pytest backend | 29/29 passing (smoke 8, contracts 7, persistence 8, graphs 6) |
| vitest frontend | 31/31 passing (reducer 16, client 7, store 4, adapter 3) |
| tsc -b | passes |
| oxlint | 0 errors |
| `mode=legacy` endpoint | retained on `/api/v1/chat?mode=legacy` for backward compatibility |

## Reference Documents

- [README.md](README.md) — setup, endpoints, current UI, and architecture overview.
- [AGENTS.md](AGENTS.md) — concise operational reference; keep it consistent with this file.
- [docs/development-plan.md](docs/development-plan.md) — earlier architecture decisions and roadmap.
- [docs/memory-ranking-plan.md](docs/memory-ranking-plan.md) — memory ranking rationale.
- [docs/persistence.md](docs/persistence.md) — authoritative DDL for `agent.requirement_draft`, `agent.report_version`, `app.report_template`, and extended `agent.session`.
- [docs/state-machine.md](docs/state-machine.md) — HTTP `phase` ↔ LangGraph state mapping; legal entry/exit/error paths.
- [docs/api-reference.md](docs/api-reference.md), [docs/sse-v2.md](docs/sse-v2.md) — API surface and SSE v2 event protocol.
- [docs/contracts/](docs/contracts/) — backend/frontend contract mirrors (start with [docs/contracts/requirement-card.md](docs/contracts/requirement-card.md)).
- [docs/ui-style-guide.md](docs/ui-style-guide.md), [docs/code-style-conventions.md](docs/code-style-conventions.md) — visual and code conventions.
- [docs/plans/2026-07-24-conversational-workbench.md](docs/plans/2026-07-24-conversational-workbench.md) — **active 8-phase rework plan**; supersedes older plans where they conflict.
- [docs/plans/2026-07-24-intelligent-analysis-workbench-design.md](docs/plans/2026-07-24-intelligent-analysis-workbench-design.md) — design rationale for the workbench.
- [docs/plans/2026-07-22-frontend-ui-refactor.md](docs/plans/2026-07-22-frontend-ui-refactor.md) — earlier frontend refactor plan; design-token work overlaps with the active plan.
- [docs/plans/2026-07-24-intelligent-analysis-workbench-html.md](docs/plans/2026-07-24-intelligent-analysis-workbench-html.md) — implementation plan for the approved HTML prototype.
