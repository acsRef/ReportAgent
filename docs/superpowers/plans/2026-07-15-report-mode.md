# Report Mode Implementation Plan (MCP Architecture)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a natural-language-to-report feature: users ask data questions in chat, a LangGraph Agent generates/executes SQL via LLM + MCP tools, returns results as table + chart + insight, with a debug panel showing the agent's execution trace.

**Architecture:** Two backend services + one frontend. The frontend React Chat page renders report results (table/chart/insight) and a debug panel. A FastAPI + LangGraph backend orchestrates the Agent. Schema discovery is handled by a standalone **MCP Schema Server** — the LLM calls it as a tool via MCP protocol during SQL generation, enabling dynamic on-demand schema retrieval instead of one-shot pre-fetch.

**Tech Stack:** Frontend — React 19 + TypeScript + ECharts + Zustand. Backend — FastAPI + LangGraph + DuckDB + OpenAI-compatible LLM client + mem0（记忆层）. MCP — Python MCP SDK (`mcp` package), ChromaDB vector search.

---

## Architecture Diagram (Interview Version)

```
┌─ User ──────────────────────────┐
│  ChatPage (React + TS)          │
│  ├─ ChatInput / ChatMessage     │
│  ├─ ReportResult (Table+Chart)  │
│  ├─ InsightBlock                │
│  └─ DebugPanel (Trace log)      │
└──────────┬───▲──────────────────┘
           │   │ SSE Stream
           ▼   │
┌──────────────────────────────────────────┐
│ ReportAgent Backend (FastAPI :8001)      │
│                                           │
│  ┌─ LangGraph Agent ──────────────────┐  │
│  │                                     │  │
│  │  intent_classify → gen_sql_with_tools → validate → execute → assemble
│  │                         │                                     │
│  │                    MCP Client                          chart_advisor
│  │                         │                              insight_analyst
│  │                         ▼                               (local funcs)
│  │              ┌──────────────────┐                       │
│  │              │ MCP Schema Svr   │                       │
│  │              │ (:8002)          │                       │
│  │              │                  │                       │
│  │              │ search_tables()  │                       │
│  │              │ get_table_ddl()  │                       │
│  │              │ list_tables()    │                       │
│  │              └──────────────────┘                       │
│  └─────────────────────────────────────────────────────────┘
│                                           │
│  DuckDB (embedded, read-only)             │
└──────────────────────────────────────────┘
```

**MCP 的关键设计：**
- `gen_sql_with_tools` 节点将 MCP tools 绑定给 LLM → LLM 在生成 SQL 过程中自主调 `search_tables`/`get_table_ddl`
- 不再有独立的 `schema_linking` 节点 —— schema 发现从"一次性预取"变成"按需动态检索"
- MCP Server 是独立进程，可被 Claude Desktop / Cline / 其他 Agent 复用

**mem0 记忆层：**
- `classify` 节点前：从 mem0 检索用户历史偏好（"上次只看华南区"、"偏好柱状图"），注入 system prompt
- `assemble` 节点后：将本次交互中的新偏好/事实写回 mem0
- 实现多轮对话记忆的自动提取和向量检索，不需要手写规则

---

## File Structure

### Frontend (existing files to modify)
| File | Responsibility |
|------|---------------|
| `frontend/src/types/chat.ts` | Add `trace`, `insight`, `report` types |
| `frontend/src/types/api.ts` | Add report response types |
| `frontend/src/services/chatApi.ts` | Extend SSE handling for `trace` / `insight` / `report` events |
| `frontend/src/stores/chatStore.ts` | Add trace_log, insight to store |
| `frontend/src/components/chat/ChatMessage.tsx` | Integrate InsightBlock, DebugPanel |
| `frontend/src/components/chat/ChatMessage.css` | Add styles for debug panel |
| `frontend/src/pages/ChatPage.tsx` | Wire up new SSE callbacks |
| `frontend/src/pages/ChatPage.css` | Minor layout adjustments |

### Frontend (new files to create)
| File | Responsibility |
|------|---------------|
| `frontend/src/components/chat/InsightBlock.tsx` | Display insight text with icon/color |
| `frontend/src/components/chat/InsightBlock.css` | Styles for insight block |
| `frontend/src/components/chat/DebugPanel.tsx` | Collapsible agent trace panel |
| `frontend/src/components/chat/DebugPanel.css` | Styles for debug panel |

### Backend: ReportAgent Service (:8001)
| File | Responsibility |
|------|---------------|
| `backend/app/__init__.py` | Package init |
| `backend/app/main.py` | FastAPI app, SSE streaming endpoints |
| `backend/app/state.py` | AgentState TypedDict |
| `backend/app/agent.py` | LangGraph agent graph definition |
| `backend/app/nodes.py` | Agent nodes: classify, gen_sql_with_tools, validate, execute, correct, clarify, assemble |
| `backend/app/tools.py` | Tool functions: run_sql, validate_sql, chart_advisor, insight_analyst |
| `backend/app/llm.py` | LLM client wrapper (OpenAI-compatible) |
| `backend/app/db.py` | DuckDB setup + sample data seed |
| `backend/app/schemas.py` | Pydantic request/response models |
| `backend/app/mcp_client.py` | MCP client — connects to MCP Schema Server, fetches tool list, calls tools |
| `backend/app/memory.py` | mem0 记忆层 — 多轮对话记忆存储和检索 |
| `backend/requirements.txt` | Python dependencies |
| `backend/seed_data.sql` | SQL script with sample retail data |

### Backend: MCP Schema Server (:8002)
| File | Responsibility |
|------|---------------|
| `mcp-schema-server/__init__.py` | Package init |
| `mcp-schema-server/server.py` | MCP server entry point, tool definitions |
| `mcp-schema-server/registry.py` | Schema registry — introspects DuckDB, builds search index |
| `mcp-schema-server/vector_store.py` | ChromaDB-based vector search for table schemas |
| `mcp-schema-server/requirements.txt` | Python dependencies |

---

## Task Breakdown

### Subsystem 1: Frontend Report Components
*(unchanged from original plan — Tasks 1.1–1.6)*

#### Task 1.1: Extend Types for Report/Insight/Trace
#### Task 1.2: Extend SSE Handling in chatApi
#### Task 1.3: Update ChatStore for Trace and Report
#### Task 1.4: Create InsightBlock Component
#### Task 1.5: Create DebugPanel Component
#### Task 1.6: Integrate Everything into ChatPage

---

### Subsystem 2: MCP Schema Server

#### Task 2.1: MCP Schema Server Scaffold

**Files:**
- Create: `mcp-schema-server/__init__.py`
- Create: `mcp-schema-server/requirements.txt`

- [ ] **Step 1: Create requirements.txt**

`mcp-schema-server/requirements.txt`:

```
mcp>=1.0.0
duckdb>=1.1.0
chromadb>=0.5.0
sentence-transformers>=3.0.0
httpx>=0.27.0
```

- [ ] **Step 2: Create package init (empty)**

- [ ] **Step 3: Commit**

```bash
cd d:/PyProject/ReportAgent && git add mcp-schema-server/ && git commit -m "chore: scaffold MCP Schema Server"
```

---

#### Task 2.2: Schema Registry — DB Introspection + Search Index

**Files:**
- Create: `mcp-schema-server/registry.py`

