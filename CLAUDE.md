# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ReportAgent is an AI-powered natural-language-to-report system. Users ask data questions in Chinese chat, a LangGraph agent generates/executes SQL against DuckDB, and returns results as tables + charts + insights.

**Architecture:**
```
User ←SSE→ FastAPI + LangGraph Agent (:8100) ←MCP→ MCP Schema Server (:8101)
                                                          |
                                                     DuckDB (read-only)
                                                          |
                                                     PostgreSQL (memory + trace)
```

Two backend services:
- **ReportAgent** (:8100) — FastAPI + LangGraph, SQL generation/execution, chart/insight assembly
- **MCP Schema Server** (:8101) — standalone MCP server for on-demand table schema discovery

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API | FastAPI + SSE streaming |
| Agent | LangGraph (Parent + 3 SubGraphs) |
| LLM | OpenAI-compatible (MiniMax API, configurable via .env) |
| Embeddings | SiliconFlow API (via `embedding/service.py`) |
| Database | DuckDB (embedded, read-only queries) |
| Schema Discovery | MCP protocol (separate process) + local fallback |
| Memory | QueryMemory (DuckDB-based SQL templates) + Mem0 (optional) |
| Tracing | Custom Tracer SDK (in-memory accumulation, async PostgreSQL flush) |
| Checkpoint | LangGraph MemorySaver (dev) / PostgreSQL (planned) |
| Persistence | PostgreSQL + asyncpg + pgvector |

## Data Model

Retail + e-commerce star-schema with 6 dimension tables and 4 fact tables (defined in `backend/seed_data.sql`):
- **Dimensions:** dim_date, dim_region, dim_product, dim_customer, dim_warehouse, dim_employee
- **Facts:** fact_sales (48 records), fact_returns (12), fact_inventory (30), fact_attendance (20)

## PostgreSQL Schema (backend/scripts/init_pg.sql)

Three schemas in PostgreSQL (separate from DuckDB business data):

| Schema | Tables | Purpose |
| ------- | ------- | --------- |
| `agent` | `session` | Session tracking (thread_id, user_id, status) |
| `memory` | `query_template`, `semantic_entry` | SQL templates with VECTOR(4096) embeddings, semantic memory |
| `observability` | `agent_trace`, `agent_trace_span`, `llm_call` | Trace spans, token usage, latency |

## Setup & Run

```bash
# 1. Create conda environment
conda create -n agent python=3.11
conda activate agent

# 2. Install dependencies
pip install -r backend/requirements.txt
pip install -r mcp_schema_server/requirements.txt

# 3. Start PostgreSQL (required for session + trace + memory)
docker run -d --name ragent-postgres \
  -e POSTGRES_USER=ragent \
  -e POSTGRES_PASSWORD=ragent \
  -e POSTGRES_DB=ragent \
  -p 5432:5432 \
  pgvector/pgvector:0.7.0-pg15

# 4. Initialize PostgreSQL tables
docker exec -i ragent-postgres psql -U ragent -d ragent < backend/scripts/init_pg.sql

# Note: If PostgreSQL was already initialized, re-run init_pg.sql after the VECTOR(4096)→VECTOR(1536) change:
# docker exec -i ragent-postgres psql -U ragent -d ragent -c "DROP TABLE IF EXISTS memory.query_template CASCADE;"
# Then re-run init_pg.sql

# 5. Configure .env (copy from template, fill in keys)
# MINIMAX_API_KEY, LLM_MODEL, LLM_BASE_URL, SILICONFLOW_API_KEY, DATABASE_URL

# 6. Terminal 1: MCP Schema Server
python -m mcp_schema_server.server

# 7. Terminal 2: ReportAgent API
cd backend
uvicorn app.main:app --port 8100 --reload
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/v1/chat` | POST | Send query, get SSE stream |

### SSE Event Protocol

```
event: token     → streaming LLM response text
event: trace     → execution step update {step, status, detail}
event: report    → final report {answer: {text, table, chart, insight}}
event: clarify   → clarification request {question}
event: error     → error message
event: done      → stream complete
```

## Key Configuration (.env)

| Variable | Default | Notes |
| --------- | ------- | ----- |
| `MINIMAX_API_KEY` | — | LLM API key (primary) |
| `LLM_MODEL` | `MiniMax-M2.7-highspeed` | Model name |
| `LLM_BASE_URL` | `https://api.minimax.chat/v1` | API base URL |
| `LLM_API_KEY` | (falls back to MINIMAX_API_KEY) | Alternative key name |
| `SILICONFLOW_API_KEY` | — | Embedding API key (separate provider) |
| `EMBEDDING_MODEL` | `Qwen/Qwen3-Embedding-8B` | Embedding model |
| `DATABASE_URL` | `postgresql://ragent:ragent@localhost:5432/ragent` | PostgreSQL connection |
| `MEM0_ENABLED` | (disabled) | Optional semantic memory |

