# Plan: 让「执行成功 vs 执行失败」与「零行 vs 超时」在系统内可被准确区分

## Context

用户在桌面上抛出两个核心原则，以及三类后果：

1. **可执行成功（isError: false）**——即使零结果（"无匹配记录"），当作确定结论，不让 agent / 用户误以为查询出错。
2. **执行失败（isError: true）**——必须给错误详情；不能把数据库不可达 / 超时 / 权限错吞成「没找到」。
3. **错误信息要包含尝试了什么**——让 agent 能用同样的参数重试，让用户能精准报告什么没查到。

`feat/atelier-demo` 分支上**已经有** CTE 行数上限、`statement_timeout` / `connect_timeout`、6 类错误分类（`timeout | syntax | object | connection | permission | other`），但**分类在 SSE 输出层和持久化层全部被吞**：

- `confirmed_execution_graph.py:215-257` `_confirmed_report_agent` 用 `if rows: has_data = True` 判定 → 合法零行被误判 `FAILED`。
- `confirmed_execution_graph.py:366-378` `_route_after_report` 在 FAILED 短路 `persist_report` → 失败不入库，零行也不入库。
- `main.py:706-718` SSE error 事件硬编码 `{code: "QUERY_FAILED", message: "查询未返回数据", failed_action: "confirm"}`——error_kind、SQL、kind 全部丢失。
- `report_version_repository.py:46-67` 写入字段不含 `error` / `error_kind` / `truncated`。
- 前端 `ReportPaper` footer 硬编码 "需求已确认 · SQL 已校验"，无 zero-row 占位；adapter 丢弃空 table；Reducer 看到 `report/received` 立刻 `phase: 'report_ready'`，从不看 status / row_count。

目标：
1. **执行成功 + 零行 → 写一份带「无数据」徽章的报告版本入库**，正常发 SSE `report`，前端空 paper 渲染。
2. **执行失败（任意 kind）→ SSE error 事件带 kind + tried SQL（≤200 字符）+ 中文友好 message**；前端 ErrorCard 区分 timeout / connection / permission / syntax / object / other。
3. **执行失败 → 也写一行 `status='error'` 历史版本**，query_snapshot 保留 sql + error_kind + error + truncated。
4. 前端 ReportPaper footer 在 `row_count=0` 时改为「未找到匹配记录」；adapter 在空 table 时附 `empty: true` 标记，让 paper 渲染占位提示。

## Design

### A. 后端：SSE 事件 + 持久化全面分类

#### A1 主图（`confirmed_execution_graph.py`）三态分离

`_confirmed_report_agent` 的 `has_data` 判定改为**看 `qr.error` 是否为 None**：

```python
qr = state.get("query_result")
err = (qr or {}).get("error") if isinstance(qr, dict) else None
has_data = err is None and (qr or {}).get("rows")
# 三态:
# - err is not None  → sql 执行失败
# - err is None, rows=[] → 合法零行（无 error 但没数据）
# - err is None, rows=[] 之外 → 真成功
if err is not None:
    status = "FAILED"
    state["error"] = err          # ErrorDetail{code, message, kind}
elif not has_data:
    status = "EMPTY"              # 新增枚举值
else:
    status = "SUCCESS"
```

把 `execution_status="EMPTY"` 通过 TypedDict 加进 `ConfirmedExecutionState`，语义：`execution_status` 集合增加 `"EMPTY"` 一档，不修改其它。

#### A2 persist_report 接受 EMPTY 与 FAILED 双路径

`_route_after_report` 从 `status in ("SUCCESS", "EMPTY")` 都走 `persist_report`，FAILED 才短路 `END`。`persist_report` 内部按 status 决定写入：

```python
if verdict == "FAILED":
    err = state.get("error")
    error_detail = (
        err.model_dump() if hasattr(err, "model_dump") else
        (err if isinstance(err, dict) else {"code": "QUERY_FAILED", "message": "", "kind": "other"})
    )
    row = await report_version_service.persist_error_run(...)
elif verdict == "EMPTY":
    row = await report_version_service.persist_empty_run(...)
else:  # SUCCESS
    if base_version is None:
        row = await report_version_service.persist_confirmed_run(...)
    else:
        row = await report_version_service.persist_adjust_run(...)
```

新增 `report_version_service.persist_empty_run(...)` 和 `persist_error_run(...)`，不动 `persist_confirmed_run` 内部语义，仅扩展两个新 helper。

#### A3 SSE error 事件结构化

`main.py` `/confirm` 路径和 `/chat adjust` 路径的 error 事件统一升级为 `_build_sse_error(err, sql, failed_action)` helper：

