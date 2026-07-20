# ReportAgent Development Plan

> Generated from 17 grilling sessions. Last updated: 2026-07-20.

---

## Architecture Decisions (Summary)

| # | Issue | Decision |
|---|-------|----------|
| 1 | Report/Dashboard relationship | Unified LangGraph, branching after classify |
| 2 | Multi-Agent timing | Now, before frontend |
| 3 | SubGraph implementation | Parent + SubGraph (not module-level split) |
| 4 | State boundary | Parent State = cross-Agent data contract; SubState = internal execution details |
| 5 | Clarify flow | Parent Clarify Node + `execution_status` protocol; SubGraphs never interrupt |
| 6 | Tool Registry | Hybrid — SubGraph declares capabilities, Registry provides instances |
| 7 | Evaluate Node | Pure execution-status routing, no business logic |
| 8 | Permission | Data model (Resource/Permission/Role) first, checker later |
| 9 | Checkpoint | MemorySaver first + Session table, PostgreSQL checkpoint interface reserved |
| 10 | Trace | 3 tables (trace/span/llm_call), sync write + exception isolation, async interface reserved |
| 11 | Dashboard Schema | Component → Dataset → SQL Registry (3-layer decoupling) |
| 12 | Data Agent | Schema Ranking Pipeline — responsible for "where to find data" |
| 13 | SQL Agent | Plan → SQL → Validate → Execute → Error-based Recovery |
| 14 | Memory | Policy layer controls writes; Semantic Memory (Mem0) + Query Memory (pgvector) |
| 15 | MCP + Tool Registry | One MCP Server per data source + capability-based Tool Metadata |
| 16 | Agent communication | Pydantic contracts (SchemaContext/QueryPlan/QueryResult/ReportSpec) + version |
| 17 | Deployment | Docker Compose + modular monolith + LLM Client abstraction + PostgreSQL schema layering |

---

## Execution Principles

1. Each Phase produces a runnable closure — no debt carried to the next Phase
2. After each Phase, `uvicorn app.main:app` can start and be tested
3. Frontend comes last (Phase 8)

---

## Phase 1: Backbone Refactoring (3-4 days)

**Goal**: Refactor current monolithic Graph into Parent + SubGraph architecture, verify the minimal closure.

### Steps

#### 1.1. File Structure Reorganization

```
backend/app/
├── __init__.py
├── main.py                    # Refactor: session mgmt + checkpoint integration
├── llm.py                     # Keep: unified LLM Client
├── db.py                      # Keep
│
├── models/                    # [NEW] Contract models layer
│   ├── __init__.py
│   └── contracts.py           # SchemaContext, QueryPlan, QueryResult, ReportSpec
│
├── agent/                     # [NEW] Agent SubGraphs
│   ├── __init__.py
│   ├── parent_graph.py        # Parent Graph: routing + evaluate + clarify
│   ├── data_graph.py          # Data SubGraph (skeleton)
│   ├── sql_graph.py           # SQL SubGraph (skeleton)
│   └── report_graph.py        # Report SubGraph (skeleton)
│
├── tools/                     # [NEW] Tool system
│   ├── __init__.py
│   ├── registry.py            # ToolRegistry + ToolMetadata
│   ├── data_tools.py          # Data Agent tools
│   ├── sql_tools.py           # SQL Agent tools
│   └── report_tools.py        # Report Agent tools
│
├── infra/                     # [NEW] Infrastructure
│   ├── __init__.py
│   └── checkpoint/
│       ├── __init__.py
│       ├── factory.py         # Checkpointer factory (MemorySaver dev, Postgres prod)
│       └── session.py         # Session table CRUD
│
└── old/                       # [ARCHIVE] Old code moved here
    ├── agent.py
    ├── nodes.py
    ├── state.py
    ├── tools.py
    ├── schemas.py
    ├── clarify_tool.py
    └── mcp_client.py
```

#### 1.2. Contract Models (`models/contracts.py`)

