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
    """验证 SQL 语法和安全性。"""
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
    """执行只读 SQL 查询。"""
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
    """根据 SQL 查询结果推荐图表类型。"""
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
    """分析 SQL 查询结果，生成数值洞察。"""
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
