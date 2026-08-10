from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Schema RAG Phase 1：FAQ 知识库单一数据源（与 backend/app/tools/faq_tools.py 读同一份 JSON）。
_FAQ_PATH = Path(__file__).resolve().parent.parent / "backend" / "scripts" / "schema_faq.json"
_FAQ_ENTRIES: list[dict] | None = None


def _load_faq() -> list[dict]:
    """惰性加载 FAQ 知识库；文件缺失/损坏降级为空列表。"""
    global _FAQ_ENTRIES
    if _FAQ_ENTRIES is not None:
        return _FAQ_ENTRIES
    try:
        if _FAQ_PATH.exists():
            with open(_FAQ_PATH, encoding="utf-8") as f:
                _FAQ_ENTRIES = json.load(f)
    except Exception:
        _FAQ_ENTRIES = []
    if _FAQ_ENTRIES is None:
        _FAQ_ENTRIES = []
    return _FAQ_ENTRIES


# ── Hardcoded schema — matches backend/seed_data.sql ──────────────────────
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
        "ddl": None,  # generated on init
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
        "ddl": None,
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
        "ddl": None,
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
        "ddl": None,
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
        "ddl": None,
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
        "ddl": None,
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
        "ddl": None,
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
        "ddl": None,
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
        "ddl": None,
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
        "ddl": None,
        "description": "考勤记录事实表，关联员工，包含考勤状态和工时。典型问题：出勤率统计、加班工时、部门考勤对比",
    },
]


def _build_ddl(table: dict) -> str:
    lines = [f"CREATE TABLE {table['table_name']} ("]
    col_lines = [f"  {c['name']} {c['type']}" for c in table["columns"]]
    lines.append(",\n".join(col_lines))
    lines.append(");")
    return "\n".join(lines)


class SchemaRegistry:
    def __init__(self):
        self._tables_cache: list[dict] | None = None

    def build_index(self):
        self._tables_cache = []
        for t in _TABLES:
            entry = dict(t)
            entry["ddl"] = _build_ddl(t)
            entry["col_lines"] = [f"- {c['name']} ({c['type']})" for c in t["columns"]]
            self._tables_cache.append(entry)
        count = len(self._tables_cache)

        # Try DuckDB for dynamic enrichment (optional — schema mismatch silently falls back)
        try:
            import duckdb
            from pathlib import Path
            db_path = Path(__file__).parent.parent / "backend" / "report.duckdb"
            if db_path.exists():
                conn = duckdb.connect(str(db_path), read_only=True)
                known = {t["table_name"] for t in self._tables_cache}
                rows = conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
                ).fetchall()
                for (tname,) in rows:
                    if tname not in known:
                        cols = conn.execute(
                            "SELECT column_name, data_type FROM information_schema.columns "
                            "WHERE table_schema='main' AND table_name=?",
                            [tname],
                        ).fetchall()
                        col_list = [{"name": c[0], "type": c[1]} for c in cols]
                        entry = {
                            "table_name": tname,
                            "columns": col_list,
                            "ddl": None,
                            "description": f"表 {tname}，包含字段: {', '.join(c[0] for c in cols)}",
                        }
                        entry["ddl"] = _build_ddl(entry)
                        entry["col_lines"] = [f"- {c['name']} ({c['type']})" for c in col_list]
                        self._tables_cache.append(entry)
                conn.close()
        except Exception:
            pass

        return count

    def search_tables(self, query: str, top_k: int = 3) -> list[dict]:
        if not self._tables_cache:
            self.build_index()

        if not query or not query.strip():
            return [self._format_table(t) for t in self._tables_cache[:top_k]]

        query_lower = query.lower()
        keywords = set(query_lower.replace(",", " ").split())

        scored = []
        for t in self._tables_cache:
            score = 0.0
            name_tokens = set(t["table_name"].lower().replace("_", " ").split())
            desc_tokens = set(t["description"].lower().replace("，", " ").replace("、", " ").split())
            col_tokens = set()
            for c in t["columns"]:
                col_tokens.update(c["name"].lower().replace("_", " ").split())

            match_name = keywords & name_tokens
            match_desc = keywords & desc_tokens
            match_col = keywords & col_tokens

            score += len(match_name) * 3.0
            score += len(match_desc) * 2.0
            score += len(match_col) * 1.0

            if score > 0:
                scored.append((score, t))

        scored.sort(key=lambda x: -x[0])
        results = [self._format_table(t) for _, t in scored[:top_k]]

        if not results:
            return [self._format_table(t) for t in self._tables_cache[:top_k]]

        return results

    def get_table_ddl(self, table_name: str) -> Optional[str]:
        if not self._tables_cache:
            self.build_index()

        for t in self._tables_cache:
            if t["table_name"] == table_name:
                return t["ddl"]
        return None

    def list_tables(self) -> list[dict]:
        if not self._tables_cache:
            self.build_index()

        return [
            {
                "table_name": t["table_name"],
                "description": t["description"],
                "column_count": len(t["columns"]),
            }
            for t in self._tables_cache
        ]

    def search_faq(self, query: str, top_k: int = 3) -> list[dict]:
        """检索 FAQ 知识库（常见问题 + SQL 模板 + 业务口径要点）。

        scoring 与 backend faq_tools 一致：keywords 子串命中 ×3、question 含核心词 +1。
        空/无命中返回 []。
        """
        entries = _load_faq()
        if not query or not query.strip() or not entries:
            return []

        qlower = query.lower()
        scored: list[tuple[float, dict]] = []
        for e in entries:
            score = 0.0
            for kw in (e.get("keywords", []) or []):
                if isinstance(kw, str) and kw and kw.lower() in qlower:
                    score += 3.0
            q_terms = set(str(e.get("question", "")).lower().replace(",", " ").split())
            for term in q_terms:
                if term and len(term) > 1 and term in qlower:
                    score += 1.0
            if score > 0:
                scored.append((score, e))

        scored.sort(key=lambda x: -x[0])
        return [
            {
                "question": e.get("question", ""),
                "sql": e.get("sql", ""),
                "note": e.get("note", ""),
                "tables": e.get("tables", []),
                "score": round(score, 2),
            }
            for score, e in scored[:top_k]
        ]

    def _format_table(self, t: dict) -> dict:
        return {
            "table_name": t["table_name"],
            "description": t["description"],
            "ddl": t["ddl"],
            "columns": t["columns"],
            "score": 1.0,
        }


registry = SchemaRegistry()
