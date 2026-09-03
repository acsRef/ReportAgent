# ReportAgent

AI 驱动的自然语言 → 报表系统。用户用中文提问，Agent 先拆解并让用户**确认需求**，确认后自动生成并执行 SQL，在工作台呈现图表 + 明细 + 洞察。界面按已批准的原型 [docs/intelligent-analysis-workbench.html](docs/intelligent-analysis-workbench.html) 1:1 实现。

## 系统架构

```
用户 ←SSE v2→ React + Vite (:3000)
                 ↓ 代理 /api (Vite proxy)
           FastAPI + LangGraph Agent (:8100)
                 ↓ MCP
            MCP Schema Server (自动发现，本地工具兜底)
                 ↓
            PostgreSQL (分析数据 + 会话 + 需求草稿 + 报告版本 + 记忆 + 追踪)
```

三个服务（严格按此顺序启动）：

- **MCP Schema Server**（随机端口）— 通过 MCP 协议暴露数据库 Schema
- **后端**（:8100）— FastAPI + LangGraph 双图协作
- **前端**（:3000）— React 19 + Vite + ECharts + atelier 组件库（**antd 已完全移除**）

### 两图协作流程（核心）

1. **需求分析图**（`requirement_analysis_graph`）：**只暴露 schema 工具**（search_tables / get_table_ddl / list_tables），产出 `RequirementCard`（缺失字段带后端受控选项）。
2. **确认执行图**（`confirmed_execution_graph`）：门控（status=complete、无缺失、假设全部表态、owner 校验）→ 锁定草稿 → schema → SQL（plan → generate → validate(EXPLAIN) → execute）→ report → 落库 `agent.report_version`（append-only）。

关键语义：

- **三态分离，失败绝不伪装成功**：SQL 执行结果分 `SUCCESS` / `EMPTY`（合法零行）/ `FAILED`（执行出错）三态，**三者都落库**（`agent.report_version` append-only，失败/空也留版本供回溯）。FAILED/EMPTY 经 SSE 透出 `error_kind` + 尝试过的 SQL，前端 ErrorCard 按 kind 分类显示、ReportPaper 渲染「未找到匹配记录」或失败归档带；可恢复错误出现「重试当前任务」卡（`POST /sessions/{sid}/retry`）。
- **reasoning 模型兼容**：`utils/text.extract_sql` 剥离 `
</think>

` 块；校验失败的 SQL + 错误信息会喂回重新生成的 prompt（重试不盲打）。
- 旧的 `mode=legacy` 单图流程（interrupt + chosen_tool）仍保留一个兼容周期。

### 记忆与对话上下文

让多轮对话连贯（先问「2024 华东销售趋势」再说「再按产品细分」，第二轮知道「华东」）。分四层（`app/context.py`）：

- **L1 原始消息**：最近 10 条对话**逐字保留**（不压缩），近期确切内容不丢。
- **L2 叙事摘要**（`agent.session.digest`，≤800 字）：超出窗口的旧消息压缩成摘要，**覆盖重写**（绝不追加，防膨胀）。摘要只保留话题脉络/决策背景，**刻意不含具体字段名和数值**。
- **L2.5 长期归档**（`mid_digest`，≤400 字）：每 5 次摘要重写归档一次长期脉络。
- **L3 结构化事实**（`memory.semantic_entry`）：压缩时顺带抽取**字段映射 / 计算口径 / 用户偏好**，作为结构化记录存储——这些精确属性不走有损摘要。

**字段类型为何不会因压缩漂移**（如 Amount int 被记成 string）：字段类型**永远从数据库 schema 实时取**（`get_table_ddl`/`search_tables`），不来自记忆；注入 prompt 的上下文块还显式声明「表名/列名/字段类型以可用表结构为准」（`format_context_block`），且 SQL 执行前过 `sqlglot` AST + `EXPLAIN` 用真实 schema 校验。三重保证下，有损摘要不会把字段类型带偏。

**记忆召回**（`MemoryManager` → `UserMemory`/`QueryMemory`）：pgvector 语义搜索，综合打分 = 语义相似度×0.6 + 重要性×0.2 + LFU(访问频率)×0.1 + LRU(新鲜度)×0.1——**语义主导，LFU/LRU 作排序因子**（不做纯淘汰，避免高频噪声压过相关性）。每用户记忆有条数上限（默认 200），超出按 LFU/LRU+重要性混合分淘汰最冷的、保护高重要项。

**mem0（可选）**：`MEM0_ENABLED=true` 时用 mem0 从对话自动抽取 L3 长期事实（自带去重/更新）；默认关闭，降级为纯 LLM 抽取。**mem0 只做抽取，不做召回**（召回主路径始终是 pgvector 语义排序）。

