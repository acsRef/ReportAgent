from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


RequirementStatus = Literal["missing", "complete", "locked"]
RequirementFieldKey = Literal[
    "time_range",
    "scope",
    "metric",
    "comparison",
    "granularity",
]
RequirementFieldKind = Literal["single", "multiple"]

SelectedValue = str | list[str]


class RequirementOption(BaseModel):
    label: str
    value: str


class RequirementMissingField(BaseModel):
    key: RequirementFieldKey
    label: str
    kind: RequirementFieldKind = "single"
    options: list[RequirementOption] = Field(default_factory=list)
    # The user's selection for this field. For `kind=single`, this is a
    # single string (or None if unselected). For `kind=multiple`, a
    # list of strings (possibly empty). The service layer translates
    # selections into the card's structured fields (time_range, scope,
    # etc.) and removes this MissingField from the persisted card
    # when filled.
    selected_value: SelectedValue | None = None


class RequirementAssumption(BaseModel):
    key: str
    text: str
    accepted: bool | None = None
    alternatives: list[RequirementOption] = Field(default_factory=list)


class RequirementCard(BaseModel):
    id: str
    version: int = Field(default=1, ge=1)
    status: RequirementStatus
    summary: str
    target_metrics: list[str] = Field(default_factory=list)
    time_range: str | None = None
    scope: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    analysis_methods: list[str] = Field(default_factory=list)
    expected_blocks: list[str] = Field(default_factory=list)
    missing_fields: list[RequirementMissingField] = Field(default_factory=list)
    assumptions: list[RequirementAssumption] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    confirmed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_status_consistency(self) -> RequirementCard:
        # NOTE: status/form-state consistency used to be enforced here.
        # We relaxed it because PATCH bodies carry whatever `status` the
        # server issued at analysis time, even after the user filled the
        # form. The hard invariants are now enforced by
        # `service.requirement_service.patch_requirement`, which
        # normalizes the card before persisting it. This validator now
        # only enforces the `locked` invariants, which are still strict
        # because locked cards come from server-side execution, never
        # from the client.
        if self.status == "locked" and self.confirmed_at is None:
            raise ValueError("locked requirement requires confirmed_at")
        if self.status != "locked" and self.confirmed_at is not None:
            raise ValueError("only locked requirement can contain confirmed_at")
        return self
