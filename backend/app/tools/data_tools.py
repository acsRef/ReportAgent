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
        "description": "日期维度表，包含年/季度/月/周以及节假日标记。典型问题：按 2024年/Q1/本月 过滤时间，或按年/季度粒度分组",
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
        "description": "区域和城市映射表，包含华北/华东/华南/西南等大区及对应城市。典型问题：各区域销售、华东华南对比、省份排名",
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
        "description": "产品信息表，包含产品名称、所属品类、子品类、品牌和单价。典型问题：品类销售排名、品牌对比、单价与成本",
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
        "description": "客户维度表，包含客户名称、等级、行业和注册日期。典型问题：客户等级贡献、行业对比、大客户 Top N",
    },
    {
        "table_name": "dim_warehouse",
        "columns": [
            {"name": "warehouse_id", "type": "INTEGER"},
            {"name": "warehouse_name", "type": "VARCHAR"},
            {"name": "city", "type": "VARCHAR"},
            {"name": "capacity", "type": "INTEGER"},
        ],
        "description": "仓库维度表，包含仓库名称、所在城市和容量。典型问题：各仓库库存、容量分布",
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
        "description": "员工维度表，包含部门、岗位和入职日期。典型问题：各部门考勤、岗位人数统计",
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
        "description": "销售记录事实表，含区域、产品、客户、数量、金额、折扣、成本和利润。典型问题：销售额/销量排名、区域对比、月度趋势、毛利分析",
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
        "description": "退货记录事实表，关联销售记录，包含退货原因和处理方式。典型问题：退货原因分析、产品退货率、区域退货对比",
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
        "description": "库存记录事实表，按产品+仓库+日期记录库存量、预留量和可售量。典型问题：当前库存、可售量、库存周转",
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
        "description": "考勤记录事实表，关联员工，包含考勤状态和工时。典型问题：出勤率统计、加班工时、部门考勤对比",
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
    """根据中文业务关键词搜索数据库表，返回表名、字段列表、DDL 和描述。
    用途：不知道数据在哪个表时用来「找表」。
    场景：用户提问含销售额/退货率/库存/趋势等业务概念，需要先定位表。
    反例：已经知道表名（如 fact_sales），只是要看它的字段 → 用 get_table_ddl。
    输入：query（中文描述），top_k（返回条数，默认 3）
    输出：JSON 数组，每项 {table_name, columns[{name,type}], ddl, description, score}
    示例：search_tables('退货原因') → 优先返回 fact_returns（退货事实表）"""
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
    """获取指定单张表的完整 CREATE TABLE DDL（字段名、类型、精度）。
    用途：确定了目标表后，看精确字段名和类型用于写 SQL。
    前置：不确定表名时先用 search_tables 搜索，不要猜表名。
    输入：table_name（英文下划线格式，如 'fact_sales'、'dim_product'）
    输出：CREATE TABLE 文本
    示例：get_table_ddl('dim_region') → 返回含 region_name、province、city 的建表语句
    失败：表不存在时返回 'Table xxx not found'"""
    for t in _TABLES:
        if t["table_name"] == table_name:
            return _build_ddl(table_name, t["columns"])
    return f"Table '{table_name}' not found"


@tool
def list_tables() -> str:
    """列出数据库全部可用表（表名、中文描述、字段数），用于总览数据库全貌。
    用途：首次接入系统不了解结构时调用，或 search_tables 未返回理想结果时兜底。
    输入：无参数。
    输出：JSON 数组，每项 {table_name, description, column_count}
    限制：不返回字段详情。需要字段结构用 get_table_ddl。需要语义搜索用 search_tables。"""
    results = [
        {
            "table_name": t["table_name"],
            "description": t["description"],
            "column_count": len(t["columns"]),
        }
        for t in _TABLES
    ]
    return json.dumps(results, ensure_ascii=False)