```python
def _build_sse_error(err, sql, failed_action):
    err_dict = err if isinstance(err, dict) else {}
    kind = err_dict.get("kind") or "other"
    if kind not in _ERROR_FRIENDLY: kind = "other"
    code = _ERROR_CODE[kind]
    base_message = _ERROR_FRIENDLY[kind]
    snippet = _normalize_sql_snippet(sql, limit=200)
    message = base_message if not snippet else f"{base_message}\n尝试的 SQL: {snippet}"
    return {
        "event": "error",
        "data": json.dumps({
            "code": code, "message": message,
            "recoverable": kind in ("timeout", "connection", "object", "other"),
            "failed_action": failed_action,
            "kind": kind, "sql": snippet,
        }, ensure_ascii=False),
    }
```

`_ERROR_FRIENDLY` / `_ERROR_CODE` 在 `main.py` 顶层维护：
| kind | 中文 message | code |
|---|---|---|
| timeout | 查询超时，请缩小时间范围或维度后重试 | QUERY_TIMEOUT |
| connection | 数据库连接失败，请稍后重试 | QUERY_CONNECTION |
| permission | 权限不足，无法执行该查询 | QUERY_PERMISSION |
| syntax | SQL 语法错误，请调整查询条件后重试 | QUERY_SYNTAX |
| object | 查询引用的表/列不存在，请检查维度后重试 | QUERY_OBJECT |
| other | 查询执行失败，请稍后重试或调整需求 | QUERY_FAILED |

`failed_action` 在 `confirmed_execution_graph` 失败时透传：`failed_action="sql"`，因为失败发生在 sql_graph 子图里；REQUIREMENT_INCOMPLETE / SESSION_NOT_FOUND / INTERNAL 仍然用各自的原值。

#### A4 失败也写入历史版本

新增 `report_version_service.persist_error_run(...)`：

```json
{
  "answer": {"text": "查询执行失败", "table": null, "chart": null, "insight": null},
  "trace": [],
  "execution_status": "FAILED",
  "error": {"code": "...", "message": "...", "kind": "..."}
}
```

`query_snapshot` 字段：
```json
{
  "sql": "...", "error_kind": "...", "error": "...",
  "row_count": 0, "truncated": false,
  "columns": [], "rows": []
}
```

`_route_after_report` 的 FAILED 分支不再直接 `END`，改为「走 persist_report（写入 status='error'）→ END」。这样版本列表能看 v1 done / v2 error / v3 done，`/api/v1/sessions/{sid}/reports/{v}` 返回完整 payload + `status='error'`，前端 ReportPaper 据此决定渲染。

#### A5 models 扩展

- `QueryResult` 增 `error_kind: Optional[Literal["timeout","syntax","object","connection","permission","other"]] = None` 字段。
- `SessionSummary.report_versions[].status` 已有 `Literal["generating", "done", "error"]`，无需改。
- `ReportVersionDetailResponse` 加 `execution_status: Optional[Literal["SUCCESS","EMPTY","FAILED"]] = None`（向后兼容，旧版本为 None 时前端按 SUCCESS 处理）。

#### A6 sql_graph._evaluate 已实现，不动

`_evaluate`（`sql_graph.py:412-441`）的 timeout/connection/permission 快路径已生成友好 message；本轮不改这块逻辑，仅保证这些 message 能透传到 SSE。

唯一小改：`_confirmed_sql_agent` 把 `ss.get("error")` 与 `ss.get("generated_sql")` 透传到主图 state，`ConfirmedExecutionState` TypedDict 增 `sql: Optional[str]` 字段。

### B. 前端：UI 区分 + 错误码映射

#### B1 AnalysisError 增字段

`frontend/src/types/analysis.ts` 的 `AnalysisError` 增：
- `kind?: 'timeout' | 'syntax' | 'object' | 'connection' | 'permission' | 'other' | null`
- `sql?: string | null`
- `failed_action` 联合加入 `'sql'`

`frontend/src/api/analysisEvents.ts` 解析时把这两个字段写进 `AnalysisError` 对象，kind 经 6 类白名单校验，sql 任意字符串。

#### B2 ErrorCard 文案映射

`frontend/src/components/workbench/ErrorCard.tsx`：

```tsx
const KIND_TITLE: Record<NonNullable<Props['kind']>, string> = {
  timeout:    '查询超时',
  connection: '数据库连接失败',
  permission: '权限不足',
  syntax:     'SQL 语法错误',
  object:     '查询对象不存在',
  other:      '查询执行失败',
}
const title = kind ? KIND_TITLE[kind] : '执行分析时发生错误'
const detail = message ?? '查询未能返回数据。'
```

