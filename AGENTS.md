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
| Frontend typecheck | `cd frontend && npx tsc -b` |

## Testing

**Backend (pytest):** Run from `backend/`. `asyncio_mode=auto`, `asyncio_default_fixture_loop_scope=session`, `testpaths=tests`, strict markers.

| Command | Purpose |
|---------|---------|
| `pytest` | Full offline suite |
| `pytest -m smoke` | Smoke tests only |
| `pytest -m contracts` | Frontend/backend contract parity |
| `pytest -m graphs` | LangGraph tests |
| `pytest -m persistence` | PostgreSQL-dependent tests (auto-skip if `DATABASE_URL` unset) |
| `pytest tests/smoke/test_models.py -k "keyword"` | Single file + keyword filter |
| `python -m pytest tests/e2e/test_full_flow.py -s` | E2E (requires full stack) |

Markers in `backend/pytest.ini`: `smoke`, `persistence`, `graphs`, `contracts`, `api`, `e2e`.

**Frontend (vitest):** Run from `frontend/`. jsdom environment, `src/**/__tests__/` dir.

| Command | Purpose |
|---------|---------|
| `npm run test:run` | Vitest one-shot |
| `npm run test` | Vitest watch mode |
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
- **Naming:** `snake_case` for functions/variables, `PascalCase` for classes, `UPPER_SNAKE` for constants. Private helpers prefixed with `_`.
- **Error handling:** Catch specific exceptions; never bare `except:`. Use `raise ... from exc` for chaining. Define custom exceptions as module-level classes. `logger.warning` for recoverable, `logger.error` + `raise` for fatal. Use `%s` formatting in logger calls (never f-strings).
- **Async:** All I/O-bound functions are `async def`. Use `async with pool.acquire() as conn: async with conn.transaction():` for DB writes. Pure transformations remain sync.
- **Logger:** Module-level `logger = logging.getLogger(__name__)`. Log with `%s` formatting (e.g. `logger.info("msg %s", var)`), not f-strings.
- **Database:** `asyncpg` uses `$1` parameter binding. All writes scoped by `(user_id, session_id)` from JWT context (never request body). Services use keyword-only args with `*`.
- **Lint:** `ruff check` equivalent (no config file present; code manually formatted to consistent 4-space indent, ~100-120 char lines).
- **Docstrings:** Module-level triple-quoted docstrings on most files. Function docstrings for non-trivial logic. Inline `# NOTE:` and `# --- Section ---` markers for organization.
- **`__init__.py`:** All intentionally 0 bytes (namespace packages).

## Code Style — TypeScript/React (Frontend)

- **Imports:** React/external first, then local relative. No `@/` alias — all imports are relative paths. `verbatimModuleSyntax: true` in tsconfig, so use `import type` for type-only imports.
- **Types:** `interface` for Props, State, and data shapes. `type` for unions, string literals, and type aliases. Discriminated unions on `type` field for actions and SSE events. Use `ReadonlySet` for validation constants. Prefer `X | null` over `Optional`.
- **Components:** Default export function components. Props interface named `Props` (local per file, not exported). No class components. `forwardRef` uses named function expressions.
- **State:** Zustand with `immer` middleware. Pure `analysisReducer` function is the single source of truth — React components never write `phase` directly. Actions are discriminated unions with `type: 'domain/verb'` (e.g. `'phase/received'`, `'report/received'`). Auth store uses `zustand/middleware/persist` to `localStorage` key `ragent_auth`.
- **Styling:** CSS custom properties from `src/styles/tokens.css` + `src/styles/workbench.css`. BEM-like naming: `atelier-*` for shared UI kit, `wb-*` for workbench. No hardcoded hex values outside tokens.css. No `!important`. Inline `style` only for truly dynamic values.
- **Design tokens** (single source of truth in `tokens.css`): `--ink`, `--teal`, `--paper`, `--canvas`, `--rail`, `--muted`, `--amber`, `--red`, `--green`, `--font-display`, `--font-ui`, `--font-mono`, `--sp-*` spacing scale.
- **State machine:** `AnalysisPhase` transitions live in both backend and frontend reducer. Frontend mirrors backend SSE `phase` events.
- **Lint:** oxlint (not eslint). Rules in `.oxlintrc.json`: `react/rules-of-hooks: error`, `react/only-export-components: warn`.
- **Naming conventions:** `use{Name}Store` for hooks, `{Name}Store` for store interfaces, `{Name}State` for state interfaces, `initial{Name}State` for initial state, `is{TypeName}` for type guards, `{domain}Reducer` for reducer functions. Test files colocated in `src/**/__tests__/` with `.test.ts`/`.test.tsx` extension.
- **TypeScript config:** `tsconfig.app.json` has `noUnusedLocals: true`, `noUnusedParameters: true`, `erasableSyntaxOnly: true`, `verbatimModuleSyntax: true`.

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

## Git Conventions

- Conventional Commits: `feat(scope): msg`, `fix(scope): msg`, `docs: msg`, `chore(scope): msg`, `test(scope): msg`, `refactor: msg`, `style(scope): msg`.
- English messages, lowercase after colon, no period at end.

## Design Documentation

Every non-trivial design decision must be documented in `docs/plans/` before implementation.

### Language

All plan documents must be written in Chinese. Rationale: the domain language and end-user communication are all in Chinese; plans are internal design records best kept in the same language as the domain.

### Naming

```
docs/plans/YYYY-MM-DD-short-description.md
```

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

- **High cohesion**: related behavior stays together; one file/module = one responsibility
- **Low coupling**: modules communicate through narrow, well-defined interfaces (models, repos, services), not by sharing internal state
- **Design docs are immutable records**: append amendments; never rewrite history
- **Every plan must be filed before code changes begin**

## Known Quirks

- `__init__.py` files are intentionally 0 bytes (namespace packages)
- `infra/trace/repository.py` uses the shared `asyncpg` pool (`infra/db/postgres.py` `get_pool`) for both trace writes and the read-only observability queries (`list_traces`/`get_trace`/`get_spans`/`get_llm_calls`/`get_metrics`) — it no longer opens its own connection
- MCP Schema Server's `registry.py` and `app/tools/data_tools.py` are **two independent** keyword schema-matching implementations
- Session persisted in `localStorage` key `ragent_session_id` (Zustand, 24h TTL). Auth store key: `ragent_auth`.
- Frontend TypeScript ~6.0; lint uses oxlint, not eslint
- TSD-encrypted `.py` files: read via `git show HEAD:<path>` instead of working tree bytes
- Embedding uses SiliconFlow API (`.env`: `SILICONFLOW_API_KEY`), separate from LLM (MiniMax). Falls back to `ILIKE ANY($1::text[])` on failure.
- `.env` must set `EMBEDDING_DIM=1536` to match `VECTOR(1536)` in `init_pg.sql`
- Startup verifies embedding dimension; failure degrades to keyword matching (non-blocking)
- No `pyproject.toml`, `Makefile`, `docker-compose.yml`, or CI configs exist
- test files use module-level `pytestmark = pytest.mark.<marker_name>` and fixtures in `tests/conftest.py` (dummy_jwt_user, mock_pool, pg_pool)
- `backend/app/__init__.py` is 0 bytes
