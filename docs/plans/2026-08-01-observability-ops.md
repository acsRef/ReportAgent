# Plan: 可观测性运维闭环——指标 + trace 可视化页面

> 状态: 已完成（指标 + trace 列表 + agent 执行链路明细页；完成率口径按 DONE/SUCCESS；全套 150 passed + 前端 245 + 冒烟验证观测端点）
>
> **来源**：代码评审第 4 点「可观测性没有运维闭环」。用户拍板范围：做指标 + 一个看日志/trace 的网页；**外部链路追踪（OpenTelemetry）与告警先不做**。

## Context（背景）

现状：trace/span/llm_call 已落库（`observability.agent_trace` 等，已有数据），但：

1. **没有指标（metrics）**——无法回答「成功率多少、平均耗时、LLM token 消耗」这类运维问题。
2. **没有可视化**——trace 只能直接查库看，没有页面。
3. ~~`trace/repository.py` 用独立连接~~ ——**核查后已不成立**：现有代码每个方法都用共享池 `get_pool()`。CLAUDE.md/AGENTS.md 的 Known Quirk 过时，本 plan 顺带更正。

目标（用户确认的范围）：

- 提供**聚合指标**接口 + **trace 列表/明细**接口。
- 提供一个**网页**显示指标与 trace 日志（够看即可，不追求花哨）。
- **不做** OpenTelemetry 对接、不做告警、不做实时流。

## Design（设计）

### 后端：查询方法 + API

`app/infra/trace/repository.py` `TraceRepository` 只读查询方法（复用共享池）：

- `list_traces(limit=50, offset=0, status=None)` → trace 列表（trace_id/session_id/user_query/status/total_duration_ms/start_time），按 start_time 倒序。
- `get_trace(trace_id)` → 单条 trace。
- `get_spans(trace_id)` → 该 trace 的 span（按 start_time 升序）。
- `get_llm_calls(trace_id)` → 该 trace 的 LLM 调用（经 span 关联：`llm_call.span_id = span.span_id AND span.trace_id = $1`）。
- `get_metrics()` → 聚合指标（见下）。

API（新建 `app/api/observability.py` router，挂 `/api/v1/observability`，需登录）：

- `GET /metrics` → 聚合指标。
- `GET /traces?limit=&offset=&status=` → 列表。
- `GET /traces/{trace_id}` → `{trace, spans, llm_calls}`。

**指标定义**（`get_metrics` 一条/几条聚合 SQL）：

| 指标 | 口径 |
|---|---|
| `trace_total` | `count(*)` |
| `status_breakdown` | `group by status` 计数 |
| `success_rate` | `status='SUCCESS'` 占比（无 trace 时为 null） |
| `avg_duration_ms` / `p95_duration_ms` | `avg` / `percentile_cont(0.95)` over `total_duration_ms` |
| `llm_call_total` / `llm_tokens_total` / `llm_avg_latency_ms` | `llm_call` 表聚合（tokens=prompt+completion） |
| `recent_traces` | 最近 N 条（供页面首屏，复用 list_traces） |

### 前端：`/observability` 页面

- 新增 `frontend/src/pages/ObservabilityPage.tsx`，路由 `/observability`（`App.tsx` 注册，AuthGuard 内）。
- `frontend/src/api/observabilityClient.ts`：调上述三个接口。
- 页面结构（用现有 atelier 组件 + tokens，不写新 hex）：
  - **指标卡片区**：总 trace 数、成功率、平均耗时、P95 耗时、LLM 调用数、总 tokens。
  - **trace 表格**：trace_id（缩写）、user_query、status pill、耗时、时间；点击展开/进入明细。
  - **trace 明细 = agent 执行链路时间线（核心视图）**：把该 trace 的 span 按执行顺序渲染成链路——每个节点一行：`span_name`（agent 节点名，如 load_confirmed_requirement / sql_gate / sql_agent / report_agent）、耗时条、状态 pill（SUCCESS/FAILED）、报错；点击节点展开 `input`/`output`（JSON）。span 下挂该节点触发的 **LLM 调用**（model / prompt+completion tokens / latency）。一眼看清「接口内部 agent 跑了哪几步、顺序、每步多久、哪步挂了、调了几次 LLM」。数据全部来自已有的 `agent_trace_span` + `llm_call`，**无需新增埋点**。
- 入口：WorkbenchPage topbar 加一个「可观测」链接（或仅通过 `/observability` URL 直达，二者取其一，默认加 topbar 链接）。

### 文档更正

- CLAUDE.md / AGENTS.md 的 Known Quirk「trace/repository.py 用裸 asyncpg 独立连接」更正为「已用共享池」。

## Files to change（文件改动）

| 操作 | 文件 | 说明 |
|---|---|---|
| MODIFY | `app/infra/trace/repository.py` | + list_traces/get_trace/get_spans/get_llm_calls/get_metrics |
| CREATE | `app/api/observability.py` | metrics / traces / traces/{id} 三端点 |
| MODIFY | `app/main.py` | include observability router |
| CREATE | `frontend/src/api/observabilityClient.ts` | 观测接口客户端 |
| CREATE | `frontend/src/pages/ObservabilityPage.tsx` | 指标卡 + trace 表 + 明细 |
| MODIFY | `frontend/src/App.tsx` | 注册 `/observability` 路由 |
| MODIFY | `frontend/src/components/workbench/`（topbar）| 加「可观测」入口链接 |
| MODIFY | `CLAUDE.md` / `AGENTS.md` | 更正过时的 trace 连接 Known Quirk |

## Reused existing utilities（复用工具）

- `app.infra.db.postgres.get_pool()` —— 共享 asyncpg 池（trace 查询复用它）。
- `observability.agent_trace / agent_trace_span / llm_call` —— 已有表，无需建表。
- `app.infra.auth.deps.get_current_user` —— 端点鉴权。
- 前端 atelier 组件（Card/Table 等）+ `styles/tokens.css` —— 视觉一致。
- 现有 API 客户端模式（`jsonFetch`/auth header）——观测客户端沿用。

## Verification（验证）

- **后端单测**（`tests/test_observability.py`，smoke + 用真 PG 的 persistence 各一组）：
  - `get_metrics` 对空库返回零值/`success_rate=null`，不报错。
  - 插入若干 trace/span/llm_call 后，`list_traces`/`get_trace`/`get_spans`/`get_llm_calls`/`get_metrics` 返回正确聚合（用独立 trace_id，测后清理）。
  - 端点测试：`/observability/metrics`、`/traces`、`/traces/{id}` 返回结构正确（需鉴权）。
- **前端**：`tsc -b` + `oxlint` clean；`ObservabilityPage` 组件测试（mock client，渲染指标卡 + trace 行）。
- **手动冒烟**：起后端 + 前端，访问 `/observability`，看到已有 70 条 trace 的指标与列表。
- **回归**：全套 pytest 绿。

## Explicitly NOT doing（明确不做）

- **不对接 OpenTelemetry / Jaeger / Prometheus** 等外部链路追踪（用户明确先不管）。
- **不做告警**（无阈值触发通知）。
- **不做实时流式刷新**（页面按需加载/手动刷新即可）。
- **不改 trace 写入链路**（sdk.py / Tracer 不动；连接池已是共享池，无需改造）。
- **不做 trace 采样/清理策略**（数据增长治理独立排期）。
- **不做基于角色的访问控制**（观测页与其他页一样仅需登录）。