- [ ] **Step 1: Create schema registry**

`mcp-schema-server/registry.py`:

```python
"""Schema Registry — introspects DuckDB tables, builds search index.

Designed to be called by MCP tools. On startup, it connects to DuckDB,
reads all table schemas, generates natural-language descriptions,
and indexes them in ChromaDB for semantic search.
"""
import json
import duckdb
from pathlib import Path
from typing import Optional
import chromadb
from chromadb.config import Settings as ChromaSettings


_DB_PATH = Path(__file__).parent.parent / "backend" / "report.duckdb"
_COLLECTION_NAME = "table_schemas"


class SchemaRegistry:
    """Schema registry with vector search for table discovery."""

    def __init__(self):
        self._chroma = chromadb.Client(Settings(
            chroma_db_impl="duckdb+parquet",
            persist_directory=str(Path(__file__).parent / ".chroma"),
        ))
        self._collection = None

    # ── Initialization ──

    def build_index(self, embedding_fn):
        """Introspect DuckDB and build vector index of table schemas."""
        conn = duckdb.connect(str(_DB_PATH), read_only=True)
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main'"
        ).fetchall()

        docs = []
        ids = []
        metadatas = []
        for (tname,) in tables:
            cols = conn.execute(
                f"SELECT column_name, data_type FROM information_schema.columns "
                f"WHERE table_schema='main' AND table_name='{tname}'"
            ).fetchall()

            # Build column descriptions
            col_lines = []
            for cname, ctype in cols:
                col_lines.append(f"- {cname} ({ctype})")

            # Generate a natural-language description for search
            description = self._gen_description(tname, [c[0] for c in cols])
            ddl = f"CREATE TABLE {tname} (\n" + ",\n".join(
                f"  {c[0]} {c[1]}" for c in cols
            ) + "\n);"

            doc_text = f"表名: {tname}\n描述: {description}\n\n字段:\n" + "\n".join(col_lines) + f"\n\nDDL:\n{ddl}"
            docs.append(doc_text)
            ids.append(f"table_{tname}")
            metadatas.append({
                "table_name": tname,
                "columns": json.dumps([{"name": c[0], "type": c[1]} for c in cols], ensure_ascii=False),
                "ddl": ddl,
                "description": description,
            })

        conn.close()

        # Create or replace collection
        try:
            self._chroma.delete_collection(_COLLECTION_NAME)
        except Exception:
            pass

        self._collection = self._chroma.create_collection(_COLLECTION_NAME)

        # Generate embeddings and add to collection
        if docs:
            embeddings = embedding_fn(docs)
            self._collection.add(
                documents=docs,
                embeddings=embeddings,
                ids=ids,
                metadatas=metadatas,
            )

        return len(docs)

    def _gen_description(self, table_name: str, columns: list[str]) -> str:
        """Map table name + columns to a natural-language description.
        
        Uses heuristics based on naming conventions. In production,
        these would come from table/column comments in the database.
        """
        descriptions = {
            "regions": "区域和城市映射表，包含华北/华东/华南/西南等大区及对应城市",
            "products": "产品信息表，包含产品名称、所属品类和单价",
            "sales": "销售记录表，含区域、城市、产品、数量、销售日期和金额，每条记录代表一笔销售",
            "returns": "退货记录表，关联销售记录，包含退货日期和退货原因",
        }
        if table_name in descriptions:
            return descriptions[table_name]

        # Fallback: generate from column names
        desc = f"表 {table_name}，包含字段: {', '.join(columns)}"
        return desc

    # ── MCP Tool Implementations ──

    def search_tables(self, query: str, top_k: int = 3) -> list[dict]:
        """Semantic search for tables relevant to a natural-language query."""
        col = self._get_collection()
        if col is None:
            return self._fallback_search(query, top_k)

        # Get query embedding using the same embedding function
        results = col.query(query_texts=[query], n_results=top_k)
        if not results or not results["metadatas"][0]:
            return self._fallback_search(query, top_k)

        output = []
        for meta, doc, dist in zip(
            results["metadatas"][0],
            results["documents"][0],
            results["distances"][0],
        ):
            output.append({
                "table_name": meta["table_name"],
                "description": meta.get("description", ""),
                "ddl": meta.get("ddl", ""),
                "columns": json.loads(meta.get("columns", "[]")),
                "score": round(1.0 - dist, 4),
            })
        return output

    def get_table_ddl(self, table_name: str) -> Optional[str]:
        """Get the full CREATE TABLE DDL for a specific table."""
        col = self._get_collection()
        if col is None:
            return None

        results = col.get(ids=[f"table_{table_name}"])
        if results and results["metadatas"]:
            return results["metadatas"][0].get("ddl")
        return None

    def list_tables(self) -> list[dict]:
        """List all available tables with brief descriptions."""
        col = self._get_collection()
        if col is None:
            return []

        results = col.get()
        if not results or not results["metadatas"]:
            return []

        return [
            {
                "table_name": m["table_name"],
                "description": m.get("description", ""),
                "column_count": len(json.loads(m.get("columns", "[]"))),
            }
            for m in results["metadatas"]
        ]

    def _get_collection(self):
        if self._collection is None:
            try:
                self._collection = self._chroma.get_collection(_COLLECTION_NAME)
            except Exception:
                return None
        return self._collection

    def _fallback_search(self, query: str, top_k: int) -> list[dict]:
        """Fallback keyword search when vector index is unavailable."""
        conn = duckdb.connect(str(_DB_PATH), read_only=True)
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main'"
        ).fetchall()

        results = []
        query_lower = query.lower()
        for (tname,) in tables:
            # Simple keyword match on table name
            keywords = set(query_lower.split())
            name_tokens = set(tname.lower().split("_"))
            if keywords & name_tokens:
                cols = conn.execute(
                    f"SELECT column_name, data_type FROM information_schema.columns "
                    f"WHERE table_schema='main' AND table_name='{tname}'"
                ).fetchall()
                ddl = f"CREATE TABLE {tname} (\n" + ",\n".join(
                    f"  {c[0]} {c[1]}" for c in cols
                ) + "\n);"
                results.append({
                    "table_name": tname,
                    "description": self._gen_description(tname, [c[0] for c in cols]),
                    "ddl": ddl,
                    "columns": [{"name": c[0], "type": c[1]} for c in cols],
                    "score": 0.5,
                })
        conn.close()
        return results[:top_k]


# Singleton
registry = SchemaRegistry()
```

- [ ] **Step 2: Commit**

```bash
cd d:/PyProject/ReportAgent && git add mcp-schema-server/registry.py && git commit -m "feat: add schema registry with ChromaDB vector search"
```

---

#### Task 2.3: MCP Server — Tool Definitions + Run

**Files:**
- Create: `mcp-schema-server/server.py`

- [ ] **Step 1: Create MCP server**

`mcp-schema-server/server.py`:

