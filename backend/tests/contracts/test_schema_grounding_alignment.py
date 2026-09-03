"""Schema grounding 防漂移钉（2026-09-03 Final Hardening ①）。

背景：2026-09-02 commit 3a81dc4 只对齐了 `_FK_CHAIN_HINTS`，但 plan few-shot、
FAQ 模板、输出 schema 示例、JOIN 规则、工具描述仍引用旧演示 schema
（fact_sales / fact_returns / dim_region / …），而现役库已是零售订单 schema
（scripts/seed_business_p15prelude.sql：fact_orders/fact_payments +
dim_date/dim_store/dim_product/dim_customer/dim_promotion）。同一 prompt 里
「真实表 + 旧 few-shot」并存 = grounding inconsistency，LLM 会被喂着去写
已退役的表，再靠 retry 修回来——系统性反模式。

本文件把「生产 prompt / FAQ / 工具描述源文件不得含旧 schema token」钉成契约。
新增 prompt 或工具描述源文件时，若其内容是给 LLM 看的 schema 示例，必须加进
_SCANNED_FILES，否则旧表名会再次漂进来。

注意：backend/scripts/seed_pg.sql / seed_data.sql 是已退役的历史数据文件（保留
作归档，不进扫描清单）；backend/tests/ 的 fake fixture 表名是惰性输入，不扫。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contracts

_REPO_ROOT = Path(__file__).resolve().parents[3]

# 现役零售 schema 合法业务表（seed_business_p15prelude.sql）
_RETAIL_TABLES = {
    "fact_orders", "fact_payments",
    "dim_date", "dim_store", "dim_product", "dim_customer", "dim_promotion",
}

# 旧演示 schema 的禁 token（表名 + 独有列名，词边界匹配防误伤 discount_rate 之类）
_FORBIDDEN_TOKENS = [
    "fact_sales", "fact_returns", "fact_inventory", "fact_attendance",
    "dim_region", "dim_warehouse", "dim_employee",
    "region_id", "sale_id", "total_amount", "return_reason", "return_amount",
    "cost_amount", "profit", "customer_tier", "day_name", "region_name",
]

# 内容会进 LLM prompt / 工具契约（schema 示例面）的源文件——新加此类文件必须登记
_SCANNED_FILES = [
    "backend/app/agent/sql_graph.py",            # _PLAN_FEWSHOT / _SQL_GENERATION_RULES / hints
    "backend/app/agent/prompts/sql_prompts.py",  # plan/generate 输出示例 + dim_date 列清单
    "backend/app/agent/prompts/requirement_prompts.py",
    "backend/app/memory/prompts/conversation_prompts.py",  # field_mapping 示例
    "backend/app/agent/requirement_options.py",  # scope/metric 词汇表
    "backend/app/tools/__init__.py",             # 8 工具注册 description/examples
    "backend/app/tools/data_tools.py",
    "backend/app/tools/faq_tools.py",
    "backend/app/tools/rag_schema.py",
    "backend/app/tools/interface_dict_tools.py",
    "backend/scripts/schema_faq.json",
    "mcp_schema_server/server.py",
]

_FORBIDDEN_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in _FORBIDDEN_TOKENS) + r")\b"
)


@pytest.mark.parametrize("rel_path", _SCANNED_FILES)
def test_no_legacy_schema_tokens_in_production_sources(rel_path):
    path = _REPO_ROOT / rel_path
    assert path.exists(), f"登记文件不存在（挪位/改名需同步本清单）: {rel_path}"
    text = path.read_text(encoding="utf-8")
    matches = sorted({m.group(1) for m in _FORBIDDEN_RE.finditer(text)})
    assert not matches, (
        f"{rel_path} 仍含旧演示 schema token {matches}——现役 schema 是零售订单"
        f"（fact_orders/fact_payments），旧表示例会教 LLM 写已退役 SQL"
    )


def test_plan_fewshot_grounded_on_retail_schema():
    from app.agent.sql_graph import _PLAN_FEWSHOT, _SQL_GENERATION_RULES
    assert "fact_orders" in _PLAN_FEWSHOT
    assert "退货" not in _PLAN_FEWSHOT  # 退货域在新 schema 无对应（退款走 fact_payments）
    assert "day_of_week" in _SQL_GENERATION_RULES
    assert "full_date = " in _SQL_GENERATION_RULES or "full_date=" in _SQL_GENERATION_RULES
    assert "date_id 外键关联 dim_date" not in _SQL_GENERATION_RULES  # 事实表自带 DATE 列


def test_generate_prompt_examples_on_retail_schema():
    from app.agent.prompts.sql_prompts import SQL_PLAN_V1, SQL_GENERATE_V1
    assert '"fact_orders"|null' in SQL_PLAN_V1["output_schema"]
    assert "fact_orders.store_id = dim_store.store_id" in SQL_GENERATE_V1["output_schema"]
    assert "day_of_week" in SQL_GENERATE_V1["output_schema"]
    assert "没有 month" not in SQL_GENERATE_V1["output_schema"]


def test_faq_entries_all_on_retail_tables():
    faq = json.loads((_REPO_ROOT / "backend/scripts/schema_faq.json").read_text(encoding="utf-8"))
    assert faq, "FAQ 知识库不应为空"
    for entry in faq:
        tables = set(entry.get("tables") or [])
        assert tables, f"FAQ {entry.get('id')} 缺少 tables"
        assert tables <= _RETAIL_TABLES, (
            f"FAQ {entry.get('id')} 涉及非现役表 {tables - _RETAIL_TABLES}"
        )
        sql = entry.get("sql") or ""
        assert any(t in sql for t in _RETAIL_TABLES), (
            f"FAQ {entry.get('id')} SQL 未引用任何现役表"
        )


def test_requirement_option_vocabulary_aligned():
    from app.agent.requirement_options import metric_options, scope_options
    regions = {o.value for o in scope_options()}
    assert regions == {"华东", "华北", "华南", "华中", "西南", "西北", "ALL"}  # 现役无东北
    metrics = {o.value for o in metric_options()}
    assert "毛利率" not in metrics and "退货率" not in metrics  # 新 schema 无毛利/退货域
    assert "退款率" in metrics
