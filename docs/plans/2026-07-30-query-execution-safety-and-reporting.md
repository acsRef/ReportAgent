# Plan: 查询执行安全 + 报告壳正确性 + 三态分离

> 状态: 已完成（层 2/6/7 落地）

> **Supersedes（合并自）**：[2026-07-30-sql-row-cap-and-export.md](2026-07-30-sql-row-cap-and-export.md)、[2026-07-30-confirmed-exec-three-state.md](2026-07-30-confirmed-exec-three-state.md)
>
> **References**：[2026-07-30-bug-review.md](2026-07-30-bug-review.md)（B-2 残留语义错位来源）、[2026-07-30-design-principles-grill.md](2026-07-30-design-principles-grill.md)（合规性 review）

## 背景（Context）

`feat/atelier-demo` 在 SQL 执行与报告呈现两个层面都暴露出真实的生产风险，三份相关工作计划中两件已完成（`sql-row-cap-and-export` 实现于 commit `e8e9b1e`、`confirmed-exec-three-state` 实现于 `56fb0fa`），还有一件（`bug-review`）枚举了**未在已落地工作中处理**的残留问题。本文件把这三份合为一份，作为「设计意图 + 已落地 + 待补」单一来源。

### 涉及的三层风险

1. **数据量失控**（`execute_sql` 原 `cur.fetchall()`）：1M 行 → 内存爆、SSE 卡、PG JSONB 爆、客户端解析爆，**没有行数上限**。
2. **超时与其他错误混淆**：`psycopg2.connect(DATABASE_URL)` 原本不设 `statement_timeout` / `connect_timeout` / `options`，且 `except Exception` 一把抓、`_evaluate` 只看 `error` 是否非空决定要不要重试——超时、语法、字段错乱、连接错误**走同一条 3 次重生成路径**，用户看到的误导信息「重试 3 次失败转 clarify」。
3. **三态不分离**（成功 vs 失败 vs 零行的混淆）：`confirmed_execution_graph.py:215-257` `_confirmed_report_agent` 用 `if rows: has_data = True` 判定——合法零行被误判 `FAILED`；`confirmed_execution_graph.py:366-378` `_route_after_report` 在 FAILED 短路 `persist_report`——失败不入库、零行也不入库；`main.py:706-718` SSE error 事件硬编码 `{code: "QUERY_FAILED", message: "查询未返回数据", failed_action: "confirm"}`——error_kind、SQL、kind 全部丢失；前端 `ReportPaper` footer 硬编码 "需求已确认 · SQL 已校验" 误导零行场景，adapter 丢弃空 table。
4. **Excel 导出缺口**：`ReportPaper` 的「导出」按钮只是 toast demo；`utils/export.ts` 只导出 HTML（且截前 50 行）；后端 0 个导出端点；`requirements.txt` 无 `openpyxl`。

### Bug review（2026-07-30）补充的「未在已落地工作中处理」的真实问题

实施 `confirmed-exec-three-state` 后，agent `sql_graph._build_output` 仍有**残留语义错位**：

- `_build_output` 主路径（line 457-464）写 `status='FAILED' if has_error else 'SUCCESS'`——**没有 EMPTY 分支**。`Literal['SUCCESS', 'FAILED', 'EMPTY']` 在 `models/contracts.py:56` 已被允许，但 SQL 子图内部永远不会写出 EMPTY。父图（`confirmed_execution_graph._confirmed_report_agent`）现在「从 rows 反推 verdict」绕过去了，但 `query_result.status` 仍是 2 态。下游任何用 `query_result.status == 'EMPTY'` 判定的代码会无声失效——这是埋伏
- 主路径同时错误覆盖 `row_count = len(rows)`（line 461），丢弃 `execute_sql` 已经填好的真实 total（`sql_tools.py:157` 的 CTE `count(*)`）。截断场景：50000 行查询 → `truncated=True` 但 `QueryResult.row_count=5001`，LLM 在 `_plan_analysis` 看到错误的行数估算

另两个 review 中**关键**的未修 bug：

| 编号 | 文件:行 | 描述 |
|---|---|---|
| B-7 | `infra/db/{report_version,requirement}_repository.py:41-44`、`services/report_version_service.py:194-195` | `MAX(version)+1` READ COMMITTED 下并发竞态，UNIQUE 捕获但无重试 → 并发 confirm 返 500 |
| B-3（trace 子方面） | `infra/trace/sdk.py` + `agent/{sql,data,report}_graph.py` TypedDict | 子图 state 不声明 `trace_id` → 所有子图 trace 进 `_local[""]` 同一桶，并发污染 |