ErrorCard 增加折叠「查看 SQL」按钮：`<details><summary>查看尝试的 SQL</summary><pre><code>{sql}</code></pre></details>`——默认折叠，避免大段 SQL 噪音。

#### B3 ReportPaper zero-row 占位

- `ReportVersionDetail` 增 `execution_status?: 'SUCCESS' | 'EMPTY' | 'FAILED'` 字段。
- 在渲染前：
  - `if detail.status === 'error' || verdict === 'FAILED'` → 切到错误带（用 `detail.report_payload.error` 渲染 message + sql）。当用户从右栏切到失败版本时显示（不依赖 SSE）。
  - `if execution_status === 'EMPTY' || row_count === 0` → 在 paper 顶部加 `<section className="wb-empty-band">未找到匹配记录</section>`；footer 改为「查询已执行，未匹配到数据」；table block 渲染占位（"未找到匹配记录"），但仍可导出 xlsx。

#### B4 适配器（adapter/reportAdapter.ts）附 empty 标记

不再丢弃空 table：
```ts
if (answer.table && answer.table.columns.length > 0) {
  blocks.push({
    id: 'table',
    type: 'table',
    title: '数据明细',
    data: { ...answer.table, empty: answer.table.rows.length === 0 },
  })
}
```

TableBlock 根据 `data.empty` 渲染 0 行占位（已经有 `rows.length === 0` 早返，加一个「未找到匹配记录」 hint）。

#### B5 canRetryFailedAction 接受 sql

`frontend/src/stores/analysisReducer.ts` 的 `canRetryFailedAction` 从只接受 `failed_action === 'confirm'` 改为接受 `'confirm' | 'sql'`，让 SQL 失败也能重试（按 user 要求，分离 retry 路径时不阻塞）。

### C. 测试 + 验证

#### C1 单元测试

`backend/tests/test_sql_error_envelope.py`（新，10+ 用例）：
- `test_build_sse_error_includes_kind_and_sql`
- `test_build_sse_error_truncates_sql_to_200`
- `test_build_sse_error_permission_is_not_recoverable`
- `test_build_sse_error_unknown_kind_falls_back_to_other`
- `test_build_sse_error_collapses_newlines_in_sql`
- `test_confirmed_report_agent_empty_rows_yields_empty_status`
- `test_confirmed_report_agent_error_yields_failed_status`
- `test_confirmed_report_agent_success_with_rows_yields_success`
- `test_query_snapshot_for_failure_keeps_sql_and_error_kind`
- `test_query_snapshot_for_empty_keeps_rows_metadata`
- `test_persist_report_routes_failed_to_persist_error_run`
- `test_persist_report_routes_empty_to_persist_empty_run`
- `test_persist_report_routes_success_to_persist_confirmed_run`

旧测试更新：
- `tests/graphs/test_confirmed_routing.py` —— FAILED/EMPTY 不再短路 persist，全部入库
- `tests/graphs/test_confirmed_report_agent.py` —— EMPTY 不再是 FAILED

前端 vitest：
- `frontend/src/components/workbench/__tests__/ErrorCard.test.tsx`（新，7 用例）：kind 标题映射 + SQL 折叠 + retry 可用
- `frontend/src/components/workbench/__tests__/ReportPaper.test.tsx`（加 2 用例）：EMPTY 渲染「未找到匹配记录」band，FAILED 归档带 + tried SQL
- `frontend/src/adapter/__tests__/reportAdapter.test.ts`（更新）：空表附 `empty=true`
- `frontend/src/api/__tests__/analysisEvents.test.ts`（新，5 用例）：kind/sql 透传
- `frontend/src/stores/__tests__/analysisReducer.test.ts`（加 1 用例）：`'sql'` 也可重试

#### C2 端到端（手动 + e2e）

- 后端 `pytest --ignore=tests/e2e` 全绿（90 passed）。
- 前端 `npm run lint && npm run test:run` 全绿（241 passed）。
- tsc clean。
- 手动矩阵（用 vite dev + uvicorn 启 PG）：见下表，按 5 行手测一次确认 SSE / UI / DB 三方一致：

