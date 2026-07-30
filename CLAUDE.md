# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 沟通语言

始终用**中文**回复用户。代码、文件路径、类型名、函数名、错误码、命令、SQL 片段保持原文，不要翻译。

## 配套文档

- 详细的 Python / TypeScript 代码风格规范、SSE 事件协议、Git 提交约定、已知坑（Known Quirks）见 [AGENTS.md](AGENTS.md)。本文件与其冲突时以 AGENTS.md 的代码风格部分为准，避免两处重复维护。
- API 端点、SSE v2 事件载荷、工作台界面约定见 [README.md](README.md)。

## 开发前必读（plan 驱动）

任何非平凡改动（≥2 文件或 ≥1 设计决策）动手前，必须先：

1. 读**固定入口** [docs/plans/README.md](docs/plans/README.md)（永久索引，唯一入口——**不要按日期找 plan**）。
2. 在索引「进行中」区定位本次任务对应的 plan；找不到就 `grep -rl "^> 状态: 进行中" docs/plans/`（锚定行首的 `> 状态:` 标记行，避免命中索引正文）。
3. 以该 plan 的「设计 / 复用工具 / 明确不做」为硬约束：复用优先、不越界、错误路径按 plan 枚举实现。
4. 开新 plan：命名 `docs/plans/YYYY-MM-DD-<slug>.md`，顶部写 `> 状态: 进行中`，并登记进 README.md 索引。
5. plan 落地后状态改 `已完成`（带 commit），被合并/取代的改 `已归档` 并注明并入哪份。

状态机：`进行中 → 已完成 → 已归档`；另有 `暂缓`（已批准但搁置）与 `只读评审`（review/grill 类）。

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

## Planning Discipline

> **Every multi-step change leaves a plan behind.** No "I'll just quickly do X" — anything that touches ≥2 files or ≥1 design decision is planned first, written to a file, and tracked.

### Where plans live

Plans live in two mirrored locations during a session, but the **canonical, traceable copy** is always in the repo:

| Location | Purpose |
|---|---|
| `~/.claude/plans/<token>.md` | Live plan edited while in plan mode (Claude Code internal). Throwaway. |
| `docs/plans/YYYY-MM-DD-<topic-slug>.md` | Canonical plan committed to git. The only one that survives the session. |

When plan mode is invoked, after writing the initial plan to `~/.claude/plans/`, copy it to `docs/plans/`. When the plan evolves during execution, edit the in-repo copy. On commit, the in-repo plan goes in alongside the code change — same commit, message `feat|fix(<scope>): <title> + plan: <topic-slug>`.

### Naming convention

```
docs/plans/YYYY-MM-DD-<topic-slug>.md
```

Rules (do not relax these):

1. **Date prefix is `YYYY-MM-DD`**, ISO 8601, zero-padded, today's date in the user's local timezone. One plan per day per topic — if a topic rolls past midnight, add `-v2` (and `-v3`, …) instead of changing the date.
2. **`<topic-slug>` is kebab-case**, 2–6 words, derived from the feature or fix area (e.g. `sql-row-cap-and-export`, `confirmed-exec-three-state`, `workbench-shell-css-rebuild`).
3. **No random suffixes.** The token-style `<adjective>-<verb>-<noun>.md` files Claude Code generates for its own scratchpad are **not** reused as canonical names — they look like noise in `git log -- docs/plans/`.
4. **One topic per file.** If you find yourself adding a section that doesn't fit, it's a new plan — split it.
5. **Same slug in the commit and the PR title** so `git log --grep "<slug>"` retrieves every artifact of the change.

### Required structure

Each `docs/plans/*.md` file must contain these sections in this order. Skipping one is a defect; "I'll fix it later" never wins.

