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
                                         PostgreSQL (all data)
```

**PostgreSQL schemas:** `public` (dim/fact tables via `seed_pg.sql`), `app` (users, conversations), `agent` (session, requirement_draft, report_version), `memory` (query_template VECTOR(1536), semantic_entry), `observability` (trace spans, LLM calls).

## Commands

| Purpose | Command |
|---------|---------|
| MCP start | `python -m mcp_schema_server.server` |
| Backend | `cd backend && uvicorn app.main:app --port 8100 --reload` |
| Frontend dev | `cd frontend && npm run dev` |
| Frontend build | `cd frontend && npm run build` (tsc -b && vite build) |
| Frontend lint | `cd frontend && npm run lint` (oxlint, not eslint) |
| Frontend preview | `cd frontend && npm run preview` |

## Testing

**Backend (pytest):** Run from `backend/`. `asyncio_mode=auto`, `testpaths=tests`, strict markers.

| Command | Purpose |
|---------|---------|
| `pytest` | Full suite |
| `pytest -m smoke` | Smoke tests only |
| `pytest -m contracts` | Frontend/backend contract parity |
| `pytest -m graphs` | LangGraph tests |
| `pytest -m persistence` | PostgreSQL-dependent tests (auto-skip if `DATABASE_URL` unset) |
| `pytest tests/smoke/test_models.py -k "keyword"` | Single file + keyword filter |
| `python -m pytest tests/e2e/test_full_flow.py -s` | E2E (requires full stack) |

Markers defined in `backend/pytest.ini`: `smoke`, `persistence`, `graphs`, `contracts`, `api`, `e2e`.

**Frontend (vitest):** Run from `frontend/`. jsdom environment, `src/**/__tests__/` directory.

| Command | Purpose |
|---------|---------|
| `npm run test:run` | Vitest one-shot |
| `npm run test` | Vitest watch mode |
| `npx vitest --run src/stores/__tests__/analysisReducer.test.ts -t "test name"` | Single file + test name |

**Manual test:**
```bash
curl -X POST http://localhost:8100/api/v1/auth/login -H "Content-Type: application/json" -d '{"username":"admin","password":"admin123"}'
curl -X POST http://localhost:8100/api/v1/chat -H "Content-Type: application/json" -H "Authorization: Bearer <token>" -d '{"user_query":"今年华东销售趋势","session_id":"test-1"}'
curl http://localhost:8100/health
```

## Code Style — Python (Backend)

- **Imports:** stdlib first, then third-party, then local. Group with blank lines. `from __future__ import annotations` at the top.
- **Types:** Always annotate function signatures. Use `X | None` not `Optional[X]`. Use Pydantic v2 `BaseModel` + `Field` + `model_validator`.
- **Naming:** `snake_case` for functions/variables, `PascalCase` for classes, `UPPER_SNAKE` for constants. Private helpers prefixed with `_`.
- **Error handling:** Catch specific exceptions; `logger.warning` for recoverable, `raise` for fatal. Log with `%s` formatting, not f-strings. Avoid bare `except:`.
- **Patterns:** Module-level lazy singletons with explicit getter (e.g. `get_connection()`). Globals OK for connections.
- **Database:** `asyncpg` queries must use `$1` parameter binding (no string concatenation). All writes scoped by `(user_id, session_id)` from JWT context (never request body). Writes belong in a single `async with pool.acquire()` transaction.
- **Lint:** `ruff check` equivalent.

## Code Style — TypeScript/React (Frontend)

- **Imports:** React/external first, then local relative. Use `import type` for type-only imports. `verbatimModuleSyntax` enabled.
- **Types:** Prefer `interface` for Props/State, `type` for unions/utilities. Use discriminated unions over `Record<string, unknown>`. SSE type guards required; no `unknown` into reducer.
- **Components:** Default export function components. Props interface named `Props` (local per file). No class components.
- **State:** Zustand stores with pure reducers. `analysisReducer` is the single source of truth — React components never write `phase` directly. Use immer middleware.
- **Styling:** Use CSS variables from `src/styles/tokens.css` + Ant Design `ConfigProvider` theme (`src/theme/antdTheme.ts`). No inline style magic values. No `!important`. No global CSS selectors.
- **Design tokens** (single source of truth in `tokens.css`): `--ink`, `--teal`, `--paper`, `--canvas`, `--rail`, `--muted`, `--amber`, `--red`, `--green`, `--font-display`, `--font-ui`, `--font-mono`. AntD theme mirrors these exactly.
- **State machine:** `AnalysisPhase` transitions live in both backend and frontend reducer. Frontend mirrors backend SSE `phase` events.
- **Lint:** oxlint (not eslint). `react/rules-of-hooks: error`, `react/only-export-components: warn`.

## SSE Event Protocol

| event | purpose |
|-------|---------|
| `token` | streaming LLM text (report node only, legacy mode) |
| `trace` | agent step update `{step, status, detail}` |
| `thinking` | lightweight "planning" hint pre-SQL |
| `card` | interactive cards (legacy mode: intent_card / options_group / preview_card / confirm_card) |
| `report` | final answer `{answer: {text, table, chart, insight}}` |
| `phase` | workbench phase transition (v2: parsing / awaiting_missing / awaiting_confirm / generating / adjusting / report_ready / error) |
| `requirement` | full `RequirementCard` (v2) |
| `clarify` | clarification question (legacy) |
| `error` | `{code, message, recoverable, failed_action}` |
| `done` | `{final_phase}` |

**v2 flow:** `POST /api/v1/chat` with `mode: new|supplement` → `phase` → `requirement` → PATCH endpoint → `POST /confirm` → `phase: generating` → `report` → `done`.
**Legacy flow:** `mode=legacy` → `intent_card` → user picks tool → `chosen_tool` in next request → SQL/report → `report` → `done`.

## Agent Graph (Legacy)

```
User Query → security_guard (score ≥ 3 → block)
  ├─ 闲聊 → END
  └─ 报表 → data_agent → sql_agent → evaluate
       ├─ SUCCESS → report_agent → END
       └─ NEED_CLARIFICATION → clarify_node (interrupt) → data_agent
```

- `clarify` is the only node calling `interrupt()`. SubGraphs run via `.ainvoke()` (not LangGraph sub-graphs).
- Checkpoint saves Parent State only. `original_query` frozen; `current_query` enhanced with clarification context.

## SQL Safety (3 Layers)

1. Blacklist: reject non-SELECT (DDL/DML keywords)
2. AST: `sqlglot` verifies parsed result is `Select`
3. EXPLAIN: run `EXPLAIN <sql>` before execution

## Known Quirks

- `__init__.py` files are intentionally 0 bytes (namespace packages)
- `infra/trace/repository.py` uses raw `asyncpg` (not `infra/db/postgres.py` pool) — pool integration not wired
- MCP Schema Server's `registry.py` and `app/tools/data_tools.py` are **two independent** keyword schema-matching implementations
- Session persisted in `localStorage` key `ragent_session_id` (Zustand, 24h TTL). Auth store key: `ragent_auth`.
- Frontend TypeScript ~6.0; lint uses oxlint, not eslint
- TSD-encrypted `.py` files: read via `git show HEAD:<path>` instead of working tree bytes
- Embedding uses SiliconFlow API (`.env`: `SILICONFLOW_API_KEY`), separate from LLM (MiniMax). Falls back to `ILIKE ANY($1::text[])` on failure.
- `.env` must set `EMBEDDING_DIM=1536` to match `VECTOR(1536)` in `init_pg.sql`
- Startup verifies embedding dimension; failure degrades to keyword matching (non-blocking)
