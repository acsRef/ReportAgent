from __future__ import annotations

import pytest

from app.agent.prompts.sql_prompts import build_sql_generate_prompt, build_sql_plan_prompt
from app.agent.sql_graph import RepairContext
from app.models.contracts import QueryPlan

pytestmark = pytest.mark.contracts


def test_generate_prompt_with_repair_ctx_contains_seven_elements():
    plan = QueryPlan(target_metric="销售额", dimensions=["区域"], filters=[], aggregation="sum", time_range="2024年")
    # F7: schema_context_ref 字段已删（set-but-never-rendered）。
    # F8: fewshot 由 6 段 faq_block 注入，repair 段不再重复。
    # F11: 测试名"seven elements"必须断言 7 要素齐全（+target_metric，避免 prompt
    #      静默退化不被测试捕获）。
    ctx = RepairContext(
        original_requirement="2024年各区域销售额",
        plan=plan,
        target_metric="销售额",
        prev_sql="SELECT bad_col FROM fact_sales",
        error='column "bad_col" does not exist',
        error_kind="object",
        validation_result={"valid": False, "error": "bad_col not found"},
        retry_count={"sql_generation": 1, "plan": 0},
        hint="maybe use total_amount",
    )
    prompt = build_sql_generate_prompt(
        today="2024-01-01",
        target_metric="销售额",
        dimensions=["区域"],
        filters=[],
        aggregation="sum",
        time_range="2024年",
        schema_text="表 fact_sales ...",
        fk_chain_hints="",
        faq_block="",
        sql_generation_rules="",
        repair_ctx=ctx,
    )
    assert "原始需求：2024年各区域销售额" in prompt  # original_requirement
    assert "目标指标：销售额" in prompt                # target_metric
    assert "上一次的 SQL：\nSELECT bad_col FROM fact_sales" in prompt  # prev_sql
    assert 'column "bad_col" does not exist' in prompt  # error
    assert "错误分类：object" in prompt                # error_kind
    assert "bad_col not found" in prompt               # validation_result
    assert "重试计数" in prompt                       # retry_count
    assert "修复提示：maybe use total_amount" in prompt  # hint
    assert "请针对该错误修正 SQL" in prompt
    # F11 增量：schema 已注入 6 段，repair 段不重复拼 schema 摘要（F7 拍板）。
    assert "当前查询计划：fact_sales" not in prompt  # Pydantic model repr 不再泄漏
    assert "相似案例" not in prompt                   # F8 拍板：fewshot 不重复


def test_generate_prompt_without_repair_ctx_has_no_repair_block():
    prompt = build_sql_generate_prompt(
        today="2024-01-01",
        target_metric="销售额",
        dimensions=[],
        filters=[],
        aggregation="sum",
        time_range=None,
        schema_text="表 fact_sales ...",
        repair_ctx=None,
    )
    assert "上一次生成失败" not in prompt


def test_generate_prompt_with_dict_repair_ctx():
    ctx = {
        "original_requirement": "q",
        "prev_sql": "SELECT 1",
        "error": "err",
        "error_kind": "syntax",
        "retry_count": {"sql_generation": 0},
    }
    prompt = build_sql_generate_prompt(
        today="2024-01-01",
        target_metric="",
        dimensions=[],
        filters=[],
        aggregation="",
        time_range=None,
        schema_text="...",
        repair_ctx=ctx,
    )
    assert "原始需求：q" in prompt
    assert "SELECT 1" in prompt
    assert "err" in prompt


def test_plan_prompt_with_repair_ctx():
    ctx = RepairContext(
        original_requirement="各区域销售额",
        prev_sql="SELECT x FROM fact_sales",
        error="syntax error",
        error_kind="syntax",
        retry_count={"plan": 1},
    )
    prompt = build_sql_plan_prompt(
        today="2024-01-01",
        user_query="各区域销售额",
        schema_text="表 fact_sales ...",
        repair_ctx=ctx,
    )
    assert "上一次生成失败" in prompt
    assert "syntax error" in prompt


def test_plan_prompt_without_repair_ctx():
    prompt = build_sql_plan_prompt(
        today="2024-01-01",
        user_query="q",
        schema_text="...",
    )
    assert "上一次生成失败" not in prompt