```python
"""MCP Schema Server — exposes database schema discovery tools via MCP protocol.

Start with: python server.py

The server is a standalone MCP-compatible process that can be consumed by:
  - Claude Desktop (via claude_desktop_config.json)
  - LangGraph agents (via MCP client)
  - Other MCP-compatible AI tools

Tools exposed:
  - search_tables: Semantic search for database tables
  - get_table_ddl:  Get full DDL for a specific table
  - list_tables:    List all available tables
"""
import asyncio
import json
import sys
from typing import Any

from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.types import Tool, TextContent, CallToolResult

from .registry import registry


server = Server("schema-registry")


def _get_embedding_fn():
    """Get the embedding function for vector search.
    
    Uses sentence-transformers for local embedding. If unavailable,
    falls back to a simple hash-based embedding for ChromaDB.
    """
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        return lambda texts: model.encode(texts).tolist()
    except ImportError:
        # Fallback: use ChromaDB's default embedding function
        return None


@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_tables",
            description="Semantic search for database tables relevant to a query. "
                        "Example: search_tables('退货率趋势') → finds returns, sales tables",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query describing what data you're looking for",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (default: 3)",
                        "default": 3,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_table_ddl",
            description="Get the full CREATE TABLE DDL for a specific table. "
                        "Use this when you need to know all columns and their types. "
                        "Example: get_table_ddl('sales')",
            inputSchema={
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "Name of the table (e.g. 'sales', 'products', 'returns')",
                    },
                },
                "required": ["table_name"],
            },
        ),
        Tool(
            name="list_tables",
            description="List all available tables with brief descriptions and column counts.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
    if arguments is None:
        arguments = {}

    if name == "search_tables":
        query = arguments.get("query", "")
        top_k = arguments.get("top_k", 3)
        results = registry.search_tables(query, top_k)
        return [TextContent(
            type="text",
            text=json.dumps(results, ensure_ascii=False, indent=2),
        )]

    elif name == "get_table_ddl":
        table_name = arguments.get("table_name", "")
        ddl = registry.get_table_ddl(table_name)
        if ddl:
            return [TextContent(type="text", text=ddl)]
        return [TextContent(type="text", text=f"Table '{table_name}' not found")]

    elif name == "list_tables":
        tables = registry.list_tables()
        return [TextContent(
            type="text",
            text=json.dumps(tables, ensure_ascii=False, indent=2),
        )]

    raise ValueError(f"Unknown tool: {name}")


async def main():
    from mcp.server.stdio import stdio_server

    # Build search index on startup
    embedding_fn = _get_embedding_fn()
    count = registry.build_index(embedding_fn)
    print(f"Schema registry initialized: {count} tables indexed", file=sys.stderr)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="schema-registry",
                server_version="1.0.0",
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Commit**

```bash
cd d:/PyProject/ReportAgent && git add mcp-schema-server/server.py && git commit -m "feat: add MCP server with search_tables/get_table_ddl/list_tables tools"
```

---

### Subsystem 3: ReportAgent Backend (FastAPI + LangGraph)

#### Task 3.1: Backend Scaffold + Dependencies

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/app/__init__.py`

- [ ] **Step 1: Create requirements.txt**

`backend/requirements.txt`:

```
fastapi==0.115.0
uvicorn[standard]==0.30.0
sse-starlette==2.1.0
langgraph==0.2.0
langchain-core==0.3.0
langchain-openai==0.2.0
duckdb==1.1.0
pydantic==2.9.0
python-dotenv==1.0.0
httpx==0.27.0
mcp>=1.0.0
```

- [ ] **Step 2: Create package init (empty)**

- [ ] **Step 3: Commit**

---

#### Task 3.2: DuckDB — 星型模型 + 示例数据

沿用 `backend/seed_data.sql`，完整设计：

##### 数据模型（3个业务域，6维3事实）

```
📦 零售 + 电商 星型模型
                                    ┌─────────────┐
                         ┌──────────│ dim_product  │◄──────────┐
                         │          │ (20 products)│           │
                         │          └─────────────┘           │
                         │                                    │
┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│ dim_region  │◄──│ fact_sales  │──►│ dim_customer│   │ dim_date    │
│ (17 regions)│   │ (48 records)│   │ (12 custs)  │   │ (52 days)   │
└─────────────┘   └──────┬──────┘   └─────────────┘   └─────────────┘
                         │              │
                         ▼              ▼
                  ┌─────────────┐   ┌─────────────┐
                  │ fact_returns│   │ fact_invtry │
                  │ (12 records)│   │ (30 records)│
                  └─────────────┘   └─────────────┘
                       
┌─────────────┐   ┌─────────────┐
│ dim_employee│   │ dim_warehse │
│ (8 empls)   │   │ (5 whses)   │
└──────┬──────┘   └──────┬──────┘
       │                 │
       ▼                 ▼
┌─────────────┐   ┌─────────────┐
│ fact_attend │   │ fact_invtry │
│ (20 rec)    │   │ (reused)    │
└─────────────┘   └─────────────┘
```

##### 各表行数和面试价值

| 表 | 行数 | 面试能问的 |
|:---|:----|:----------|
| `dim_product` | 20 | 品类/子品类/品牌多层下钻 |
| `dim_region` | 17 | 大区→省→城市→等级 |
| `dim_customer` | 12 | 客户等级分析（钻石/金卡/银卡/普通）|
| `dim_date` | 52 | 年/季度/月/周 + 节假日标记 |
| `dim_employee` | 8 | 部门/岗位维度 |
| `dim_warehouse` | 5 | 仓库容量 |
| `fact_sales` | 48 | 含折扣、成本、利润 — 可以做毛利分析 |
| `fact_returns` | 12 | 退货原因、处理方式 — 关联 sales |
| `fact_inventory` | 30 | 库存量/预留量/可售量 — 分仓库 |
| `fact_attendance` | 20 | 考勤状态/工时 — 关联 employee |

##### 天然可问的分析问题

这些问题的 SQL 涉及 2-4 表 JOIN + 聚合，能充分展示 Agent 能力：

1. "Q1 各区域销售额排名" → `sales + region`，GROUP BY
2. "哪个品类退货率最高？" → `sales + returns + product`，多表 JOIN + 聚合
3. "毛利率最高的产品 TOP5" → `sales + product`，利润/成本计算
4. "华南区库存不足的产品" → `inventory + product + region`，多条件过滤
5. "哪个供应商的产品利润最低？" → `sales + product`，按供应商聚合
6. "节假日 vs 工作日销售额对比" → `sales + date`，is_holiday 分组

---

#### Task 3.3: LLM Client Wrapper

*(unchanged)*

---

#### Task 3.4: mem0 Memory Layer

**Files:**
- Create: `backend/app/memory.py`
- Modify: `backend/requirements.txt` (add mem0)

- [ ] **Step 1: Add mem0 to requirements.txt**

In `backend/requirements.txt`, append:
```
mem0ai>=0.1.0
```

- [ ] **Step 2: Create memory module**

`backend/app/memory.py`:

