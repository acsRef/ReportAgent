# Analysis State Machine

> 前后端共享的阶段定义与合法转移表。`phase` 是 UI 阶段权威来源；后端 SSE 推送，前端 reducer 镜像。

## 1. Phase 列表

| Phase | 含义 | busy 派生 |
| --- | --- | --- |
| `idle` | 空会话或用户重置 | false |
| `parsing` | 启动 requirement-analysis 流程 | true |
| `awaiting_missing` | 需求草稿有 missing_fields，等待补充 | false |
| `awaiting_confirm` | 需求草稿完整，等待用户确认 | false |
| `generating` | confirmed-execution 执行中 | true |
| `adjusting` | 报告调整流程执行中 | true |
| `report_ready` | 至少 1 个 report_version | false |
| `error` | 错误终态，等待重试 | false |

`busy = phase ∈ { parsing, generating, adjusting }`

## 2. 合法转移表

| From | Event | To | 触发位置 |
| --- | --- | --- | --- |
| `idle` | 发送新问题 | `parsing` | 后端 SSE |
| `parsing` | 需求草稿缺失 | `awaiting_missing` | 后端 SSE |
| `parsing` | 需求草稿完整 | `awaiting_confirm` | 后端 SSE |
| `awaiting_missing` | 用户 PATCH 仍缺 | `awaiting_missing` | 后端 SSE |
| `awaiting_missing` | 用户 PATCH 完整 | `awaiting_confirm` | 后端 SSE |
| `awaiting_confirm` | 用户 /confirm | `generating` | 后端 SSE |
| `generating` | 执行成功 | `report_ready` | 后端 SSE |
| `generating` | 执行失败 | `error` | 后端 SSE |
| `report_ready` | 发送调整请求 | `adjusting` | 后端 SSE |
| `adjusting` | 调整成功 | `report_ready` | 后端 SSE |
| `adjusting` | 调整失败 | `error` | 后端 SSE |
| `report_ready` | 切换会话 | `idle` 或新会话 phase | 客户端 reducer |
| `error` | /retry | 原 phase | 后端 SSE |
| `error` | 切换会话 | `idle` | 客户端 reducer |

## 3. 后端层

`backend/app/agent/parent_graph.py` 收敛为两个 graph：

- **requirement-analysis**：`security_guard → classify_intent → data_agent(schema only) → requirement_parse → END`
- **confirmed-execution**：`load_confirmed_requirement → data_agent(refresh) → sql_agent → evaluate/retry → report_agent → persist_report → END`

SQL gate：

- requirement-analysis graph 中只注册 Schema tools（`search_tables` / `get_table_ddl` / `list_tables`）。
- `validate_sql` / `execute_sql` / Report Agent tools 不在 requirement-analysis graph 中。
- confirmed-execution graph 在进入 `sql_agent` 前必须通过 gate：
  - `draft.user_id == jwt.user_id`
  - `draft.status == 'complete'`
  - `draft.missing_fields == []`
  - 所有 `assumptions.accepted != null`

## 4. 前端层

`frontend/src/stores/analysisReducer.ts` 是纯函数 reducer。组件只 dispatch action，不直接修改 phase。

```text
SSE 事件 → API client → dispatch → reducer → React re-render
```

`isBusyPhase(phase)` 派生 busy 状态；不允许组件自行写 `busy: boolean`。

## 5. 错误处理

- `error` 事件携带 `failed_action`，告诉前端是哪个 action 失败。
- 前端 reducer 写 `error: AnalysisError`，但**不清空** `reportVersions` 与 `selectedReportVersion`，便于失败后恢复上下文。
- `retry` 后服务端按 `failed_action` 恢复，phase 流转与正常路径一致。
