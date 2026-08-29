from __future__ import annotations

"""P7 D2/D4: 每个 prompt 必须由 6 段组成。

6 段: system_contract / role / task_contract / tool_policy / output_schema / safety_policy。
漏段视为分层缺失,plan D2 验收硬约束。
"""

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

REQUIRED_SECTIONS = (
    "system_contract",
    "role",
    "task_contract",
    "tool_policy",
    "output_schema",
    "safety_policy",
)

ALL_PROMPTS = {
    "intent_classify": INTENT_CLASSIFY_V1,
    "requirement_parse": REQUIREMENT_PARSE_V1,
    "sql_intent_analyze": SQL_INTENT_ANALYZE_V1,
    "sql_plan": SQL_PLAN_V1,
    "sql_generate": SQL_GENERATE_V1,
    "report_plan": REPORT_PLAN_V1,
    "conversation_summarize": CONVERSATION_SUMMARIZE_V1,
}


@pytest.mark.parametrize("prompt_name", sorted(ALL_PROMPTS.keys()))
def test_prompt_has_all_six_sections(prompt_name: str) -> None:
    """每个 prompt 6 段齐全 (system_contract/role/task_contract/tool_policy/output_schema/safety_policy)。"""
    prompt = ALL_PROMPTS[prompt_name]
    missing = [s for s in REQUIRED_SECTIONS if s not in prompt]
    assert missing == [], f"{prompt_name} 缺段: {missing}"


@pytest.mark.parametrize("prompt_name", sorted(ALL_PROMPTS.keys()))
def test_prompt_each_section_non_empty(prompt_name: str) -> None:
    """6 段每段都必须非空字符串,不允许占位。"""
    prompt = ALL_PROMPTS[prompt_name]
    for section in REQUIRED_SECTIONS:
        value = prompt.get(section, "")
        assert isinstance(value, str) and value.strip(), (
            f"{prompt_name}.{section} 为空或非字符串"
        )


@pytest.mark.parametrize("prompt_name", sorted(ALL_PROMPTS.keys()))
def test_prompt_section_order_is_layered(prompt_name: str) -> None:
    """6 段按 system→role→task→tool_policy→output_schema→safety_policy 顺序定义。

    测试调用方只依赖 (in) operator,不依赖有序,但 dict 保序 (Python 3.7+)。
    此测试钉住「顺序 = 分层」,防止有人打乱 6 段顺序。
    """
    prompt = ALL_PROMPTS[prompt_name]
    keys = list(prompt.keys())
    # 至少前 6 个 key 必须是 6 段; 允许后续追加扩展段 (如 fewshot 段),但前 6 段顺序定死
    assert keys[: len(REQUIRED_SECTIONS)] == list(REQUIRED_SECTIONS), (
        f"{prompt_name} 6 段顺序错: {keys[: len(REQUIRED_SECTIONS)]}"
    )


def test_no_legacy_prompt_constants_outside_prompts_package() -> None:
    """P7 收敛: 7 个 prompt 已迁入 prompts/ 包, 原裸 f-string 不应再有同名模块常量。

    已知保留: requirement_parser._PARSE_PROMPT (T4 才删),本测试仅钉 7 个
    新 prompt 模块已建立且非空。
    """
    expected_names = {
        "INTENT_CLASSIFY_V1", "REQUIREMENT_PARSE_V1", "SQL_INTENT_ANALYZE_V1",
        "SQL_PLAN_V1", "SQL_GENERATE_V1", "REPORT_PLAN_V1",
        "CONVERSATION_SUMMARIZE_V1",
    }
    from app.agent.prompts import __all__ as agent_prompts_all
    from app.memory.prompts import __all__ as memory_prompts_all

    combined = set(agent_prompts_all) | set(memory_prompts_all)
    missing = expected_names - combined
    assert missing == set(), f"prompts 包漏导: {missing}"