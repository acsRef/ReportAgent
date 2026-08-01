# Plan: extract_sql 多语句截断（P-8 安全加固）

> 状态: 已完成（P-8 落地；8 测试，全套 119 passed）

## Context（背景）

来源：[2026-07-30-bug-review.md](2026-07-30-bug-review.md) P-8（LOW，安全加固）。

`app/utils/text.py` 的 `extract_sql` 从 LLM 输出里定位 `select` 后，把**其后所有内容**原样返回。当 LLM 吐出多语句（`SELECT 1; SELECT 2`）时：

1. 下游 `check_sql_safety` 的 `sqlglot.parse_one` 对多语句**抛异常**，错误文本被喂回 `_generate_sql` 的重试 prompt——一次本可避免的盲重试。
2. 更重要的是攻击面：若前端诱导 LLM 生成 `SELECT …; DELETE FROM …`，原样透传会把注入尾部带进安检链路。虽然 DDL/DML 黑名单 + sqlglot AST + EXPLAIN 三层安检挡得住主要注入，但多语句本身是一个**额外的、应在最前置收敛掉**的攻击面（defense-in-depth）。

## Design（设计）

`extract_sql` 在定位到 `select` 之后，只保留**第一条语句**——丢弃首个 `;` 之后的全部内容：

```python
return text.split(";", 1)[0].strip()
```

一次改动同时解决三件事：

- **修解析失败**：`SELECT 1; SELECT 2` → `SELECT 1`，`sqlglot.parse_one` 不再因多语句抛错。
- **截注入尾部**：`SELECT …; DELETE FROM x` → `SELECT …`，注入的第二条语句被直接丢弃。
- **去尾随分号**：`SELECT * FROM t;` → `SELECT * FROM t`，避免 `execute_sql` 把它包进 `WITH src AS (…;)` 造成语法错（`execute_sql` 本就 `rstrip(";")`，此处与之一致，属冗余但安全的收敛）。

只做**语句边界截断**，不做 SQL 语法级解析——语法正确性仍由下游 `check_sql_safety` 三层安检负责（职责不变，本改是其前置收敛）。

## Files to change（文件改动）

- `backend/app/utils/text.py`：`extract_sql` 末尾改为 `text.split(";", 1)[0].strip()`。
- `backend/tests/`：新增 `extract_sql` 多语句/尾随分号/注入尾部的用例。

## Reused existing utilities（复用工具）

- 现有 `strip_think` / `strip_markdown_fence` 清洗流程不变，本改接在其后。
- 下游 `check_sql_safety`（黑名单 + sqlglot + EXPLAIN）三层安检保持不变。

## Verification（验证）

- 新增单测：
  - `test_extract_sql_single_statement_unchanged`：`SELECT * FROM t` → 原样。
  - `test_extract_sql_strips_trailing_semicolon`：`SELECT * FROM t;` → `SELECT * FROM t`。
  - `test_extract_sql_takes_first_of_multi_statement`：`SELECT 1; SELECT 2` → `SELECT 1`。
  - `test_extract_sql_drops_injection_tail`：`SELECT 1; DELETE FROM x` → `SELECT 1`。
  - `test_extract_sql_no_select_returns_empty`：无 select → `""`（既有行为不回归）。
- 回归：`pytest -m graphs -m smoke -q` 全绿（`test_sql_generation.py` 覆盖 `_generate_sql → extract_sql` 链路）。

## Explicitly NOT doing（明确不做）

- 不改 `check_sql_safety` 三层安检逻辑。
- 不改 `execute_sql` 的 CTE 包装。
- 不引入 SQL 解析器做语法级多语句判定（只做 `;` 边界截断）。
- 不处理「字符串字面量内含 `;`」的极端误截——LLM 生成的分析 SQL 几乎不含分号字符串字面量，且误截只会让语句变短被安检拒绝，不构成安全/正确性风险。
