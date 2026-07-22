# ReportAgent — Agent Instructions

## Startup Order (MCRITICAL)

1. `python -m mcp_schema_server.server` (MCP first — no fixed port)
2. `cd backend && uvicorn app.main:app --port 8100 --reload` (FastAPI backend)
3. `cd frontend && npm run dev` (Vite dev server on :3000)

Backend auto-discovers MCP Schema Server via MCP protocol. Starting backend first causes connection failure.

## Architecture Overview

```
User ←SSE→ React+Vite (:3000) → proxy /api → FastAPI+LangGraph (:8100)
                                              ↓ MCP
                                         MCP Schema Server
                                              ↓
                                         DuckDB (read-only, auto-seeded)
                                         PostgreSQL (session+trace+memory)
```

## Key Commands

| Purpose | Command |
|---------|---------|
| MCP start | `python -m mcp_schema_server.server` |
| Backend | `cd backend && uvicorn app.main:app --port 8100 --reload` |
| Frontend dev | `cd frontend && npm run dev` |
| Frontend build | `cd frontend && npm run build` (tsc -b && vite build) |
| Frontend lint | `cd frontend && npm run lint` (oxlint, not eslint) |
| Frontend preview | `cd frontend && npm run preview` |
| Test API | `curl -X POST http://localhost:8100/api/v1/chat -H "Content-Type: application/json" -d '{"user_query":"今年华东销售趋势","session_id":"test-1"}'` |
| Health check | `curl http://localhost:8100/health` |

**No tests exist in this repo.** All verification is manual via curl. No CI, no Dockerfile, no Makefile.

## Setup

```bash
conda create -n agent python=3.11; conda activate agent
pip install -r backend/requirements.txt
pip install -r mcp_schema_server/requirements.txt
cd frontend && npm install
```

PostgreSQL: `docker run -d --name ragent-postgres -e POSTGRES_USER=ragent -e POSTGRES_PASSWORD=ragent -e POSTGRES_DB=ragent -p 5432:5432 pgvector/pgvector:0.7.0-pg15`
Init PG: `docker exec -i ragent-postgres psql -U ragent -d ragent < backend/scripts/init_pg.sql`

**.env** (create manually, no `.env.example`): `MINIMAX_API_KEY`, `SILICONFLOW_API_KEY`, `DATABASE_URL`, `LLM_MODEL`, `LLM_BASE_URL`, `EMBEDDING_DIM` (must be 1536 to match `VECTOR(1536)` in `init_pg.sql`).

## Code Style — Python (Backend)

- **Imports:** stdlib first, then third-party, then local. Group with blank lines. Use `from __future__ import annotations` at top of every file. Prefer `from X import Y` over `import X` when Y is a class/function used directly.
- **Types:** Always annotate function signatures. Use `|` for unions (`str | None` not `Optional[str]`) consistently. Use `BaseModel` from pydantic for data contracts. Prefer `list[dict]` over `List[dict]`.
- **Naming:** `snake_case` for functions/variables, `PascalCase` for classes, `UPPER_SNAKE` for constants/module-level configs. Private helpers prefixed with `_`.
- **Error handling:** Catch specific exceptions; use `logger.warning` for recoverable errors, `raise` for fatal. Avoid bare `except:`. Log exceptions with `%s` formatting (not f-strings in log calls).
- **Patterns:** Module-level lazy singletons (e.g. `_conn_rw: DuckDBPyConnection | None = None` with getter). Globals are acceptable for connections.
- **Docstrings:** Optional. Keep them short when present. Comments in Chinese when addressing domain concepts, English for technical notes.

## Code Style — TypeScript/React (Frontend)

- **Imports:** React/external first, then local relative imports. Use `import type` for type-only imports. Group with blank lines.
- **Types:** Prefer `interface` for Props/State shapes, `type` for unions/utility types. Use `Record<string, unknown>` for dynamic data. Use `as const` on literal type assertions. Enable `verbatimModuleSyntax` — always use `import type` for type-only imports.
- **Naming:** `PascalCase` for components and interfaces, `camelCase` for functions/variables, `UPPER_SNAKE` for constants. File names match default export (e.g. `ChartBlock.tsx` exports `ChartBlock`).
- **Components:** Default export function components. Props interface named `Props` (local to file). Use `interface Props { block: ReportBlock }` pattern. Avoid class components.
- **Styling:** Inline `style={{}}` objects (no CSS modules/tailwind). Use Ant Design `Typography.Text`, `Typography.Title`, etc. for text styling. Color tokens: `#1677ff` (primary blue), `#e8e8e8` (border), `#f0f0f0` (divider), `#8f959e`/`#646a73` (secondary text), `#1f2329` (body text).
- **Error handling:** Use `try/catch` with `err instanceof Error` type narrowing. Fallback rendering with `Text type="secondary"`. `catch { /* ignore */ }` for non-critical JSON parsing.
- **Imports from `antd`:** Destructure specific components (`import { Typography, Button } from 'antd'`). Avoid `import antd from 'antd'`.
- **State management:** Zustand stores only. No Redux or Context. Store interfaces defined inline in the `create<>()` call.

## Agent Graph (Parent + 3 SubGraphs)

```
User Query → security_guard (score ≥ 3 → block)
  ├─ 闲聊 → END
  └─ 报表 → data_agent → sql_agent → evaluate
       ├─ SUCCESS → report_agent → END
       └─ NEED_CLARIFICATION → clarify_node (interrupt) → data_agent
```

Rules:
- `clarify` is the **only** node calling `interrupt()` — SubGraphs never interrupt
- SubGraphs run via `.ainvoke()` inside parent nodes (not LangGraph sub-graphs)
- Checkpoint saves Parent State only (SubStates are ephemeral)
- `original_query` = first user message frozen; `current_query` = enhanced with clarification context

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

## Known Quirks

- `__init__.py` files are intentionally 0 bytes (namespace packages, Python 3.3+)
- `infra/trace/repository.py` uses raw `asyncpg` (not `infra/db/postgres.py` pool) — pool integration not yet wired
- MCP Schema Server's `registry.py` and `app/tools/data_tools.py` are **two independent** keyword schema-matching implementations
- `POST /api/v1/chat` `session_id` doubles for new session and checkpoint resume
- Frontend TypeScript ~6.0 (very new); lint uses oxlint, not eslint
- Embedding uses SiliconFlow API (`.env`: `SILICONFLOW_API_KEY`), separate from LLM (MiniMax)
- Session persisted in `localStorage` key `ragent_session_id` (Zustand, `stores/session.ts`)
- No Cursor rules (`.cursor/rules/`) or Copilot instructions (`.github/copilot-instructions.md`) exist
