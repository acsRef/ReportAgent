"""P4b T1 lifecycle 契约枚举钉子。

memory-architecture.md §五/§六 冻结字面值钉死；后续所有层依赖这些常量，
typo 即断（枚举是 §六 状态机 + §五 confidence 规则的单一真相源）。
"""
from __future__ import annotations

import pytest

from app.memory.lifecycle import (
    CONFIDENCE_EXPLICIT_DEFINITION,
    CONFIDENCE_EXPLICIT_STATEMENT,
    CONFIDENCE_LLM_INFERRED,
    RECALLABLE_STATUSES,
    MemoryConfidence,
    MemoryScope,
    MemoryStatus,
)

pytestmark = pytest.mark.contracts


def test_status_values_match_six_state_machine():
    # §六 candidate → active → superseded / expired
    assert {s.value for s in MemoryStatus} == {
        "candidate", "active", "superseded", "expired",
    }


def test_scope_values_match_user_or_session():
    assert {s.value for s in MemoryScope} == {"user", "session"}


def test_confidence_three_levels():
    assert {c.value for c in MemoryConfidence} == {"high", "medium", "low"}


def test_confidence_rule_is_fixed_not_llm():
    # §五：explicit_user_statement→high; explicit_business_definition→high;
    # LLM inferred→(不进 active)记 low
    assert CONFIDENCE_EXPLICIT_STATEMENT is MemoryConfidence.HIGH
    assert CONFIDENCE_EXPLICIT_DEFINITION is MemoryConfidence.HIGH
    assert CONFIDENCE_LLM_INFERRED is MemoryConfidence.LOW


def test_only_active_is_recallable():
    # candidate/superseded/expired 一律不召回（§五 + §六）
    assert RECALLABLE_STATUSES == frozenset({"active"})
