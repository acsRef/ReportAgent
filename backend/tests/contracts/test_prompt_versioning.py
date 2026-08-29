from __future__ import annotations

"""P7 D3: 每个 prompt 必须有完整 META 5 字段 (name/version/purpose/input/output)。

Versioning 元数据是「Langfuse 实际接入留 P13」前的可追踪钉子。
"""

import pytest

from app.agent.prompts import (
    INTENT_CLASSIFY_META,
    REPORT_PLAN_META,
    REQUIREMENT_PARSE_META,
    SQL_GENERATE_META,
    SQL_INTENT_ANALYZE_META,
    SQL_PLAN_META,
)
from app.memory.prompts import CONVERSATION_SUMMARIZE_META

pytestmark = pytest.mark.contracts

REQUIRED_META_FIELDS = ("name", "version", "purpose", "input", "output")

ALL_META = {
    "intent_classify": INTENT_CLASSIFY_META,
    "requirement_parse": REQUIREMENT_PARSE_META,
    "sql_intent_analyze": SQL_INTENT_ANALYZE_META,
    "sql_plan": SQL_PLAN_META,
    "sql_generate": SQL_GENERATE_META,
    "report_plan": REPORT_PLAN_META,
    "conversation_summarize": CONVERSATION_SUMMARIZE_META,
}


@pytest.mark.parametrize("prompt_name", sorted(ALL_META.keys()))
def test_meta_has_all_five_fields(prompt_name: str) -> None:
    meta = ALL_META[prompt_name]
    missing = [f for f in REQUIRED_META_FIELDS if f not in meta]
    assert missing == [], f"{prompt_name} META 缺字段: {missing}"


@pytest.mark.parametrize("prompt_name", sorted(ALL_META.keys()))
def test_meta_version_is_int(prompt_name: str) -> None:
    meta = ALL_META[prompt_name]
    assert isinstance(meta["version"], int) and meta["version"] >= 1, (
        f"{prompt_name} version 应为正整数, 实际 {meta['version']!r}"
    )


@pytest.mark.parametrize("prompt_name", sorted(ALL_META.keys()))
def test_meta_name_is_kebab_or_snake(prompt_name: str) -> None:
    """name 字段约定 snake_case; 与测试 lookup key 一致。"""
    meta = ALL_META[prompt_name]
    name = meta["name"]
    assert name == prompt_name, f"prompt name '{name}' 与期待 '{prompt_name}' 不一致"


@pytest.mark.parametrize("prompt_name", sorted(ALL_META.keys()))
def test_meta_input_output_are_lists(prompt_name: str) -> None:
    meta = ALL_META[prompt_name]
    assert isinstance(meta["input"], (list, tuple)) and meta["input"], (
        f"{prompt_name}.input 应为非空 list/tuple"
    )
    assert isinstance(meta["output"], str) and meta["output"].strip(), (
        f"{prompt_name}.output 应为非空 string"
    )


@pytest.mark.parametrize("prompt_name", sorted(ALL_META.keys()))
def test_meta_purpose_non_empty(prompt_name: str) -> None:
    meta = ALL_META[prompt_name]
    purpose = meta["purpose"]
    assert isinstance(purpose, str) and purpose.strip(), (
        f"{prompt_name}.purpose 应为非空字符串"
    )


def test_meta_names_unique() -> None:
    """7 个 prompt name 必须互不重复 (避免 trace 里 trace_id 冲突)。"""
    names = [m["name"] for m in ALL_META.values()]
    assert len(names) == len(set(names)), f"prompt name 重复: {names}"