"""Smoke tests for the RequirementCard Pydantic model.

Validates the three legal statuses (missing / complete / locked) and the
five consistency rules enforced by `model_validator`.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.smoke

from app.models.requirement import (
    RequirementAssumption,
    RequirementCard,
    RequirementMissingField,
    RequirementOption,
)


def _base() -> dict:
    return {
        "id": "draft-1",
        "summary": "Test requirement",
    }


def test_missing_status_allows_empty_missing_fields() -> None:
    """Validator relaxed: status/form consistency enforced at service layer."""
    card = RequirementCard(
        **_base(),
        status="missing",
        missing_fields=[],
    )
    assert card.status == "missing"
    assert card.missing_fields == []


def test_complete_status_allows_missing_fields() -> None:
    """Validator relaxed: status/form consistency enforced at service layer."""
    card = RequirementCard(
        **_base(),
        status="complete",
        missing_fields=[
            RequirementMissingField(
                key="time_range",
                label="时间范围",
                options=[RequirementOption(label="本月", value="本月")],
            )
        ],
        assumptions=[
            RequirementAssumption(key="a1", text="默认华东", accepted=True),
        ],
    )
    assert card.status == "complete"


def test_locked_status_requires_confirmed_at() -> None:
    with pytest.raises(ValidationError) as exc:
        RequirementCard(
            **_base(),
            status="locked",
            missing_fields=[],
            assumptions=[RequirementAssumption(key="a1", text="默认", accepted=True)],
            confirmed_at=None,
        )
    assert "locked requirement requires confirmed_at" in str(exc.value)


def test_complete_status_allows_unresolved_assumptions() -> None:
    """Validator relaxed: assumption resolution enforced at service layer."""
    card = RequirementCard(
        **_base(),
        status="complete",
        missing_fields=[],
        assumptions=[RequirementAssumption(key="a1", text="默认", accepted=None)],
    )
    assert card.status == "complete"
    assert card.assumptions[0].accepted is None


def test_valid_missing_card_passes() -> None:
    card = RequirementCard(
        **_base(),
        status="missing",
        missing_fields=[
            RequirementMissingField(
                key="time_range",
                label="时间范围",
                options=[RequirementOption(label="本月", value="本月")],
            )
        ],
    )
    assert card.status == "missing"
    assert card.version == 1


def test_valid_complete_card_passes() -> None:
    card = RequirementCard(
        **_base(),
        status="complete",
        target_metrics=["销售额"],
        time_range="今年",
        scope=["华东"],
        dimensions=["区域"],
        missing_fields=[],
        assumptions=[RequirementAssumption(key="a1", text="默认", accepted=True)],
    )
    assert card.status == "complete"
    assert card.confirmed_at is None


def test_valid_locked_card_passes() -> None:
    from datetime import datetime
    card = RequirementCard(
        **_base(),
        status="locked",
        target_metrics=["销售额"],
        missing_fields=[],
        assumptions=[RequirementAssumption(key="a1", text="默认", accepted=True)],
        confirmed_at=datetime(2026, 7, 24, 12, 0, 0),
    )
    assert card.status == "locked"
    assert card.confirmed_at is not None


def test_non_locked_card_cannot_have_confirmed_at() -> None:
    from datetime import datetime
    with pytest.raises(ValidationError) as exc:
        RequirementCard(
            **_base(),
            status="complete",
            missing_fields=[],
            assumptions=[RequirementAssumption(key="a1", text="默认", accepted=True)],
            confirmed_at=datetime(2026, 7, 24, 12, 0, 0),
        )
    assert "only locked requirement can contain confirmed_at" in str(exc.value)