```python
from pydantic import BaseModel
from typing import Literal, Optional


class ColumnSchema(BaseModel):
    name: str
    type: str
    description: str = ""


class TableSchema(BaseModel):
    name: str
    description: str = ""
    columns: list[ColumnSchema] = []
    relationships: list[dict] = []


class SchemaContext(BaseModel):
    """Output of Data Agent — what data is available and where."""
    version: str = "1.0"
    source: str = ""
    tables: list[TableSchema] = []
    confidence: float = 0.0
    status: Literal["SUCCESS", "FAILED"] = "SUCCESS"
    error: Optional[dict] = None


class QueryPlan(BaseModel):
    """Output of SQL Agent's planner — structured query intent."""
    version: str = "1.0"
    target_metric: str = ""
    dimensions: list[str] = []
    filters: list[dict] = []
    aggregation: str = ""
    time_range: Optional[str] = None


class QueryResult(BaseModel):
    """Output of SQL Agent — executed query results."""
    version: str = "1.0"
    sql: str = ""
    columns: list[dict] = []
    rows: list[dict] = []
    row_count: int = 0
    status: Literal["SUCCESS", "FAILED", "EMPTY"] = "SUCCESS"
    error: Optional[dict] = None


class ComponentSpec(BaseModel):
    """A single dashboard component."""
    id: str = ""
    type: str = ""  # line_chart, bar_chart, pie_chart, kpi_card, table
    title: str = ""
    layout: dict = {}
    data_binding: dict = {}
    visual_config: dict = {}


class ReportSpec(BaseModel):
    """Output of Report Agent — chart + insight."""
    version: str = "1.0"
    components: list[ComponentSpec] = []
    insight: str = ""
```

#### 1.3. Agent State Design

**Parent State** (cross-Agent contract only):

```python
class AgentState(TypedDict):
    # Conversation
    user_query: str
    session_id: str
    intent: str  # 报表 / 闲聊 / 看板

    # Data Agent output
    schema_context: Optional[SchemaContext]

    # SQL Agent output
    query_plan: Optional[QueryPlan]
    query_result: Optional[QueryResult]

    # Report Agent output
    report_spec: Optional[ReportSpec]

    # Control
    execution_status: Literal[
        "RUNNING", "SUCCESS", "FAILED",
        "NEED_CLARIFICATION"
    ]
    error: Optional[dict]
    trace_id: str
    active_sub_agent: str  # data / sql / report / clarify
    retry_count: int
    clarification_context: dict
```

**SubGraph States** (internal, not exposed to Parent):

```python
class DataAgentState(TypedDict):
    user_query: str
    discovered_tables: list
    mcp_tool_calls: list
    schema_context: str  # raw MCP output before transformation

class SQLAgentState(TypedDict):
    schema_context: dict
    query_plan: Optional[QueryPlan]
    generated_sql: str
    sql_valid: bool
    sql_error: str
    retry_count: int

class ReportAgentState(TypedDict):
    query_result: dict
    assemble_plan: list
    assemble_step_idx: int
    assemble_results: list
```

#### 1.4. Parent Graph Structure

```
User Query
    │
    ▼
classify_intent (node)
    │
    ├── "闲聊" ──► END
    │
    └── "报表/看板" ──► data_subgraph
                            │ schema_context
                            ▼
                        sql_subgraph
                            │ query_result
                            ▼
                        evaluate_node
                            │
                    ┌───────┴───────┐
                    │               │
              SUCCESS          NEED_CLARIFICATION
                    │               │
                    ▼               ▼
              report_subgraph   clarify_node
                    │               │
                    ▼          user reply
                   END              │
                                    ▼
                              re-route to sql/data
```

**Key design points**:
- `evaluate_node` routes purely on `execution_status` — no business logic
- `clarify_node` is the **only** node that calls `interrupt()` — SubGraphs never interrupt
- SubGraphs return `{"execution_status": "NEED_CLARIFICATION", "clarification": {...}}` instead
- Checkpoint saves Parent State only (SubStates are ephemeral)

#### 1.5. ToolRegistry

```python
class ToolMetadata(BaseModel):
    name: str
    description: str
    capability: str          # e.g. "schema_search", "query_execution"
    agent_type: str          # data / sql / report
    source: str              # local / mcp
    permission_required: list[str] = []
    input_schema: dict = {}
    output_schema: dict = {}


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolMetadata] = {}
        self._instances: dict[str, callable] = {}

    def register(self, name: str, tool_fn: callable, metadata: ToolMetadata):
        ...

    def get(self, names: list[str],
            user_context: Optional[dict] = None) -> list[callable]:
        # If user_context is None, return all (dev mode)
        # If user_context is provided, filter by permission
        ...

    def get_by_capability(self, capabilities: list[str]) -> list[ToolMetadata]:
        # For SubGraph capability declarations
        ...
```

