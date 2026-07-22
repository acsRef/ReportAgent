from __future__ import annotations

import json

from langchain_core.tools import tool


# ── Hardcoded schema — matches backend/seed_data.sql ─────────────────────

_TABLES: list[dict] = [
    {
        "table_name": "dim_date",
        "columns": [
            {"name": "date_id", "type": "INTEGER"},
            {"name": "full_date", "type": "DATE"},
            {"name": "year", "type": "INTEGER"},
            {"name": "quarter_num", "type": "INTEGER"},
            {"name": "quarter", "type": "VARCHAR"},
            {"name": "week_of_year", "type": "INTEGER"},
            {"name": "day_name", "type": "VARCHAR"},
            {"name": "is_holiday", "type": "BOOLEAN"},
        ],
        "description": "日期维度表，包含年/季度/月/周以及节假日标记",
    },
    {
        "table_name": "dim_region",
        "columns": [
            {"name": "region_id", "type": "INTEGER"},
            {"name": "region_name", "type": "VARCHAR"},
            {"name": "province", "type": "VARCHAR"},
            {"name": "city", "type": "VARCHAR"},
            {"name": "tier", "type": "VARCHAR"},
        ],
        "description": "区域和城市映射表，包含华北/华东/华南/西南等大区及对应城市",
    },
    {
        "table_name": "dim_product",
        "columns": [
            {"name": "product_id", "type": "INTEGER"},
            {"name": "product_name", "type": "VARCHAR"},
            {"name": "category", "type": "VARCHAR"},
            {"name": "sub_category", "type": "VARCHAR"},
            {"name": "brand", "type": "VARCHAR"},
            {"name": "unit_price", "type": "DECIMAL(10,2)"},
            {"name": "cost_price", "type": "DECIMAL(10,2)"},
            {"name": "supplier", "type": "VARCHAR"},
        ],
        "description": "产品信息表，包含产品名称、所属品类、子品类、品牌和单价",
    },
    {
        "table_name": "dim_customer",
        "columns": [
            {"name": "customer_id", "type": "INTEGER"},
            {"name": "customer_name", "type": "VARCHAR"},
            {"name": "customer_tier", "type": "VARCHAR"},
            {"name": "industry", "type": "VARCHAR"},
            {"name": "city", "type": "VARCHAR"},
            {"name": "register_date", "type": "DATE"},
        ],
        "description": "客户维度表，包含客户名称、等级、行业和注册日期",
    },
    {
        "table_name": "dim_warehouse",
        "columns": [
            {"name": "warehouse_id", "type": "INTEGER"},
            {"name": "warehouse_name", "type": "VARCHAR"},
            {"name": "city", "type": "VARCHAR"},
            {"name": "capacity", "type": "INTEGER"},
        ],
        "description": "仓库维度表，包含仓库名称、所在城市和容量",
    },
    {
        "table_name": "dim_employee",
        "columns": [
            {"name": "employee_id", "type": "INTEGER"},
            {"name": "employee_name", "type": "VARCHAR"},
            {"name": "department", "type": "VARCHAR"},
            {"name": "position", "type": "VARCHAR"},
            {"name": "city", "type": "VARCHAR"},
            {"name": "hire_date", "type": "DATE"},
        ],
        "description": "员工维度表，包含部门、岗位和入职日期",
    },
    {
        "table_name": "fact_sales",
        "columns": [
            {"name": "sale_id", "type": "INTEGER"},
            {"name": "date_id", "type": "INTEGER"},
            {"name": "product_id", "type": "INTEGER"},
            {"name": "region_id", "type": "INTEGER"},
            {"name": "customer_id", "type": "INTEGER"},
            {"name": "channel", "type": "VARCHAR"},
            {"name": "quantity", "type": "INTEGER"},
            {"name": "unit_price", "type": "DECIMAL(10,2)"},
            {"name": "discount", "type": "DECIMAL(4,2)"},
            {"name": "total_amount", "type": "DECIMAL(12,2)"},
            {"name": "cost_amount", "type": "DECIMAL(12,2)"},
            {"name": "profit", "type": "DECIMAL(12,2)"},
        ],
        "description": "销售记录事实表，含区域、产品、客户、数量、金额、折扣、成本和利润",
    },
    {
        "table_name": "fact_returns",
        "columns": [
            {"name": "return_id", "type": "INTEGER"},
            {"name": "sale_id", "type": "INTEGER"},
            {"name": "product_id", "type": "INTEGER"},
            {"name": "return_date_id", "type": "INTEGER"},
            {"name": "return_quantity", "type": "INTEGER"},
            {"name": "return_amount", "type": "DECIMAL(10,2)"},
            {"name": "return_reason", "type": "VARCHAR"},
            {"name": "handling", "type": "VARCHAR"},
        ],
        "description": "退货记录事实表，关联销售记录，包含退货原因和处理方式",
    },
    {
        "table_name": "fact_inventory",
        "columns": [
            {"name": "inventory_id", "type": "INTEGER"},
            {"name": "product_id", "type": "INTEGER"},
            {"name": "warehouse_id", "type": "INTEGER"},
            {"name": "date_id", "type": "INTEGER"},
            {"name": "quantity_on_hand", "type": "INTEGER"},
            {"name": "quantity_reserved", "type": "INTEGER"},
            {"name": "quantity_available", "type": "INTEGER"},
        ],
        "description": "库存记录事实表，按产品+仓库+日期记录库存量、预留量和可售量",
    },
    {
        "table_name": "fact_attendance",
        "columns": [
            {"name": "attendance_id", "type": "INTEGER"},
            {"name": "employee_id", "type": "INTEGER"},
            {"name": "date_id", "type": "INTEGER"},
            {"name": "status", "type": "VARCHAR"},
            {"name": "work_hours", "type": "DECIMAL(4,1)"},
        ],
        "description": "考勤记录事实表，关联员工，包含考勤状态和工时",
    },
]

