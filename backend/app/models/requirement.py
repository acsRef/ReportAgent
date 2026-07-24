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


class RequirementOption(BaseModel):
    label: str
    value: str


class RequirementMissingField(BaseModel):
    key: RequirementFieldKey
    label: str
    kind: RequirementFieldKind = "single"
    options: list[RequirementOption] = Field(default_factory=list)


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
        # A 'missing' card must EITHER have explicit missing fields OR
        # have at least one unresolved assumption (the user must accept
        # the assumption before /confirm is allowed).
        if self.status == "missing" and not self.missing_fields:
            has_unresolved = any(a.accepted is None for a in self.assumptions)
            if not has_unresolved:
                raise ValueError("missing requirement must contain missing fields")
        if self.status in {"complete", "locked"} and self.missing_fields:
            raise ValueError("complete requirement cannot contain missing fields")
        if self.status in {"complete", "locked"} and any(
            assumption.accepted is None for assumption in self.assumptions
        ):
            raise ValueError("complete requirement has unresolved assumptions")
        if self.status == "locked" and self.confirmed_at is None:
            raise ValueError("locked requirement requires confirmed_at")
        if self.status != "locked" and self.confirmed_at is not None:
            raise ValueError("only locked requirement can contain confirmed_at")
        return self
