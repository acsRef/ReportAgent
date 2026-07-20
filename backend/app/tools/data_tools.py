from __future__ import annotations

import json

from langchain_core.tools import tool

from app.db import get_readonly_connection


# Chinese keyword → table name mapping for better matching
_CHINESE_TABLE_KEYWORDS = {
    "销售": "fact_sales",
    "订单": "fact_sales",
    "退货": "fact_returns",
    "库存": "fact_inventory",
    "考勤": "fact_attendance",
    "区域": "dim_region",
    "城市": "dim_region",
    "产品": "dim_product",
    "商品": "dim_product",
    "客户": "dim_customer",
    "顾客": "dim_customer",
    "日期": "dim_date",
    "时间": "dim_date",
    "仓库": "dim_warehouse",
    "员工": "dim_employee",
    "趋势": "fact_sales",
    "利润": "fact_sales",
    "销售额": "fact_sales",
}


@tool
def search_tables(query: str, top_k: int = 3) -> str:
    """语义搜索数据库表：输入自然语言查询，返回最相关的表和字段结构。
    例：search_tables('退货率趋势') → 返回 returns, sales 表"""
    conn = get_readonly_connection()
    tables = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    ).fetchall()

    # Build scored results for ALL tables (fallback-friendly)
    all_results = []
    for (tname,) in tables:
        cols = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema='main' AND table_name=?",
            [tname],
        ).fetchall()
        col_names = [c[0] for c in cols]
        col_types = [c[1] for c in cols]

        ddl = _build_ddl(tname, col_names, col_types)
        all_results.append({
            "table_name": tname,
            "columns": [{"name": n, "type": t} for n, t in zip(col_names, col_types)],
            "ddl": ddl,
            "description": _describe_table(tname),
        })

    # Score by Chinese keyword matching against descriptions and keyword map
    scored = []
    query_lower = query.lower()
    for r in all_results:
        score = 0.0
        desc = r["description"].lower()
        tname = r["table_name"]

        # Match Chinese keywords against descriptions
        for kw, mapped_table in _CHINESE_TABLE_KEYWORDS.items():
            if kw in query:
                if mapped_table == tname or mapped_table in desc:
                    score += 3.0
                # Partial match: keyword appears in table column names
                for c in r["columns"]:
                    if kw in c["name"].lower() or kw in c["type"].lower():
                        score += 1.0

        # English token matching (for English queries)
        eng_tokens = set(query_lower.replace(",", " ").split())
        name_tokens = set(tname.lower().split("_"))
        match_name = eng_tokens & name_tokens
        if match_name:
            score += len(match_name) * 3.0

        scored.append((score, r))

    scored.sort(key=lambda x: -x[0])
    results = [r for _, r in scored[:top_k]]

    # Fallback: if no strong match, return top N tables by default relevance
    if not results or all(s == 0 for s, _ in scored[:top_k]):
        priority = ["fact_sales", "dim_region", "dim_product", "dim_date",
                     "fact_returns", "fact_inventory", "dim_customer"]
        results = []
        for p in priority:
            for r in all_results:
                if r["table_name"] == p:
                    results.append(r)
                    break
            if len(results) >= top_k:
                break
        if not results:
            results = all_results[:top_k]

    return json.dumps(results, ensure_ascii=False)


@tool
def get_table_ddl(table_name: str) -> str:
    """获取某张表的完整 CREATE TABLE DDL，包含所有字段名和类型。
    在写 SQL 需要确认字段时调用。例：get_table_ddl('fact_sales')"""
    conn = get_readonly_connection()
    try:
        cols = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema='main' AND table_name=?",
            [table_name],
        ).fetchall()
        if not cols:
            return f"Table '{table_name}' not found"
        ddl = _build_ddl(table_name, [c[0] for c in cols], [c[1] for c in cols])
        return ddl
    except Exception as e:
        return f"Error: {e}"


@tool
def list_tables() -> str:
    """列出数据库中所有可用的表及其简要描述。
    当不确定有哪些表时，先调这个看看总览。"""
    conn = get_readonly_connection()
    tables = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    ).fetchall()
    results = []
    for (tname,) in tables:
        col_count = conn.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema='main' AND table_name=?",
            [tname],
        ).fetchone()[0]
        results.append({
            "table_name": tname,
            "description": _describe_table(tname),
            "column_count": col_count,
        })
    return json.dumps(results, ensure_ascii=False)


def _build_ddl(tname: str, col_names: list[str], col_types: list[str]) -> str:
    lines = [f"CREATE TABLE {tname} ("]
    for n, t in zip(col_names, col_types):
        lines.append(f"  {n} {t},")
    lines[-1] = lines[-1].rstrip(",")
    lines.append(");")
    return "\n".join(lines)


def _describe_table(tname: str) -> str:
    descriptions = {
        "dim_region": "区域和城市映射表",
        "dim_product": "产品信息表",
        "dim_customer": "客户维度表",
        "dim_date": "日期维度表",
        "dim_warehouse": "仓库维度表",
        "dim_employee": "员工维度表",
        "fact_sales": "销售记录事实表",
        "fact_returns": "退货记录事实表",
        "fact_inventory": "库存记录事实表",
        "fact_attendance": "考勤记录事实表",
    }
    return descriptions.get(tname, f"表 {tname}")