**checkpoint 持久化**（`app/infra/checkpoint/factory.py`）：dev 用 `MemorySaver`（便于本地单步），非 dev 用 **`AsyncPostgresSaver`**（checkpoint 落 PG，跨进程重启不丢、支持多实例）。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | React 19 + Vite 8 + TypeScript 6 + Zustand + immer |
| UI | atelier 组件库（`components/atelier/`，antd 已移除）+ 手绘 SVG 图标（`components/ui/Icons.tsx`）+ `styles/tokens.css` / `styles/workbench.css` |
| 渲染 | ECharts 6 (SVG) + ReportBlock 组件体系 |
| API 协议 | SSE v2 流式推送（phase / requirement / report / error / done） |
| 认证 | JWT（PyJWT，单 token，无 refresh，24h）；fail-closed 启动安全闸 |
| Agent | LangGraph 双图 + psycopg2 + sqlglot；checkpoint 默认 MemorySaver（dev）/ **PostgresSaver**（非 dev，落 PG、跨重启持久） |
| 记忆 | 分层对话上下文（L1 原始 / L2 摘要覆盖重写 / L2.5 归档 / L3 结构化事实）+ pgvector 语义召回（语义主导，LFU/LRU 作排序因子 + 容量上限淘汰）；mem0 可选作 L3 抽取引擎（`MEM0_ENABLED`） |
| LLM | OpenAI 兼容（MiniMax，可配置） |
| Embedding | SiliconFlow API（pgvector 语义搜索，失败降级关键字匹配） |
| 数据库 | PostgreSQL 15 + pgvector（`public` 星型模型：2 事实 + 5 维度零售订单，见 `backend/scripts/seed_business_p15prelude.sql`；数据覆盖 2024） |
| 测试 | 分层：离线 gate（`pytest` backend/tests，含真 PG persistence）+ Contract E2E（Playwright，mock LLM，per-PR CI）+ Live evaluation（`REPORTAGENT_E2E=1`，真实 LLM/MCP/PG，夜间/手动） |

## 环境要求

Miniconda（Python 3.11）、Node.js 18+、Docker、MiniMax API Key、SiliconFlow API Key。

## 快速开始

### 1. 启动 PostgreSQL

```bash
docker run -d --name ragent-postgres \
  -e POSTGRES_USER=ragent -e POSTGRES_PASSWORD=ragent -e POSTGRES_DB=ragent \
  -p 5432:5432 pgvector/pgvector:0.7.0-pg15
```

### 2. 配置环境变量

仓库根创建 `.env`（可参考 [backend/.env.example](backend/.env.example)）：

```bash
MINIMAX_API_KEY=your-minimax-key
SILICONFLOW_API_KEY=your-siliconflow-key
DATABASE_URL=postgresql://ragent:ragent@localhost:5432/ragent
# LLM_MODEL / LLM_BASE_URL / EMBEDDING_MODEL / EMBEDDING_DIM(必须 1536) 可选

# 运行环境 + 开发逃生门（见下方「生产部署安全前置」）
APP_ENV=development
ALLOW_INSECURE_DEFAULT_AUTH=1

# mem0 L3 事实抽取引擎（可选，默认 false → 纯 LLM 抽取）
MEM0_ENABLED=false
```

#### 生产部署安全前置（fail-closed 启动闸）

后端启动期有 auth 安全闸（`app/infra/auth/startup_guard.py`），**fail-closed**：

- `APP_ENV` 未设置时按 `production` 处理（最严格）。
- 非开发环境（`staging` / `production`）下，`JWT_SECRET` 必须显式设置、不能是公开的开发默认值、长度 ≥ 32；`DEFAULT_PASSWORD` 不能是 `admin123`。否则**进程拒绝启动**。
- `ALLOW_INSECURE_DEFAULT_AUTH=1` 仅在 `APP_ENV=development` 下生效，生产环境忽略。
- 即便默认 admin 账户已存在，只要它还持 `admin123` 弱密码，非开发环境同样拒绝启动（堵住「开发库直接升生产」的后门）。

本地开发用 `APP_ENV=development` + `ALLOW_INSECURE_DEFAULT_AUTH=1` 即可照常启动；生产必须显式配置强 `JWT_SECRET` 与强 `DEFAULT_PASSWORD`。

### 3. 初始化数据库

