"""P7 D2/D4: 每个 prompt 必须由 6 段组成（分层）。

6 段: system_contract / role / task_contract / tool_policy / output_schema / safety_policy。
2026-11-02 收敛：原 22 条里 `has_all_six_sections[×7]` 与 `each_section_non_empty[×7]`
是同一断言，`section_order_is_layered[×7]` 无消费者（`build_*` 按 key 取段、
`prompt_kind` 用子串匹配），现合并为 3 条。
"""
from __future__ import annotations

import pytest

from app.agent.prompts import (
    INTENT_CLASSIFY_V1,
    REPORT_PLAN_V1,
    REQUIREMENT_PARSE_V1,
    SQL_GENERATE_V1,
    SQL_INTENT_ANALYZE_V1,
    SQL_PLAN_V1,
)
from app.llm.mock import KIND_MARKERS
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


def test_prompts_are_six_layered_sections_and_builders_work() -> None:
    """7 个 prompt 六段齐全且非空；7 个 build 函数产出非空长字符串。"""
    problems: list[str] = []
    for name, prompt in ALL_PROMPTS.items():
        missing = [s for s in REQUIRED_SECTIONS if s not in prompt]
        if missing:
            problems.append(f"{name} 缺段: {missing}")
            continue
        for section in REQUIRED_SECTIONS:
            value = prompt[section]
            if not (isinstance(value, str) and value.strip()):
                problems.append(f"{name}.{section} 为空或非字符串")

    from app.agent.prompts import (
        build_intent_classify_prompt,
        build_report_plan_prompt,
        build_requirement_parse_prompt,
        build_sql_generate_prompt,
        build_sql_intent_analyze_prompt,
        build_sql_plan_prompt,
    )
    from app.memory.prompts import build_conversation_summarize_prompt

    built = [
        build_intent_classify_prompt("查销售额"),
        build_requirement_parse_prompt("q", "schema", ""),
        build_sql_intent_analyze_prompt("q", "tools"),
        build_sql_plan_prompt("2026-08-29", "q", "schema"),
        build_sql_generate_prompt(
            "2026-08-29", "m", ["d"], [{"field": "f"}], "sum", None, "schema",
        ),
        build_report_plan_prompt(["col"], 10, "tools"),
        build_conversation_summarize_prompt(None, [{"role": "user", "content": "hi"}]),
    ]
    for i, text in enumerate(built, 1):
        if not (isinstance(text, str) and len(text) > 100):
            problems.append(f"build #{i} 产出过短或非字符串")

    assert problems == [], "\n".join(problems)


def test_prompt_package_exports_complete() -> None:
    """7 个 prompt 常量必须全部从包里导出（漏导出 = 运行时 ImportError）。"""
    expected = {
        "INTENT_CLASSIFY_V1", "REQUIREMENT_PARSE_V1", "SQL_INTENT_ANALYZE_V1",
        "SQL_PLAN_V1", "SQL_GENERATE_V1", "REPORT_PLAN_V1",
        "CONVERSATION_SUMMARIZE_V1",
    }
    from app.agent.prompts import __all__ as agent_prompts_all
    from app.memory.prompts import __all__ as memory_prompts_all

    missing = expected - (set(agent_prompts_all) | set(memory_prompts_all))
    assert missing == set(), f"prompts 包漏导: {missing}"


def test_kind_markers_match_real_prompts() -> None:
    """`app.llm.mock.KIND_MARKERS` 的分类首句必须真的出现在对应 prompt 里。

    Playwright Contract E2E 靠这些固定首句给 prompt 归类（`prompt_kind`）。
    此前 `test_mock_llm_adapter` 只是**复制**了一份字面量，从不校验真实 prompt——
    改 prompt 首句 → mock 静默 miss、PR 门禁全绿、只有 nightly E2E 炸。
    """
    markers = dict(KIND_MARKERS)
    problems = [
        f"{kind}: marker {marker!r} 不在 system_contract 里"
        for kind, marker in markers.items()
        if kind in ALL_PROMPTS and marker not in ALL_PROMPTS[kind]["system_contract"]
    ]

    covered = set(markers) & set(ALL_PROMPTS)
    assert covered == set(markers), f"KIND_MARKERS 有未覆盖的 kind: {set(markers) - covered}"
    assert problems == [], "\n".join(problems)