1. **Context** — the why: what triggered this, what the user is trying to achieve, what the current code does that's wrong or missing. Reuse the original prompt so the plan is interpretable without the session context.
2. **Design** — what we're building and how the pieces fit. Reference the modules touched (file paths, no line numbers — they rot).
3. **Files to change** — explicit list, with the *pattern* of change described once and a representative path or two. Not every file. The reviewer should know the blast radius without opening the diff.
4. **Reused existing utilities** — name them with paths. Stop proposing new code when `app.tools.sql_tools._classify_psycopg2_error` already does it. Reuse is the design.
5. **Verification** — how to prove it works end-to-end. List the exact test commands and the manual smoke-test matrix. No "tests pass" as a one-liner.
6. **Explicitly NOT doing** — the inverse of scope. Every "we considered X and decided against it because Y" goes here.

### Language

Plans are artifacts for **humans** (you, reviewers, future you). Write in **Chinese**. Keep code, file paths, type names, function names, error codes, and SQL fragments in their original form — those are the *ground truth* and translating them would lose precision. Everything else: prose, rationales, alternatives-considered bullets, table cell descriptions → Chinese. A reviewer should be able to skim a plan in 中文 without context-switching.

### Design quality bar (this is the rubric, not a wish list)

A plan is acceptable only when **all** of the following are true. If any one fails, the plan is rejected and rewritten — do not patch around it.

- **Single responsibility per change.** One plan, one coherent feature or fix. If the implementation touches two unrelated subsystems, the plan is actually two plans.
- **High cohesion.** All edits stay inside one module's responsibility. A change to error classification lives in `sql_tools.py` + the type that consumes it; it does not bleed into `main.py` HTTP plumbing.
- **Low coupling.** New modules expose a small, intent-revealing surface; existing callers adapt through that surface, not by reaching into private fields. When the plan introduces a new helper (e.g. `_build_sse_error`), the caller count and the call-site changes are listed in the plan.
- **No drive-by edits.** Cosmetic reformatting, unrelated test rewrites, or "while we're here" tweaks belong in a follow-up plan.
- **Reuse over reinvention.** Find the existing function/component/type first. Quote its path. If you can't find it, you've looked in the wrong place — search the repo before proposing a new utility.
- **Interface is the contract, not the implementation.** A plan that says "pass SQL as `state['sql']`" without saying who sets it, who reads it, and what happens when it's empty, is not a plan.
- **Errors are first-class.** Every error code path the change touches is enumerated (kind → user-visible message → persistence row → test). This is the level of detail that turns "the agent hides errors" into a specific fixable claim.
- **Naming carries intent.** Variables, types, files: a reader should know what it is before reading what it does. `execution_status` beats `status`; `persist_error_run` beats `mark_failed`.

### Workflow

1. **Trigger.** User says "let me plan X", asks for a non-trivial feature, or you yourself decide the change is non-trivial (≥2 files, ≥1 design decision, or any new module).
2. **Brainstorm before planning.** Follow `superpowers:brainstorming` if the idea is fuzzy; skip only for well-specified ask-the-expert prompts.
3. **Write the in-repo plan first**, dated, slugged. The `~/.claude/plans/<token>.md` scratch copy exists for plan-mode ergonomics; mirror to in-repo before `ExitPlanMode`.
4. **Validate the quality bar.** Self-review against the "Design quality bar" list above. Fix issues inline.
5. **Hand to user.** `ExitPlanMode` only — do not skip with prose.
6. **After approval, the plan stays.** Do not delete or rewrite after the commit unless the commit referenced it. `git log --follow docs/plans/<slug>.md` is the audit trail.

### Quick commands

```bash
# New plan today, on topic "sql-row-cap-and-export"
NEW_PLAN="docs/plans/$(date +%Y-%m-%d)-sql-row-cap-and-export.md"
mkdir -p docs/plans
touch "$NEW_PLAN"
$EDITOR "$NEW_PLAN"

# Find a plan back later
git log --all --diff-filter=A --name-only -- docs/plans/ | grep -v '^$'
git log --all --follow --oneline -- docs/plans/2026-07-30-sql-row-cap-and-export.md
```