```python
"""mem0 记忆层 — 多轮对话记忆存储和检索。

设计：
- 每个用户会话对应一个 mem0 memory_id
- classify 节点前：检索历史偏好，注入 system prompt
- assemble 节点后：将本次交互中提取的新偏好写回

mem0 自动完成：
1. 实体提取（表名、字段、区域、时间范围）
2. 偏好识别（"喜欢柱状图"、"只看华南区"）
3. 事实存储（"退货率最高的是产品A"）
4. 向量检索 + 时间衰减排序
"""
from __future__ import annotations
import os
from dotenv import load_dotenv
from mem0 import Memory

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

# mem0 配置
_MEM0_CONFIG = {
    "llm": {
        "provider": "openai",
        "config": {
            "model": os.getenv("LLM_MODEL", "deepseek-chat"),
            "api_key": os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY"),
            "base_url": os.getenv("LLM_BASE_URL", "https://api.minimax.chat/v1"),
        },
    },
    "embedder": {
        "provider": "openai",
        "config": {
            "model": os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
            "api_key": os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY"),
            "base_url": os.getenv("LLM_BASE_URL", "https://api.minimax.chat/v1"),
        },
    },
    "vector_store": {
        "provider": "chroma",
        "config": {
            "collection_name": "report_memories",
            "path": os.path.join(os.path.dirname(__file__), "..", ".mem0"),
        },
    },
}

_memory_client: Memory | None = None


def get_memory() -> Memory:
    global _memory_client
    if _memory_client is None:
        _memory_client = Memory.from_config(_MEM0_CONFIG)
    return _memory_client


def search_memories(query: str, user_id: str, limit: int = 5) -> list[str]:
    """Search relevant memories for a user query.
    
    Called in classify_intent node to inject user preferences.
    Returns list of memory text snippets.
    """
    try:
        memories = get_memory().search(query, user_id=user_id, limit=limit)
        return [m.get("text", "") for m in memories if m.get("text")]
    except Exception:
        return []


def add_memory(message: str, user_id: str) -> None:
    """Store a message/summary to memory.
    
    Called at the end of assemble_report node.
    mem0 will automatically extract entities, preferences, and facts.
    """
    try:
        get_memory().add(message, user_id=user_id)
    except Exception:
        pass


def format_memory_context(memories: list[str]) -> str:
    """Format memories as injectable context string."""
    if not memories:
        return ""
    lines = [f"- {m}" for m in memories]
    return "用户历史信息:\n" + "\n".join(lines)
```

- [ ] **Step 3: Commit**

```bash
cd d:/PyProject/ReportAgent && git add backend/app/memory.py && git commit -m "feat: add mem0 memory layer"
```

---

#### Task 3.5: MCP Client — Connect to MCP Schema Server

**Files:**
- Create: `backend/app/mcp_client.py`

- [ ] **Step 1: Create MCP client module**

`backend/app/mcp_client.py`:

```python
"""MCP client for connecting to the MCP Schema Server.

This module wraps the MCP protocol to call tools on the schema-registry server.
In the LangGraph agent, these are exposed as tools the LLM can call directly.

Two transport options:
  1. stdio — spawns the MCP server as a subprocess (simpler, for dev)
  2. SSE — connects to a running MCP server via HTTP (for production)
"""
from __future__ import annotations
import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from langchain_core.tools import tool
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.types import CallToolResult


class MCPSchemaClient:
    """MCP client for schema-registry server. Provides typed access to tools."""

    def __init__(self):
        self._session = None
        self._exit_stack = None
        self._connected = False

    async def connect(self):
        """Connect to the MCP schema server via stdio transport."""
        server_params = StdioServerParameters(
            command="python",
            args=["-m", "mcp_schema_server.server"],
        )
        self._exit_stack = asyncio.ExitStack()
        read_stream, write_stream = await self._exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        from mcp.client.session import ClientSession
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await self._session.initialize()
        self._connected = True

    async def disconnect(self):
        if self._exit_stack:
            await self._exit_stack.aclose()
            self._session = None
            self._exit_stack = None
            self._connected = False

    async def search_tables(self, query: str, top_k: int = 3) -> list[dict]:
        """Semantic search for database tables relevant to a query."""
        if not self._connected:
            return []
        result = await self._session.call_tool("search_tables", {
            "query": query,
            "top_k": top_k,
        })
        return json.loads(result.content[0].text)

    async def get_table_ddl(self, table_name: str) -> str | None:
        """Get full DDL for a specific table."""
        if not self._connected:
            return None
        result = await self._session.call_tool("get_table_ddl", {
            "table_name": table_name,
        })
        text = result.content[0].text
        if text.startswith("Table '"):
            return None
        return text

    async def list_tables(self) -> list[dict]:
        """List all available tables."""
        if not self._connected:
            return []
        result = await self._session.call_tool("list_tables", {})
        return json.loads(result.content[0].text)

    # ── LangChain @tool wrappers (for ToolNode compatibility) ──

    @property
    def search_tables_wrapper(self):
        @tool
        async def search_tables(query: str, top_k: int = 3) -> str:
            """语义搜索数据库表：输入自然语言查询，返回最相关的表和字段结构。
            例：search_tables('退货率趋势') → 返回 returns, sales 表"""
            results = await self.search_tables(query, top_k)
            return json.dumps(results, ensure_ascii=False)
        return search_tables

    @property
    def get_table_ddl_wrapper(self):
        @tool
        async def get_table_ddl(table_name: str) -> str:
            """获取某张表的完整 CREATE TABLE DDL，包含所有字段名和类型。
            在写 SQL 需要确认字段时调用。例：get_table_ddl('sales')"""
            ddl = await self.get_table_ddl(table_name)
            return ddl or f"Table '{table_name}' not found"
        return get_table_ddl

    @property
    def list_tables_wrapper(self):
        @tool
        async def list_tables() -> str:
            """列出数据库中所有可用的表及其简要描述。
            当不确定有哪些表时，先调这个看看总览。"""
            results = await self.list_tables()
            return json.dumps(results, ensure_ascii=False)
        return list_tables

    def get_tool_definitions(self) -> list[dict]:
        """Return tool definitions in LangChain-compatible format.
        
        These are used to bind tools to the LLM in the LangGraph agent.
        """
        return [
            {
                "name": "search_tables",
                "description": "语义搜索数据库表：输入自然语言查询，返回最相关的表和字段结构。"
                               "例：search_tables('退货率趋势') → 返回 returns, sales 表",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "自然语言查询"},
                        "top_k": {"type": "integer", "description": "返回结果数", "default": 3},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "get_table_ddl",
                "description": "获取某张表的完整 CREATE TABLE DDL，包含所有字段名和类型。"
                               "在写 SQL 需要确认字段时调用。例：get_table_ddl('sales')",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "table_name": {"type": "string", "description": "表名"},
                    },
                    "required": ["table_name"],
                },
            },
            {
                "name": "list_tables",
                "description": "列出数据库中所有可用的表及其简要描述。"
                               "当不确定有哪些表时，先调这个看看总览。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        ]


# Singleton — created once at app startup
schema_client = MCPSchemaClient()
```

- [ ] **Step 2: Commit**

```bash
cd d:/PyProject/ReportAgent && git add backend/app/mcp_client.py && git commit -m "feat: add MCP client for schema-registry server"
```

---

#### Task 3.6: Agent State + Pydantic Schemas

**Files:**
- Create: `backend/app/state.py`
- Create: `backend/app/schemas.py`

- [ ] **Step 1: Define AgentState with session_id and memory_context**

`backend/app/state.py`:

