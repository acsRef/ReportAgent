# API Reference

> 与 [`2026-07-24-conversational-workbench.md`](2026-07-24-conversational-workbench.md) 第 1–6 节配套。所有受保护接口要求 `Authorization: Bearer <jwt>`。

## 1. 鉴权

所有 `/api/v1/*`（除 `/api/v1/auth/login`、`/api/v1/auth/register`）必须携带 JWT。`/api/v1/chat` 与 `/api/v1/sessions/{id}/confirm`、`/api/v1/sessions/{id}/retry` 必须经过 SecurityGuard。

## 2. 会话与模板

### POST /api/v1/auth/login

Body: `{ username, password }`
Response: `{ access_token, user_id, username }`

### POST /api/v1/auth/register

Body: `{ username, password }`
Response: `{ access_token, user_id, username }`

### GET /api/v1/sessions

Response: `{ sessions: SessionSummary[] }`
其中 SessionSummary = `{ session_id, title, phase, msg_count, updated_at, report_versions: ReportVersionSummary[] }`

### GET /api/v1/sessions/{session_id}

Response: `{ session, messages, current_requirement, latest_report, last_failed_action }`

### GET /api/v1/sessions/{session_id}/reports/{version}

Response: `{ report: ReportVersion }`
纯 DB 读取，不调用 LLM/LangGraph。

### POST /api/v1/templates

Body: `{ name, description, requirement_payload }`
Response: `{ template: ReportTemplate }`

### GET /api/v1/templates

Response: `{ templates: ReportTemplate[] }`（按 user_id 隔离）

### DELETE /api/v1/templates/{template_id}

Response: `{ deleted: true }`（按 user_id 隔离）

## 3. 对话

### POST /api/v1/chat

Body:

```json
{
  "session_id": "optional-uuid",
  "mode": "new | supplement | adjust",
  "user_query": "string (new/supplement/adjust)",
  "base_report_version": 2
}
```

- `mode=new`：新建会话并启动 requirement-analysis。
- `mode=supplement`：基于当前 draft 补充；不执行 SQL。
- `mode=adjust`：调整上一份报告；需要 `base_report_version`。

Response：SSE 流，事件见 [sse-v2.md](sse-v2.md)。

### PATCH /api/v1/sessions/{session_id}/requirement

Body：

```json
{ "requirement": RequirementCard }
```

不启动分析图；仅返回更新后的 RequirementCard。

### POST /api/v1/sessions/{session_id}/confirm

Body：`{}`
SSE 流；服务端重新校验完整性后启动 confirmed-execution。

### POST /api/v1/sessions/{session_id}/retry

Body：`{}`
基于会话持久化的失败操作恢复，不创建新会话。

## 4. 错误码

| Code | HTTP | 含义 |
| --- | --- | --- |
| `INVALID_REQUIREMENT` | 422 | 草稿不合法 |
| `REQUIREMENT_INCOMPLETE` | 409 | 缺失字段未补全 |
| `REQUIREMENT_LOCKED` | 409 | 已锁定的草稿不可修改 |
| `CHAT_BUSY` | 409 | 会话正在执行 |
| `SESSION_NOT_FOUND` | 404 | 会话不存在或不属于当前 user |
| `VERSION_NOT_FOUND` | 404 | 报告版本不存在 |
| `TEMPLATE_NOT_FOUND` | 404 | 模板不存在或不属于当前 user |
| `SECURITY_REJECTED` | 422 | SecurityGuard 阻断 |
| `INTERNAL` | 500 | 服务器内部错误 |

## 5. 权限矩阵

| Resource | Read 权限 | Write 权限 |
| --- | --- | --- |
| `agent.session` | `user_id == jwt.user_id` | 同上 |
| `agent.requirement_draft` | 同上 | 同上 |
| `agent.report_version` | 同上 | 同上 |
| `app.conversations` | 同上 | 同上 |
| `app.report_template` | `user_id == jwt.user_id` | 同上 |
| `app.users` | 仅自己（除 admin） | 同上 |

## 6. 行为约束

- 所有 read/write 都必须在事务中。
- 创建/更新 requirement draft 与写 conversation pointer 同事务。
- 创建 report_version 与更新 session latest + conversation pointer 同事务。
- 报告版本号在事务内 `MAX(version)+1`，配 unique `(session_id, version)`。
- 不允许在事务外部分写入。
- 不允许在错误路径上写孤儿数据。