### 已落地的相关实现

- `56fb0fa`：confirmed_execution_graph 三态（SUCCESS/EMPTY/FAILED）；`persist_empty_run` / `persist_error_run` 写历史版本；`_build_sse_error` helper 把 kind 映射成 6 种中文 message + tried SQL；前端 ErrorCard 按 kind 标题 + 折叠 SQL；ReportPaper EMPTY/FAILED 分支
- `e8e9b1e`：`MAX_RESULT_ROWS=5000` + CTE `count(*)` 截断；`statement_timeout=30s`、`connect_timeout=10s`；6 类 `error_kind`；`/export.xlsx` 端点 + openpyxl
- `backend/tests/test_sql_limits.py`（10 用例）+ `test_sql_error_envelope.py`（13 用例）+ `e2e/test_full_flow.py`
- frontend ErrorCard / ReportPaper / reportAdapter / analysisEvents 测试

## 设计（Design）

按依赖顺序分层。

### 层 1：SQL 工具——行数上限 + 超时 + 错误分类（已落地于 `e8e9b1e`）

`backend/app/tools/sql_tools.py`：
- 常量 `MAX_RESULT_ROWS=5000`、`CONNECT_TIMEOUT_S=10`、`STATEMENT_TIMEOUT_MS=30_000`
- `_get_pg_conn()` 带 `connect_timeout` + `options="-c statement_timeout=30000"`
- `_classify_psycopg2_error(exc)` 返回 6 类：`timeout / connection / permission / syntax / object / other`
- `execute_sql` 改成 CTE 一次往返拿「真实行数 + 截断后行集」
  ```sql
  WITH src AS ({sql}) SELECT *, (SELECT count(*) FROM src) AS _total
  FROM src LIMIT {MAX_RESULT_ROWS + 1}
  ```
- envelope：`{columns, rows, row_count, truncated, error?, error_kind?}`
- `validate_sql` 同样用新连接，错误带 `error_kind`

### 层 2：SQL 子图——错误分类决策 + 输出三态修复（混合：部分落地 + 残留）

**已落地**（`56fb0fa`）：`sql_graph._evaluate`（line 412-441）按 `error_kind` 分支：
- `timeout / connection / permission` → 不重试，`FAILED` + 中文友好消息
- `syntax / object / other` → 维持原重试

**待补**：`sql_graph._build_output`（line 444-465）主路径需要 EMPTY 分支，并修复 `row_count` 覆盖：

```python
# 改前（line 454-464）：
has_error = "error" in result_data and result_data["error"]
qr = QueryResult(
    ...
    row_count=len(result_data.get("rows", [])),   # ← 错：覆盖真实 total
    status="FAILED" if has_error else "SUCCESS",  # ← 缺 EMPTY
)

# 改后：
has_error = "error" in result_data and result_data["error"]
rows = result_data.get("rows", [])
total = result_data.get("row_count") or len(rows)  # 优先用 CTE count
if has_error:
    status = "FAILED"
elif not rows:
    status = "EMPTY"     # 新增——三态枚举补全到子图
else:
    status = "SUCCESS"
qr = QueryResult(
    sql=state.get("generated_sql", ""),
    columns=columns,
    rows=rows,
    row_count=total,     # 修复：保留真实 total（含截断）
    status=status,
    truncated=bool(result_data.get("truncated")),
    error=ErrorDetail(
        code="EXECUTION_ERROR",
        message=str(result_data["error"]),
        kind=result_data.get("error_kind"),
    ) if has_error else None,
)
```

### 层 3：父图 + SSE——三态分离 + 错误信封（已落地于 `56fb0fa`）

`backend/app/agent/confirmed_execution_graph.py`：
- `_confirmed_report_agent`：三态（`err is not None or qr is None → FAILED`；`err None + rows=[] → EMPTY`；其他 → SUCCESS）
- `_confirmed_sql_agent` 把 `ss.error` 与 `ss.generated_sql` 透传到主图 state（新增 TypedDict 字段 `sql: Optional[str]`）
- `_route_after_report` 不再短路：SUCCESS/EMPTY/FAILED 都走 `persist_report`
- `_persist_report` 三路：`FAILED → persist_error_run`、`EMPTY → persist_empty_run`、SUCCESS 走原路径
- `ConfirmedExecutionState` 增 `sql: Optional[str]`、`execution_status` 集合加 `"EMPTY"`

