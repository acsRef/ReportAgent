# ReportAgent

AI 驱动的自然语言 → 报表系统。用户用中文提问，LangGraph Agent 自动生成并执行 SQL，在前端呈现图表 + 洞察分析。

## 系统架构

```
用户 ←SSE→ React + Vite (:3000)
              ↓ 代理 /api (Vite proxy)
        FastAPI + LangGraph Agent (:8100)
              ↓ MCP
         MCP Schema Server (自动发现)
              ↓
         PostgreSQL (分析数据 + 会话 + 追踪 + 记忆)
```

三个服务（严格按此顺序启动）：
- **MCP Schema Server** (随机端口) — 通过 MCP 协议暴露数据库 Schema
- **后端** (:8100) — FastAPI + LangGraph 8 节点父图 (+ 3 个子图)
- **前端** (:3000) — React + Vite + Ant Design + ECharts，深色 BI 仪表盘风格

## 技术栈

| 层          | 技术                                           |
|-------------|------------------------------------------------|
| 前端         | React 18 + Vite + TypeScript + Ant Design       |
| 渲染         | ECharts (SVG) + 自定义 ReportBlock 组件          |
| 状态管理      | Zustand + persist (localStorage)                |
| API 协议     | Server-Sent Events (SSE) 流式推送                |
| 认证         | JWT (PyJWT, 单 token, 无 refresh)               |
| Agent       | LangGraph (Parent + 3 SubGraph)                 |
| LLM         | OpenAI 兼容 (MiniMax M2.7-highspeed，可配置)      |
| Embedding   | SiliconFlow API (pgvector 语义搜索)              |
| 分析数据库    | PostgreSQL (public schema, 10 张星型模型表)      |
| 持久化       | PostgreSQL + asyncpg + pgvector (pgvector/pgvector:0.7.0-pg15) |
| 记忆         | MemoryManager (UserMemory + QueryMemory, pgvector 混合排序) |
| 安全         | SecurityGuard 规则引擎 (风险评分: LOW / HIGH)      |
| 追踪         | 自定义 Trace SDK → PostgreSQL                     |
| 检查点       | LangGraph MemorySaver (dev)                      |

## 数据模型

零售 + 电商星型模型，6 张维度表 + 4 张事实表（存储在 PostgreSQL `public` schema）：

- **维度表：** dim_date (51行), dim_region (17行), dim_product (20行), dim_customer (12行), dim_warehouse (5行), dim_employee (8行)
- **事实表：** fact_sales (48条), fact_returns (12条), fact_inventory (30条), fact_attendance (20条)

## 环境要求

- Miniconda (Python 3.11)
- Node.js 18+
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
# 3a. 创建 PG 表结构（认证 + 会话）
docker exec -i ragent-postgres psql -U ragent -d ragent < backend/scripts/init_pg.sql

# 3b. 导入分析数据（10 张星型模型表）
docker exec -i ragent-postgres psql -U ragent -d ragent < backend/scripts/seed_pg.sql
```

### 4. 安装依赖

```bash
conda create -n agent python=3.11
conda activate agent
pip install -r backend/requirements.txt
pip install -r mcp_schema_server/requirements.txt
cd frontend && npm install
```

### 5. 启动服务（严格按此顺序）

```bash
# 终端 1: MCP Schema Server（必须先启动）
python -m mcp_schema_server.server

# 终端 2: ReportAgent API
cd backend && uvicorn app.main:app --port 8100 --reload

# 终端 3: 前端开发服务器
cd frontend && npm run dev
```

### 6. 登录测试

```bash
# 默认管理员账号
curl -X POST http://localhost:8100/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# 发送查询（需要先登录获取 token）
curl -X POST http://localhost:8100/api/v1/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"user_query": "2024年各区域销售总额", "session_id": "test-1"}'
```

## API 接口

| 端点                      | 方法   | 认证   | 说明                        |
|---------------------------|--------|--------|-----------------------------|
| `/health`                 | GET    | 否     | 健康检查                    |
| `/api/v1/auth/login`      | POST   | 否     | 登录，返回 JWT token        |
| `/api/v1/auth/register`   | POST   | 否     | 注册新用户                  |
| `/api/v1/sessions`        | GET    | 是     | 查询历史会话列表            |
| `/api/v1/conversations/{session_id}` | GET | 是 | 查询会话消息详情          |
| `/api/v1/chat`            | POST   | 是     | 发送查询，返回 SSE 事件流    |

### SSE 事件协议

| 事件      | 说明                      |
|-----------|---------------------------|
| `token`   | LLM 流式输出文本           |
| `trace`   | 执行步骤更新 {step, status, detail} |
| `report`  | 最终结果 {answer: {text, table, chart, insight}} |
| `clarify` | 追问用户 {question}        |
| `error`   | 错误信息                  |
| `done`    | 流结束                    |

> 所有需要认证的接口在 Header 中传入 `Authorization: Bearer <token>`。
> token 过期后返回 401，前端自动跳转登录页。

## 项目结构

```
backend/
  app/
    main.py              — FastAPI 入口 + SSE 流处理
    llm.py               — LLM 统一客户端
    db.py                — DuckDB 连接管理 (旧版兼容)
    agent/               — LangGraph Agent
      security_guard.py  — ⭐ 安全守卫 (规则引擎 + 风险评分)
      parent_graph.py    — 父图 (8个节点)
      data_graph.py      — Data SubGraph (Schema发现)
      sql_graph.py       — SQL SubGraph (生成/执行)
      report_graph.py    — Report SubGraph (图表/洞察)
    models/              — Pydantic 契约模型
    tools/               — 工具注册 (含 risk_level 元数据)
      __init__.py        — 10个工具的中文决策边界描述
      registry.py        — ToolRegistry + ToolMetadata
      data_tools.py      — Schema 发现工具
      sql_tools.py       — SQL 校验/执行/AST解析 (连接 PostgreSQL)
      report_tools.py    — 图表/洞察/趋势/异常检测
    infra/
      auth/              — JWT 认证 (FastAPI Depends)
        jwt.py           — token 签发/验证
        repository.py    — 用户查询
        deps.py          — FastAPI 依赖注入
      conversation/      — 会话存储
        repository.py    — 消息 CRUD
      db/postgres.py     — asyncpg 连接池
      trace/             — Trace SDK + 持久化
      memory/            — MemoryManager + UserMemory + QueryMemory
      checkpoint/        — Session管理
    utils/
      text.py            — 文本工具类
    embedding/service.py — EmbeddingService (SiliconFlow)
  scripts/
    init_pg.sql          — PG 认证/会话建表
    seed_pg.sql          — PG 分析数据灌入 (10张星型表)