## Code Architecture

### Directory Structure

```
backend/
  app/
    main.py              — FastAPI entrypoint, SSE streaming, session resume
    llm.py               — Unified LLM client (OpenAI-compatible, strips reasoning blocks)
    db.py                — DuckDB connection manager (auto-seeds from seed_data.sql)
    memory.py            — Top-level memory integration (wires QueryMemory + Mem0)

    agent/               — LangGraph Agent definitions
      parent_graph.py    — Parent graph: 7 nodes, interrupt-only in clarify node
      data_graph.py      — Data SubGraph: intent detection → schema search → context building
      sql_graph.py       — SQL SubGraph: plan → generate → validate → execute → evaluate
      report_graph.py    — Report SubGraph: plan analysis → run steps → finalize

    models/
      contracts.py       — 7 Pydantic models: SchemaContext, QueryPlan, QueryResult,
                           ReportSpec, ComponentSpec, ClarificationRequest, ErrorDetail

    tools/               — Capability-based tool registry
      __init__.py        — register_all_tools() wires 10 tools
      registry.py        — ToolRegistry + ToolMetadata (capability-based binding)
      data_tools.py      — search_tables, get_table_ddl, list_tables
      sql_tools.py       — validate_sql, execute_sql, chart_advisor, insight_analyst
      report_tools.py    — trend_analysis, group_compare, detect_anomaly

    infra/               — Infrastructure layer
      db/postgres.py     — asyncpg connection pool management
      trace/             — Tracer SDK + models + async PostgreSQL repository
      memory/            — QueryMemory (pgvector) + MemoryPolicy
      checkpoint/        — SessionManager + MemorySaver factory

    embedding/
      service.py         — EmbeddingService (SiliconFlow API, separate from LLM)

  scripts/
    init_pg.sql          — PostgreSQL schema (agent, memory, observability)
  seed_data.sql          — DuckDB sample data (retail star schema)

mcp_schema_server/
  server.py              — MCP Schema Server exposing search_tables, get_table_ddl, list_tables
  registry.py            — SchemaRegistry with keyword-scoring logic
```

### Agent Architecture (Parent + SubGraph)

```
User Query → classify_intent
  ├── "闲聊" → END
  └── "报表/看板" → data_agent → sql_agent → evaluate
       ┌────────────────────────────────┴──────────────┐
       │ SUCCESS                                 NEED_CLARIFICATION
       ▼                                                    ▼
  report_agent                                        clarify_node (interrupt)
       │                                                    │
       ▼                                              user reply → data_agent
      END
```

**Key design rules:**
- `evaluate` routes purely on `execution_status` — no business logic
- `clarify` is the **only** node that calls `interrupt()` — SubGraphs never interrupt
- SubGraphs run synchronously via `.invoke()` inside parent nodes (not as sub-graphs in the LangGraph sense)
- Checkpoint saves Parent State only (SubStates are ephemeral)
- Parent State = cross-Agent data contract; SubState = internal execution details

### Parent State (AgentState)

```python
class AgentState(TypedDict):
    user_query, original_query, current_query, clarification_history: list
    session_id, intent, memory_context
    schema_context: Optional[SchemaContext]   # Data Agent output
    query_plan: Optional[QueryPlan]           # SQL Agent planning output
    query_result: Optional[QueryResult]       # SQL Agent execution output
    report_spec: Optional[ReportSpec]         # Report Agent output
    chart_config: dict, insight_text: str
    execution_status: str                     # RUNNING / SUCCESS / FAILED / NEED_CLARIFICATION
    error: Optional[ErrorDetail]
    trace_id: str, active_sub_agent: str, retry_count: int
    clarification_context: dict
```

**State management rules:**

- `original_query` = the user's first message, frozen at session start (used for memory storage)
- `current_query` = augmented query with clarification context appended (used for SQL/report generation)
- `clarification_history` = list of {"question", "answer"} dicts from clarify turns
- `user_query` = maintained for backward compatibility with SubGraphs (they receive current_query as user_query)
- Memory saves always use `original_query` to prevent garbage accumulation

### Tool Registry (Capability-based)

10 tools registered via `register_all_tools()` in `tools/__init__.py`:

| Tool | Capability | Agent |
| ---- | ---------- | ----- |
| `search_tables` | `schema_search` | data |
| `get_table_ddl` | `schema_read` | data |
| `list_tables` | `schema_list` | data |
| `validate_sql` | `sql_validate` | sql |
| `execute_sql` | `sql_execute` | sql (requires `data.query.execute` permission) |
| `chart_advisor` | `chart_recommend` | report |
| `insight_analyst` | `insight_generate` | report |
| `trend_analysis` | `trend_analysis` | report |
| `group_compare` | `group_compare` | report |
| `detect_anomaly` | `anomaly_detection` | report |

### SQL Agent Retry Logic

**Internal (sql_graph):**