`backend/app/main.py`：
- `_build_sse_error(err, sql, failed_action)` helper：6 类 kind 映射 + 中文 message + QUERY_TIMEOUT/QUERY_OBJECT/... 6 种 code + tried SQL（≤200 字符）
- `/confirm` SSE FAILED 分支（line 706-718）改用 helper，`failed_action='sql'`
- `/chat adjust` SSE FAILED 分支（line 326-361）改用 helper
- `/reports/{v}` 返回值透出 `execution_status` 字段

### 层 4：报告壳——空数据占位 + 错误归档渲染（已落地于 `56fb0fa`）

- `frontend/src/adapter/reportAdapter.ts`：空 table 不再被丢弃，附 `data.empty=true`
- `frontend/src/components/report/blocks/TableBlock.tsx`：渲染「未找到匹配记录」+ footer 改文案
- `frontend/src/components/workbench/ReportPaper.tsx`：
  - `execution_status==='EMPTY'` → 渲染 `.wb-empty-band` + footer「查询已执行 · 未匹配到数据」
  - `status==='error' || verdict==='FAILED'` → 渲染失败归档带 + tried SQL 折叠
- `frontend/src/components/workbench/ErrorCard.tsx`：6 类 kind 标题 + 折叠「查看尝试的 SQL」
- `frontend/src/api/analysisEvents.ts`：FAILED_ACTIONS 加 `'sql'`、解析 kind/sql（白名单 6 类）
- `frontend/src/stores/analysisReducer.ts`：`canRetryFailedAction` 接受 `failed_action='sql'`

### 层 5：Excel 导出（已落地于 `e8e9b1e`）

- `backend/app/main.py` 新增 `GET /api/v1/sessions/{sid}/reports/{v}/export.xlsx`
- JWT + owner 校验；用 openpyxl 把 `query_snapshot` 完整数据集（不受 5000 行 UI 截断影响）写成 xlsx 流式返回
- `backend/requirements.txt` + `openpyxl>=3.1`
- `frontend/src/components/workbench/ReportPaper.tsx` 导出按钮接真端点 + 保留「导出 HTML」次级按钮

### 层 6：B-7 MAX+1 并发竞态修复（待补）

`MAX(version)+1` 在 READ COMMITTED 下并发竞态——UNIQUE 约束捕获但 service 层无重试，并发 confirm 返 500。

改动：

1. `report_version_repository.append_version` 改单语句：
   ```sql
   INSERT INTO agent.report_version (...)
   SELECT $1, $2, COALESCE(MAX(version), 0) + 1, ...
   FROM agent.report_version WHERE session_id = $1
   RETURNING ...
   ```
   ——PG 在该 INSERT 上对索引行加锁，并发请求会排队而非撞 UNIQUE
2. `requirement_repository.create_draft` 同步改造
3. `report_version_service._persist` 移除 `try/except VersionConflictError` 路径（不再需要）

### 层 7：B-3 trace 子图污染修复（待补，但 priority 较 B-7 低）

子图 `SQLAgentState` / `DataAgentState` / `ReportAgentState` TypedDict 都没声明 `trace_id`——LangGraph 对未声明 key 默认丢弃。实测：

```
node saw trace_id = '<MISSING>'
node saw keys     = ['user_query']
```

后果：
- 所有子图 span 写进 `_local[""]` 同一桶
- **多请求 trace 数据交叉污染**
- `_local` dict 无上限增长（内存泄露）

最小修复（不动 LangGraph 切 PostgresSaver）：
- `SQLAgentState` / `DataAgentState` / `ReportAgentState` 都加 `trace_id: str` 字段
- 每个子图入口（`build_sql_graph` / `build_data_graph` / `build_report_graph` 调用点）显式注入 `trace_id: state["trace_id"]`
- `_confirmed_sql_agent` 当前已经把 `trace_id` 传给 `sql_graph.ainvoke` 但子图 state 不接，**实际丢**——加上声明后才生效

## 文件改动（Files to change）