**Capability-based binding**:
- SubGraph declares: `required_capabilities = ["schema_search", "schema_read"]`
- Registry resolves: which tools satisfy these capabilities
- Permission filters: `user_context` → allowed tools only

#### 1.6. Checkpoint + Session

```python
# infra/checkpoint/factory.py
def create_checkpointer(env: str = "dev"):
    if env == "dev":
        return MemorySaver()
    elif env == "production":
        # return AsyncPostgresSaver(...)  # reserved for later
        raise NotImplementedError
```

```sql
-- infra/checkpoint/session.sql
CREATE TABLE agent.session (
    id SERIAL PRIMARY KEY,
    thread_id VARCHAR(64) UNIQUE NOT NULL,
    user_id VARCHAR(64),
    title VARCHAR(256),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    status VARCHAR(32) DEFAULT 'active'
);
```

**main.py behavior change**:
```python
# Before: always creates new initial_state
async for event in _agent.astream_events(initial_state, config, version="v2"):

# After: resume if thread_id exists, else start fresh
config = {"configurable": {"thread_id": session_id}}
if is_new_session:
    async for event in _agent.astream(initial_state, config):
        ...
else:
    async for event in _agent.astream(None, config):  # resume from checkpoint
        ...
```

#### 1.7. Verification

```bash
# Start backend
uvicorn app.main:app --port 8100

# Test classify
curl -X POST http://localhost:8100/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"user_query": "你好", "session_id": "test-1"}'
# → intent="闲聊" → END

# Test report mode (basic)
curl -X POST http://localhost:8100/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"user_query": "今年华东销售趋势", "session_id": "test-2"}'
# → data → sql → report → result
```

---

## Phase 2: Data Agent + MCP (2-3 days)

**Goal**: Data Agent with Schema Ranking Pipeline, supporting multi-MCP sources.

### 2.1. Data SubGraph Flow

```
user_query
    │
    ▼
Schema Retrieval
    │ keyword search + vector search → candidates
    ▼
Metadata Ranking
    │ field match × 0.35 + description × 0.25
    │ + business domain × 0.2 + history × 0.1 + permission × 0.1
    ▼
Conflict Detection
    │ top1 - top2 < threshold(0.15) → NEED_CLARIFICATION
    ▼
SchemaContext (output contract)
```

### 2.2. Schema Metadata Enhancement

Extend current `data_asset` / `data_field` concepts:

```python
class DataAsset(BaseModel):
    id: str
    source_id: str
    database_name: str
    table_name: str
    description: str
    business_domain: str          # 销售 / 财务 / 库存 / ...
    owner: str
    columns: list[DataField]
    synonyms: list[str] = []       # e.g. ["订单", "order"]

class DataField(BaseModel):
    name: str
    type: str
    description: str
    synonyms: list[str] = []       # e.g. ["金额", "销售额", "amount"]
```

### 2.3. Multi-MCP Discovery

```python
class MCPDataSource:
    """One MCP Server = one data source."""
    name: str
    server_command: str
    tools: list[ToolMetadata]

class MCPManager:
    """Manages multiple MCP data sources."""
    def discover_all(self) -> list[MCPDataSource]:
        # Connect to each registered MCP, list_tools(), cache metadata
        ...

    def get_source(self, name: str) -> MCPDataSource:
        ...
```

### 2.4. Ranking Engine

```python
def rank_schemas(
    query: str,
    candidates: list[DataAsset],
    user_context: Optional[dict] = None
) -> list[ScoredAsset]:
    for asset in candidates:
        score = 0.0
        score += field_match(query, asset) * 0.35
        score += desc_similarity(query, asset) * 0.25
        score += domain_match(query, asset) * 0.20
        score += historical_usage(query, asset) * 0.10
        score += permission_score(asset, user_context) * 0.10
        scored.append(ScoredAsset(asset=asset, score=score))

    # Conflict detection
    if len(scored) >= 2 and (scored[0].score - scored[1].score) < 0.15:
        return NEED_CLARIFICATION
    return scored
```

### 2.5. Output

Data Agent produces a structured `SchemaContext` with:
- Selected data source identity
- Table + column schemas with descriptions
- Confidence score
- `NEED_CLARIFICATION` status with alternatives list when ambiguous

---

## Phase 3: SQL Agent Workflow (2-3 days)

**Goal**: Plan → SQL → Validate → Execute → Error Recovery.

### 3.1. SQL SubGraph Flow

