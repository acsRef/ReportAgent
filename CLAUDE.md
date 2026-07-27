# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ReportAgent turns Chinese natural-language questions into PostgreSQL queries and renders the results as tables, charts, and insights inside a conversational workbench.

```text
User ←SSE→ React + Vite (:3000) → /api proxy → FastAPI + LangGraph (:8100)
                                                    │
                                                    ├─MCP→ Schema Server (auto-discovered port)
                                                    └────→ PostgreSQL
                                                          public: analytical star schema
                                                          app/agent/memory/observability: persistence
```

The frontend, backend, and MCP schema server are separate processes. PostgreSQL is the only active database; [backend/app/db.py](backend/app/db.py) is a legacy DuckDB compatibility path.

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

Startup initializes the async PostgreSQL pool, creates the default user if missing, checks embedding dimensions, and compiles the graphs. Embedding failures degrade memory search to keyword matching rather than blocking startup.

## Architecture

### Storage and infrastructure

- [backend/scripts/seed_pg.sql](backend/scripts/seed_pg.sql) recreates the 6 dimension and 4 fact tables in PostgreSQL's `public` schema (data covers 2020–2024). It contains destructive `DROP TABLE ... CASCADE` statements.
- [backend/scripts/init_pg.sql](backend/scripts/init_pg.sql) creates `app` (users, conversations, report templates), `agent` (sessions, requirement drafts, append-only report versions), `memory` (query templates and semantic entries), and `observability` (traces, spans, LLM calls).
- Analysis SQL uses synchronous `psycopg2` connections in [backend/app/tools/sql_tools.py](backend/app/tools/sql_tools.py). Application persistence uses the `asyncpg` pool in [backend/app/infra/db/postgres.py](backend/app/infra/db/postgres.py); the trace repository still creates its own asyncpg connection.
- The MCP server exposes table discovery tools. [mcp_schema_server/registry.py](mcp_schema_server/registry.py) and [backend/app/tools/data_tools.py](backend/app/tools/data_tools.py) are independent schema-matching implementations; local tools provide the fallback when MCP is unavailable.
- Many Python files are TSD-encrypted in the working tree and begin with `%TSD-Header-###%`. Inspect committed plaintext with `git show HEAD:<path>` instead of treating the working-tree bytes as source. Empty `__init__.py` files are intentional.

### Workbench request flow (active path)

`POST /api/v1/chat` accepts `mode: new | supplement | adjust | legacy` and streams SSE v2. The two-graph split:

1. **Requirement analysis** — [backend/app/agent/requirement_analysis_graph.py](backend/app/agent/requirement_analysis_graph.py) exposes only schema tools (`search_tables`, `get_table_ddl`, `list_tables`); SQL/Report tools are unreachable (pinned by `tests/graphs/test_requirement_analysis_sqlgate.py`). It produces a `RequirementCard` (shared contract: [backend/app/models/requirement.py](backend/app/models/requirement.py) ↔ [frontend/src/types/requirement.ts](frontend/src/types/requirement.ts), parity enforced by `tests/contracts/test_requirement_card_mirror.py`).
2. **Confirmed execution** — `PATCH /api/v1/sessions/{sid}/requirement` normalizes the card server-side (fills `selected_value` into structured fields, recomputes `status`). `POST /api/v1/sessions/{sid}/confirm` runs [backend/app/agent/confirmed_execution_graph.py](backend/app/agent/confirmed_execution_graph.py): gate (`status == 'complete'`, no missing fields, assumptions resolved, owner check) → lock draft → schema → `sql_agent` (reuses `sql_graph`: plan → generate → validate → execute) → `report_agent` → `persist_report`.

Key semantics:

- **FAILED never fakes success**: `report_agent` builds `answer.table` from real rows and sets `execution_status=FAILED` when there are none; a conditional edge then **skips `persist_report`**, so `main.py` emits an SSE `error` event (`{code, message, recoverable, failed_action}`) and stamps `session.phase='error'` + `last_failed_action='confirm'` instead of writing a hollow report.
- **SQL generation hardening** ([backend/app/utils/text.py](backend/app/utils/text.py)): reasoning models emit `
</think>