```python
from typing import TypedDict, Optional, Any


class TraceStep(TypedDict):
    step: str        # 节点名，如 "SQL 生成" / "MCP: search_tables"
    status: str      # success / error / retry / clarify
    detail: str      # 详细描述（含错误信息 / 工具调用参数 / 结果摘要）
    duration: str    # 耗时，如 "0.3s"
    input: str       # 可选：该步骤的输入摘要（调试面板用）
    output: str      # 可选：该步骤的输出摘要（调试面板用）


class AgentState(TypedDict):
    messages: list              # 对话历史
    user_query: str             # 当前用户问题
    session_id: str             # 会话ID（用于 mem0 记忆检索）
    intent: str                 # 报表 / 看板 / 闲聊
    memory_context: str         # mem0 检索到的历史记忆文本
    schema_context: str         # 检索到的表结构 DDL（备用的直接传递，MCP 故障时降级）
    generated_sql: str          # 生成的 SQL
    sql_valid: bool             # SQL 是否通过验证
    sql_result: str             # 查询结果（JSON 字符串）
    sql_error: str              # 错误信息
    retry_count: int            # 重试次数（最多 3 次）
    need_clarification: bool    # 是否需要反问用户
    clarification_question: str # 反问内容
    chart_config: dict          # 图表推荐结果（类型 + 配置）
    insight_text: str           # 洞察文字
    trace_log: list[TraceStep]  # 调试链路日志（前端 DebugPanel 展示）
    # Plan & Execute 分析计划
    assemble_plan: list         # 分析步骤 [{tool, args, description}]
    assemble_step_idx: int      # 当前执行到的步骤索引
    assemble_results: list      # 每步结果 [{step, result}]
```

- [ ] **Step 2: Define Pydantic schemas**

`backend/app/schemas.py`:

```python
from __future__ import annotations
from pydantic import BaseModel
from typing import Optional, Any


class ChatRequest(BaseModel):
    user_query: str
    session_id: str


class ColumnDef(BaseModel):
    key: str
    title: str


class TableData(BaseModel):
    columns: list[ColumnDef]
    rows: list[dict[str, Any]]


class ChartConfig(BaseModel):
    type: str
    config: dict[str, Any]


class Answer(BaseModel):
    text: str
    table: Optional[TableData] = None
    chart: Optional[ChartConfig] = None
    insight: Optional[str] = None


class TraceStepOut(BaseModel):
    step: str
    status: str
    detail: str
    duration: str


class ChatResponse(BaseModel):
    answer: Answer
    trace: list[TraceStepOut]
```

- [ ] **Step 3: Commit**

```bash
cd d:/PyProject/ReportAgent && git add backend/app/state.py backend/app/schemas.py && git commit -m "feat: add AgentState and Pydantic schemas"
```

---

#### Task 3.7: Agent Tools（含安全设计）

**Files:**
- Modify: `backend/app/tools.py`

tools.py 包含 4 个工具：

| 工具 | 权限级别 | 白名单 | 黑名单 |
|:----|:--------|:-------|:-------|
| `run_sql` | READ_ONLY | 仅 `.upper()` 以 `SELECT` 开头 | INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE |
| `validate_sql` | READ_ONLY | 仅 SELECT + EXPLAIN | 同上 |
| `chart_advisor` | INTERNAL | 仅本系统调用 | — |
| `insight_analyst` | INTERNAL | 仅本系统调用 | — |

安全校验核心逻辑：

```python
def _check_sql_safety(sql: str) -> tuple[bool, str]:
    """SQL 安全检查：白名单 + 黑名单双重校验。"""
    sql_upper = sql.strip().upper()
    if not sql_upper.startswith("SELECT"):
        return False, "只允许 SELECT 查询语句"
    DANGEROUS_KEYWORDS = [
        "INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
        "TRUNCATE", "CREATE", "REPLACE", "GRANT", "REVOKE",
        "EXECUTE", "EXEC", "CALL", "MERGE", "LOAD",
    ]
    tokens = set(sql_upper.split())
    for kw in DANGEROUS_KEYWORDS:
        if kw in tokens:
            return False, f"禁止使用 {kw}，仅支持只读查询"
    return True, ""
```

安全防线总计：
1. **代码层**：白名单（只允许 SELECT）+ 黑名单（禁止 12 种危险关键字）
2. **连接层**：DuckDB `read_only=True`，数据库级别禁止写入
3. **语法层**：`EXPLAIN` 预检 SQL 对象存在性
4. **策略层**：`chart_advisor` / `insight_analyst` 标记为 INTERNAL 工具，不暴露给 LLM

额外工具——`ask_clarification_tool`（内部工具，不暴露给 MCP 客户端）：

```python
from langchain_core.tools import tool
from langgraph.types import interrupt


@tool
async def ask_clarification_tool(question: str) -> str:
    """当用户问题缺少关键信息时调用此工具追问。
    
    例：用户没说区域 → ask_clarification_tool("请问看哪个区域？")
    调用后暂停 Agent 等用户回复，回复后继续。
    """
    return interrupt({
        "type": "clarify",
        "question": question,
    })
```

---

#### Task 3.8: Agent Nodes with MCP Tools

**Files:**
- Create: `backend/app/nodes.py`

- [ ] **Step 1: Create agent nodes**

`backend/app/nodes.py`:

```python
"""LangGraph agent nodes for the report generation pipeline.

Key design: The `gen_sql_with_tools` node binds MCP Schema Server tools
to the LLM, enabling dynamic on-demand schema discovery during SQL generation.
"""
from __future__ import annotations
import json
import time
from typing import Any

from app.state import AgentState, TraceStep
from app.tools import run_sql, validate_sql
from app.llm import get_chat_llm
from app.mcp_client import schema_client
from app.memory import search_memories, add_memory, format_memory_context


def make_trace(step: str, status: str, detail: str, start: float) -> TraceStep:
    return TraceStep(
        step=step,
        status=status,
        detail=detail,
        duration=f"{time.time() - start:.1f}s"
    )


def classify_intent(state: AgentState) -> dict:
    start = time.time()
    query = state["user_query"]

    # ── mem0: 检索用户历史偏好 ──
    user_id = state.get("session_id", "default_user")
    memories = search_memories(query, user_id)
    memory_context = format_memory_context(memories)

    keywords_report = ["销售", "排名", "趋势", "增长", "利润", "退货",
                       "多少", "统计", "哪个", "分析", "最高", "最低",
                       "占比", "比较", "去年", "本月", "上月", "季度"]
    keywords_chitchat = ["你好", "hi", "hello", "你是谁", "你能做什么"]

    q = query.lower()
    if any(k in q for k in keywords_chitchat):
        intent = "闲聊"
    elif any(k in q for k in keywords_report):
        intent = "报表"
    else:
        intent = "报表"

    trace = make_trace("意图分类", "success", f"识别为: {intent}", start)
    return {
        "intent": intent,
        "memory_context": memory_context,
        "trace_log": [trace],
    }


async def gen_sql_llm(state: AgentState) -> dict:
    """LLM node with MCP tools bound for dynamic schema discovery.
    
    LangGraph ToolNode + tools_condition handle the loop:
      gen_sql_llm ─→ (has tool_call?) ─→ mcp_tools → gen_sql_llm
                        │ (no tool_call)
                        ▼
                      validate
    """
    start = time.time()

    if state["intent"] != "报表":
        return {"trace_log": [make_trace("SQL 生成", "success", "非报表模式，跳过", start)]}

    llm = get_chat_llm()
    # MCP tools + 内部澄清工具
    tool_defs = schema_client.get_tool_definitions() + [
        {
            "name": "ask_clarification",
            "description": "当用户问题缺少关键信息时调用此工具向用户追问。"
                           "例：用户没说区域 → ask_clarification('请问您想看哪个区域的数据？')",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "追问问题（一句话）"},
                },
                "required": ["question"],
            },
        },
    ]

    _SYSTEM = """你是一个数据分析助手，工作语言是中文。
请根据用户的问题，使用可用工具发现数据库表结构，然后生成合适的 SQL 查询语句。

可用工具：
{tool_defs}

{memory_block}

工作流程：
1. 先调 list_tables() 看看有哪些表
2. 如果用户问题缺少关键信息，调 ask_clarification 追问
3. 调 search_tables("关键词") 语义搜索相关表
4. 调 get_table_ddl("表名") 查看具体字段
5. 综合信息后生成 SQL

规则：
- 只生成 SELECT 语句
- 表名和列名不加反引号
- 使用 SUM/COUNT/AVG/GROUP BY/ORDER BY
- 返回纯 SQL，不加额外解释"""

    memory_ctx = state.get("memory_context", "")
    mem = memory_ctx if memory_ctx else "（无历史记忆）"
    system_prompt = _SYSTEM.replace("{tool_defs}", str(tool_defs)).replace("{memory_block}", mem)

    llm_with_tools = llm.bind_tools(tool_defs, tool_choice="auto")

    human = f"用户问题: {state['user_query']}\n\n请先探索表结构，然后生成 SQL。"

    # Single LLM call — ToolNode handles the loop at graph level
    result = llm_with_tools.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": human},
    ])

    sql = result.content or ""
    if sql.startswith("```"):
        sql = sql.split("\n", 1)[-1]
        sql = sql.rsplit("```", 1)[0]
    sql = sql.strip()

    trace = make_trace("SQL 生成", "success", f"已生成 SQL:\n{sql}", start)
    return {"generated_sql": sql, "trace_log": [trace]}


