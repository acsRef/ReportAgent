"""P7 D4/D5: 每个 prompt 的 safety_policy 段必须带基线禁止项。

伞形 plan §十 基线 4 条:
1. Do NOT invent tables/columns
2. Do NOT fabricate query results
3. Do NOT assume unavailable schema
4. Do NOT call search_schema when schema is already known

2026-11-02 收敛（43 → 4）：原来「4 条基线 × 7 个 prompt」= 28 个用例，而这 4 句
在 7 个 prompt 里是**逐字复制的同一段 boilerplate**——同一句话被验了 28 遍。
`tool_policy is_non_empty_and_actionable` 的关键词白名单含 "tool"/"schema"/"调用"，
近乎恒真，且与非空断言重复，一并删除。
"""
from __future__ import annotations

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


def test_all_prompts_carry_four_baseline_negative_instructions() -> None:
    """7 个 prompt 的 safety_policy 都必须覆盖基线 4 条禁止项。"""
    problems: list[str] = []
    for name, prompt in ALL_PROMPTS.items():
        safety = prompt["safety_policy"]
        for rule_name, pattern in BASELINE_PATTERNS.items():
            if not pattern.search(safety):
                problems.append(f"{name} 缺基线禁止项 {rule_name}")
    assert problems == [], "\n".join(problems)


def test_sql_generate_forbids_destructive_sql() -> None:
    """SQL 生成器只能出 SELECT：必须显式禁止 DROP 等写操作。"""
    safety = ALL_PROMPTS["sql_generate"]["safety_policy"]
    assert "do not generate drop" in safety.lower(), (
        f"sql_generate 未禁止 DROP：\n{safety}"
    )


def test_report_plan_does_not_emit_sql() -> None:
    """报告规划阶段 SQL 已经执行完了，不能再去生成 SQL。"""
    safety = ALL_PROMPTS["report_plan"]["safety_policy"]
    assert "do not generate sql" in safety.lower(), (
        f"report_plan 未禁止生成 SQL：\n{safety}"
    )


def test_conversation_summarize_does_not_extract_preferences() -> None:
    """滚动摘要只做叙事压缩，不抽未明说的偏好（偏好走显式写入口）。"""
    safety = ALL_PROMPTS["conversation_summarize"]["safety_policy"]
    assert "do not extract preferences" in safety.lower(), (
        f"conversation_summarize 未禁止抽偏好：\n{safety}"
    )