```
SQL_SYNTAX_ERROR → regenerate SQL (up to 3 retries)
SCHEMA_ERROR     → re-plan query (up to 1 retry)
Exhausted        → NEED_CLARIFICATION (ask user)
```

**Parent graph retry (augmented):**

```
Failed SQL execution → retry sql_agent (up to 3×) instead of report_agent
Exhausted retries    → route to clarify node
```

This prevents the report agent from hallucinating on null/empty SQL results.

### SQL Safety (3-layer validation)

1. **Blacklist**: reject any non-SELECT statement (DDL/DML keywords)
2. **AST parse**: use `sqlglot` to verify the parsed statement is a `Select`
3. **EXPLAIN**: execute `EXPLAIN <sql>` to catch DuckDB-specific syntax errors

### Session Management

- `POST /api/v1/chat` with `session_id` → checks if session exists via `session_manager.get_session()`
- New session: creates session in `agent.session` table, starts fresh graph
- Existing session: resumes from LangGraph checkpoint (via `thread_id` config)
- Checkpoint uses `MemorySaver` (in-memory) in dev; PostgreSQL interface reserved

### Trace SDK

- `Tracer` accumulates spans in memory during graph execution
- `traced_node` decorator wraps graph nodes with span recording (supports sync + async)
- `tracer.flush()` writes to PostgreSQL via `TraceRepository` (asyncpg) at the end of the request
- TraceRepository uses asyncpg pool but is called synchronously in `sdk.py` — repository code is effectively dead code until the SDK is updated to match

## Known Architectural Notes

- **No tests exist anywhere in the repo.** All verification is manual via curl.
- **TraceRepository is async PostgreSQL** (`infra/trace/repository.py` uses `asyncpg`), but **`sdk.py` calls it with `await` in flush()** — this is correct for the async flush path, but `main.py` must ensure flush is awaited.
- **PostgreSQL pool** (`infra/db/postgres.py`) is initialized in `main.py` lifespan via `init_pool()`.
- **MCP Schema Server has its own `SchemaRegistry`** with independent scoring logic. The local `data_tools.py` also has its own keyword-matching approach. These are two separate implementations of the same concept.
- **Embedding service** uses SiliconFlow API (configured via `SILICONFLOW_API_KEY`), a different provider from the LLM (MiniMax). This is not yet wired into QueryMemory (which uses keyword matching, not vector search).
- **`.claude/settings.local.json`** contains stale permission paths referencing `d:/PyProject/ragent-py/` (the old project path).
- **`infra/trace/repository.py`** uses asyncpg directly (not the pool from `infra/db/postgres.py`).
- **Source files are TSD-encrypted** — all `.py` files show `%TSD-Header-###%` and are unreadable without the decryption layer. Use `git show HEAD:<path>` to read decrypted content.
- **No `.env.example` or `.env.template` exists** in the repo. Create `.env` manually with the env vars listed in the Key Configuration section above.
- **`sqlglot` is used for SQL AST validation** (3-layer safety) but is not listed in `backend/requirements.txt`. Install manually: `pip install sqlglot`.
- **`__init__.py` files are empty (0 bytes)** — intentional; Python 3.3+ namespace packages make content unnecessary.

### Fixes Applied (2026-07-21)

| # | Issue | Files Changed | Status |
|---|-------|---------------|--------|
| 1 | Embedding dimension 4096→1536 | `init_pg.sql`, `main.py` (+startup check) | ✅ |
| 2 | Clarify overwrites user_query | `parent_graph.py`, `main.py` | ✅ |
| 3 | Evaluate routes failed SQL to report_agent | `parent_graph.py` (→`sql_agent` on retry) | ✅ |
| 4 | SQL injection in LIKE queries | `query_memory.py` (→`LIKE ANY($1::text[])`) | ✅ |
| 5 | Default model deepseek-chat | `llm.py` (→`MiniMax-M2.7-highspeed`) | ✅ |
| 6 | Markdown fence duplication | `utils/text.py` + `sql_graph.py`, `report_graph.py` | ✅ |
| 7 | `_safe_import_call` → `registry.get()` | `report_graph.py` | ✅ |
| 8 | `sqlglot` missing from deps | `requirements.txt` | ✅ |
| 9 | Session table missing `last_checkpoint_at` | `init_pg.sql` | ✅ |

## Development Commands

```bash
# Run backend (with hot-reload)
cd backend && uvicorn app.main:app --port 8100 --reload

# Run MCP schema server
python -m mcp_schema_server.server

# Test API
curl -X POST http://localhost:8100/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"user_query": "今年华东销售趋势", "session_id": "test-1"}'

# Health check
curl http://localhost:8100/health
```

## Reference

- `docs/development-plan.md` — Detailed 8-phase plan with 17 architecture decisions, design rationale, and future roadmap. Many decisions in the code match this plan exactly.
- `backend/seed_data.sql` — Complete data model with all dimension and fact table definitions and sample data.