```bash
docker exec -i ragent-postgres psql -U ragent -d ragent < backend/scripts/init_pg.sql
# 现役业务星型 seed（零售订单；会清理旧演示业务表，可重复执行）
docker exec -i ragent-postgres psql -U ragent -d ragent < backend/scripts/seed_business_p15prelude.sql
# 分析只读角色（LLM SQL 执行路径的最小权限身份，ANALYSIS_DSN 用）
docker exec -i ragent-postgres psql -U ragent -d ragent < backend/scripts/setup_app_role.sql
```

### 4. 安装依赖

```bash
conda create -n agent python=3.11 && conda activate agent
pip install -r backend/requirements.txt
pip install -r backend/requirements-dev.txt   # 测试依赖
pip install -r mcp_schema_server/requirements.txt
cd frontend && npm install
```

### 5. 启动服务（严格按此顺序）

```bash
python -m mcp_schema_server.server                      # 终端 1：MCP（必须先启动）
cd backend && uvicorn app.main:app --port 8100 --reload # 终端 2：后端
cd frontend && npm run dev                              # 终端 3：前端
```

### 6. 登录测试

```bash
# 默认管理员 admin / admin123
curl -X POST http://localhost:8100/api/v1/auth/login \
  -H "Content-Type: application/json" -d '{"username":"admin","password":"admin123"}'

# 发起分析（SSE v2 流）
curl -N -X POST http://localhost:8100/api/v1/chat \
  -H "Content-Type: application/json" -H "Authorization: Bearer <token>" \
  -d '{"user_query":"2024年各区域销售额排名","session_id":"test-1","mode":"new"}'
```

## API 接口

| 端点 | 方法 | 认证 | 说明 |
|---|---|---|---|
| `/health` | GET | 否 | 健康检查 |
| `/api/v1/auth/login` / `/api/v1/auth/register` | POST | 否 | 登录 / 注册 |
| `/api/v1/chat` | POST | 是 | `mode: new/supplement/adjust/legacy`，SSE v2 流 |
| `/api/v1/sessions` | GET | 是 | 会话列表（含标题/phase/首末消息/报告版本） |
| `/api/v1/sessions/{sid}` | GET | 是 | 全量快照（会话 + 消息 + 当前需求 + 报告版本） |
| `/api/v1/sessions/{sid}/requirement` | PATCH | 是 | 服务端重算 `RequirementCard`（填充选项、重算 status） |
| `/api/v1/sessions/{sid}/confirm` | POST | 是 | SSE v2：执行确认图，成功出 `report`，失败出 `error` |
| `/api/v1/sessions/{sid}/retry` | POST | 是 | 按 `last_failed_action` 恢复（清标记后委托 confirm） |
| `/api/v1/sessions/{sid}/reports/{version}` | GET | 是 | 报告版本详情（纯 PG 读，无 LLM） |
| `/api/v1/sessions/{sid}/reports/{version}/export.xlsx` | GET | 是 | 报告数据集 Excel 导出：全量读 `query_snapshot`（用户实际执行的数据，非 UI 预览；数据量仍受执行层 `MAX_RESULT_ROWS`=5000 约束） |
| `/api/v1/conversations/{sid}` | GET | 是 | 会话消息明细 |
| `/api/v1/templates` | POST/GET/PATCH/DELETE | 是 | PG 模板 CRUD |

### SSE v2 事件

| 事件 | 载荷 |
|---|---|
| `phase` | `{phase, reason?}` |
| `requirement` | 完整 `RequirementCard` |
| `trace` | `{step, status, detail?, kind?}` —— 执行进度；`kind ∈ agent/tool/sql/repair/report`（P11 progress 族，见 `docs/sse-v2.md`） |
| `thinking` | `{phase?, text?}` |
| `report` | `{version, parent_version, title, answer}`；闲聊回复为 `{answer:{text}}`（无 version） |
| `error` | `{code, message, recoverable, failed_action}` |
| `done` | `{final_phase}` |

`card` / `clarify` / `token` 仅在 `mode=legacy` 出现。

## 工作台界面

按原型 1:1 还原（`docs/intelligent-analysis-workbench.html`）：

- **左栏**：新建分析（青绿按钮）+ 会话分桶（今天/过去 7 天/更早，状态 pill + 相对时间）+ 激活会话的 version-box 时间线
- **中央**：canvas-head（phase kicker）→ 滚动态（气泡、需求卡、进度卡/错误卡、报告壳）→ **底部浮动对话框**（input + 发送 ↗，placeholder 随 phase 变化）
- **右栏**：分析助手（phase 完成度 + 需求范围 + report_ready 推荐分析）
- **需求卡**：左色条（amber/teal）+ AGENT REQUIREMENT BRIEF 刊头 + chips 网格 + 缺失字段 pill 选项 + 假设接受条 + 两步动作（补充完成查看确认 → 确认并生成报告），**无 spinner**
- **报告壳**：REPORT/v{n} 刊头 + meta（数据范围/分析范围/可信度）+ 核心发现（insight）+ 编号分节（OVERVIEW / VISUALIZATION / EVIDENCE 平铺明细表，展开收起）+ 脚注；内容严格来自真实载荷，**不伪造演示数据**

