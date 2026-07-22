# ReportAgent — Agent Instructions

## 启动顺序（重要）

1. `python -m mcp_schema_server.server` (MCP 先启动)
2. `cd backend && uvicorn app.main:app --port 8100 --reload` (后端)
3. `cd frontend && npm run dev` (前端，port 3000，Vite 自动代理 `/api` → `:8100`)

MCP Schema Server 无端口固定，后端通过 MCP 协议自动发现。先后端先启动会导致连接失败。

## 架构总览

```
User ←SSE→ React+Vite (:3000) → proxy /api → FastAPI+LangGraph (:8100)
                                              ↓ MCP
                                         MCP Schema Server (:8101)
                                              ↓
                                         DuckDB (只读，自动种子)
                                         PostgreSQL (session+trace+memory)
```

- 前端无 `.env` 要求，Vite proxy 将 `/api/*` 转发到后端
- DuckDB 首次连接时从 `backend/seed_data.sql` 自动建表+填充

## Setup

```bash
conda create -n agent python=3.11; conda activate agent
pip install -r backend/requirements.txt
pip install -r mcp_schema_server/requirements.txt
cd frontend && npm install
```

- PostgreSQL: `docker run -d --name ragent-postgres -e POSTGRES_USER=ragent -e POSTGRES_PASSWORD=ragent -e POSTGRES_DB=ragent -p 5432:5432 pgvector/pgvector:0.7.0-pg15`
- 初始化 PG 表: `docker exec -i ragent-postgres psql -U ragent -d ragent < backend/scripts/init_pg.sql`
- 无 `.env.example`，需手动创建 `.env`，必填项: `MINIMAX_API_KEY`, `SILICONFLOW_API_KEY`, `DATABASE_URL`, `LLM_MODEL`
- 向量维度 **1536** (`init_pg.sql` 中 VECTOR(1536)，需与 `.env` 的 `EMBEDDING_DIM` 一致)

## 关键命令

| 用途 | 命令 |
|------|------|
| 后端启动 | `cd backend && uvicorn app.main:app --port 8100 --reload` |
| MCP 启动 | `python -m mcp_schema_server.server` |
| 前端启动 | `cd frontend && npm run dev` |
| 前端 lint | `cd frontend && npm run lint`（oxlint，不是 eslint） |
| 前端构建 | `cd frontend && npm run build`（tsc -b && vite build） |
| 测试 API | `curl -X POST http://localhost:8100/api/v1/chat -H "Content-Type: application/json" -d '{"user_query":"今年华东销售趋势","session_id":"test-1"}'` |
| 健康检查 | `curl http://localhost:8100/health` |

**整个仓库无测试代码**，所有验证通过 curl 手动完成。无 CI 配置、无 Dockerfile、无 Makefile。

## TSD 加密源文件

许多 `.py` 文件直接查看显示 `%TSD-Header-###%`（加密占位符）。需通过 `git show HEAD:<path>` 读取解密内容。只有 `main.py`、`llm.py` 和空的 `__init__.py` 可读。

## Agent 图（Parent + 3 SubGraphs）

```
User Query → security_guard (score≥3→block)
  ├─ 闲聊 → END
  └─ 报表/看板 → data_agent → sql_agent → evaluate
       ├─ SUCCESS → report_agent → END
       └─ NEED_CLARIFICATION → clarify_node (interrupt) → data_agent
```

关键规则：
- `clarify` 是**唯一**调用 `interrupt()` 的节点 — SubGraphs 从不 interrupt
- SubGraphs 通过 `.invoke()` 在父节点内同步执行（非 LangGraph sub-graph 概念）
- Checkpoint 只保存 Parent State（SubStates 是临时的）
- `original_query` = 首条用户消息冻结；`current_query` = 携带追问上下文的增强查询

## SQL 重试逻辑

**内部（sql_graph）：** 语法错误 → 重新生成（最多 3 次），Schema 错误 → 重新规划（1 次），耗尽 → `NEED_CLARIFICATION`

**父图：** SQL 失败 → 重试 `sql_agent`（最多 3 次）而非跳到 `report_agent`，防止对空结果产生幻觉。

## SQL 安全（3 层）

1. 黑名单：拦截非 SELECT 语句（DDL/DML 关键字）
2. AST 解析：`sqlglot` 验证解析结果为 `Select`
3. EXPLAIN：执行 `EXPLAIN <sql>` 捕获 DuckDB 特有语法错误

## 记忆系统

统一入口 `infra/memory/memory_manager.py`，两个后端：
- **QueryMemory**（`memory.query_template`）：pgvector 语义 + 关键词混合搜索，排序 `semantic×0.5 + success_rate×0.3 + freq×0.1 + recency×0.1`
- **UserMemory**（`memory.semantic_entry`）：用户偏好/洞察，排序 `semantic×0.6 + importance×0.2 + freq×0.1 + recency×0.1`

Embedding API 失败时优雅回退到关键词匹配（`ILIKE ANY($1::text[])` 参数化）。

## 已知怪癖

- `__init__.py` 文件故意为空（0 字节）— Python 3.3+ namespace packages
- `infra/trace/repository.py` 直接使用 `asyncpg`（非 `infra/db/postgres.py` 的连接池）— 在 sdk.py 使用连接池前实际上无效
- MCP Schema Server 的 `registry.py` 和 `app/tools/data_tools.py` 是**两个独立的**关键字 Schema 匹配实现
- `POST /api/v1/chat` 的 `session_id` 同时用于新建会话和恢复检查点
- 前端 TypeScript ~6.0（非常新），lint 使用 oxlint 而非 eslint
- Embedding 用 SiliconFlow API（`.env` 中 `SILICONFLOW_API_KEY`），与 LLM（MiniMax）不同厂商