def validate_sql_step(state: AgentState) -> dict:
    """Validate SQL syntax using DuckDB EXPLAIN."""
    start = time.time()
    sql = state.get("generated_sql", "")

    if not sql:
        return {"sql_valid": False, "sql_error": "无 SQL 语句",
                "trace_log": [make_trace("SQL 验证", "error", "无 SQL 语句", start)]}

    result = json.loads(validate_sql(sql))
    valid = result.get("valid", False)

    if valid:
        return {"sql_valid": True,
                "trace_log": [make_trace("SQL 验证", "success", "语法检查通过", start)]}
    else:
        error = result.get("error", "未知错误")
        return {"sql_valid": False, "sql_error": error,
                "trace_log": [make_trace("SQL 验证", "error", error, start)]}


def execute_sql_step(state: AgentState) -> dict:
    """Execute SQL against DuckDB (read-only)."""
    start = time.time()
    sql = state.get("generated_sql", "")

    if not sql:
        return {"trace_log": [make_trace("SQL 执行", "error", "无 SQL 语句", start)]}

    result = run_sql(sql)
    trace = make_trace("SQL 执行", "success", "查询已执行", start)
    return {"sql_result": result, "trace_log": [trace]}


async def self_correct(state: AgentState) -> dict:
    """Self-correct SQL on error. Can also call MCP tools for more schema info."""
    start = time.time()
    error = state.get("sql_error", "")
    old_sql = state.get("generated_sql", "")

    llm = get_chat_llm()

    prompt = f"""你是一个 SQL 修复助手。之前的 SQL 执行出错。

错误信息: {error}

原始 SQL:
{old_sql}

请修正这条 SQL 语句。如果需要查看表结构，可以使用 get_table_ddl 工具。
只返回修正后的 SQL 本身："""

    # Bind MCP tools so the LLM can look up schemas during correction
    llm_with_tools = llm.bind_tools(
        schema_client.get_tool_definitions(),
        tool_choice="auto",
    )
    response = llm_with_tools.invoke([{"role": "user", "content": prompt}])
    
    new_sql = response.content or ""
    if new_sql.startswith("```"):
        new_sql = new_sql.split("\n", 1)[-1]
        new_sql = new_sql.rsplit("```", 1)[0]
    new_sql = new_sql.strip()

    retry_count = state.get("retry_count", 0) + 1
    trace = make_trace(f"自我纠错(第{retry_count}次)", "success",
                       f"原始 SQL 出错: {error}\n已修正", start)

    return {
        "generated_sql": new_sql,
        "retry_count": retry_count,
        "sql_error": "",
        "trace_log": [trace],
    }


def clarify(state: AgentState) -> dict:
    """反问节点：暂停 Agent，等待用户补充信息。
    
    触发时机（两种）：
    A. LLM 在 gen_sql_llm 阶段发现用户问题模糊，主动触发
    B. 3 次 SQL 纠错都失败后被动触发
    
    使用 LangGraph interrupt 机制：
      interrupt(question) → Agent PAUSE → SSE 发前端
      → 用户回复 → Agent RESUME → 继续流程
    """
    from langgraph.types import interrupt
    
    start = time.time()
    question = state.get("clarification_question", "")

    if not question:
        llm = get_chat_llm()
        error_info = state.get("sql_error", "")
        user_query = state["user_query"]

        prompt = f"""用户的问题是: "{user_query}"
{'SQL 执行出错: ' + error_info if error_info else ''}

经过分析，这个问题缺少关键信息无法完成查询。
请生成一个简短的追问（一句话），引导用户补充以下信息之一：
1. 具体的时间范围（如"上个月"指哪年哪月？）
2. 具体的区域/维度（想看哪个区域？）
3. 明确的需求指标（想分析销售额、退货率还是别的？）

只返回追问问题本身，不要多余的解释："""

        response = llm.invoke(prompt)
        question = response.content.strip()

    trace = make_trace("主动澄清", "clarify", f"需要用户补充信息: {question}", start)

    # ⭐ interrupt: Agent 暂停，等待用户回复
    user_response = interrupt({
        "type": "clarify",
        "question": question,
    })

    trace = make_trace("主动澄清", "success", f"用户回复: {user_response}", start)

    return {
        "need_clarification": False,
        "clarification_answer": str(user_response),
        "user_query": str(user_response),  # 用用户回答作为新的查询
        "generated_sql": "",
        "sql_valid": False,
        "sql_result": "",
        "sql_error": "",
        "retry_count": 0,
        "trace_log": [trace],
    }
        "clarification_question": question,
        "trace_log": [trace],
    }