```
SchemaContext
    │
    ▼
QueryPlanner (LLM node)
    │ structured QueryPlan output
    ▼
SQL Generator (LLM node)
    │ Plan + Schema → SQL
    ▼
Validator (code node)
    │ 3 layers: Security → Schema → Business
    ▼
Executor (tool node)
    │ execute_sql()
    ▼
Result Evaluator (code node)
    │ status: SUCCESS / FAILED / EMPTY
    ▼
Error? ──► Error Router
    │
    ├── SQL_SYNTAX_ERROR → SQL Generator (retry)
    ├── SCHEMA_ERROR     → QueryPlanner (retry)
    ├── AMBIGUOUS_METRIC → execution_status = NEED_CLARIFICATION
    └── retry_exhausted  → execution_status = NEED_CLARIFICATION
```

### 3.2. QueryPlan Output

```json
{
  "version": "1.0",
  "target_metric": "sales_amount",
  "dimensions": ["month", "region"],
  "filters": [
    {"field": "region", "operator": "=", "value": "华东"},
    {"field": "year", "operator": "=", "value": "2026"}
  ],
  "aggregation": "sum",
  "time_range": "2026"
}
```

### 3.3. Validator 3-Layer Design

```python
def validate_sql(sql: str, schema: SchemaContext) -> ValidationResult:
    # Layer 1: Security — AST parse, reject DDL/DML
    if not check_security(sql):
        return ValidationResult(valid=False, type="SECURITY_ERROR", ...)

    # Layer 2: Schema — check all referenced columns exist
    schema_errors = check_schema(sql, schema)
    if schema_errors:
        return ValidationResult(valid=False, type="SCHEMA_ERROR", ...)

    # Layer 3: Business — check aggregation合理性
    biz_warnings = check_business_logic(sql, schema)
    if biz_warnings:
        return ValidationResult(valid=True, warnings=biz_warnings, ...)

    return ValidationResult(valid=True)
```

### 3.4. Self-Correction Design

```python
class SQLAgentState(TypedDict):
    ...
    retry_counters: dict = {
        "plan": 0,
        "sql_generation": 0,
        "max_plan_retries": 1,
        "max_sql_retries": 2,
    }

def error_router(error_type: str, state: SQLAgentState) -> str:
    if error_type == "SQL_SYNTAX_ERROR":
        if state["retry_counters"]["sql_generation"] < 2:
            return "sql_generator"    # retry SQL node
        else:
            return "planner"          # escalate to plan revision

    if error_type == "SCHEMA_ERROR":
        return "planner"              # schema issue → re-plan

    if state["retry_counters"]["plan"] >= 1:
        return "NEED_CLARIFICATION"   # exhausted → ask user

    return "planner"
```

### 3.5. Verification

Test cases for SQL Agent:
- Simple query: "查询销售额" → single table aggregation
- Multi-table: "各区域退货率" → JOIN + GROUP BY
- Error recovery: wrong column name → self-correct
- Ambiguous: "利润是多少" (毛利 vs 净利) → NEED_CLARIFICATION

---

## Phase 4: Report Agent + Dashboard Mode (2 days)

**Goal**: Chart recommendation + insight generation + Dashboard Component JSON schema.

### 4.1. Report SubGraph Flow

```
QueryResult + QueryPlan
    │
    ├── Chart Advisor (code + LLM)
    │   Plan + Result → chart type + mappings
    │
    ├── Insight Generator (LLM)
    │   Result + Plan → natural language insight
    │
    ▼
ReportSpec (output contract)
```

### 4.2. Chart Advisor Logic

```python
def recommend_chart(query_plan: QueryPlan, data: QueryResult) -> ComponentSpec:
    dim_count = len(query_plan.dimensions)
    row_count = data.row_count

    if dim_count == 1 and row_count <= 8:
        return pie_chart(query_plan, data)
    elif dim_count == 1 and row_count > 8:
        return bar_chart(query_plan, data)
    elif dim_count == 2:
        return grouped_bar_chart(query_plan, data)
    elif "trend" in str(query_plan).lower() or "month" in str(query_plan.dimensions):
        return line_chart(query_plan, data)
    else:
        return table_component(data)
```

### 4.3. Dashboard Mode Flow

