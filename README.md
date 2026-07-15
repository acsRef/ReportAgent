# ReportAgent

AI-powered natural-language-to-report system. Users ask data questions in chat, a LangGraph Agent generates/executes SQL, returns results as tables + charts + insights.

## Architecture

```
User ←SSE→ FastAPI + LangGraph Agent (:8100) ←MCP→ MCP Schema Server (:8101)
                                                          |
                                                     DuckDB (read-only)
```

Two backend services:
- **ReportAgent** (:8100) — FastAPI + LangGraph agent, SQL generation/execution, chart/insight assembly
- **MCP Schema Server** (:8101) — standalone MCP server for on-demand table schema discovery

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API | FastAPI + SSE streaming |
| Agent | LangGraph (classify → gen_sql → validate → execute → assemble) |
| LLM | OpenAI-compatible (MiniMax API) |
| Database | DuckDB (embedded, read-only) |
| Schema Discovery | MCP protocol with keyword-based table search |

## Setup

```bash
# Create conda environment (recommended)
conda create -n agent python=3.11
conda activate agent

# Install dependencies
pip install -r backend/requirements.txt
pip install -r mcp_schema_server/requirements.txt

# Configure API key in .env
echo "MINIMAX_API_KEY=your-key" > .env
```

## Run

```bash
# Terminal 1: MCP Schema Server
python -m mcp_schema_server.server

# Terminal 2: ReportAgent API
uvicorn app.main:app --port 8100 --reload
```

## Data Model

Retail + e-commerce star-schema with 6 dimension tables and 4 fact tables:
- **Dimensions:** date, region, product, customer, warehouse, employee
- **Facts:** sales (48 records), returns (12), inventory (30), attendance (20)

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/v1/chat` | POST | Send query, get SSE stream (token/trace/report/done) |
