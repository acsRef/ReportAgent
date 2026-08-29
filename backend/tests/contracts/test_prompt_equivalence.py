from __future__ import annotations

"""P7 T5 等价性钉: 6 段重构后,新 prompt 必须保留旧裸 f-string 的关键内容 marker。

plan §D6 Golden Set 闭环: 'P7 主要目的是结构改造,文案/温度参数不动,
因此 Before/After 指标应基本持平。任何显著退化 (>5%) 要回查 prompt 改写是否引入歧义。'

本测试不取代 P12 真端到端 runner, 仅钉 '6 段重构没有破坏关键内容'。
"""

import pytest

from app.agent.prompts import (
    REPORT_PLAN_V1,
    REQUIREMENT_PARSE_V1,
    SQL_GENERATE_V1,
    SQL_INTENT_ANALYZE_V1,
    SQL_PLAN_V1,
)
from app.memory.prompts import CONVERSATION_SUMMARIZE_V1

pytestmark = pytest.mark.contracts

# 旧裸 f-string 里的关键内容 marker —— 6 段重构后必须仍在
# 每个 entry: prompt_name → 必含子串列表
KEY_MARKERS = {
    "requirement_parse": [
        "data_source_type=stream",
        "data_source:<接口名>",
        "field_meaning:<字段名>",
        "维度判断规则",
        "analysis_methods",
        "missing_fields",
    ],
    "sql_intent_analyze": [
        "chart_advisor",
        "insight_analyst",
        "group_compare",
        "trend_analysis",
        "detect_anomaly",
        "工具选择指南",
        "params_preview",
        "needs_options_group",
    ],
    "sql_plan": [
        "决策策略",
        "run_direct",
        "clarify",
        "missing_dimensions",
        "predicted_table",
        "clarify_decision",
    ],
    "sql_generate": [
        "PostgreSQL",
        "DuckDB",
        "EXTRACT",
        "SELECT 语句",
        "JOIN 条件使用外键",
        "中文别名",
    ],
    "report_plan": [
        "数据分析规划师",
        "steps",
        "tool",
        "args",
        "description",
    ],
    "conversation_summarize": [
        "滚动摘要",
        "extracted_schemas",
        "extracted_preferences",
        "field_mapping",
        "calculation",
        "db_field",
    ],
}


PROMPT_DICTS = {
    "requirement_parse": REQUIREMENT_PARSE_V1,
    "sql_intent_analyze": SQL_INTENT_ANALYZE_V1,
    "sql_plan": SQL_PLAN_V1,
    "sql_generate": SQL_GENERATE_V1,
    "report_plan": REPORT_PLAN_V1,
    "conversation_summarize": CONVERSATION_SUMMARIZE_V1,
}


@pytest.mark.parametrize("prompt_name", sorted(KEY_MARKERS.keys()))
def test_prompt_preserves_key_markers(prompt_name: str) -> None:
    """每个 prompt 必须保留旧版的关键内容 marker (6 段拆分不能丢语义)。"""
    prompt_dict = PROMPT_DICTS[prompt_name]
    full_text = "\n".join(prompt_dict[s] for s in prompt_dict)

    for marker in KEY_MARKERS[prompt_name]:
        assert marker in full_text, (
            f"{prompt_name} 丢失关键 marker: {marker!r}\n"
            f"prompt 全文:\n{full_text}"
        )


def test_intent_classify_preserved() -> None:
    """INTENT_CLASSIFY_V1 (在 test_prompt_layering 已覆盖 6 段) 内容 marker 钉。"""
    from app.agent.prompts import INTENT_CLASSIFY_V1

    full = "\n".join(INTENT_CLASSIFY_V1[s] for s in INTENT_CLASSIFY_V1)
    for marker in ("report", "interface", "chitchat", "other", "confidence", "reason"):
        assert marker in full, f"intent_classify 丢失 {marker!r}"


def test_build_functions_return_non_empty_strings() -> None:
    """所有 build 函数返回值非空字符串 (基本 sanity)。"""
    from app.agent.prompts import (
        build_intent_classify_prompt,
        build_report_plan_prompt,
        build_requirement_parse_prompt,
        build_sql_generate_prompt,
        build_sql_intent_analyze_prompt,
        build_sql_plan_prompt,
    )
    from app.memory.prompts import build_conversation_summarize_prompt

    p1 = build_intent_classify_prompt("查销售额")
    p2 = build_requirement_parse_prompt("q", "schema", "")
    p3 = build_sql_intent_analyze_prompt("q", "tools")
    p4 = build_sql_plan_prompt("2026-08-29", "q", "schema")
    p5 = build_sql_generate_prompt("2026-08-29", "m", ["d"], [{"field": "f"}], "sum", None, "schema")
    p6 = build_report_plan_prompt(["col"], 10, "tools")
    p7 = build_conversation_summarize_prompt(None, [{"role": "user", "content": "hi"}])

    for i, p in enumerate([p1, p2, p3, p4, p5, p6, p7], 1):
        assert isinstance(p, str) and p.strip(), f"build #{i} 返回空"
        assert len(p) > 100, f"build #{i} prompt 过短 ({len(p)} chars)"