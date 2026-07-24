# ReportAgent — Agent Instructions

## Startup Order (MCRITICAL)

1. `python -m mcp_schema_server.server` (MCP first — no fixed port)
2. `cd backend && uvicorn app.main:app --port 8100 --reload` (FastAPI backend)
3. `cd frontend && npm run dev` (Vite dev server on :3000)

Backend auto-discovers MCP via MCP protocol. Starting backend first causes connection failure.

## Architecture

```
User ←SSE→ React+Vite (:3000) → proxy /api → FastAPI+LangGraph (:8100)
                                              ↓ MCP
                                         MCP Schema Server
                                              ↓
                                         PostgreSQL (all data — analytical + session + trace + memory)
```

Business data moved from DuckDB to PostgreSQL `public` schema in migration. DuckDB path is legacy (`backend/app/db.py` marked 旧版兼容).

**PostgreSQL schemas:** `public` (dim/fact tables via `seed_pg.sql`), `app` (users, conversations via `init_pg.sql`), `agent` (session), `memory` (query_template VECTOR(1536), semantic_entry), `observability` (trace spans, LLM calls).

## Key Commands

| Purpose | Command |
|---------|---------|
| MCP start | `python -m mcp_schema_server.server` |
| Backend | `cd backend && uvicorn app.main:app --port 8100 --reload` |
| Frontend dev | `cd frontend && npm run dev` |
| Frontend build | `cd frontend && npm run build` (tsc -b && vite build) |
| Frontend lint | `cd frontend && npm run lint` (oxlint, not eslint) |
| Frontend preview | `cd frontend && npm run preview` |
| Login (default) | `curl -X POST http://localhost:8100/api/v1/auth/login -H "Content-Type: application/json" -d '{"username":"admin","password":"admin123"}'` |
| Test API | `curl -X POST http://localhost:8100/api/v1/chat -H "Content-Type: application/json" -H "Authorization: Bearer <token>" -d '{"user_query":"今年华东销售趋势","session_id":"test-1"}'` |
| Health check | `curl http://localhost:8100/health` |

**No tests exist.** All verification manual via curl. No CI.

## Setup

```bash
conda create -n agent python=3.11; conda activate agent
pip install -r backend/requirements.txt
pip install -r mcp_schema_server/requirements.txt
cd frontend && npm install
docker run -d --name ragent-postgres -e POSTGRES_USER=ragent -e POSTGRES_PASSWORD=ragent -e POSTGRES_DB=ragent -p 5432:5432 pgvector/pgvector:0.7.0-pg15
docker exec -i ragent-postgres psql -U ragent -d ragent < backend/scripts/init_pg.sql
docker exec -i ragent-postgres psql -U ragent -d ragent < backend/scripts/seed_pg.sql
```

**.env** (create manually, no `.env.example`): `MINIMAX_API_KEY`, `SILICONFLOW_API_KEY`, `DATABASE_URL`, `LLM_MODEL`, `LLM_BASE_URL`, `EMBEDDING_DIM` (must be 1536 to match VECTOR(1536) in `init_pg.sql`). `LLM_API_KEY` also accepted — falls back to `MINIMAX_API_KEY`. Both also populate `OPENAI_API_KEY` automatically in `main.py`.

## JWT Auth

All `/api/v1/chat`, `/api/v1/sessions`, `/api/v1/conversations/{session_id}` require `Authorization: Bearer <token>`. Token from `/api/v1/auth/login`. Default: admin/admin123. Frontend auto-redirects to `/login` on 401.

## SSE Event Protocol

| event | purpose |
|-------|---------|
| `token` | streaming LLM text (report node only) |
| `trace` | agent step update {step, status, detail} |
| `thinking` | lightweight "planning" hint pre-SQL |
| `card` | interactive cards (intent_card / options_group / preview_card / confirm_card) |
| `report` | final answer {answer: {text, table, chart, insight}} |
| `clarify` | clarification question |
| `error` | error message |
| `done` | stream end |

**2-stage intent card flow:** agent emits `intent_card` → user picks option → frontend sends `chosen_tool` in next `/api/v1/chat` request (with same `session_id`). State resumes via LangGraph `update_state`.

## Agent Graph

```
User Query → security_guard (score ≥ 3 → block)
  ├─ 闲聊 → END
  └─ 报表 → data_agent → sql_agent → evaluate
       ├─ SUCCESS → report_agent → END
       └─ NEED_CLARIFICATION → clarify_node (interrupt) → data_agent
```

