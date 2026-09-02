"""Pydantic dynamic dim key roundtrip 测试。

P14 边界：BaselineCase → TurnExpectation → model_dump() 链路上不能丢失
memory / retrieval / sql / tool_selection / repair / frontend / e2e 等
dynamic dim 期望 key（这些不在 TurnExpectation 显式字段定义）。
"""
from __future__ import annotations

import pytest

from evaluation.schema import TurnExpectation, BaselineCase, TurnSpec


def test_turn_expectation_extra_dim_keys_roundtrip_via_model_dump():
    """TurnExpectation.model_validate + model_dump 不应丢失 dynamic dim key。"""
    raw = {
        "requirement": {"status": "complete"},
        "memory": {"recalled": True, "types_any_of": ["conversation"]},
        "retrieval": {"recalled": True, "k_min": 1},
        "sql": {"sql_nonempty": True, "rows_gt": 0},
        "tool_selection": {"tool_chosen": "search_schema"},
        "repair": {"used": True},
        "frontend": {"any_key": True},
        "e2e": {"e2e_x": True},
    }
    exp = TurnExpectation.model_validate(raw)
    dumped = exp.model_dump()
    assert dumped["memory"] == {"recalled": True, "types_any_of": ["conversation"]}
    assert dumped["retrieval"] == {"recalled": True, "k_min": 1}
    assert dumped["sql"] == {"sql_nonempty": True, "rows_gt": 0}
    assert dumped["tool_selection"] == {"tool_chosen": "search_schema"}
    assert dumped["repair"] == {"used": True}
    assert dumped["frontend"] == {"any_key": True}
    assert dumped["e2e"] == {"e2e_x": True}


def test_baseline_case_expectation_dynamic_dim_keys_roundtrip():
    """BaselineCase.expectations[i].model_dump() 也应保留 dynamic dim key。"""
    raw = {
        "id": "p14-dim-key-roundtrip",
        "category": "explicit_query",
        "description": "验证 dynamic dim key 不在 Pydantic roundtrip 中丢失",
        "turns": [{"query": "t", "mode": "new"}],
        "expectations": [
            {
                "requirement": {"status": "complete"},
                "memory": {"recalled": True},
            },
        ],
    }
    case = BaselineCase.model_validate(raw)
    dumped = case.expectations[0].model_dump()
    assert "memory" in dumped, (
        f"P14 P0 失守：memory dim key 丢失，got keys={list(dumped.keys())}"
    )
    assert dumped["memory"] == {"recalled": True}


def test_existing_typed_fields_still_validate_after_extra_allow():
    """extra='allow' 不应破坏 RequirementExpectation 等 typed 字段的 strict 校验。"""
    raw = {
        "requirement": {"status": "INVALID_VALUE"},  # bad enum
        "memory": {"recalled": True},
    }
    with pytest.raises(ValueError, match="status"):
        TurnExpectation.model_validate(raw)
