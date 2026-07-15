from __future__ import annotations

import json

import duckdb

from app.db import get_readonly_connection


DANGEROUS_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
    "TRUNCATE", "CREATE", "REPLACE", "GRANT", "REVOKE",
    "EXECUTE", "EXEC", "CALL", "MERGE", "LOAD",
]


def check_sql_safety(sql: str) -> tuple[bool, str]:
    sql_upper = sql.strip().upper()
    if not sql_upper.startswith("SELECT"):
        return False, "只允许 SELECT 查询语句"
    tokens = set(sql_upper.replace("(", " ").replace(")", " ").replace(";", " ").split())
    for kw in DANGEROUS_KEYWORDS:
        if kw in tokens:
            return False, f"禁止使用 {kw}，仅支持只读查询"
    return True, ""


def run_sql(sql: str) -> str:
    safe, msg = check_sql_safety(sql)
    if not safe:
        return json.dumps({"error": msg, "columns": [], "rows": []}, ensure_ascii=False)

    conn = None
    try:
        conn = get_readonly_connection()
        result = conn.execute(sql)
        columns = [desc[0] for desc in result.description]
        rows = [dict(zip(columns, row)) for row in result.fetchall()]
        return json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc), "columns": [], "rows": []}, ensure_ascii=False)
    finally:
        if conn:
            conn.close()


def validate_sql(sql: str) -> str:
    safe, msg = check_sql_safety(sql)
    if not safe:
        return json.dumps({"valid": False, "error": msg}, ensure_ascii=False)

    conn = None
    try:
        conn = get_readonly_connection()
        conn.execute(f"EXPLAIN {sql}")
        return json.dumps({"valid": True, "error": ""}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False)
    finally:
        if conn:
            conn.close()


def chart_advisor(sql_result: str) -> str:
    data = json.loads(sql_result)
    columns = data.get("columns", [])
    rows = data.get("rows", [])
    if not columns or not rows:
        return json.dumps({"type": "table", "config": {}}, ensure_ascii=False)

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
    data = json.loads(sql_result)
    rows = data.get("rows", [])
    columns = data.get("columns", [])
    if not rows:
        return "查询结果为空，无法生成洞察。"

    numeric_cols = [c for c in columns if isinstance(rows[0].get(c), (int, float))]
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