- `clarify` is the **only** node calling `interrupt()` — SubGraphs never interrupt
- SubGraphs run via `.ainvoke()` inside parent nodes (not LangGraph sub-graphs)
- Checkpoint saves Parent State only (SubStates are ephemeral)
- `original_query` frozen; `current_query` enhanced with clarification context

## SQL Retry Logic

**Internal (sql_graph):** syntax error → regenerate (max 3×), schema error → replan (1×), exhausted → `NEED_CLARIFICATION`
**Parent graph:** SQL fail → retry `sql_agent` (max 3×) instead of falling through to `report_agent`

## SQL Safety (3 Layers)

1. Blacklist: reject non-SELECT (DDL/DML keywords)
2. AST: `sqlglot` verifies parsed result is `Select`
3. EXPLAIN: run `EXPLAIN <sql>` to catch DuckDB-specific syntax errors

## Memory System

Entry point: `infra/memory/memory_manager.py`. Two backends:
- **QueryMemory** (`memory.query_template`): pgvector + keyword hybrid search. Score: `semantic×0.5 + success_rate×0.3 + freq×0.1 + recency×0.1`
- **UserMemory** (`memory.semantic_entry`): Score: `semantic×0.6 + importance×0.2 + freq×0.1 + recency×0.1`
- Embedding API failure → graceful fallback to `ILIKE ANY($1::text[])` parameterized keyword matching

## TSD-Encrypted Source Files

Many `.py` files show `%TSD-Header-###%` (encrypted placeholders). Read via `git show HEAD:<path>`. Only `main.py`, `llm.py`, and empty `__init__.py` are readable directly.

## Code Style — Python (Backend)

- **Imports:** stdlib first, then third-party, then local. Group with blank lines. Use `from __future__ import annotations` at top of every file.
- **Types:** Always annotate function signatures. Use `|` for unions (`str | None` not `Optional[str]`). Use `BaseModel` from pydantic for data contracts.
- **Naming:** `snake_case` for functions/variables, `PascalCase` for classes, `UPPER_SNAKE` for constants. Private helpers prefixed with `_`.
- **Error handling:** Catch specific exceptions; use `logger.warning` for recoverable errors, `raise` for fatal. Avoid bare `except:`. Log exceptions with `%s` formatting.
- **Patterns:** Module-level lazy singletons (e.g. `_conn_rw: DuckDBPyConnection | None = None` with getter). Globals acceptable for connections.
- **Docstrings:** Optional. Comments in Chinese for domain concepts, English for technical notes.

## Code Style — TypeScript/React (Frontend)

- **Imports:** React/external first, then local relative. Use `import type` for type-only imports. `verbatimModuleSyntax` enabled.
- **Types:** Prefer `interface` for Props/State, `type` for unions/utilities. Use `Record<string, unknown>` for dynamic data.
- **Components:** Default export function components. Props interface named `Props` (local to file). No class components.
- **Styling:** Inline `style={{}}` objects (no CSS modules/tailwind). Ant Design `Typography`. Color tokens: `#1677ff` (primary), `#e8e8e8` (border), `#f0f0f0` (divider), `#8f959e`/`#646a73` (secondary), `#1f2329` (body).
- **State:** Zustand only. No Redux/Context. Store interfaces inline in `create<>()`.
- **Imports from `antd`:** Destructure specific components. Avoid `import antd from 'antd'`.
- **Error handling:** `try/catch` with `err instanceof Error` narrowing. `catch { /* ignore */ }` for non-critical JSON parsing.

## Known Quirks

- `__init__.py` files are intentionally 0 bytes (namespace packages, Python 3.3+)
- `infra/trace/repository.py` uses raw `asyncpg` (not `infra/db/postgres.py` pool) — pool integration not wired
- MCP Schema Server's `registry.py` and `app/tools/data_tools.py` are **two independent** keyword schema-matching implementations
- `POST /api/v1/chat` `session_id` doubles for new session and checkpoint resume
- Frontend TypeScript ~6.0; lint uses oxlint, not eslint
- Embedding uses SiliconFlow API (`.env`: `SILICONFLOW_API_KEY`), separate from LLM (MiniMax)
- Session persisted in `localStorage` key `ragent_session_id` (Zustand, `stores/session.ts`, 24h TTL). Auth store key: `ragent_auth`.
- LLM timeout on frontend: 180s. Error message in Chinese.
- Frontend locale: `zh_CN` via Ant Design.
- `.claude/settings.local.json` contains stale permission paths referencing `d:/PyProject/ragent-py/`.