视觉 token 全部收敛在 `frontend/src/styles/tokens.css`（不在组件里写新 hex）；图标为 `components/ui/Icons.tsx` 手绘 16×16 描边 SVG。

## 测试

```bash
# 后端（backend/ 目录）
pytest                          # 离线套件（persistence/e2e 自动跳过）
pytest -m graphs                # 图测试（SQL sanitize/重试反馈/FAILED 路由/需求解析）
pytest -m persistence           # 需 ragent-postgres 起着

# 前端（frontend/ 目录）
npm run test:run                # vitest 242 用例
npx tsc -b && npx oxlint        # 类型 + lint

# 真实端到端（需 PG + 后端 :8100 + 真实 LLM key，仓库根目录）
REPORTAGENT_E2E=1 python -m pytest backend/tests/e2e/test_full_flow.py -s
```

e2e 断言真实数据：`query_snapshot.sql` 非空、`answer.table` 有行、模板 CRUD 与用户隔离。

## 项目结构（要点）

```
backend/app/
  main.py                      — FastAPI 路由 + SSE v2 + confirm/retry 流
  context.py                   — 分层对话上下文（L1/L2/L2.5/L3）+ build_session_context
  agent/
    requirement_analysis_graph.py — 需求分析图（仅 schema 工具）
    confirmed_execution_graph.py  — 确认执行图（门控/锁定/三态路由）
    sql_graph.py               — plan→generate→validate→execute→evaluate→build_output
    parent_graph.py            — 旧单图流程（mode=legacy）
  utils/text.py                — extract_sql / strip_think / safe_json_parse
  services/                    — requirement / report_version / snapshot / template
  infra/db/                    — asyncpg 池 + requirement/report_version 仓储
  infra/memory/                — user_memory（pgvector 召回 + LFU/LRU 淘汰）/ query_memory / mem0_extractor
  infra/checkpoint/            — factory（MemorySaver/PostgresSaver 按环境切换）/ session

frontend/src/
  components/atelier/          — 18 个组件 + ToastProvider/useToast + atelier.css
  components/ui/Icons.tsx      — 手绘图标
  components/workbench/        — Composer / SessionRail / RightRail / ProgressCard /
                                 ErrorCard / ReportPaper / RequirementCardView / phaseText
  stores/analysisStore.ts      — zustand+immer，analysisReducer 是唯一 phase 入口
  api/confirmStream.ts         — confirm/retry SSE 客户端
  styles/tokens.css + workbench.css — 视觉单一来源

docs/
  intelligent-analysis-workbench.html — 已批准原型（视觉真源）
  atelier/MIGRATION.md         — antd→atelier 迁移清单（已全部完成）
  hand-off.md                  — 现场快照与已知问题
  plans/                       — 计划存档
```

## 参考文档

- [CLAUDE.md](CLAUDE.md) / [AGENTS.md](AGENTS.md) — 架构与操作指引
- [docs/plans/README.md](docs/plans/README.md) — plan 永久索引（开发前必读入口）
- [docs/plans/2026-08-01-memory-mechanism.md](docs/plans/2026-08-01-memory-mechanism.md) — 记忆机制：分层上下文 + mem0 抽取 + LFU/LRU 淘汰
- [docs/plans/2026-08-01-postgres-checkpointer.md](docs/plans/2026-08-01-postgres-checkpointer.md) — PostgresSaver checkpoint 持久化
- [docs/plans/2026-07-30-bugfix-completion.md](docs/plans/2026-07-30-bugfix-completion.md) — bug 修复完成报告
- [docs/memory-ranking-plan.md](docs/memory-ranking-plan.md) — 记忆排序设计（LFU/LRU 作排序因子的依据）
- [docs/state-machine.md](docs/state-machine.md) — phase ↔ LangGraph 状态映射
- [docs/persistence.md](docs/persistence.md) — DDL 权威
- [docs/api-reference.md](docs/api-reference.md) / [docs/sse-v2.md](docs/sse-v2.md) — API 与事件协议
- [docs/contracts/requirement-card.md](docs/contracts/requirement-card.md) — RequirementCard 契约
- [docs/ui-style-guide.md](docs/ui-style-guide.md) — 视觉规范
