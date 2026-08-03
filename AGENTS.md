# ReportAgent — Agent Instructions

## Startup Order (CRITICAL)

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

**PostgreSQL schemas:** `public` (6 dim + 4 fact tables via `seed_pg.sql`, data 2020–2024), `app` (users, conversations, templates), `agent` (session, requirement_draft, report_version append-only), `memory` (query_template VECTOR(1536), semantic_entry), `observability` (trace spans, LLM calls).

**Two graphs (v2 active path):** `requirement_analysis_graph` exposes ONLY schema tools (`search_tables`/`get_table_ddl`/`list_tables`) and produces a `RequirementCard`; `confirmed_execution_graph` gates (status=complete, no missing fields, assumptions resolved, owner check) → locks draft → schema → sql_agent (plan→generate→validate(EXPLAIN)→execute) → report_agent → persist_report. Legacy single graph (`parent_graph.py`, `mode=legacy`) kept for one compat cycle.

## Setup & Environment

Requirements: Python 3.11, Node.js 18+, Docker, MiniMax + SiliconFlow API keys. Create `.env` at repo root (see `backend/.env.example`).

```bash
conda create -n agent python=3.11 && conda activate agent
pip install -r backend/requirements.txt -r backend/requirements-dev.txt -r mcp_schema_server/requirements.txt
cd frontend && npm install

docker run -d --name ragent-postgres -e POSTGRES_USER=ragent -e POSTGRES_PASSWORD=ragent \
  -e POSTGRES_DB=ragent -p 5432:5432 pgvector/pgvector:0.7.0-pg15
docker exec -i ragent-postgres psql -U ragent -d ragent < backend/scripts/init_pg.sql
docker exec -i ragent-postgres psql -U ragent -d ragent < backend/scripts/seed_pg.sql
```

Key `.env` vars: `LLM_API_KEY` (falls back to `MINIMAX_API_KEY`), `LLM_MODEL`, `LLM_BASE_URL` (MiniMax), `SILICONFLOW_API_KEY` + `EMBEDDING_MODEL` (must match `EMBEDDING_DIM=1536`/`VECTOR(1536)`), `DATABASE_URL`, `JWT_SECRET`, `DEFAULT_USERNAME`/`DEFAULT_PASSWORD`, `APP_ENV` + `ALLOW_INSECURE_DEFAULT_AUTH=1` (dev escape hatch), `MEM0_ENABLED` (optional L3 extractor, default false). Auth startup gate is fail-closed: non-dev envs refuse to start with weak secret/password.

## Commands

| Purpose | Command |
|---------|---------|
| Backend | `cd backend && uvicorn app.main:app --port 8100 --reload` |
| Frontend dev | `cd frontend && npm run dev` |
| Frontend build | `cd frontend && npm run build` (tsc -b && vite build) |
| Frontend lint | `cd frontend && npm run lint` (oxlint, not eslint) |
| Frontend typecheck | `cd frontend && npx tsc -b` |
| Frontend preview | `cd frontend && npm run preview` |

## Testing

**Backend (pytest):** run from `backend/`. `asyncio_mode=auto`, `asyncio_default_fixture_loop_scope=session`, `testpaths=tests`, `--strict-markers`. Markers: `smoke`, `persistence`, `graphs`, `contracts`, `api`, `e2e`. `conftest.py` auto-skips persistence without `DATABASE_URL` and e2e without `REPORTAGENT_E2E`.

| Command | Purpose |
|---------|---------|
| `pytest` | Full offline suite |
| `pytest -m graphs` | LangGraph tests only (also `-m smoke`/`-m contracts`/`-m persistence`/`-m api`) |
| `pytest tests/smoke/test_models.py` | Single file |
| `pytest tests/smoke/test_models.py -k "keyword"` | Single file + test-name filter |
| `python -m pytest tests/e2e/test_full_flow.py -s` | E2E (needs full stack + real LLM keys) |

**Frontend (vitest):** run from `frontend/`. jsdom env, `vitest/globals`, tests colocated in `src/**/__tests__/` as `*.test.ts`/`*.test.tsx`.

| Command | Purpose |
|---------|---------|
| `npm run test:run` | Vitest one-shot |
| `npm run test` | Vitest watch mode |
| `npx vitest --run src/stores/__tests__/analysisReducer.test.ts` | Single file |
| `npx vitest --run src/stores/__tests__/analysisReducer.test.ts -t "test name"` | Single file + test name |

**Manual API test:**
```bash
curl -X POST http://localhost:8100/api/v1/auth/login -H "Content-Type: application/json" -d '{"username":"admin","password":"admin123"}'
curl -X POST http://localhost:8100/api/v1/chat -H "Content-Type: application/json" -H "Authorization: Bearer <token>" -d '{"user_query":"今年华东销售趋势","session_id":"test-1"}'
curl http://localhost:8100/health
```

