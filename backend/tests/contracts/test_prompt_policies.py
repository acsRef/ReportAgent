from __future__ import annotations

"""P7 D4/D5: 每个 prompt 的 safety_policy 段含基线 4 条 Negative Instructions;

tool_policy 段含显式工具调用策略 (引用工具名 / search_schema 边界)。

伞形 plan §十 基线 4 条:
1. Do NOT invent tables/columns
2. Do NOT fabricate query results
3. Do NOT assume unavailable schema
4. Do NOT call search_schema when schema is already known
"""

import re

import pytest

from app.agent.prompts import (
    INTENT_CLASSIFY_V1,
    REPORT_PLAN_V1,
    REQUIREMENT_PARSE_V1,
    SQL_GENERATE_V1,
    SQL_INTENT_ANALYZE_V1,
    SQL_PLAN_V1,
)
from app.memory.prompts import CONVERSATION_SUMMARIZE_V1

pytestmark = pytest.mark.contracts

ALL_PROMPTS = {
    "intent_classify": INTENT_CLASSIFY_V1,
    "requirement_parse": REQUIREMENT_PARSE_V1,
    "sql_intent_analyze": SQL_INTENT_ANALYZE_V1,
    "sql_plan": SQL_PLAN_V1,
    "sql_generate": SQL_GENERATE_V1,
    "report_plan": REPORT_PLAN_V1,
    "conversation_summarize": CONVERSATION_SUMMARIZE_V1,
}

# 基线 4 条——每条用一段正则模式覆盖 (中英/分号变体都允许)
BASELINE_PATTERNS = {
    "invent_tables": re.compile(r"invent\s+tables?/columns?|不在.*表结构.*?中", re.IGNORECASE),
    "fabricate_results": re.compile(r"fabricate\s+query\s+results?|不.*?预先生成数字|不预先生成数字", re.IGNORECASE),
    "assume_unavailable_schema": re.compile(r"assume\s+unavailable\s+schema|不在.*?对话中", re.IGNORECASE),
    "no_redundant_search_schema": re.compile(
        r"do\s+not\s+call\s+search_schema|不重复调用|不重复调", re.IGNORECASE,
    ),
}

# Agent 专属 (按 prompt name → 必须出现的额外禁止)
AGENT_SPECIFIC = {
    "intent_classify": ["Do NOT generate SQL"],
    "requirement_parse": ["Do NOT generate SQL"],
    "sql_plan": ["Do NOT generate SQL"],  # plan 阶段不写 SQL 字符串
    "sql_generate": ["Do NOT generate DROP"],  # 只读 SELECT
    "report_plan": ["Do NOT generate SQL"],  # 报告规划阶段 SQL 已完成
    "conversation_summarize": ["Do NOT extract preferences"],  # 不抽未明说的偏好
    # sql_intent_analyze 也会含 Do NOT generate SQL,但允许省略(分析阶段本来就不产 SQL)
}


@pytest.mark.parametrize("prompt_name", sorted(ALL_PROMPTS.keys()))
@pytest.mark.parametrize("rule_name", sorted(BASELINE_PATTERNS.keys()))
def test_baseline_negative_instruction_present(
    prompt_name: str, rule_name: str,
) -> None:
    """伞形 plan §十 基线 4 条: 每 prompt 的 safety_policy 段必须含。"""
    safety = ALL_PROMPTS[prompt_name]["safety_policy"]
    pattern = BASELINE_PATTERNS[rule_name]
    assert pattern.search(safety), (
        f"{prompt_name} 缺基线 Negative Instruction '{rule_name}'\n"
        f"safety_policy 全文:\n{safety}"
    )


@pytest.mark.parametrize("prompt_name", sorted(AGENT_SPECIFIC.keys()))
def test_agent_specific_negative_instruction_present(prompt_name: str) -> None:
    """Agent 专属 Negative Instructions: safety_policy 段含对应禁止项。"""
    safety = ALL_PROMPTS[prompt_name]["safety_policy"]
    for rule in AGENT_SPECIFIC[prompt_name]:
        assert rule.lower() in safety.lower(), (
            f"{prompt_name} 缺 Agent 专属 Negative Instruction: {rule}\n"
            f"safety_policy 全文:\n{safety}"
        )


@pytest.mark.parametrize("prompt_name", sorted(ALL_PROMPTS.keys()))
def test_tool_policy_section_is_non_empty_and_actionable(prompt_name: str) -> None:
    """D5: tool_policy 段必须非空且含 actionable 内容 (动词/工具名/边界词)。

    防止空段或单字串 (如 '无' / '空')。
    """
    tool_policy = ALL_PROMPTS[prompt_name]["tool_policy"]
    assert isinstance(tool_policy, str) and tool_policy.strip(), (
        f"{prompt_name}.tool_policy 为空"
    )
    # 必须含至少一个动作词或工具名/边界关键词
    keywords = (
        "调用", "search_schema", "do not call", "不调", "不重复",
        "tool", "schema", "可用表结构",
    )
    assert any(kw.lower() in tool_policy.lower() for kw in keywords), (
        f"{prompt_name}.tool_policy 缺乏 actionable 关键词\n"
        f"tool_policy 全文:\n{tool_policy}"
    )


def test_sql_prompts_tool_policy_explicitly_handles_search_schema_boundary() -> None:
    """SQL 三个 prompt 必须显式声明「schema 已注入就不再调 search_schema」。

    SQL Agent 是 search_schema 的最常见误用场景, 钉住边界。
    """
    for name in ("sql_intent_analyze", "sql_plan", "sql_generate"):
        tool_policy = ALL_PROMPTS[name]["tool_policy"]
        assert (
            "search_schema" in tool_policy.lower()
            or "schema 已注入" in tool_policy
            or "不重复调" in tool_policy
            or "已注入" in tool_policy
        ), (
            f"{name}.tool_policy 未显式声明 search_schema 边界\n"
            f"tool_policy 全文:\n{tool_policy}"
        )


def test_report_plan_tool_policy_distinct_from_sql() -> None:
    """Report Plan 不调 SQL tool (SQL 已在 Execution 阶段完成); 必须显式声明。"""
    tool_policy = ALL_PROMPTS["report_plan"]["tool_policy"]
    # tool_policy 应提到 SQL 阶段已完成 / 不调 SQL tool
    assert (
        "sql" in tool_policy.lower()
        or "SQL" in tool_policy
        or "Execution" in tool_policy
        or "execution 阶段" in tool_policy.lower()
    ), (
        f"report_plan.tool_policy 未显式声明不调 SQL tool\n"
        f"tool_policy 全文:\n{tool_policy}"
    )