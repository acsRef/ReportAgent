# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ReportAgent is an AI-powered natural-language-to-report system. Users ask data questions in Chinese chat, a LangGraph agent generates/executes SQL against DuckDB, and returns results as tables + charts + insights.

**Architecture:**
```
User ←SSE→ FastAPI + LangGraph Agent (:8100) ←MCP→ MCP Schema Server (:8101)
                                                          |
                                                     DuckDB (read-only)
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
| Tracing | Custom Tracer SDK + DuckDB/PostgreSQL persistence |
| Checkpoint | LangGraph MemorySaver (dev) / PostgreSQL (planned) |

## Data Model

Retail + e-commerce star-schema with 6 dimension tables and 4 fact tables (defined in `backend/seed_data.sql`):
- **Dimensions:** dim_date, dim_region, dim_product, dim_customer, dim_warehouse, dim_employee
- **Facts:** fact_sales (48 records), fact_returns (12), fact_inventory (30), fact_attendance (20)

## Setup & Run

```bash
# Create conda environment
conda create -n agent python=3.11
conda activate agent

# Install dependencies
pip install -r backend/requirements.txt
pip install -r mcp_schema_server/requirements.txt

# Configure .env (MINIMAX_API_KEY, LLM_MODEL, LLM_BASE_URL)

# Terminal 1: MCP Schema Server
python -m mcp_schema_server.server

# Terminal 2: ReportAgent API
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

### Key Files (not directory listing)

- **`backend/app/main.py`** — FastAPI entrypoint, SSE streaming, session resume logic. Uses `agr.astream_events()` for streaming and `astream()` for final state extraction.
- **`backend/app/llm.py`** — Unified LLM client. Configurable via `.env` (`LLM_MODEL`, `LLM_BASE_URL`, `MINIMAX_API_KEY`). Strips reasoning blocks from responses.
- **`backend/app/db.py`** — DuckDB connection manager. Auto-seeds from `seed_data.sql` if database is empty.
- **`backend/app/models/contracts.py`** — 6 Pydantic models that form the cross-agent data contract: `SchemaContext`, `QueryPlan`, `QueryResult`, `ReportSpec`, `ComponentSpec`, `ClarificationRequest`.
- **`backend/app/agent/parent_graph.py`** — Parent LangGraph with 7 nodes: classify → data_agent → sql_agent → evaluate → report_agent/clarify/dashboard_agent. `interrupt()` is called **only** in the clarify node.
- **`backend/app/agent/sql_graph.py`** — SQL SubGraph: plan → generate_sql → validate → execute → evaluate → build_output. Retry logic: SQL syntax errors retry generation (up to 3×), schema errors retry planning (up to 1×), then escalates to `NEED_CLARIFICATION`.
- **`backend/app/agent/report_graph.py`** — Report SubGraph: plan_analysis → run_step (loop) → finalize. Uses `chart_advisor`, `trend_analysis`, `group_compare`, `detect_anomaly` tools.
- **`backend/app/agent/data_graph.py`** — Data SubGraph: detect_intent → search_schema → build_context. Discovers table schemas via MCP/local tools.
- **`backend/app/tools/registry.py`** — Capability-based tool registry with optional permission checking.
- **`backend/app/embedding/service.py`** — Embedding service using SiliconFlow API (different provider from the LLM). Used for vector search in memory.
- **`backend/app/infra/memory/policy.py`** — MemoryPolicy: decides what enters memory and when, with preference extraction.
- **`backend/app/infra/trace/sdk.py`** — Tracer SDK with `traced_node` decorator. Wraps graph nodes with span tracing.
- **`mcp_schema_server/server.py`** — MCP server exposing `search_tables`, `get_table_ddl`, `list_tables` tools. Has its own `SchemaRegistry` with keyword-scoring logic.

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

### SQL Agent Retry Logic

```
SQL_SYNTAX_ERROR → regenerate SQL (up to 3 retries)
SCHEMA_ERROR     → re-plan query (up to 1 retry)
Exhausted        → NEED_CLARIFICATION (ask user)
```

### SQL Safety (3-layer validation)

1. **Blacklist**: reject any non-SELECT statement (DDL/DML keywords)
2. **AST parse**: use `sqlglot` to verify the parsed statement is a `Select`
3. **EXPLAIN**: execute `EXPLAIN <sql>` to catch DuckDB-specific syntax errors

## Known Architectural Notes

- **No tests exist anywhere in the repo.** All verification is manual via curl.
- **TraceRepository is async PostgreSQL** (`infra/trace/repository.py` uses `asyncpg`), but **`sdk.py` calls it synchronously** — `get_repo().save_trace(trace)` without `await`. This means the trace repository code is effectively dead code until the SDK is updated to match.
- **PostgreSQL pool** (`infra/db/postgres.py`) exists but is only used by `TraceRepository`. The `main.py` lifespan does not call `init_pool()`.
- **MCP Schema Server has its own `SchemaRegistry`** with independent scoring logic. The local `data_tools.py` also has its own keyword-matching approach. These are two separate implementations of the same concept.
- **Embedding service** uses SiliconFlow API (configured via `SILICONFLOW_API_KEY`), a different provider from the LLM (MiniMax). This is not yet wired into QueryMemory (which uses keyword matching, not vector search).
- **`.claude/settings.local.json`** contains stale permission paths referencing `d:/PyProject/ragent-py/` (the old project path).

## Key Configuration

All in `.env`:
- `MINIMAX_API_KEY` — LLM API key (primary)
- `LLM_MODEL` — model name (default: `MiniMax-M3`)
- `LLM_BASE_URL` — API base URL (default: `https://api.minimax.chat/v1`)
- `SILICONFLOW_API_KEY` — Embedding API key (separate from LLM)
- `MEM0_ENABLED` — optional semantic memory (default: disabled)

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