- **`backend/app/agent/sql_graph.py`**：`_build_output` 主路径（line 454-464）修复 EMPTY 分支 + row_count 覆盖
- `backend/app/services/report_version_service.py`、`backend/app/infra/db/{report_version,requirement}_repository.py`：层 6 单语句 INSERT 改造
- `backend/app/agent/{sql,data,report}_graph.py`：层 7 子图 TypedDict 加 `trace_id: str`
- `backend/app/agent/confirmed_execution_graph.py`：让 `_confirmed_data_agent` 与 `_confirmed_sql_agent` 显式透传 `trace_id` 到子图

## 复用工具（Reused existing utilities）

- `psycopg2.errors.{QueryCanceled, OperationalError, ProgrammingError, SyntaxError, UndefinedColumn, UndefinedTable, UndefinedFunction, AdminShutdown, CrashShutdown}` —— 错误分类枚举已有，不引入新枚举
- `openpyxl.Workbook` + `StreamingResponse` —— Excel 导出
- `ErrorDetail.kind` 6 类枚举已存在于 `models/contracts.py`，`_build_sse_error` 复用
- `psycopg2` 单语句 INSERT 的 row-lock 语义自动获取，无需新依赖
- `traced_node` 装饰器已同时支持 sync/async + 子图 trace 的获取逻辑已实现，仅缺 TypedDict 声明

## 验证（Verification）

### 已落地用例回顾（必须仍绿）

```bash
cd backend && pytest --ignore=tests/e2e -q   # 90 passed
cd frontend && npm run lint && npm run test:run   # 241 passed
```

### 层 2 修复——新增测试

`backend/tests/test_sql_limits.py` 增加：

- `test_build_output_writes_empty_status_for_legitimate_zero_match`：mock `execute_sql` 返回 `{"rows": [], "row_count": 0, "truncated": False}`，调 `sql_graph._build_output` → `query_result.status == "EMPTY"` 且 `row_count == 0`
- `test_build_output_preserves_real_row_count_after_truncation`：mock 返回 `{"rows": [...5001 rows...], "row_count": 50000, "truncated": True}` → `query_result.row_count == 50000`（不是 5001）+ `truncated is True`

### 层 6 修复——新增测试

`backend/tests/persistence/test_version_concurrent.py`（新）：
- 起 10 个 asyncio task 并发调 `report_version_service.persist_confirmed_run` 同 session_id
- 全部成功，`UNIQUE` 异常不再抛出
- 写入的 version 序列连续

### 层 7 修复——新增测试

`backend/tests/graphs/test_sql_graph.py`（已存在则改）：
- 单测 `build_sql_graph()` 接受 `trace_id="<uuid>"` 不丢
- `run_step` 节点的 trace 通过 `get_tracer(state["trace_id"])` 写入独立桶

### 端到端

```bash
cd backend && REPORTAGENT_E2E=1 pytest tests/e2e/test_full_flow.py -s
```
确认已落地的三态 + 截断 + Excel 导出场景 e2e 仍通过。

## 明确不做（Explicitly NOT doing）

- 全量流式 CSV 导出（openpyxl 一次性写已支持十万行级）
- 报告壳 / 表格分页（`MAX_RESULT_ROWS=5000` 已够 UI 翻页）
- `exportReportHTML` 的 50 行截断调整
- `psycopg2 → psycopg3` 迁移（保留 v2）
- `MemorySaver → PostgresSaver`（plan 另行立项）—— 层 7 仅修 TypedDict 声明问题
- `LLM call 计数被乘数`（`llm.py:99` 遍历所有 tracer values）—— bug 但与本轮 scope 无关
- `_run_step` 转 async 的 coroutine 陷阱——本轮不动 async 重构
- 后端 async 重构（`backend-async-refactor.md` 中 12 处 `except: pass` + 6 node sync-to-async）——独立 PR

## Outcome

> **可逆性**：低。改动主要在 SQL 子图 + persistence，类型改动明确，回滚无副作用。

- 已落地：commit `e8e9b1e`（SQL 上限 + 超时 + Excel 导出）、commit `56fb0fa`（三态分离 + SSE 错误信封）
- 待修（本计划）：
  - 层 2：`sql_graph._build_output` EMPTY 分支 + row_count 覆盖（**最小、最关键**）
  - 层 6：`MAX+1` 单语句 INSERT 化
  - 层 7：子图 TypedDict 加 `trace_id`
