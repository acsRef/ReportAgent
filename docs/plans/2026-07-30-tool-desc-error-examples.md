# Plan: 工具描述增加错误返回样例

> 状态: 已完成

## 背景

目前 `__init__.py` 中工具的五要素描述对**异常输出**仅有抽象描述，如 `"输出：{columns, rows, error}"`、`"执行失败时按 error 修正 SQL 重试"`。LLM 看不到具体错误长什么样，无法在写 SQL 阶段预判错误模式。

由于 `execute_sql` / `validate_sql` 当前不在意图分析的 tool whitelist 中，LLM 只在**SQL 生成重试反馈回路**（`sql_graph.py:361-368`）中看到实际错误文本。

但描述作为工具文档的权威来源，仍应补齐错误样例，以备：
- 未来 prompt 模板扩展时直接引用
- 作为 `ToolMetadata` 的完整文档记录
- 保持五要素描述的完整性（"输出"部分应当覆盖成功与失败两种情况）

## 设计

### 改动范围

只改 `execute_sql` 和 `validate_sql` 两个工具的 `description`。

其他工具（chart_advisor / insight_analyst / trend_analysis / group_compare / detect_anomaly）的"错误"本质上是正常返回的提示文本（如"数据量不足"、"无数据"），LLM 直接读取结果文字即可理解，不需要额外样例。

### 具体改动

**`execute_sql`：** 现有 description 末尾追加错误返回示例段：

```
错误返回示例：
- {"error": "relation xxx does not exist", "error_kind": "object"} → 表/字段不存在，先用 get_table_ddl 确认正确名称后修正 SQL
- {"error": "canceling statement due to statement timeout", "error_kind": "timeout"} → 查询超时（>30s），尝试增加 WHERE 时间筛选或减少维度
- {"error": "permission denied for table yyy", "error_kind": "permission"} → 无权限，换用其他表或缩小查询范围
- {"error": "syntax error at or near ...", "error_kind": "syntax"} → SQL 语法错误，根据提示位置修正后重新 validate
- {"error": "column xxx does not exist", "error_kind": "object"} → 字段名错误，用 get_table_ddl 确认字段后修正
```

**`validate_sql`：** 末尾追加：

```
validate 自身的错误来自安检三层（DDL/DML 黑名单 → sqlglot AST → EXPLAIN），
error 示例：{"valid":false, "error":"仅允许 SELECT 语句"} → SQL 含 DDL/DML 关键字
```

### 不改哪些

- 其他 5 个报告工具（chart_advisor / insight_analyst / trend_analysis / group_compare / detect_anomaly）
- `ToolMetadata` 模型定义
- `sql_tools.py` 代码逻辑
- `sql_graph.py` 的重试策略
- tool whitelist

## 文件改动

- `backend/app/tools/__init__.py` — `execute_sql` 和 `validate_sql` 的 `description` 追加错误示例

## 复用工具

- 无

## 验证

| 检查项 | 方法 |
|--------|------|
| 描述格式不被破坏 | 肉眼检查 description 结构仍为单行字符串拼接 |
| 内容可被 LLM 读到 | 确认 `_format_tools_for_prompt()` 输出中包含新追加内容 |
| 回归测试 | `pytest -m smoke && pytest -m graphs` 全部通过 |

## 明确不做

- 不改其他 5 个报告工具的描述
- 不修改 `sql_tools.py` 代码
- 不修改 `sql_graph.py` 重试/重生成逻辑
- 不把 `execute_sql`/`validate_sql` 加入 whitelist（这是独立的 prompt 工程决策）
