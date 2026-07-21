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
| Memory | PostgreSQL + pgvector (cosine similarity search) |
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
| `memory` | `query_template`, `semantic_entry` | SQL templates with VECTOR(1536) embeddings, semantic memory |
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

# 5. Configure .env (MINIMAX_API_KEY, LLM_MODEL, LLM_BASE_URL, SILICONFLOW_API_KEY, DATABASE_URL)

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

## Code Architecture

### Key Files

- **`backend/app/main.py`** — FastAPI entrypoint, SSE streaming, session resume. Uses `agr.astream_events()` for streaming, `_agent.get_state(config)` for interrupt detection. Initializes PostgreSQL pool + embedding dimension check on startup.
- **`backend/app/llm.py`** — Unified LLM client (OpenAI-compatible). Strips reasoning blocks from responses.
- **`backend/app/db.py`** — DuckDB connection manager. Auto-seeds from `seed_data.sql` if database is empty.
- **`backend/app/models/contracts.py`** — 6 Pydantic models: `SchemaContext`, `QueryPlan`, `QueryResult`, `ReportSpec`, `ComponentSpec`, `ClarificationRequest`.
- **`backend/app/agent/security_guard.py`** — Prompt injection guard. Scans user input for 10 rule patterns (ignore_previous, role_hijack, SQL DDL, data exfiltration, etc.). Score ≥ 3 → HIGH → blocked.
- **`backend/app/agent/parent_graph.py`** — Parent LangGraph with 8 nodes: security_guard → classify → data_agent → sql_agent → evaluate → report_agent/clarify/dashboard_agent. `interrupt()` is called **only** in the clarify node.
- **`backend/app/agent/sql_graph.py`** — SQL SubGraph: plan → generate_sql → validate → execute → evaluate → build_output. Retry logic: SQL syntax errors retry generation (up to 3×), schema errors retry planning (up to 1×), then escalates to `NEED_CLARIFICATION`.
- **`backend/app/agent/report_graph.py`** — Report SubGraph: plan_analysis → run_step (loop) → finalize. Uses `chart_advisor`, `trend_analysis`, `group_compare`, `detect_anomaly` tools.
- **`backend/app/agent/data_graph.py`** — Data SubGraph: detect_intent → search_schema → build_context. Discovers table schemas via MCP/local tools.
- **`backend/app/tools/registry.py`** — Capability-based tool registry with optional permission checking.
- **`backend/app/embedding/service.py`** — Embedding service using SiliconFlow API (separate from LLM MiniMax). Used by QueryMemory and UserMemory for pgvector search.
- **`backend/app/infra/memory/query_memory.py`** — PostgreSQL + pgvector memory for SQL templates. Scores results by semantic similarity (0.5) + success rate (0.3) + frequency (0.1) + recency (0.1).
- **`backend/app/infra/memory/user_memory.py`** — PostgreSQL + pgvector memory for user preferences/insights. Scores by semantic similarity (0.6) + importance (0.2) + frequency (0.1) + recency (0.1). Uses `embed_or_none` fallback.
- **`backend/app/infra/memory/memory_manager.py`** — Orchestrates `QueryMemory` + `UserMemory` for unified recall.
- **`backend/app/infra/trace/sdk.py`** — Tracer SDK with `traced_node` decorator. Accumulates spans in memory during graph execution, flushes to PostgreSQL via `await tracer.flush()`.
- **`backend/app/infra/db/postgres.py`** — asyncpg connection pool (min_size=2, max_size=10). Initialized by `main.py` lifespan.
- **`mcp_schema_server/server.py`** — MCP server exposing `search_tables`, `get_table_ddl`, `list_tables` tools. Has its own `SchemaRegistry` with keyword-scoring logic (independent from local `data_tools.py`).

### Agent Architecture (Parent + SubGraph)

```
User Query → security_guard
  ├── HIGH → END (blocked)
  └── LOW → classify_intent
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
- SubGraphs run via `.ainvoke()` inside parent nodes (not as sub-graphs in the LangGraph sense)
- Checkpoint saves Parent State only (SubStates are ephemeral)

### State Management

**Parent State** (`AgentState` in `parent_graph.py`):
- `original_query` = the user's first message, frozen at session start (used for memory storage)
- `current_query` = augmented query with clarification context appended (used for SQL/report generation)
- `clarification_history` = list of {"question", "answer"} dicts from clarify turns
- `security_score`, `security_level`, `security_warning` — from SecurityGuard

### SQL Agent Retry Logic

**Internal (sql_graph):**
```
SQL_SYNTAX_ERROR → regenerate SQL (up to 3 retries)
SCHEMA_ERROR     → re-plan query (up to 1 retry)
Exhausted        → NEED_CLARIFICATION (ask user)
```

**Parent graph retry:**
```
Failed SQL execution → retry sql_agent (up to 3×) instead of report_agent
Exhausted retries    → route to clarify node
```

### SQL Safety (3-layer validation)

1. **Blacklist**: reject any non-SELECT statement (DDL/DML keywords)
2. **AST parse**: use `sqlglot` to verify the parsed statement is a `Select`
3. **EXPLAIN**: execute `EXPLAIN <sql>` to catch DuckDB-specific syntax errors

### Memory Ranking System

**QueryMemory** scoring formula: `semantic_sim × 0.5 + success_rate × 0.3 + frequency × 0.1 + recency × 0.1`

**UserMemory** scoring formula: `semantic_sim × 0.6 + importance × 0.2 + frequency × 0.1 + recency × 0.1`

Both use `embed_or_none` — if embedding API fails, gracefully falls back to keyword matching with `ILIKE ANY($1::text[])` (parameterized, no SQL injection).

## Known Architectural Notes

- **No tests exist anywhere in the repo.** All verification is manual via curl.
- **TraceRepository is async PostgreSQL** (`infra/trace/repository.py` uses `asyncpg`), and **`sdk.py` now calls it with `await` in flush()** — this is correct for the async flush path, but `main.py` must ensure flush is awaited.
- **PostgreSQL pool** (`infra/db/postgres.py`) is initialized in `main.py` lifespan via `init_pool()`.
- **MCP Schema Server has its own `SchemaRegistry`** with independent scoring logic. The local `data_tools.py` also has its own keyword-matching approach. These are two separate implementations of the same concept.
- **Embedding service** uses SiliconFlow API (configured via `SILICONFLOW_API_KEY`), a different provider from the LLM (MiniMax). This is not yet wired into QueryMemory (which uses keyword matching, not vector search).
- **`.claude/settings.local.json`** contains stale permission paths referencing `d:/PyProject/ragent-py/` (the old project path).
- **`infra/trace/repository.py`** uses asyncpg directly (not the pool from `infra/db/postgres.py`).

## Key Configuration (.env)

| Variable | Default | Notes |
| --------- | ------- | ----- |
| `MINIMAX_API_KEY` | — | LLM API key (primary) |
| `LLM_MODEL` | `MiniMax-M3` | Model name |
| `LLM_BASE_URL` | `https://api.minimax.chat/v1` | API base URL |
| `LLM_API_KEY` | (falls back to MINIMAX_API_KEY) | Alternative key name |
| `SILICONFLOW_API_KEY` | — | Embedding API key (separate) |
| `EMBEDDING_MODEL` | `Qwen/Qwen2.5-7B-Instruct` | Embedding model |
| `DATABASE_URL` | `postgresql://ragent:ragent@localhost:5432/ragent` | PostgreSQL connection |
| `MEM0_ENABLED` | (disabled) | Optional semantic memory |

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
- `backend/scripts/init_pg.sql` — PostgreSQL schema for session, memory, and observability tables.