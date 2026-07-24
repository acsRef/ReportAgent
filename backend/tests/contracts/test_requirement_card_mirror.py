"""Contract-mirror tests for RequirementCard.

The Python `RequirementCard` (Pydantic) and the TypeScript mirror at
`frontend/src/types/requirement.ts` MUST stay field-for-field aligned.
Rather than introduce a codegen tool, we hardcode the expected TS
snapshot in this test and assert every Python value matches.

If you change a field name, status literal, or field key on either side,
update both files in the same commit and re-run this test.
"""
from __future__ import annotations

from app.models.requirement import (
    RequirementFieldKey,
    RequirementStatus,
    RequirementCard,
)

# --- Hardcoded mirror of frontend/src/types/requirement.ts -----------------
TS_REQUIREMENT_STATUS = ("missing", "complete", "locked")
TS_REQUIREMENT_FIELD_KEYS = (
    "time_range",
    "scope",
    "metric",
    "comparison",
    "granularity",
)
TS_REQUIREMENT_FIELD_KINDS = ("single", "multiple")

# Field names that MUST exist on RequirementCard (Pydantic) and be
# present in the TS interface (snake_case parity, per
# docs/contracts/requirement-card.md).
TS_REQUIREMENT_CARD_FIELDS = (
    "id",
    "version",
    "status",
    "summary",
    "target_metrics",
    "time_range",
    "scope",
    "dimensions",
    "analysis_methods",
    "expected_blocks",
    "missing_fields",
    "assumptions",
    "confidence",
    "confirmed_at",
)

TS_REQUIREMENT_MISSING_FIELD_FIELDS = ("key", "label", "kind", "options")
TS_REQUIREMENT_ASSUMPTION_FIELDS = ("key", "text", "accepted", "alternatives")
TS_REQUIREMENT_OPTION_FIELDS = ("label", "value")


# --- Tests -----------------------------------------------------------------


def test_status_literal_parity() -> None:
    assert tuple(RequirementStatus.__args__) == TS_REQUIREMENT_STATUS


def test_field_key_literal_parity() -> None:
    assert tuple(RequirementFieldKey.__args__) == TS_REQUIREMENT_FIELD_KEYS


def test_field_kind_literal_parity() -> None:
    from app.models.requirement import RequirementFieldKind
    assert tuple(RequirementFieldKind.__args__) == TS_REQUIREMENT_FIELD_KINDS


def test_card_field_parity() -> None:
    """Every TS field exists as an attribute on the Pydantic model."""
    schema = RequirementCard.model_fields
    assert tuple(schema.keys()) == TS_REQUIREMENT_CARD_FIELDS, (
        f"RequirementCard field mismatch.\n"
        f"  Python: {tuple(schema.keys())}\n"
        f"  TS:     {TS_REQUIREMENT_CARD_FIELDS}\n"
        f"Update one side and the mirror in the same commit."
    )


def test_missing_field_subtype_parity() -> None:
    from app.models.requirement import RequirementMissingField
    assert tuple(RequirementMissingField.model_fields.keys()) == TS_REQUIREMENT_MISSING_FIELD_FIELDS


def test_assumption_subtype_parity() -> None:
    from app.models.requirement import RequirementAssumption
    assert tuple(RequirementAssumption.model_fields.keys()) == TS_REQUIREMENT_ASSUMPTION_FIELDS


def test_option_subtype_parity() -> None:
    from app.models.requirement import RequirementOption
    assert tuple(RequirementOption.model_fields.keys()) == TS_REQUIREMENT_OPTION_FIELDS
