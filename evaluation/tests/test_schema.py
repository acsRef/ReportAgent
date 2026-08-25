"""T1: schema/loader 离线测试 —— baseline-lock plan T1。"""
from __future__ import annotations

import json
import pytest

from evaluation.schema import (
    BaselineCase,
    TurnSpec,
    TurnExpectation,
)


def _case(**overrides) -> dict:
    """最小合法 case dict，供各测试覆写字段。"""
    base = {
        "id": "explicit-sales",
        "category": "explicit_query",
        "description": "t",
        "turns": [{"query": "2024年各区域销售额"}],
        "expectations": [{}],
    }
    base.update(overrides)
    return base


class TestTurnSpec:
    def test_mode_defaults_to_new(self):
        t = TurnSpec(query="你好")
        assert t.mode == "new"

    def test_invalid_mode_rejected(self):
        with pytest.raises(Exception):
            TurnSpec(query="x", mode="destroy")

    def test_empty_query_rejected(self):
        with pytest.raises(Exception):
            TurnSpec(query="")

    def test_supplement_mode_accepted(self):
        assert TurnSpec(query="再按产品细分", mode="supplement").mode == "supplement"


class TestExpectationLengthRule:
    def test_len_one_applies_to_last_turn(self):
        c = BaselineCase(**_case(expectations=[{}]))
        assert len(c.expectations) == 1

    def test_len_equal_to_turns(self):
        c = BaselineCase(**_case(
            turns=[{"query": "a"}, {"query": "b", "mode": "supplement"}],
            expectations=[{}, {}],
        ))
        assert len(c.expectations) == 2

    @pytest.mark.parametrize("ne,nt", [(0, 1), (2, 1), (3, 2)])
    def test_bad_lengths_rejected(self, ne, nt):
        with pytest.raises(Exception):
            BaselineCase(**_case(
                turns=[{"query": "q"}] * nt,
                expectations=[{}] * ne,
            ))


class TestBaselineCaseValidation:
    def test_duplicate_ids_rejected_by_loader(self, tmp_path):
        from evaluation.loader import load_all

        case = _case()
        p = tmp_path / "cases.json"
        p.write_text(json.dumps([case, case]), encoding="utf-8")
        with pytest.raises(ValueError, match="duplicate"):
            load_all(p)

    def test_loader_roundtrip(self, tmp_path):
        from evaluation.loader import load_all

        p = tmp_path / "cases.json"
        p.write_text(
            json.dumps([_case()], ensure_ascii=False), encoding="utf-8"
        )
        cases = load_all(p)
        assert len(cases) == 1
        assert cases[0].id == "explicit-sales"
        assert cases[0].turns[0].query == "2024年各区域销售额"

    def test_known_gap_flag(self):
        c = BaselineCase(**_case(known_gap=True))
        assert c.known_gap is True

    def test_fault_injection_flag(self):
        c = BaselineCase(**_case(requires_fault_injection=True))
        assert c.requires_fault_injection is True
