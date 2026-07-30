# Plan: SQL 结果数据量保护 + 超时与错误分类 + Excel 导出

## Context

用户在桌面上指出两个真实存在的生产风险：

1. **数据量失控**：`execute_sql` 用 `cur.fetchall()` 把全部行灌进 Python 内存、塞进 JSON、`json.dumps` 进 `agent.report_version.query_snapshot` JSONB、再原样走 SSE `report` 事件回前端。1M 行 = 内存爆、SSE 卡、PG JSONB 爆、客户端解析爆。**没有行数上限**。
2. **超时与其他错误混淆**：`psycopg2.connect(DATABASE_URL)` 没设 `statement_timeout` / `connect_timeout` / `options`，一个 `EXPLAIN` 或慢 SQL 会无限挂着；`except Exception` 一把抓，`_evaluate` 只看 `error` 是否非空决定要不要重试——超时和语法/字段错乱都走同一条 3 次重生成路径，用户看到的是「重试 3 次失败转 clarify」这种误导信息。**没有分类**。
3. **Excel 导出**：`ReportPaper` 的「导出」按钮只是 toast demo；`utils/export.ts` 只能导出 HTML（且只截前 50 行）；后端 0 个导出端点，`requirements.txt` 无 `openpyxl`。

目标：
- 1M 行场景下：内存有上限、PG 有超时、SSE 不爆、`query_snapshot` 有上限、用户能导出全量 Excel
- 慢 SQL 有硬超时；超时、语法、连接、未知列各自分类，超时不盲试重生成
- 「导出」按钮接上真的端点

## Design

### A. SQL 工具：行数上限 + 超时 + 错误分类

**A1 行数上限**：在 `backend/app/tools/sql_tools.py` 把 `cur.fetchall()` 换成「CTE subquery + `LIMIT MAX_RESULT_ROWS+1`」一次往返拿「真实行数 + 截断后行集」。`MAX_RESULT_ROWS = 5000` 放在 `backend/app/tools/sql_tools.py` 顶部常量。

**A2 超时**：`_get_pg_conn()` 改为带 options：
```python
def _get_pg_conn():
    return psycopg2.connect(
        PG_DSN,
        connect_timeout=10,                                # 10s 建连超时
        options=f"-c statement_timeout={30 * 1000}",        # 30s 查询硬上限 (ms)
    )
```
`EXPLAIN` 也走同一个 `_get_pg_conn()`，自动获得超时。

**A3 错误分类**：用 `psycopg2.errors` 模块做精细捕获，按树状 except 链降级到 `str(exc)`。`execute_sql` 与 `validate_sql` 各自把错误结构化为：
```python
{
  "error": str(exc)[:300],
  "error_kind": "timeout" | "syntax" | "object" | "connection" | "permission" | "other",
  "truncated": True,        # 行数被截
  "row_count": 12345,       # 真实全表行数
}
```
实现：`execute_sql` 改成 `WITH src AS ({sql}) SELECT *, (SELECT count(*) FROM src) AS _total FROM src LIMIT {MAX_RESULT_ROWS+1}`。`truncated` 来自 `count > MAX_RESULT_ROWS`。

**A4 模型 / 前端读取 `error_kind`**：`QueryResult` 加字段 `error_kind`（Optional[Literal[...]]）；`ErrorDetail` 增 `kind` 字段。`sql_graph._evaluate` 按 `error_kind` 决策：
- `timeout` / `connection` / `permission` → 不重试；`execution_status = "FAILED"`；给友好提示「查询超时/数据库连接问题，请缩小时间范围或维度再试」
- `syntax` / `object` → 维持现有重试（把 `error` 文本喂回 LLM 重新生成）
- `other` → 维持现有重试

### B. 报告壳：导出按钮接真端点

**B1 后端导出端点**：`backend/app/main.py` 加 `GET /api/v1/sessions/{sid}/reports/{v}/export.xlsx`——拉 `report_version.query_snapshot`（完整数据集，不受 5000 行截断影响——这是 Excel 的卖点），用 `openpyxl` 生成 xlsx 流式返回。`openpyxl` 新增到 `backend/requirements.txt`（纯 Python、无原生扩展依赖）。`xlsx_response` 帮手：在 `backend/app/main.py` 内写 `StreamingResponse(BytesIO(), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': f'attachment; filename="report-{sid}-v{v}.xlsx"'})`。

**B2 路径**：`/api/v1/sessions/{sid}/reports/{version}/export.xlsx`：
1. JWT 校验 + owner 校验
2. `report_version_repository.get_version(...)` 取 `query_snapshot`
3. 从 `query_snapshot` 读 `columns`（`[{"name":..., "type":...}]`）和 `rows`（`[{col: val, ...}, ...]`）
4. `openpyxl.Workbook` → `ws.append([cols])` + `for row in rows: ws.append([row.get(c['name']) for c in cols])`
5. 写到 `BytesIO()`，返回 `StreamingResponse` 单次流