mcp_schema_server/
  server.py              — MCP Schema 发现服务
  registry.py            — 硬编码 Schema 注册 (不依赖 DuckDB)

frontend/
  src/
    api/
      api.ts             — 通用请求封装 (自动带 Authorization)
      chat.ts            — SSE 流式 chat 客户端
      sse.ts             — SSE 事件解析器
    components/
      chat/              — AgentTimeline, EmptyState
      layout/            — Navbar, AppShell
      report/            — ReportRenderer, registry, blocks (Chart/Table/Kpi/Insight/Markdown)
    pages/
      LoginPage.tsx      — 深色主题登录页
      ChatPage.tsx       — 主聊天页 (三态视图切换)
      HistoryPage.tsx    — 历史会话页
      TemplateCenter.tsx — 报告模板中心
      views/
        ChatView.tsx     — 对话视图
        RunningView.tsx  — 执行中视图
        ReportView.tsx   — 报表视图
    stores/
      session.ts         — 会话/Zustand store
      authStore.ts       — 认证 store (+ localStorage persist)
    theme/
      antdTheme.ts       — 深色 BI 仪表盘 Design Token
    types/
      report.ts          — ReportBlock, SSEEvent, ConversationMessage 等类型
    adapter/
      reportAdapter.ts   — 后端 ReportResponse → ReportBlock[]
    utils/
      export.ts          — 报告导出 HTML
```

## Agent 流程

```
用户输入 → security_guard (规则引擎 + 风险评分)
  ├── HIGH → 直接拒绝，返回错误
  └── LOW → classify_intent
       ├── "闲聊" → 直接结束
       └── "报表" → data_agent (Schema发现)
                        ↓
                  sql_agent (SQL生成+执行+重试)
                        ↓
                  evaluate (检查状态)
                   ├── SUCCESS → report_agent (图表+洞察)
                   └── NEED_CLARIFICATION → clarify (追问用户)
```

- **Security Guard** 在入口层做 Prompt Injection 风险检测，纯规则匹配 (<1ms)
- **风险分级:** LOW (放行) / HIGH (阻断)
- SQL Agent 支持自动重试 (语法错误重试3次，Schema错误重试1次)
- 追问是唯一调用 `interrupt()` 的节点
- 所有节点自动记录 Trace

## 记忆系统 (Memory Ranking)

统一入口 `MemoryManager`，两个后端：

- **QueryMemory:** 历史 SQL 模板，pgvector 语义搜索 + 关键词混合排序
  - 排序权重: `semantic×0.5 + success_rate×0.3 + freq×0.1 + recency×0.1`
  - 字段: `question, sql, schema, target_metric, access_count, failure_count, verified`
- **UserMemory:** 用户偏好/洞察记录
  - 排序权重: `semantic×0.6 + importance×0.2 + freq×0.1 + recency×0.1`
  - 类型: `stable_preference / temporary_preference / insight`
  - 字段: `content, memory_type, importance_score, access_count`

```sql
-- 安全性：关键词回退使用 LIKE ANY($1::text[]) 参数化查询
```

## 安全防护 (Security Guard)

四层纵深防御：

| 层 | 机制 | 状态 |
|----|------|------|
| 入口 | SecurityGuard 规则引擎 (10条正则，风险评分分级) | ✅ |
| Agent 隔离 | 各 Agent 只有最小工具集 (无 DDL/DML 能力) | ✅ |
| SQL 安全 | 三重校验 (关键字黑名单 + AST解析 + EXPLAIN) | ✅ |
| 数据库 | PostgreSQL 只读查询 (psycopg2, 仅 SELECT) | ✅ |

- SecurityGuard 不引入 LLM 分类器，避免增加延迟
- 10 条规则覆盖英文 jailbreak、中文变体、SQL DDL、数据泄露
- score ≥ 3 即阻断，无 MEDIUM 降级（避免不被消费的未使用代码路径）
- 工具元数据含 `risk_level` (low/medium/high)，引导 LLM 正确调用