```
User: "生成销售驾驶舱"
    │
    ▼
Dashboard Agent (in Report SubGraph)
    │
    ├── Decompose: ["销售趋势", "区域排名", "KPI总数"]
    │
    ├── For each component:
    │   ├── Data Requirement → SQL Agent
    │   └── Chart Recommendation
    │
    ▼
Dashboard JSON
    {
      "components": [
        {"id": "c1", "type": "kpi_card", "data_binding": {"dataset_id": "ds_total_sales"}, ...},
        {"id": "c2", "type": "line_chart", "data_binding": {"dataset_id": "ds_monthly_trend"}, ...},
        {"id": "c3", "type": "bar_chart", "data_binding": {"dataset_id": "ds_region_ranking"}, ...}
      ]
    }
```

### 4.4. Dataset Registry

```python
# SQL table
CREATE TABLE agent.dataset (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(256),
    description TEXT,
    query_type VARCHAR(32),    -- sql / api / mcp
    query_definition JSONB,
    sql_id VARCHAR(64),        -- FK to sql_definition
    created_by VARCHAR(64),
    version INT DEFAULT 1,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

CREATE TABLE agent.sql_definition (
    id VARCHAR(64) PRIMARY KEY,
    sql_text TEXT,
    schema_context JSONB,
    generated_by VARCHAR(64),  -- agent version
    version INT DEFAULT 1,
    status VARCHAR(32),
    created_at TIMESTAMP
);
```

---

## Phase 5: Memory (1-2 days)

**Goal**: Memory Policy Layer + Semantic Memory (Mem0) + Query Memory (pgvector).

### 5.1. Memory Policy Layer

```python
class MemoryPolicy:
    """Decides what enters memory and when."""

    def should_remember(self, event: AgentEvent) -> bool:
        # Rule 1: Explicit preference statements
        if any(kw in event.text for kw in ["以后", "默认", "每次"]):
            return True

        # Rule 2: Repeated patterns (same analysis > 3 times)
        if event.repeat_count > 3:
            return True

        # Rule 3: Temporal queries (e.g. "查昨天销售") → do NOT save
        if event.is_temporal_query:
            return False

        return False

    def extract_preference(self, event: AgentEvent) -> Optional[MemoryEntry]:
        # Use LLM to extract structured preference from text
        # "以后显示万元" → {"type": "user_preference", "key": "currency_unit", "value": "万元"}
        ...

    def classify_memory_type(self, event: AgentEvent) -> str:
        # semantic / query_template / pattern
        ...
```

### 5.2. Memory Recall by Agent

Different agents recall different memory types:

| Agent | What it recalls |
|-------|----------------|
| classify | Brief user identity context |
| Data Agent | Nothing (pure schema discovery) |
| SQL Agent | Metric definitions, query templates |
| Report Agent | Display preferences (chart type, units) |

### 5.3. Query Memory (pgvector)

```sql
CREATE TABLE memory.query_template (
    id SERIAL PRIMARY KEY,
    intent_embedding VECTOR(1536),
    question TEXT,
    sql TEXT,
    schema_context JSONB,
    success_rate FLOAT DEFAULT 0.0,
    use_count INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    last_used_at TIMESTAMP
);

CREATE INDEX idx_query_template_embedding
ON memory.query_template
USING ivfflat (intent_embedding vector_cosine_ops)
WITH (lists = 100);
```

```python
class QueryMemory:
    def search(self, question: str, top_k: int = 3) -> list[QueryTemplate]:
        embedding = self.embed(question)
        # pgvector cosine similarity search
        return self.db.query(
            "SELECT * FROM memory.query_template "
            "ORDER BY intent_embedding <=> :emb LIMIT :k",
            {"emb": embedding, "k": top_k}
        )

    def save(self, question: str, sql: str, schema: dict) -> None:
        embedding = self.embed(question)
        self.db.execute(
            "INSERT INTO memory.query_template "
            "(intent_embedding, question, sql, schema_context) "
            "VALUES (:emb, :q, :sql, :schema)",
            {"emb": embedding, "q": question, "sql": sql, "schema": schema}
        )
```

---

## Phase 6: Permission (1-2 days)

**Goal**: Resource/Permission/Role data models + Permission Checker in ToolRegistry.

### 6.1. Data Models

```python
class Resource(BaseModel):
    id: str                    # "tool.execute_sql", "dataset.sales", "table.fact_sales"
    type: str                  # TABLE / API / MCP_TOOL / ANALYSIS_TOOL / DATASET
    name: str
    metadata: dict = {}

class Permission(BaseModel):
    code: str                  # "data.query.execute", "finance.report.read"
    description: str

class Role(BaseModel):
    name: str                  # "admin", "sales_analyst", "finance_manager"
    permissions: list[str]     # ["data.schema.read", "data.query.execute", ...]
```