def assemble_planner(state: AgentState) -> dict:
    """Plan & Execute — 第一步：LLM 制定分析计划。
    
    根据 SQL 查询结果的数据特征，制定多步分析计划。
    每步执行一个分析工具，结果逐步累加形成最终洞察。
    """
    start = time.time()
    result_json = state.get("sql_result", "{}")
    data = json.loads(result_json)

    if "error" in data:
        return {"trace_log": [make_trace("报表组装", "error", data["error"], start)]}

    columns = data.get("columns", [])
    row_count = len(data.get("rows", []))
    
    llm = get_chat_llm()
    prompt = f"""你是一个数据分析规划师。根据以下数据特征，制定一个分析计划。

数据特征：
- 列: {', '.join(columns)}
- 行数: {row_count}

可用的分析工具：
1. trend_analysis(data) — 数据趋势分析
2. group_compare(data, group_col, value_col) — 按维度分组对比
3. detect_anomaly(data, value_col) — 检测异常值
4. recommend_chart(data) — 推荐可视化图表类型

请输出一个 JSON 格式的分析计划：
{{
  "steps": [
    {{"tool": "recommend_chart", "args": {{}}, "description": "第一步：推荐图表类型"}},
    {{"tool": "trend_analysis", "args": {{}}, "description": "第二步：分析整体趋势"}}
  ],
  "reasoning": "为什么这样分析"
}}
只返回 JSON，不要额外解释。"""

    response = llm.invoke(prompt)
    plan_text = response.content.strip()
    if plan_text.startswith("```"):
        plan_text = plan_text.split("\n", 1)[-1]
        plan_text = plan_text.rsplit("```", 1)[0]
    
    try:
        plan = json.loads(plan_text)
        steps = plan.get("steps", [])
    except json.JSONDecodeError:
        steps = [{"tool": "recommend_chart", "args": {}, "description": "推荐图表"}]

    trace = make_trace("报表规划", "success",
                       f"规划了 {len(steps)} 步分析: {', '.join(s['description'] for s in steps)}", start)

    return {
        "assemble_plan": steps,
        "assemble_step_idx": 0,
        "assemble_results": [],
        "trace_log": [trace],
    }


async def assemble_executor(state: AgentState) -> dict:
    """Plan & Execute — 第二步：执行分析计划中的当前步骤。
    
    执行完当前步骤后自动推进到下一步。
    如果某一步结果异常（如数据为空），LLM 判断是否需要调整计划。
    """
    start = time.time()
    plan = state.get("assemble_plan", [])
    step_idx = state.get("assemble_step_idx", 0)

    if not plan or step_idx >= len(plan):
        return _finalize_report(state, start)

    current_step = plan[step_idx]
    tool_name = current_step.get("tool", "")
    result_json = state.get("sql_result", "{}")

    # 执行当前分析步骤
    result_text = ""
    if tool_name == "trend_analysis":
        result_text = _trend_analysis(result_json)
    elif tool_name == "group_compare":
        group_col = current_step.get("args", {}).get("group_col", "")
        value_col = current_step.get("args", {}).get("value_col", "")
        result_text = _group_compare(result_json, group_col, value_col)
    elif tool_name == "detect_anomaly":
        value_col = current_step.get("args", {}).get("value_col", "")
        result_text = _detect_anomaly(result_json, value_col)
    elif tool_name == "recommend_chart":
        result_text = _recommend_chart(result_json)
    else:
        result_text = f"未知分析工具: {tool_name}"

    partial_results = list(state.get("assemble_results", []))
    partial_results.append({
        "step": current_step.get("description", tool_name),
        "result": result_text,
    })

    # LLM 检查结果，决定是否继续
    llm = get_chat_llm()
    check_prompt = f"""分析步骤 '{current_step.get('description', tool_name)}' 已完成。
结果: {result_text[:500]}

这个结果是否正常？回复 '继续' 或给出调整建议。"""
    check_response = llm.invoke(check_prompt)
    check_text = check_response.content.strip()

    trace_status = "success"
    trace_detail = f"执行: {current_step.get('description', tool_name)}"
    if "调整" in check_text or "修改" in check_text:
        trace_status = "retry"
        trace_detail += f" → 计划调整: {check_text[:100]}"

    trace = make_trace(f"报表分析({step_idx + 1}/{len(plan)})", trace_status, trace_detail, start)

    return {
        "assemble_step_idx": step_idx + 1,
        "assemble_results": partial_results,
        "trace_log": [trace],
    }


def _finalize_report(state: AgentState, start: float) -> dict:
    """综合所有分析结果，生成最终报表。"""
    partial_results = state.get("assemble_results", [])
    chart_config = {}
    insight_text = ""

    for pr in partial_results:
        if "推荐图表" in pr["step"] or "图表" in pr["step"]:
            try:
                chart_config = json.loads(pr["result"])
            except json.JSONDecodeError:
                chart_config = json.loads(_recommend_chart(state.get("sql_result", "{}")))
        else:
            insight_text += pr["result"] + "\n"

    if not chart_config:
        chart_config = json.loads(_recommend_chart(state.get("sql_result", "{}")))

    # LLM 综合生成一句话洞察
    llm = get_chat_llm()
    results_summary = "\n".join(f"- {pr['step']}: {pr['result'][:200]}" for pr in partial_results)
    insight_prompt = f"""基于以下分析结果，用一句话总结核心洞察（中文）：
{results_summary}"""
    insight_response = llm.invoke(insight_prompt)
    insight_text = insight_response.content.strip() or insight_text

    # mem0 记忆存储
    user_id = state.get("session_id", "default_user")
    add_memory(f"用户查询: {state['user_query']} | 洞察: {insight_text[:200]}", user_id)

    trace = make_trace("报表组装", "success",
                       f"综合 {len(partial_results)} 步分析结果，生成最终洞察", start)

    return {
        "chart_config": chart_config,
        "insight_text": insight_text,
        "assemble_plan": [],
        "assemble_step_idx": 0,
        "trace_log": [trace],
    }


# ── 分析工具函数 ─────────────────────────────

def _trend_analysis(data_json: str) -> str:
    data = json.loads(data_json)
    rows = data.get("rows", [])
    if len(rows) < 2:
        return "数据量不足，无法进行趋势分析"
    first = rows[0]
    numeric_keys = [k for k, v in first.items() if isinstance(v, (int, float))]
    if not numeric_keys:
        return "没有数值列，无法分析趋势"
    val_col = numeric_keys[0]
    values = [r[val_col] for r in rows if r.get(val_col) is not None]
    if len(values) >= 2:
        half = len(values) // 2
        first_avg = sum(values[:half]) / half
        second_avg = sum(values[half:]) / (len(values) - half)
        if second_avg > first_avg * 1.1:
            return f"整体呈上升趋势，后半段增长 {((second_avg/first_avg)-1)*100:.1f}%"
        elif first_avg > second_avg * 1.1:
            return f"整体呈下降趋势，后半段下降 {((first_avg/second_avg)-1)*100:.1f}%"
        else:
            return "整体趋势平稳"
    return "趋势分析完成"


def _group_compare(data_json: str, group_col: str = "", value_col: str = "") -> str:
    data = json.loads(data_json)
    rows = data.get("rows", [])
    if not rows:
        return "无数据"
    first = rows[0]
    if not group_col or group_col not in first:
        cat_keys = [k for k in first if not isinstance(first[k], (int, float))]
        group_col = cat_keys[0] if cat_keys else list(first.keys())[0]
    if not value_col or value_col not in first:
        num_keys = [k for k in first if isinstance(first[k], (int, float))]
        value_col = num_keys[0] if num_keys else list(first.keys())[-1]
    groups = {}
    for r in rows:
        g = str(r.get(group_col, "未知"))
        v = r.get(value_col, 0) or 0
        groups.setdefault(g, []).append(float(v))
    summary = [f"{g}: 合计={sum(vals):,.2f}" for g, vals in sorted(groups.items(), key=lambda x: sum(x[1]), reverse=True)]
    return "\n".join(summary)


