"""Schema grounding 防漂移钉（2026-09-03 Final Hardening ①）。

背景：2026-09-02 commit 3a81dc4 只对齐了 `_FK_CHAIN_HINTS`，但 plan few-shot、
FAQ 模板、输出 schema 示例、JOIN 规则、工具描述仍引用旧演示 schema
（fact_sales / fact_returns / dim_region / …），而现役库已是零售订单 schema
（scripts/seed_business_p15prelude.sql：fact_orders/fact_payments +
dim_date/dim_store/dim_product/dim_customer/dim_promotion）。同一 prompt 里
「真实表 + 旧 few-shot」并存 = grounding inconsistency，LLM 会被喂着去写
已退役的表，再靠 retry 修回来——系统性反模式。

2026-11-02 收敛（16 → 4）：原来「同一断言 × 12 个文件」被抄成 12 个 parametrize
用例，合并为一次遍历（违规一次性列出）。负向文案钉子（`"退货" not in`、
`"没有 month" not in`）删除——它们只证明文案没写某句话，不证明 schema 正确。
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


def test_no_legacy_schema_tokens_in_production_sources() -> None:
    """12 个「喂给 LLM 的 schema 示例」源文件都不得含退役表名/列名。"""
    problems: list[str] = []
    for rel_path in _SCANNED_FILES:
        path = _REPO_ROOT / rel_path
        if not path.exists():
            problems.append(f"{rel_path} 不存在（挪位/改名需同步 _SCANNED_FILES）")
            continue
        text = path.read_text(encoding="utf-8")
        matches = sorted({m.group(1) for m in _FORBIDDEN_RE.finditer(text)})
        if matches:
            problems.append(f"{rel_path} 仍含退役 token {matches}")
    assert problems == [], "\n".join(problems)


def test_prompts_grounded_on_retail_schema() -> None:
    """plan few-shot 与 generate 示例必须只出现现役表与现役列。"""
    from app.agent.prompts.sql_prompts import SQL_GENERATE_V1, SQL_PLAN_V1
    from app.agent.sql_graph import _PLAN_FEWSHOT, _SQL_GENERATION_RULES

    assert "fact_orders" in _PLAN_FEWSHOT, "plan few-shot 未落在现役事实表"
    assert "day_of_week" in _SQL_GENERATION_RULES, "generate 规则缺 dim_date 列清单"

    plan_schema = SQL_PLAN_V1["output_schema"]
    gen_schema = SQL_GENERATE_V1["output_schema"]
    assert '"fact_orders"|null' in plan_schema, "plan 输出示例的 predicted_table 未对齐现役表"
    assert "fact_orders.store_id = dim_store.store_id" in gen_schema, (
        "generate 示例缺现役外键 JOIN"
    )


def test_faq_entries_all_on_retail_tables() -> None:
    faq = json.loads((_REPO_ROOT / "backend/scripts/schema_faq.json").read_text(encoding="utf-8"))
    assert faq, "FAQ 知识库不应为空"
    problems: list[str] = []
    for entry in faq:
        tables = set(entry.get("tables") or [])
        if not tables:
            problems.append(f"FAQ {entry.get('id')} 缺少 tables")
            continue
        if tables - _RETAIL_TABLES:
            problems.append(f"FAQ {entry.get('id')} 涉及非现役表 {tables - _RETAIL_TABLES}")
        sql = entry.get("sql") or ""
        if not any(t in sql for t in _RETAIL_TABLES):
            problems.append(f"FAQ {entry.get('id')} SQL 未引用任何现役表")
    assert problems == [], "\n".join(problems)


def test_requirement_option_vocabulary_aligned() -> None:
    """需求卡可选口径必须能被现役库回答（否则用户选了也查不出来）。

    2026-11-04：口径选项改为**从 `metrics.yaml` 派生**，value 统一为
    `metric:<名字>`（PATCH 侧按前缀确定性翻译）。这里按 label 比对业务口径，
    并额外钉住前缀契约。
    """
    from app.agent.requirement_options import metric_options, scope_options

    all_regions = {"华东", "华北", "华南", "华中", "西南", "西北", "ALL"}
    regions = {o.value for o in scope_options()}
    assert regions, "scope 选项不应为空"
    assert regions <= all_regions, f"出现未定义区域: {regions - all_regions}"
    assert "ALL" in regions, "必须保留「全部区域」选项"

    options = metric_options()
    metrics = {o.label for o in options}
    assert "毛利率" not in metrics and "退货率" not in metrics  # 新 schema 无毛利/退货域
    assert "退款率" in metrics
    # 前缀契约：前端回填的是 `metric:<名字>`，PATCH 侧按前缀翻译成 target_metrics。
    assert all(o.value == f"metric:{o.label}" for o in options)
    # 唯一事实源是注册表：每个选项都必须能在 metrics.yaml 里解析到表达式。
    from app.knowledge import registry as knowledge

    for option in options:
        assert knowledge.resolve_metric(option.label) is not None, option.label