### 6.2. Permission Checker

```python
class PermissionChecker:
    def __init__(self, db):
        self.db = db

    def check(self, user_context: dict, required_permissions: list[str]) -> bool:
        role = user_context.get("role")
        if role == "admin":
            return True
        user_perms = self._get_user_permissions(user_context["user_id"])
        return all(p in user_perms for p in required_permissions)

    def filter_tools(self, tools: list[ToolMetadata],
                     user_context: dict) -> list[ToolMetadata]:
        return [
            t for t in tools
            if not t.permission_required
            or self.check(user_context, t.permission_required)
        ]
```

### 6.3. ToolRegistry Integration

```python
class ToolRegistry:
    def __init__(self, permission_checker: Optional[PermissionChecker] = None):
        self._tools = {}
        self._instances = {}
        self._checker = permission_checker

    def get(self, names: list[str],
            user_context: Optional[dict] = None) -> list[callable]:
        if user_context is None or self._checker is None:
            # Dev mode: all tools available
            return [self._instances[n] for n in names]

        # Production: filter by permission
        allowed = []
        for name in names:
            meta = self._tools[name]
            if self._checker.check(user_context, meta.permission_required):
                allowed.append(self._instances[name])
        return allowed
```

---

## Phase 7: Trace (1 day)

**Goal**: Trace SDK + PostgreSQL persistence, zero impact on Agent code.

### 7.1. Database Schema

```sql
CREATE SCHEMA IF NOT EXISTS observability;

CREATE TABLE observability.agent_trace (
    id BIGSERIAL PRIMARY KEY,
    trace_id VARCHAR(64) UNIQUE NOT NULL,
    session_id VARCHAR(64),
    user_query TEXT,
    status VARCHAR(32),
    start_time TIMESTAMP DEFAULT NOW(),
    end_time TIMESTAMP,
    total_duration INT,         -- milliseconds
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE observability.agent_trace_span (
    id BIGSERIAL PRIMARY KEY,
    trace_id VARCHAR(64) NOT NULL,
    parent_span_id VARCHAR(64),
    span_id VARCHAR(64) NOT NULL,
    span_name VARCHAR(128),
    span_type VARCHAR(32),       -- NODE / LLM / TOOL / SQL / MCP
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    duration INT,                -- milliseconds
    status VARCHAR(32),
    input JSONB,
    output JSONB,
    error TEXT
);

CREATE TABLE observability.llm_call (
    id BIGSERIAL PRIMARY KEY,
    span_id VARCHAR(64) NOT NULL,
    model VARCHAR(64),
    prompt_tokens INT DEFAULT 0,
    completion_tokens INT DEFAULT 0,
    latency INT,                 -- milliseconds
    cost NUMERIC(10, 6) DEFAULT 0
);
```

### 7.2. Trace SDK

```python
class Tracer:
    def __init__(self, repo: TraceRepository):
        self._repo = repo
        self._stack: list[Span] = []

    @contextmanager
    def span(self, name: str, span_type: str = "NODE"):
        span = Span(
            trace_id=self._trace_id,
            parent_span_id=self._current_span_id(),
            span_id=uuid4().hex,
            span_name=name,
            span_type=span_type,
        )
        self._stack.append(span)
        try:
            yield span
            span.end(success=True)
        except Exception as e:
            span.end(success=False, error=str(e))
            raise
        finally:
            self._stack.pop()
            self._repo.save_span(span)  # sync write, best-effort
```

### 7.3. Decorator Integration

```python
def traced_node(name: str):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(state: dict):
            tracer = get_tracer(state["trace_id"])
            with tracer.span(name, span_type="NODE"):
                return await func(state)
        return wrapper
    return decorator
```

```python
# Usage in graph nodes:
@traced_node("sql_planner")
async def query_planner(state: SQLAgentState) -> dict:
    ...
```

---

## Phase 8: Frontend (Last)

**Goal**: React + TypeScript + ECharts UI.

### 8.1. Tech Stack

- React 19 + TypeScript
- Vite for build
- ECharts for charts
- react-grid-layout for dashboard drag-and-drop
- SSE client for streaming

### 8.2. Main Views

