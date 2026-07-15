from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import duckdb

_DB_PATH = Path(__file__).parent.parent / "backend" / "report.duckdb"


class SchemaRegistry:
    def __init__(self):
        self._tables_cache: list[dict] | None = None

    def build_index(self, embedding_fn=None):
        conn = duckdb.connect(str(_DB_PATH), read_only=True)
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()

        self._tables_cache = []
        for (tname,) in tables:
            cols = conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema='main' AND table_name=?",
                [tname],
            ).fetchall()

            col_list = [{"name": c[0], "type": c[1]} for c in cols]
            col_lines = [f"- {c[0]} ({c[1]})" for c in cols]
            description = self._gen_description(tname, [c[0] for c in cols])
            ddl = f"CREATE TABLE {tname} (\n" + ",\n".join(
                f"  {c[0]} {c[1]}" for c in cols
            ) + "\n);"

            self._tables_cache.append({
                "table_name": tname,
                "columns": col_list,
                "ddl": ddl,
                "description": description,
                "col_lines": col_lines,
            })

        conn.close()
        return len(self._tables_cache)

    def _gen_description(self, table_name: str, columns: list[str]) -> str:
        descriptions = {
            "dim_region": "区域和城市映射表，包含华北/华东/华南/西南等大区及对应城市",
            "dim_product": "产品信息表，包含产品名称、所属品类、子品类、品牌和单价",
            "dim_customer": "客户维度表，包含客户名称、等级、行业和注册日期",
            "dim_date": "日期维度表，包含年/季度/月/周以及节假日标记",
            "dim_warehouse": "仓库维度表，包含仓库名称、所在城市和容量",
            "dim_employee": "员工维度表，包含部门、岗位和入职日期",
            "fact_sales": "销售记录事实表，含区域、产品、客户、数量、金额、折扣、成本和利润",
            "fact_returns": "退货记录事实表，关联销售记录，包含退货原因和处理方式",
            "fact_inventory": "库存记录事实表，按产品+仓库+日期记录库存量、预留量和可售量",
            "fact_attendance": "考勤记录事实表，关联员工，包含考勤状态和工时",
        }
        return descriptions.get(table_name, f"表 {table_name}，包含字段: {', '.join(columns)}")

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

    def _format_table(self, t: dict) -> dict:
        return {
            "table_name": t["table_name"],
            "description": t["description"],
            "ddl": t["ddl"],
            "columns": t["columns"],
            "score": 1.0,
        }


registry = SchemaRegistry()
