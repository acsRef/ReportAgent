from __future__ import annotations

import json
import os

import sqlglot
from sqlglot import exp as sql_exp
import psycopg2
import psycopg2.extras

from decimal import Decimal

PG_DSN = os.getenv("DATABASE_URL", "postgresql://ragent:ragent@localhost:5432/ragent")


def _get_pg_conn():
    """创建同步 PostgreSQL 连接用于分析查询。"""
    return psycopg2.connect(PG_DSN)


DANGEROUS_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
    "TRUNCATE", "CREATE", "REPLACE", "GRANT", "REVOKE",
    "EXECUTE", "EXEC", "CALL", "MERGE", "LOAD",
]


def check_sql_safety(sql: str) -> tuple[bool, str]:
    """SQL 安全检查：白名单 + 黑名单 + AST 解析三重校验。"""
    sql_upper = sql.strip().upper()

    if not sql_upper.startswith("SELECT"):
        return False, "只允许 SELECT 查询语句"

    tokens = set(sql_upper.replace("(", " ").replace(")", " ").replace(";", " ").split())
    for kw in DANGEROUS_KEYWORDS:
        if kw in tokens:
            return False, f"禁止使用 {kw}，仅支持只读查询"

    # AST parse — reject anything that isn't a SELECT statement
    try:
        parsed = sqlglot.parse_one(sql)
        if not isinstance(parsed, sql_exp.Select):
            return False, "只允许 SELECT 查询语句"
    except Exception as e:
        return False, f"SQL 语法解析失败: {e}"

    return True, ""


def validate_sql(sql: str) -> str:
    """三重校验 SQL 语法和安全性，不执行查询。
    用途：每次 execute_sql 前的安全检查。必须校验通过后才能执行。
    校验链：(1) 黑名单检查（禁止 INSERT/UPDATE/DELETE/DROP 等 DDL/DML）
           (2) AST 解析（sqlglot 验证是标准 SELECT）
           (3) EXPLAIN 执行（在实际 PG 连接上跑 EXPLAIN 捕获语法错误）
    输入：sql（要校验的 SQL 文本）
    输出：{"valid": bool, "error": string}
    约束：只接受 SELECT 语句。校验通过不代表逻辑正确，只代表安全可执行。
    不要用来：不要用它执行查询或获取数据。"""
    safe, msg = check_sql_safety(sql)
    if not safe:
        return json.dumps({"valid": False, "error": msg}, ensure_ascii=False)

    conn = _get_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"EXPLAIN {sql}")
        return json.dumps({"valid": True, "error": ""}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False)
    finally:
        conn.close()


def execute_sql(sql: str) -> str:
    """执行只读 SELECT 查询，返回列结构和行数据。
    前置条件：必须先调 validate_sql 且返回 {"valid": true}
    安全限制：只接受 SELECT 语句。任何 DDL/DML 都会被拒绝。
    输入：sql（合法 SELECT，字段名必须引用已确认的表结构）
    输出：{"columns": [{name, type}], "rows": [{col: value}], "error": string}
      - error 为空字符串表示成功
      - error 有内容表示执行失败（如字段不存在、语法错误）
    重试策略：失败最多重试 3 次，每次携带错误信息回 LLM 修正 SQL。
              3 次全失败则转 clarify 节点要求用户澄清。
    注意：连接的是只读 PostgreSQL 副本，无法修改数据。"""
    safe, msg = check_sql_safety(sql)
    if not safe:
        return json.dumps({"error": msg, "columns": [], "rows": []}, ensure_ascii=False)

    conn = _get_pg_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            columns = [{"name": desc.name, "type": str(desc.type_code)} for desc in cur.description]
            raw_rows = cur.fetchall()
            rows = []
            for row in raw_rows:
                row_dict = dict(row)
                for k, v in row_dict.items():
                    if isinstance(v, Decimal):
                        row_dict[k] = float(v)
                rows.append(row_dict)
            return json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc), "columns": [], "rows": []}, ensure_ascii=False)
    finally:
        conn.close()


def chart_advisor(sql_result: str) -> str:
    """根据已执行的查询结果推荐图表类型和配置。
    用途：SQL 执行成功并返回数据后，决定如何可视化。
    输入：sql_result（execute_sql 返回的完整 JSON，含 columns 和 rows）
    输出：{"type": "pie"|"bar"|"table", "config": {data, dimensions}}
    判断逻辑：
      - 1 个分类字段 + 1 个数值字段，行数 ≤ 8 → 饼图
      - 1 个分类字段 + 1 个数值字段，行数 > 8 → 柱状图
      - 无合适维度组合 → 纯表格
    不要用来：不执行 SQL 查询、不修改数据、不生成数值摘要（用 insight_analyst）。"""
    data = json.loads(sql_result)
    columns_raw = data.get("columns", [])
    rows = data.get("rows", [])
    if not columns_raw or not rows:
        return json.dumps({"type": "table", "config": {}}, ensure_ascii=False)

    # columns can be dicts or plain strings
    columns = [c["name"] if isinstance(c, dict) else c for c in columns_raw]

    numeric_cols = [c for c in columns if isinstance(rows[0].get(c), (int, float))]
    categorical_cols = [c for c in columns if c not in numeric_cols]

    if categorical_cols and numeric_cols:
        cat = categorical_cols[0]
        num = numeric_cols[0]
        if len(rows) <= 8:
            return json.dumps({
                "type": "pie",
                "config": {"data": rows, "dimensions": {"category": cat, "value": num}},
            }, ensure_ascii=False)
        else:
            return json.dumps({
                "type": "bar",
                "config": {"data": rows, "dimensions": {"x": cat, "y": num}},
            }, ensure_ascii=False)

    return json.dumps({"type": "table", "config": {"data": rows}}, ensure_ascii=False)


def insight_analyst(sql_result: str) -> str:
    """对查询结果的数值列生成统计摘要（合计、均值、最大、最小）。
    用途：SQL 执行成功并返回数据后，提炼数值维度的核心指标。
    输入：sql_result（execute_sql 返回的完整 JSON，含 columns 和 rows）
    输出：多行文本，每行对应一个数值列的统计，如 "销售额: 合计=1,234,567.00, 平均=102,880.58, 最大=999,999.00, 最小=1.00"
    处理方式：对前 3 个数值列分别计算。空数据返回提示文本。
    不要用来：不执行 SQL 查询、不生成图表配置（用 chart_advisor）、不做趋势预测。"""
    data = json.loads(sql_result)
    rows = data.get("rows", [])
    columns_raw = data.get("columns", [])
    if not rows:
        return "查询结果为空，无法生成洞察。"

    columns = [c["name"] if isinstance(c, dict) else c for c in columns_raw]
    numeric_cols = [c for c in columns if rows and isinstance(rows[0].get(c), (int, float))]
    if not numeric_cols:
        return f"共 {len(rows)} 条记录，包含维度: {', '.join(columns)}。"

    insights = []
    for col in numeric_cols[:3]:
        values = [r[col] for r in rows if r.get(col) is not None]
        if not values:
            continue
        total = sum(values)
        avg = total / len(values)
        max_val = max(values)
        min_val = min(values)
        insights.append(f"{col}: 合计={total:,.2f}, 平均={avg:,.2f}, 最大={max_val:,.2f}, 最小={min_val:,.2f}")

    return "\n".join(insights)
