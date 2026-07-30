# Plan: 修复 Legacy SQL Agent 三个 Bug

> 状态: 已完成（Bug1/Bug2；Bug3 随 async）

## 背景

在完整审查从用户查询到工具调用的全链路后，发现 legacy `sql_graph.py` 中存在三个实际 bug。新路径 `confirmed_execution_graph.py` 已正确处理其中两个，但老路径仍在使用中（`mode=legacy` 和未走 requirement 卡片的流程）。

### Bug 1：SQL 执行错误不在重试反馈回路中

**位置：** `backend/app/agent/sql_graph.py:361-368`

**现象：** 当 `validate_sql` 通过但 `execute_sql` 运行时失败（如字段不存在），`_generate_sql` 的重试反馈只检查 `prev_validation.get("valid")`——但 validation 是 passed 的。执行阶段的错误被静默丢弃，重试时 LLM 看不到任何错误信息，极大概率生成完全相同的 SQL。

**触发链路：**
```
_generate_sql → _validate(valid=true) → _execute(column不存在) → _evaluate(SQL_SYNTAX_ERROR) → _generate_sql(重试)
```
重试时 `prev_validation={"valid": true}`，反馈块被跳过。

### Bug 2：`truncated` 标记和 `row_count` 在 legacy 路径丢失

**位置：** `backend/app/agent/sql_graph.py:444-465` `_build_output`

**现象：** `execute_sql` 通过 CTE `count(*)` 正确返回了 `row_count`（真实总数）和 `truncated` 布尔标记。但 `_build_output`：
1. `row_count` 使用 `len(result_data.get("rows", []))` —— 截断后的行数
2. 完全不读 `truncated` 字段
3. 不读 `error_kind` 字段

`contracts.py` 的 `QueryResult` 模型已定义 `truncated: bool = False` 和 `error_kind: Optional[str]`，只是 `_build_output` 没填充。`confirmed_execution_graph.py:437-438` 已正确处理。

### Bug 3：`_intent_analyze` sync 在 async 上下文阻塞

**位置：** `backend/app/agent/parent_graph.py:222`

**现象：** `_run_sql_agent` 是 `async def`，但第 222 行直接调 sync `_intent_analyze(intent_state)`，没有 `await`。内部调 `call_llm`（1-5s HTTP），阻塞 event loop。已在 `docs/plans/2026-07-30-backend-async-refactor.md` Detail C 中覆盖，本 plan 只记录引用。

## 设计

### Bug 1 修复方案

在 `_generate_sql` 的重试反馈条件中，额外检查 `sql_result` 中是否包含错误：

```python
# 改前
prev_validation = state.get("validation_result") or {}
prev_sql = (state.get("generated_sql") or "").strip()
if prev_sql and prev_validation.get("valid") is False:
    prompt += f"""
【上一次生成失败，必须修正】
上一次的 SQL：
{prev_sql}
校验错误：{prev_validation.get("error", "")}
请针对该错误修正 SQL：只使用上面「可用表结构」中真实存在的表名和列名，不要臆造列。"""

# 改后
prev_validation = state.get("validation_result") or {}
prev_sql = (state.get("generated_sql") or "").strip()
prev_sql_result = state.get("sql_result") or ""
_sql_err = ""
if prev_sql_result:
    try:
        _parsed = json.loads(prev_sql_result)
        _sql_err = _parsed.get("error", "") if isinstance(_parsed, dict) else ""
    except json.JSONDecodeError:
        pass

if prev_sql and (prev_validation.get("valid") is False or _sql_err):
    error_to_show = prev_validation.get("error") or _sql_err
    prompt += f"""
【上一次生成失败，必须修正】
上一次的 SQL：
{prev_sql}
错误：{error_to_show}
请针对该错误修正 SQL：只使用上面「可用表结构」中真实存在的表名和列名，不要臆造列。"""
```

### Bug 2 修复方案

改 `_build_output` 中 `row_count`、`truncated`、`error_kind` 的取值：

```python
# 改前
qr = QueryResult(
    sql=state.get("generated_sql", ""),
    columns=columns,
    rows=result_data.get("rows", []),
    row_count=len(result_data.get("rows", [])),
    status="FAILED" if has_error else "SUCCESS",
    error=...,
)

# 改后
qr = QueryResult(
    sql=state.get("generated_sql", ""),
    columns=columns,
    rows=result_data.get("rows", []),
    row_count=result_data.get("row_count", len(result_data.get("rows", []))),
    status="FAILED" if has_error else "SUCCESS",
    truncated=bool(result_data.get("truncated", False)),
    error_kind=result_data.get("error_kind") if has_error else None,
    error=...,
)
```

### Bug 3

不修——由 async refactor plan 覆盖。本 plan 只在 `parent_graph.py:222` 加一行注释引用 plan 文档。

## 文件改动

| 文件 | 改动 |
|------|------|
| `backend/app/agent/sql_graph.py:361-368` | Bug 1：重试反馈增加 sql_result error 检查 |
| `backend/app/agent/sql_graph.py:455-464` | Bug 2：_build_output 填充 truncated / 正确 row_count / error_kind |
| `backend/app/agent/parent_graph.py:222` | Bug 3：加注释引用 async refactor plan |

## 复用工具

- `json.loads` — 已在 `sql_graph.py` 顶部 import
- `QueryResult.truncated` / `error_kind` — 模型已定义，只需填充

## 验证

| 检查项 | 方法 |
|--------|------|
| Bug 1：execute 错误喂回 retry | `pytest -m graphs`（SQL 子图测试覆盖重试逻辑） |
| Bug 2：truncated 被传递 | 检查 `_build_output` 返回的 `QueryResult.truncated` |
| Bug 2：row_count 取真实值 | 确认 `row_count` 等于 `execute_sql` 返回的 `row_count` |
| 回归 | `pytest -m graphs && pytest -m smoke` 全部通过 |

## 明确不做

- 不改 `confirmed_execution_graph.py`（两个 bug 它已修）
- 不改 `contracts.py` 模型定义
- Bug 3 的 async 改造——由 async refactor plan 覆盖
- 不改 `execute_sql` / `validate_sql` 工具代码