**B3 前端接线**：`ReportPaper.tsx` 的导出按钮改为 `window.open('/api/v1/sessions/.../reports/.../export.xlsx', '_blank')` 触发浏览器下载（Cookie 自动带 JWT）；同时保留 `utils/export.ts` 的 `exportReportHTML` 作为「HTML 导出」备选（次级按钮），原 toast demo 拆为「下载 xlsx」+「下载 html」。

### C. 测试 + 验证

新增 `backend/tests/test_sql_limits.py`（marker smoke）：
- `execute_sql` 对 6 行的种子数据不截断：`truncated=false`、`rows` 长度 = 6
- 模拟 7000 行返回：`truncated=true`、`rows` 长度 = 5000、`row_count>=7000`
- 超时模拟：monkeypatch `_get_pg_conn` 抛 `psycopg2.errors.QueryCanceled` → 结果里 `error_kind="timeout"`
- 语法错模拟：抛 `psycopg2.errors.SyntaxError` → `error_kind="syntax"`
- 未知列：抛 `psycopg2.errors.UndefinedColumn` → `error_kind="object"`
- 连接错：抛 `psycopg2.OperationalError` → `error_kind="connection"`
- 端点测试：`/export.xlsx` 200 + content-type xlsx；用 `openpyxl.load_workbook` 反向加载校验列名/行数

回归：`pytest --ignore=tests/e2e` 全绿。

`REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_full_flow.py -s` 验证：现有 23 行的 fact_sales 走完 → 报告正常 → 访问导出端点 → 收到 6 行 6 列 xlsx。

前端：报告壳「导出」按钮在 vite dev 加载后实测下载（用 Playwright 烟测脚本，或在 .claude 浏览器手测）。

## Files to change

- `backend/app/tools/sql_tools.py` — 行数上限 + 超时 options + 错误分类 + 返回 truncated/row_count/error_kind；同改 `validate_sql`
- `backend/app/agent/sql_graph.py` — `_evaluate` 错误分类分支（新增 `timeout`/`connection`/`permission` 不重试；`syntax`/`object` 走原重试）
- `backend/app/models/contracts.py` — `QueryResult.error_kind: Optional[Literal[...]]` 字段；`ErrorDetail.kind` 同
- `backend/app/main.py` — `GET /api/v1/sessions/{sid}/reports/{version}/export.xlsx` 端点
- `backend/requirements.txt` — `+ openpyxl>=3.1`
- `frontend/src/components/workbench/ReportPaper.tsx` — 导出按钮接 `window.open('/api/v1/.../export.xlsx', '_blank')`；保留「下载 HTML」次级按钮
- 新增 `backend/tests/test_sql_limits.py`

## Reused existing utilities

- `psycopg2.errors.{QueryCanceled, OperationalError, ProgrammingError, SyntaxError, UndefinedColumn, UndefinedTable, UndefinedFunction, AdminShutdown, CrashShutdown}` —— 不引入自建错误分类枚举
- `openpyxl.Workbook` + `StreamingResponse` —— 不重写 xlsx 库
- `report_version_repository.append_version` —— 不另起新表，沿用 `agent.report_version.query_snapshot` JSONB
- `sql_graph._evaluate` 已有的 kind → 友好 message 分支（不动）

## Verification

- `cd backend && pytest --ignore=tests/e2e` 全绿（67 → 77+）
- `cd frontend && npm run lint && npm run test:run` 全绿
- `cd backend && REPORTAGENT_E2E=1 pytest tests/e2e/test_full_flow.py -s` 通过
- 手动验证矩阵（用 vite dev + uvicorn 启 PG）：

| 场景 | 期望 SSE | 期望前端 | 期望 DB |
|---|---|---|---|
| 正常查询返回 6 行 | `report` | ReportPaper 渲染表格 | v1 status=done |
| 合法零匹配（待下一轮）| `report` (execution_status=EMPTY) | ReportPaper 「未找到匹配记录」 | v1 status=done, row_count=0 |
| SQL 超时 | `error` code=QUERY_TIMEOUT kind=timeout sql=<query> | ErrorCard「查询超时」+ 「查看 SQL」折叠 | v1 status=error, error_kind=timeout |

## Explicitly NOT doing

- 把 `exportReportHTML` 也重写到后端（HTML 仍是纯前端）
- `psycopg2 → psycopg3` 迁移（保持 v2 即可，v2 异常类够用）
- 全量流式输出（openpyxl 一次性写 BytesIO 已经能支持十万行级；继续做大 CSV 流式分块不在本轮范围）
- 报告壳 / 表格增加分页（`MAX_RESULT_ROWS=5000` 已够覆盖 UI 翻页需求）
- `exportReportHTML` 的 50 行截断调整（保持，HTML 是备份路径）
- 把失败也写入历史版本（这是下一轮 `confirmed-exec-three-state` 的范围）