- **ChatPage**: input box + SSE streaming message list
- **ReportResult**: Table component + ECharts chart + Insight block
- **DebugPanel**: collapsible trace execution trace
- **DashboardPage**: react-grid-layout, component configuration

### 8.3. SSE Event Protocol

```
event: token     → streaming LLM response text
event: trace     → execution step update {step, status, detail}
event: report    → final report {answer: {text, table, chart, insight}, trace: [...]}
event: clarify   → clarification request {question}
event: error     → error message
event: done      → stream complete
```

### 8.4. Dashboard Edit Flow

```
1. AI generates Dashboard JSON
2. User drags/resizes components (react-grid-layout)
3. User modifies chart type or data binding
4. Modification → new Dataset request → SQL Agent generates new query
5. Component binds to new Dataset
6. Save to backend
```

---

## Dependency Graph

```
Phase 1: Backbone
  ├── Phase 2: Data Agent (parallel with Phase 3)
  │
  ├── Phase 3: SQL Agent (parallel with Phase 2)
  │   │
  │   ├── Phase 4: Report + Dashboard (depends on 2+3)
  │   │
  │   ├── Phase 5: Memory (can start after Phase 3)
  │   ├── Phase 6: Permission (can start after Phase 3)
  │   └── Phase 7: Trace (can start after Phase 1)
  │
  └── Phase 8: Frontend (last, depends on Phase 4 API stability)
```

---

## How to Start Phase 1

### Step 1: Read current code

```bash
# Review existing files to understand what to refactor
ls backend/app/
cat backend/app/agent.py
cat backend/app/nodes.py
cat backend/app/state.py
```

### Step 2: Create new directory structure

```bash
mkdir -p backend/app/models
mkdir -p backend/app/agent
mkdir -p backend/app/tools
mkdir -p backend/app/infra/checkpoint
mkdir -p backend/app/old
```

### Step 3: Move old code to archive

```bash
git mv backend/app/agent.py backend/app/old/agent.py
git mv backend/app/nodes.py backend/app/old/nodes.py
git mv backend/app/state.py backend/app/old/state.py
git mv backend/app/tools.py backend/app/old/tools.py
git mv backend/app/schemas.py backend/app/old/schemas.py
git mv backend/app/clarify_tool.py backend/app/old/clarify_tool.py
git mv backend/app/mcp_client.py backend/app/old/mcp_client.py
```

### Step 4: Implement contracts

- `backend/app/models/contracts.py`

### Step 5: Implement ToolRegistry

- `backend/app/tools/registry.py`
- `backend/app/tools/data_tools.py`
- `backend/app/tools/sql_tools.py`
- `backend/app/tools/report_tools.py`

### Step 6: Implement SubGraphs

- `backend/app/agent/data_graph.py`
- `backend/app/agent/sql_graph.py`
- `backend/app/agent/report_graph.py`
- `backend/app/agent/parent_graph.py`

### Step 7: Implement infrastructure

- `backend/app/infra/checkpoint/factory.py`
- `backend/app/infra/checkpoint/session.py`

### Step 8: Refactor main.py

- Session management
- Checkpoint integration
- SSE streaming

### Step 9: Verify

```bash
pip install -r backend/requirements.txt
uvicorn app.main:app --port 8100 --reload
```

---

## File Manifest (Phase 1 Output)

```
backend/app/
├── __init__.py
├── main.py                    # REFACTORED
├── llm.py                     # UNCHANGED
├── db.py                      # UNCHANGED
├── models/
│   ├── __init__.py
│   └── contracts.py           # NEW
├── agent/
│   ├── __init__.py
│   ├── parent_graph.py        # NEW
│   ├── data_graph.py          # NEW
│   ├── sql_graph.py           # NEW
│   └── report_graph.py        # NEW
├── tools/
│   ├── __init__.py
│   ├── registry.py            # NEW
│   ├── data_tools.py          # NEW
│   ├── sql_tools.py           # NEW
│   └── report_tools.py        # NEW
├── infra/
│   ├── __init__.py
│   └── checkpoint/
│       ├── __init__.py
│       ├── factory.py         # NEW
│       └── session.py         # NEW
├── old/                       # MOVED (archive)
│   ├── agent.py
│   ├── nodes.py
│   ├── state.py
│   ├── tools.py
│   ├── schemas.py
│   ├── clarify_tool.py
│   └── mcp_client.py
├── requirements.txt           # UNCHANGED
└── seed_data.sql              # UNCHANGED
```
