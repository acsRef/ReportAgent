"""用户模拟器单测（plan 2026-11-04-requirement-slot-contract Phase 4）。

模拟器本身是纯函数：给卡 + GT → 只按 GT 作答。它的正确性决定
`priority_question_sr` / `turns_used` / `follow_up_sr` 三个度量可不可信。
"""
from __future__ import annotations

import pytest

from evaluation.user_sim import GroundTruth, GroundTruthUser, summarize

pytestmark = pytest.mark.graphs


def _card(*fields: dict, assumptions: list[dict] | None = None) -> dict:
    return {
        "status": "missing",
        "missing_fields": list(fields),
        "assumptions": assumptions or [],
    }


def _field(key: str, options: list[tuple[str, str]], kind: str = "single") -> dict:
    return {
        "key": key,
        "label": key,
        "kind": kind,
        "options": [{"label": label, "value": value} for label, value in options],
        "selected_value": None,
    }


METRIC_FIELD = _field("metric", [
    ("销售额", "metric:销售额"),
    ("净销售额", "metric:净销售额"),
    ("实收金额", "metric:实收金额"),
])


def test_answers_matching_ground_truth_option() -> None:
    user = GroundTruthUser(truth=GroundTruth(metric="净销售额"))

    filled = user.answer_one(_card(METRIC_FIELD))

    assert filled is not None
    assert filled["missing_fields"][0]["selected_value"] == "metric:净销售额"
    assert user.answered_slots == ["metric"]


def test_answers_one_slot_per_turn() -> None:
    user = GroundTruthUser(
        truth=GroundTruth(metric="销售额", time_range="2024年"), patience=3,
    )
    card = _card(
        _field("time_range", [("2024年", "2024年"), ("今年", "今年")]),
        METRIC_FIELD,
    )

    first = user.answer_one(card)
    assert first is not None
    assert first["missing_fields"][0]["selected_value"] == "2024年"
    assert first["missing_fields"][1]["selected_value"] is None, "一次只答一个槽位"
    assert user.answered_slots == ["time_range"]


def test_patience_exhausted_returns_none() -> None:
    user = GroundTruthUser(truth=GroundTruth(metric="销售额"), patience=1)
    card = _card(METRIC_FIELD)

    assert user.answer_one(card) is not None
    assert user.answer_one(card) is None


def test_observes_asked_slots_and_must_not_ask_violations() -> None:
    user = GroundTruthUser(
        truth=GroundTruth(metric="销售额"), must_not_ask=("time_range",),
    )
    user.observe_card(_card(METRIC_FIELD, _field("time_range", [("今年", "今年")])))

    assert user.asked_slots == ["metric", "time_range"]
    assert user.must_not_ask_violations == ["time_range"]


def test_falls_back_to_first_option_when_gt_is_silent() -> None:
    """GT 没说这一槽位 → 用户「你决定」，取第一个选项（而不是卡住）。"""
    user = GroundTruthUser(truth=GroundTruth(metric="销售额"))
    card = _card(_field("granularity", [("日", "day"), ("周", "week")]))

    filled = user.answer_one(card)

    assert filled is not None
    assert filled["missing_fields"][0]["selected_value"] == "day"


def test_refuses_when_gt_option_is_absent() -> None:
    """GT 想要的口径没出现在选项里 → 用户拒绝作答（不替代理选个错的）。

    这条是夹具可信度的关键：否则「代理只给了销售额/净销售额、用户其实要实收金额」
    会被静默记成「用户选了销售额」，看起来收敛、实际是假绿。
    """
    user = GroundTruthUser(truth=GroundTruth(metric="实收金额"))
    card = _card(_field("metric", [("销售额", "metric:销售额"), ("净销售额", "metric:净销售额")]))

    assert user.answer_one(card) is None
    assert user.gt_option_missing == ["metric"]
    assert user.answered_slots == []


def test_accepts_assumptions() -> None:
    user = GroundTruthUser(truth=GroundTruth(metric="销售额"))
    card = _card(
        METRIC_FIELD,
        assumptions=[{"key": "a", "text": "口径按订单金额", "accepted": None}],
    )

    filled = user.answer_one(card)

    assert filled is not None
    assert filled["assumptions"][0]["accepted"] is True


def test_multiple_scope_selects_all_matching() -> None:
    user = GroundTruthUser(truth=GroundTruth(scope=("华东", "华南")))
    card = _card(_field(
        "scope",
        [("华东", "华东"), ("华北", "华北"), ("华南", "华南")],
        kind="multiple",
    ))

    filled = user.answer_one(card)

    assert filled is not None
    assert filled["missing_fields"][0]["selected_value"] == ["华东", "华南"]


def test_summary_aggregates_metrics() -> None:
    cases = [
        {"id": "a", "priority_ok": True, "follow_up_ok": True, "turns_used": 0, "converged": True},
        {"id": "b", "priority_ok": False, "follow_up_ok": True, "turns_used": 2,
         "converged": False, "must_not_ask_violations": ["metric"]},
    ]

    summary = summarize(cases)

    assert summary["total"] == 2
    assert summary["priority_question_sr"] == 0.5
    assert summary["follow_up_sr"] == 1.0
    assert summary["turns_used_avg"] == 1.0
    assert summary["must_not_ask_violations"] == 1
    assert summary["not_converged"] == ["b"]