## Code Style — Python (Backend)

- **`from __future__ import annotations`** at the very top of every file (line 1), before all other imports.
- **Imports:** stdlib first, then third-party, then local. Group with blank lines between blocks.
- **Types:** Always annotate function signatures. Prefer `X | None` over `Optional[X]`. Use `TypedDict` for graph state, Pydantic `BaseModel` for data contracts. Use `Literal` for status/phase constants.
- **Pydantic v2:** `Field(default_factory=...)` for mutable defaults. `@model_validator(mode="after")` for cross-field validation. Use `.model_dump(mode="json")` and `.model_validate()` for serialization.
- **Naming:** `snake_case` functions/variables, `PascalCase` classes, `UPPER_SNAKE` constants. Private helpers prefixed with `_`.
- **Error handling:** Catch specific exceptions; never bare `except:`. Use `raise ... from exc` for chaining. Define custom exceptions as module-level classes. `logger.warning` for recoverable, `logger.error` + `raise` for fatal.
- **Async:** I/O-bound functions are `async def`. Use `async with pool.acquire() as conn: async with conn.transaction():` for DB writes. Pure transformations remain sync. (Analysis SQL uses sync `psycopg2` in `tools/sql_tools.py`; app persistence uses the shared `asyncpg` pool in `infra/db/postgres.py`.)
- **Logger:** Module-level `logger = logging.getLogger(__name__)`. Log with `%s` formatting (e.g. `logger.info("msg %s", var)`), never f-strings.
- **Database:** `asyncpg` uses `$1` parameter binding. All writes scoped by `(user_id, session_id)` from JWT context (never request body). Services use keyword-only args with `*`.
- **Lint:** no config file present; code manually formatted to consistent 4-space indent, ~100-120 char lines.
- **Docstrings:** Module-level triple-quoted docstrings on most files. Function docstrings for non-trivial logic. Inline `# NOTE:` and `# --- Section ---` markers for organization.
- **`__init__.py`:** All intentionally 0 bytes (namespace packages).

## Code Style — TypeScript/React (Frontend)

- **Imports:** React/external first, then local relative. No `@/` alias in code (defined only in `vitest.config.ts`, unused) — all imports are relative paths. `verbatimModuleSyntax: true`, so use `import type` for type-only imports.
- **Types:** `interface` for Props, State, and data shapes; `type` for unions, string literals, and aliases. Discriminated unions on `type` field for actions and SSE events. Use `ReadonlySet` for validation constants. Prefer `X | null` over `Optional`.
- **Components:** Default export function components. Props interface named `Props` (local per file, not exported). No class components. `forwardRef` uses named function expressions.
- **State:** Zustand with `immer` middleware. Pure `analysisReducer` is the single source of truth — components never write `phase` directly. Actions are discriminated unions with `type: 'domain/verb'` (e.g. `'phase/received'`). Auth store uses `zustand/middleware/persist` to `localStorage` key `ragent_auth`.
- **Styling:** CSS custom properties from `src/styles/tokens.css` (design tokens: `--ink`, `--teal`, `--paper`, `--canvas`, `--rail`, `--muted`, `--amber`, `--red`, `--green`, `--font-*`, `--sp-*` spacing scale) + `src/styles/workbench.css`. BEM-like naming: `atelier-*` for shared UI kit, `wb-*` for workbench. No hardcoded hex outside tokens.css. No `!important`. Inline `style` only for truly dynamic values.
- **State machine:** `AnalysisPhase` transitions live in both backend and frontend reducer. Frontend mirrors backend SSE `phase` events.
- **Lint:** oxlint (not eslint). Rules in `.oxlintrc.json`: `react/rules-of-hooks: error`, `react/only-export-components: warn`.
- **Naming:** `use{Name}Store` hooks, `{Name}Store` store interfaces, `{Name}State` state interfaces, `initial{Name}State` initial state, `is{TypeName}` type guards, `{domain}Reducer` reducers. Tests colocated in `src/**/__tests__/` with `.test.ts`/`.test.tsx` extension. TypeScript config (`tsconfig.app.json`): `noUnusedLocals`, `noUnusedParameters`, `erasableSyntaxOnly`, `verbatimModuleSyntax` all on.

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

**v2 flow:** `POST /api/v1/chat` with `mode: new|supplement` → `phase` → `requirement` → PATCH requirement → `POST /confirm` → `phase: generating` → `report` → `done`.
**Legacy flow:** `mode=legacy` → `intent_card` → user picks tool → `chosen_tool` in next request → SQL/report → `report` → `done`.

## Agent Graph (Legacy)