| 场景 | 期望 SSE | 期望前端 | 期望 DB |
|---|---|---|---|
| 正常查询返回 6 行 | `report` | ReportPaper 渲染表格 | v1 status=done |
| 查询合法但零匹配 | `report`（execution_status=EMPTY） | ReportPaper 「未找到匹配记录」+ footer 改文案 | v1 status=done, row_count=0 |
| SQL 超时 | `error` code=QUERY_TIMEOUT kind=timeout sql=<query> | ErrorCard 「查询超时」+ 「查看 SQL」折叠 | v1 status=error, error_kind=timeout |
| 表名写错（mock） | `error` code=QUERY_OBJECT kind=object sql=<query> | ErrorCard 「查询对象不存在」 | v1 status=error |
| 权限错（mock） | `error` code=QUERY_PERMISSION kind=permission | ErrorCard 「权限不足」 | v1 status=error |

## Files to change

- `backend/app/agent/confirmed_execution_graph.py` —— 三态、TypedDict、route、persist_report 三路
- `backend/app/services/report_version_service.py` —— `persist_empty_run` / `persist_error_run`
- `backend/app/infra/db/report_version_repository.py` —— `append_version` 接受任意 status 字符串（已支持，无需改）
- `backend/app/models/contracts.py` —— `QueryResult.error_kind`、`ReportVersionDetailResponse.execution_status`
- `backend/app/main.py` —— `_build_sse_error` helper + `_normalize_sql_snippet` + 两条 SSE 路径
- `frontend/src/types/analysis.ts` —— `AnalysisError.kind` / `.sql`、`failed_action` 加 `'sql'`
- `frontend/src/api/analysisEvents.ts` —— `FAILED_ACTIONS` 加 `'sql'`、`ERROR_KINDS` 白名单、解析 kind/sql
- `frontend/src/api/sessionsClient.ts` —— `ReportVersionDetail.execution_status?`
- `frontend/src/components/workbench/ErrorCard.tsx` —— kind 映射 + SQL 折叠
- `frontend/src/pages/WorkbenchPage.tsx` —— 透传 kind + sql
- `frontend/src/components/workbench/ReportPaper.tsx` —— EMPTY / FAILED / status=error 分支
- `frontend/src/adapter/reportAdapter.ts` —— empty 标志
- `frontend/src/components/report/blocks/TableBlock.tsx` —— 0 行占位 + footer 改文案
- `frontend/src/styles/workbench.css` —— `.wb-empty-band`
- `frontend/src/stores/analysisReducer.ts` —— `canRetryFailedAction` 接受 `'sql'`
- 新增 `backend/tests/test_sql_error_envelope.py`
- 更新 `backend/tests/graphs/test_confirmed_routing.py`、`test_confirmed_report_agent.py`
- 新增 `frontend/src/components/workbench/__tests__/ErrorCard.test.tsx`、`frontend/src/api/__tests__/analysisEvents.test.ts`
- 更新 `frontend/src/adapter/__tests__/reportAdapter.test.ts`、`frontend/src/components/workbench/__tests__/ReportPaper.test.tsx`、`frontend/src/stores/__tests__/analysisReducer.test.ts`

## Reused existing utilities

- `app.models.contracts.ErrorDetail.kind` 已有 6 类枚举语义。
- `report_version_repository.append_version` 沿用，`status='error'` 直接落库（无 schema 改动）。
- `psycopg2` 错误类已被 `_classify_psycopg2_error` 处理，本轮不再扩展。
- 前端 `WorkbenchPage → ErrorCard` 的现有 wired path（只加 kind/sql 两个 prop）。
- `canRetryFailedAction` 现成的 retryability gate（扩一个 action 类型即可）。
- `useToast` 仍用于 console 提示。`ToastProvider` 已在测试 setup 里。

## Explicitly NOT doing

- 不重写 `psycopg2 → asyncpg` 路径（`_get_pg_conn` 同步连接 OK）
- 不重做 LLM 的 prompt（`chat_advisor` / `insight_analyst` 对 0 行已有 fallback 文本「查询结果为空，无法生成洞察。」——保留）
- 不做 SSE 错误事件的多语种 i18n（中文足够）
- 不改 ReportPaper 「重新生成 / 继续调整」按钮的语义（保留当前 `onAdjust` 触发器）
- 不为「失败重试」加新按钮（沿用现有 ErrorCard 的「重试当前任务」）
- 不把错误日志里的 SQL 文本脱敏（用户自己的 session 内可见，与 query_snapshot 一致）

## Outcome

- Commit：`56fb0fa` "feat(workbench): 三态分离 — 成功零行 vs 失败超时可被区分"
- backend 90 passed（77 → 90，+13 envelope）
- frontend 241 passed（+17 ErrorCard/ReportPaper/adapter/events/reducer）
- tsc clean、oxlint clean
- 已推到 master + feat/atelier-demo
