"""Repair 上下文必须是「六要素 + 目标指标」齐全的（B4：否则就是盲重试）。

2026-11-02 收敛（5 → 3）：
- dict 形态与 plan 侧合并且**必须保留 dict**：checkpoint 反序列化后 repair_ctx
  就是 dict，只认 dataclass 会让重启后续跑的 repair 静默丢上下文（B7×B4）。
- 无 ctx 时不渲染失败样板，两个 builder 合并成 1 条。
"""
from __future__ import annotations

import pytest

from app.agent.prompts.sql_prompts import build_sql_generate_prompt, build_sql_plan_prompt
from app.agent.sql_graph import RepairContext
from app.models.contracts import QueryPlan

pytestmark = pytest.mark.contracts


def test_generate_prompt_with_repair_ctx_contains_seven_elements():
    """七要素齐全：原始需求 / 目标指标 / 上次 SQL / 错误 / 错误分类 / 校验结果 / 重试计数。"""
    plan = QueryPlan(
        target_metric="销售额", dimensions=["区域"], filters=[],
        aggregation="sum", time_range="2024年",
    )
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
    assert "原始需求：2024年各区域销售额" in prompt
    assert "目标指标：销售额" in prompt
    assert "上一次的 SQL：\nSELECT bad_col FROM fact_sales" in prompt
    assert 'column "bad_col" does not exist' in prompt
    assert "错误分类：object" in prompt
    assert "bad_col not found" in prompt
    assert "重试计数" in prompt
    assert "修复提示：maybe use total_amount" in prompt
    assert "请针对该错误修正 SQL" in prompt
    # F7/F8 拍板：schema 已注入 6 段、fewshot 走 faq_block，repair 段不重复拼
    assert "当前查询计划：fact_sales" not in prompt
    assert "相似案例" not in prompt


def test_repair_ctx_renders_in_dict_and_plan_forms():
    """dict 形态（checkpoint 反序列化后）与 plan 侧 repair 都必须渲染出上下文。"""
    dict_ctx = {
        "original_requirement": "q",
        "prev_sql": "SELECT 1",
        "error": "err",
        "error_kind": "syntax",
        "retry_count": {"sql_generation": 0},
    }
    generate_prompt = build_sql_generate_prompt(
        today="2024-01-01",
        target_metric="",
        dimensions=[],
        filters=[],
        aggregation="",
        time_range=None,
        schema_text="...",
        repair_ctx=dict_ctx,
    )
    assert "原始需求：q" in generate_prompt
    assert "SELECT 1" in generate_prompt
    assert "错误分类：syntax" in generate_prompt

    plan_ctx = RepairContext(
        original_requirement="各区域销售额",
        prev_sql="SELECT x FROM fact_sales",
        error="syntax error",
        error_kind="syntax",
        retry_count={"plan": 1},
    )
    plan_prompt = build_sql_plan_prompt(
        today="2024-01-01",
        user_query="各区域销售额",
        schema_text="表 fact_sales ...",
        repair_ctx=plan_ctx,
    )
    assert "上一次生成失败" in plan_prompt
    assert "syntax error" in plan_prompt


def test_prompt_without_repair_ctx_has_no_repair_block():
    """没有 repair 上下文时不得渲染失败样板（否则首轮就教模型「你在修错」）。"""
    generate_prompt = build_sql_generate_prompt(
        today="2024-01-01",
        target_metric="销售额",
        dimensions=[],
        filters=[],
        aggregation="sum",
        time_range=None,
        schema_text="表 fact_sales ...",
        repair_ctx=None,
    )
    plan_prompt = build_sql_plan_prompt(
        today="2024-01-01", user_query="q", schema_text="...",
    )
    assert "上一次生成失败" not in generate_prompt
    assert "上一次生成失败" not in plan_prompt
