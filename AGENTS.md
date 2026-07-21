# ReportAgent — Agent Instructions

## Architecture

**Two services** — start MCP Schema Server first, then ReportAgent:

1. `python -m mcp_schema_server.server` (port auto)
2. `cd backend && uvicorn app.main:app --port 8100 --reload`

```
User ←SSE→ FastAPI+LangGraph (:8100) ←MCP→ MCP Schema Server (:8101) → DuckDB (read-only)
                                                                  → PostgreSQL (session+trace+memory)
```

DuckDB auto-seeds from `backend/seed_data.sql` on first connect (retail star schema, 6 dims + 4 facts).

## Setup Essentials

- **PostgreSQL** via Docker: `pgvector/pgvector:0.7.0-pg15`, port 5432, user/pass/db=`ragent`
- Init PG schema: `docker exec -i ragent-postgres psql -U ragent -d ragent < backend/scripts/init_pg.sql`
- No `.env.example` — create `.env` manually with `MINIMAX_API_KEY`, `SILICONFLOW_API_KEY`, `DATABASE_URL`, `LLM_MODEL`
- Embedding vector dimension: **1536** (configurable via `EMBEDDING_DIM` in `.env`; must match `init_pg.sql`)
- Install both: `pip install -r backend/requirements.txt` (15 deps, includes `sqlglot` for SQL AST safety)
- **No tests exist** — all verification via curl

## TSD-Encrypted Source Files

Many `.py` files show `%TSD-Header-###%` and are unreadable in the working tree. Use `git show HEAD:<path>` to read decrypted content. Only `main.py`, `llm.py`, and empty `__init__.py` files are readable directly.

## Agent Graph (Parent + 3 SubGraphs)

```
User Query → classify_intent
  ├─ "闲聊" → END
  └─ "报表/看板" → data_agent → sql_agent → evaluate
       ├─ SUCCESS → report_agent → END
       └─ NEED_CLARIFICATION → clarify_node (interrupt) → data_agent
```

Key rules:
- `clarify` is the **only** node that calls `interrupt()` — SubGraphs never interrupt
- SubGraphs run via `.invoke()` inside parent nodes (synchronous, not LangGraph sub-graphs)
- Checkpoint saves **Parent State only** — SubStates are ephemeral
- `original_query` = first user message (frozen for memory storage); `current_query` = augmented with clarification context

## SQL Retry Logic

**Internal (sql_graph):** syntax error → regenerate (up to 3×), schema error → re-plan (1×), exhausted → `NEED_CLARIFICATION`

**Parent:** failed SQL → retry `sql_agent` (up to 3×) instead of routing to `report_agent`. This prevents hallucination on null SQL results.

## SQL Safety (3-layer)

1. Blacklist: reject non-SELECT (DDL/DML keywords)
2. AST parse: `sqlglot` verifies parsed statement is a `Select`
3. EXPLAIN: `EXPLAIN <sql>` to catch DuckDB-specific syntax errors

## Memory System (MemoryManager)

Unified entry point at `infra/memory/memory_manager.py`. Two backends:

- **QueryMemory** (`memory.query_template`): pgvector semantic + keyword search with ranking (`semantic×0.5 + success_rate×0.3 + freq×0.1 + recency×0.1`). Tracks `access_count`, `failure_count`, `verified`.
- **UserMemory** (`memory.semantic_entry`): stores user preferences/insights. Ranking (`semantic×0.6 + importance×0.2 + freq×0.1 + recency×0.1`). Fields: `memory_type` (`stable_preference`/`temporary_preference`/`insight`), `importance_score`.

Both fix SQL injection — keyword fallback uses `LIKE ANY($1::text[])` parameterized.

Mem0 (optional) still available via `backend/app/memory.py` with `MEM0_ENABLED=true`.

## Known Quirks

- `__init__.py` files are intentionally empty (0 bytes) — Python 3.3+ namespace packages
- `infra/trace/repository.py` uses raw `asyncpg` (not the pool from `infra/db/postgres.py`) — code is effectively dead until sdk.py uses the pool
- MCP Schema Server's `registry.py` and `app/tools/data_tools.py` are **two independent implementations** of keyword-based schema matching
- `POST /api/v1/chat` with `session_id` for new session creation or checkpoint resume