```
User Query → security_guard (score ≥ 3 → block)
  ├─ 闲聊 → END
  └─ 报表 → data_agent → sql_agent → evaluate
       ├─ SUCCESS → report_agent → END
       └─ NEED_CLARIFICATION → clarify_node (interrupt) → data_agent
```

- `clarify` is the only node calling `interrupt()`. SubGraphs run via `.ainvoke()` (not LangGraph sub-graphs). Checkpoint saves Parent State only. `original_query` frozen; `current_query` enhanced with clarification context.

## SQL Safety (3 Layers)

1. Blacklist: reject non-SELECT (DDL/DML keywords)
2. AST: `sqlglot` verifies parsed result is `Select`
3. EXPLAIN: run `EXPLAIN <sql>` before execution

## Git Conventions

- Conventional Commits: `feat(scope): msg`, `fix(scope): msg`, `docs: msg`, `chore(scope): msg`, `test(scope): msg`, `refactor: msg`, `style(scope): msg`.
- English messages, lowercase after colon, no period at end.

## Plan-Driven Development

**Every non-trivial change (≥2 files or ≥1 design decision) is planned first** — no "I'll just quickly do X". Plans live in two mirrored locations during a session; the canonical, traceable copy is always in the repo:

| Location | Purpose |
|---|---|
| `docs/plans/README.md` | **Permanent index — the only entry point. Do NOT find plans by date.** |
| `docs/plans/YYYY-MM-DD-<topic-slug>.md` | Canonical plan committed to git. The only one that survives the session. |

### Workflow

1. **Read `docs/plans/README.md` first** — locate the task's plan in the 「进行中」(in-progress) table.
2. Not found → `grep -rl "^> 状态: 进行中" docs/plans/` (anchor the line-start `> 状态:` marker to avoid hitting index prose).
3. Treat that plan's 「设计 / 复用工具 / 明确不做」 as hard constraints: reuse first, don't overreach, implement error paths as enumerated.
4. New plan: name `docs/plans/YYYY-MM-DD-<slug>.md`, top line `> 状态: 进行中`, register it in the README.md index.
5. On landing: change status to `已完成` (with commit) and move to the completed table; merged/superseded plans → `已归档` with a note on what they folded into.

State machine: `进行中 → 已完成 → 已归档`; plus `暂缓` (approved but shelved) and `只读评审` (review/grill only).

### Template

Each plan doc uses these sections:

| Section | Purpose |
|---------|---------|
| `# Title`（标题） | One-line summary of the decision/plan |
| `## Context`（背景） | Why this change exists, what problem it solves, prior discussion |
| `## Design`（设计） | The chosen approach with rationale. MUST reference real schemas and real code paths — never fabricate APIs or data. |
| `## Files to change`（文件改动） | Concrete file list with one-liner per file |
| `## Reused existing utilities`（复用工具） | What existing code is leveraged (not rewritten) |
| `## Verification`（验证） | How to test, including manual test matrix |
| `## Explicitly NOT doing`（不做事项） | What is deliberately out of scope |

### Principles

- **Language:** All plan documents in **Chinese** (domain + end-user language is Chinese; keep code, file paths, type names, error codes, SQL in original form).
- **High cohesion / low coupling**: modules communicate through narrow, well-defined interfaces, not shared internal state.
- **Design docs are immutable records**: append amendments; never rewrite history.
- **Design quality bar**: single responsibility per change; no drive-by edits; reuse over reinvention; errors are first-class (every error code path enumerated: kind → user-visible message → persistence row → test); naming carries intent.

## Known Quirks

- `infra/trace/repository.py` uses the shared `asyncpg` pool (`infra/db/postgres.py` `get_pool`) for both trace writes and read-only observability queries — no separate connection
- MCP Schema Server's `registry.py` and `app/tools/data_tools.py` are **two independent** keyword schema-matching implementations; local tools provide the fallback when MCP is down
- Session persisted in `localStorage` key `ragent_session_id` (Zustand, 24h TTL). Auth store key: `ragent_auth`
- Frontend TypeScript ~6.0; lint uses oxlint, not eslint
- TSD-encrypted `.py` files: read via `git show HEAD:<path>` instead of working tree bytes
- Embedding uses SiliconFlow API (`.env`: `SILICONFLOW_API_KEY`), separate from LLM (MiniMax). Falls back to `ILIKE ANY($1::text[])` on failure; startup degrades to keyword matching (non-blocking) if embedding dimension mismatches
- No `pyproject.toml`, `Makefile`, `docker-compose.yml`, or CI configs exist
- Backend test files use module-level `pytestmark = pytest.mark.<marker_name>`; fixtures in `tests/conftest.py` (`dummy_jwt_user`, `mock_pool`, `pg_pool`)