# Chinese keyword → table name mapping
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

_PRIORITY_TABLES = [
    "fact_sales", "dim_region", "dim_product", "dim_date",
    "fact_returns", "fact_inventory", "dim_customer",
]


def _build_ddl(tname: str, columns: list[dict]) -> str:
    lines = [f"CREATE TABLE {tname} ("]
    col_lines = [f"  {c['name']} {c['type']}" for c in columns]
    lines.append(",\n".join(col_lines))
    lines.append(");")
    return "\n".join(lines)


@tool
def search_tables(query: str, top_k: int = 3) -> str:
    """语义搜索数据库表：输入自然语言查询，返回最相关的表和字段结构。
    例：search_tables('退货率趋势') → 返回 returns, sales 表"""
    all_results = []
    for t in _TABLES:
        all_results.append({
            "table_name": t["table_name"],
            "columns": t["columns"],
            "ddl": _build_ddl(t["table_name"], t["columns"]),
            "description": t["description"],
        })

    scored = []
    query_lower = query.lower()
    for r in all_results:
        score = 0.0
        desc = r["description"].lower()
        tname = r["table_name"]

        for kw, mapped_table in _CHINESE_TABLE_KEYWORDS.items():
            if kw in query:
                if mapped_table == tname or mapped_table in desc:
                    score += 3.0
                for c in r["columns"]:
                    if kw in c["name"].lower() or kw in c["type"].lower():
                        score += 1.0

        eng_tokens = set(query_lower.replace(",", " ").split())
        name_tokens = set(tname.lower().split("_"))
        match_name = eng_tokens & name_tokens
        if match_name:
            score += len(match_name) * 3.0

        scored.append((score, r))

    scored.sort(key=lambda x: -x[0])
    results = [r for _, r in scored[:top_k]]

    if not results or all(s == 0 for s, _ in scored[:top_k]):
        results = []
        for p in _PRIORITY_TABLES:
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
    """获取某张表的完整 CREATE TABLE DDL，包含所有字段名和类型。"""
    for t in _TABLES:
        if t["table_name"] == table_name:
            return _build_ddl(table_name, t["columns"])
    return f"Table '{table_name}' not found"


@tool
def list_tables() -> str:
    """列出数据库中所有可用的表及其简要描述。"""
    results = [
        {
            "table_name": t["table_name"],
            "description": t["description"],
            "column_count": len(t["columns"]),
        }
        for t in _TABLES
    ]
    return json.dumps(results, ensure_ascii=False)