def _detect_anomaly(data_json: str, value_col: str = "") -> str:
    import statistics
    data = json.loads(data_json)
    rows = data.get("rows", [])
    if not rows:
        return "无数据"
    first = rows[0]
    if not value_col or value_col not in first:
        num_keys = [k for k in first if isinstance(first[k], (int, float))]
        value_col = num_keys[0] if num_keys else ""
    if not value_col:
        return "没有数值列"
    values = [r[value_col] for r in rows if r.get(value_col) is not None]
    if len(values) < 3:
        return "数据量不足"
    try:
        mean = statistics.mean(values)
        stdev = statistics.stdev(values)
        threshold = 2 * stdev
        anomalies = []
        for r in rows:
            v = r.get(value_col, 0) or 0
            if abs(v - mean) > threshold:
                cat_keys = [k for k in first if not isinstance(first[k], (int, float))]
                label = str(r.get(cat_keys[0], "")) if cat_keys else ""
                anomalies.append(f"{label}: {v:,.2f}")
        if anomalies:
            return f"发现 {len(anomalies)} 个异常值: " + "; ".join(anomalies[:5])
        return "未发现明显异常值"
    except statistics.StatisticsError:
        return "无法计算标准差"


def _recommend_chart(data_json: str) -> str:
    data = json.loads(data_json)
    columns = data.get("columns", [])
    rows = data.get("rows", [])
    if not columns or not rows:
        return json.dumps({"type": "table", "config": {}})
    numeric_cols = [c for c in columns if rows and isinstance(rows[0].get(c), (int, float))]
    categorical_cols = [c for c in columns if c not in numeric_cols]
    if categorical_cols and numeric_cols:
        cat = categorical_cols[0]
        num = numeric_cols[0]
        if len(rows) <= 8:
            return json.dumps({"type": "pie", "config": {"data": rows, "dimensions": {"category": cat, "value": num}}})
        else:
            return json.dumps({"type": "bar", "config": {"data": rows, "dimensions": {"x": cat, "y": num}}})
    return json.dumps({"type": "table", "config": {"data": rows}})
```

- [ ] **Step 2: Commit**

---

#### Task 3.9: LangGraph Agent Graph

- Modify: `backend/app/agent.py`

Same as original but with updated node references: `gen_sql_with_tools` instead of `schema_linking` + `generate_sql`.

```python
from typing import Literal
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode, tools_condition
from app.state import AgentState
from app.nodes import (
    classify_intent, gen_sql_llm,
    validate_sql_step, execute_sql_step, self_correct,
    clarify, assemble_planner, assemble_executor,
)
from app.tools import run_sql, validate_sql
from app.mcp_client import schema_client


def build_agent() -> StateGraph:
    workflow = StateGraph(AgentState)

    # ── MCP Schema ToolNode（SQL 生成阶段用）──
    mcp_tools = [
        schema_client.search_tables_wrapper,
        schema_client.get_table_ddl_wrapper,
        schema_client.list_tables_wrapper,
    ]

    # ── 内部工具：ask_clarification（调用 interrupt 暂停 Agent）──
    from app.tools import ask_clarification_tool
    all_tools = mcp_tools + [ask_clarification_tool]
    tool_node = ToolNode(all_tools)

    workflow.add_node("classify", classify_intent)
    workflow.add_node("gen_sql_llm", gen_sql_llm)
    workflow.add_node("mcp_tools", tool_node)
    workflow.add_node("validate", validate_sql_step)
    workflow.add_node("execute", execute_sql_step)
    workflow.add_node("correct", self_correct)
    workflow.add_node("clarify", clarify)

    # ── Plan & Execute 报表组装阶段 ──
    workflow.add_node("assemble_plan", assemble_planner)    # LLM 制定分析计划
    workflow.add_node("assemble_exec", assemble_executor)   # 逐条执行，Loop 到完成

    workflow.set_entry_point("classify")

    # ── 意图分类 → 路由 ──
    workflow.add_conditional_edges(
        "classify",
        lambda state: state["intent"],
        {"报表": "gen_sql_llm", "闲聊": END, "看板": END},
    )

    # ── ReAct 循环：SQL 生成阶段，LLM 调 MCP 工具发现表结构 ──
    workflow.add_conditional_edges(
        "gen_sql_llm",
        tools_condition,  # 有 tool_call → "mcp_tools", 无 → "validate"
        {"tools": "mcp_tools", END: "validate"},
    )
    workflow.add_edge("mcp_tools", "gen_sql_llm")

    # ── SQL 验证 → 纠错循环 ──
    workflow.add_conditional_edges(
        "validate",
        lambda state: "execute" if state["sql_valid"] else "correct",
    )
    workflow.add_conditional_edges(
        "correct",
        lambda state: "clarify" if state.get("retry_count", 0) >= 3 else "validate",
    )

    # ── SQL 执行 → 报表组装 Phase ──
    workflow.add_conditional_edges(
        "execute",
        lambda state: _after_execute(state),
    )

    # ── Plan & Execute 循环：报表组装 ──
    # assemble_plan → assemble_exec → (还有步骤?) → assemble_exec → END
    workflow.add_edge("assemble_plan", "assemble_exec")
    workflow.add_conditional_edges(
        "assemble_exec",
        lambda state: "assemble_exec" if state.get("assemble_step_idx", 0) < len(state.get("assemble_plan", [])) else END,
    )

    workflow.add_edge("clarify", END)

    return workflow.compile()


def _after_execute(state: AgentState) -> Literal["assemble_plan", "correct", "clarify"]:
    import json
    data = json.loads(state.get("sql_result", "{}"))
    if "error" in data:
        if state.get("retry_count", 0) >= 3:
            return "clarify"
        state["sql_error"] = data["error"]
        return "correct"
    return "assemble_plan"
```
```

---

#### Task 3.10: FastAPI App + SSE Streaming Endpoints

Same as original but with MCP client lifecycle management in startup/shutdown.

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.mcp_client import schema_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: connect to MCP Schema Server
    await schema_client.connect()
    yield
    # Shutdown: disconnect
    await schema_client.disconnect()


app = FastAPI(title="ReportAgent API", version="1.0.0", lifespan=lifespan)
```

---

## Self-Review

### MCP-Architecture-Specific Checks

| Question | Answer |
|----------|--------|
| MCP Server 是否独立可运行？ | 是，`python mcp-schema-server/server.py` 即可启动 |
| 是否可被 Claude Desktop 调用？ | 是，在 `claude_desktop_config.json` 里配成 stdio 子进程 |
| LangGraph 是否通过 MCP 协议调工具？ | 是，通过 `mcp.client` 包用 stdio transport 连接 |
| 没有 MCP Server 时 Agent 是否降级？ | 否（当前设计是强依赖，稍后可以加 fallback） |
| MCP tools 是否绑为 LangChain tools？ | 是，通过 `llm.bind_tools()` 绑到 tool-calling LLM |
| schema 检索是"一次性预取"还是"动态按需"？ | **动态按需** — LLM 在 SQL 生成过程中自主调 MCP 工具 |

### Spec Coverage

All requirements from the original spec are covered. Key changes vs original plan:
- One `schema_linking` node + one `generate_sql` node → replaced by single `gen_sql_with_tools` node with MCP tool binding
- Schema retrieval goes through MCP protocol instead of direct DuckDB queries in `tools.py`
- MCP Schema Server is a standalone deployable unit (bonus for interview discussion)