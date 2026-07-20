# ReportAgent

AI 驱动的自然语言 → 报表系统。用户用中文提问，LangGraph Agent 自动生成并执行 SQL，返回表格 + 图表 + 洞察分析。

## 系统架构

```
用户 ←SSE→ FastAPI + LangGraph Agent (:8100) ←MCP→ MCP Schema Server (:8101)
                                                          |
                                                     DuckDB (只读)
                                                          |
                                                     PostgreSQL (记忆 + 追踪)
```

两个后端服务：
- **ReportAgent** (:8100) — FastAPI + LangGraph Agent，负责 SQL 生成/执行、图表渲染、洞察分析
- **MCP Schema Server** (:8101) — 独立 MCP 服务，按需发现表结构

## 技术栈

| 层        | 技术                                   |
|-----------|----------------------------------------|
| API       | FastAPI + SSE 流式推送                   |
| Agent     | LangGraph (Parent + 3 SubGraph)         |
| LLM       | OpenAI 兼容 (MiniMax API，可配置)         |
| Embedding | SiliconFlow API (pgvector 语义搜索)       |
| 业务数据库  | DuckDB (嵌入，只读查询)                   |
| 持久化     | PostgreSQL + asyncpg + pgvector          |
| 记忆       | QueryMemory (SQL模板) + Mem0 (可选)       |
| 追踪       | 自定义 Trace SDK → PostgreSQL            |
| 检查点     | LangGraph MemorySaver (dev) / PG (计划)   |

## 数据模型

零售 + 电商星型模型，6 张维度表 + 4 张事实表：

- **维度表：** dim_date, dim_region, dim_product, dim_customer, dim_warehouse, dim_employee
- **事实表：** fact_sales (48条), fact_returns (12条), fact_inventory (30条), fact_attendance (20条)

## 环境要求

- Miniconda (Python 3.11)
- PostgreSQL 15+ (含 pgvector 扩展)
- Docker (用于启动 PostgreSQL)
- SiliconFlow API Key (用于 Embedding)
- MiniMax API Key (用于 LLM)

## 快速开始

### 1. 启动 PostgreSQL

```bash
docker run -d --name ragent-postgres \
  -e POSTGRES_USER=ragent \
  -e POSTGRES_PASSWORD=ragent \
  -e POSTGRES_DB=ragent \
  -p 5432:5432 \
  pgvector/pgvector:0.7.0-pg15
```

### 2. 配置环境变量

```bash
# 复制 .env 并填入 API Key
MINIMAX_API_KEY=your-minimax-key
LLM_MODEL=MiniMax-M2.7-highspeed
SILICONFLOW_API_KEY=your-siliconflow-key
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-8B
DATABASE_URL=postgresql://ragent:ragent@localhost:5432/ragent
```

### 3. 初始化数据库

```bash
# 创建 PG 表结构
docker exec -i ragent-postgres psql -U ragent -d ragent < backend/scripts/init_pg.sql
```

### 4. 安装依赖

```bash
conda activate agent
pip install -r backend/requirements.txt
pip install -r mcp_schema_server/requirements.txt
```

### 5. 启动服务

```bash
# 终端 1: MCP Schema Server
python -m mcp_schema_server.server

# 终端 2: ReportAgent API
uvicorn app.main:app --port 8100 --reload
```

### 6. 测试

```bash
curl -X POST http://localhost:8100/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"user_query": "今年华东销售趋势", "session_id": "test-1"}'
```

## API 接口

| 端点           | 方法   | 说明                        |
|----------------|--------|-----------------------------|
| `/health`      | GET    | 健康检查                    |
| `/api/v1/chat` | POST   | 发送查询，返回 SSE 事件流    |

### SSE 事件协议

| 事件      | 说明                      |
|-----------|---------------------------|
| `token`   | LLM 流式输出文本           |
| `trace`   | 执行步骤更新 {step, status, detail} |
| `report`  | 最终结果 {answer: {text, table, chart, insight}} |
| `clarify` | 追问用户 {question}        |
| `error`   | 错误信息                  |
| `done`    | 流结束                    |

## 项目结构

```
backend/
  app/
    main.py              — FastAPI 入口 + SSE 流处理
    llm.py               — LLM 统一客户端
    db.py                — DuckDB 连接管理
    agent/               — LangGraph Agent
      parent_graph.py    — 父图 (7个节点)
      data_graph.py      — Data SubGraph (Schema发现)
      sql_graph.py       — SQL SubGraph (生成/执行)
      report_graph.py    — Report SubGraph (图表/洞察)
    models/              — Pydantic 契约模型
    tools/               — 工具注册
    infra/
      db/postgres.py     — asyncpg 连接池
      trace/             — Trace SDK + 持久化
      memory/            — QueryMemory (pgvector)
      checkpoint/        — Session管理
    embedding/service.py — EmbeddingService (SiliconFlow)
  scripts/
    init_pg.sql          — PG 建表脚本
  seed_data.sql          — DuckDB 示例数据

mcp_schema_server/
  server.py              — MCP Schema 发现服务
```

## Agent 流程

```
用户输入 → classify_intent
  ├── "闲聊" → 直接结束
  └── "报表" → data_agent (Schema发现)
                      ↓
                sql_agent (SQL生成+执行+重试)
                      ↓
                evaluate (检查状态)
                 ├── SUCCESS → report_agent (图表+洞察)
                 └── NEED_CLARIFICATION → clarify (追问用户)
```

- SQL Agent 支持自动重试 (语法错误重试3次，Schema错误重试1次)
- 追问是唯一调用 `interrupt()` 的节点
- 所有节点自动记录 Trace

## 记忆系统

- **Query Memory:** 历史 SQL 模板，支持语义搜索 (pgvector) + 关键词混合
- **Semantic Memory:** 用户偏好/洞察记录，按 session 检索
- **Mem0:** 可选的语义记忆层 (配置 `MEM0_ENABLED=true`)
