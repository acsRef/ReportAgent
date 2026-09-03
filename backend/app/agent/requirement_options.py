"""Server-side options generator for RequirementCard fields.

The LLM never invents option labels directly; it identifies WHICH
dimensions are missing or ambiguous, and this module builds the
controlled option list. The frontend then renders the structured
options as a form (no free-form text from the LLM reaches the user).

The options are intentionally coarse (4-6 entries per dimension) and
match the canonical business vocabulary used in `backend/scripts/seed_business_p15prelude.sql`.
"""
from __future__ import annotations

from datetime import datetime

from app.models.requirement import (
    RequirementAssumption,
    RequirementFieldKey,
    RequirementMissingField,
    RequirementOption,
)


# --- Time options ----------------------------------------------------------

def time_options(current_date: datetime | None = None) -> list[RequirementOption]:
    """Coarse Chinese time-range options. Resolved at the server level —
    we don't ask the user to fill a free-text date.
    """
    return [
        RequirementOption(label="本月", value="本月"),
        RequirementOption(label="上月", value="上月"),
        RequirementOption(label="本季度", value="本季度"),
        RequirementOption(label="今年", value="今年"),
        RequirementOption(label="最近 30 天", value="最近30天"),
    ]


# --- Scope (region) options -------------------------------------------------

def scope_options() -> list[RequirementOption]:
    """Region options pulled from the dim_store.region table vocabulary (6 大区，
    现役零售 schema 无东北)。Static for now; can be extended to query dim_store
    when a database session is available.
    """
    return [
        RequirementOption(label="华东", value="华东"),
        RequirementOption(label="华北", value="华北"),
        RequirementOption(label="华南", value="华南"),
        RequirementOption(label="华中", value="华中"),
        RequirementOption(label="西南", value="西南"),
        RequirementOption(label="西北", value="西北"),
        RequirementOption(label="全部区域", value="ALL"),
    ]


# --- Metric options --------------------------------------------------------

def metric_options() -> list[RequirementOption]:
    """Metric options. Keep this list aligned with the
    `chart_advisor` / `trend_analysis` tool arguments.
    """
    return [
        RequirementOption(label="销售额", value="销售额"),
        RequirementOption(label="销售量", value="销售量"),
        RequirementOption(label="订单数", value="订单数"),
        RequirementOption(label="退款率", value="退款率"),
        RequirementOption(label="客户数", value="客户数"),
    ]


# --- Granularity / comparison options -------------------------------------

def granularity_options() -> list[RequirementOption]:
    return [
        RequirementOption(label="日", value="day"),
        RequirementOption(label="周", value="week"),
        RequirementOption(label="月", value="month"),
        RequirementOption(label="季度", value="quarter"),
    ]


def comparison_options() -> list[RequirementOption]:
    return [
        RequirementOption(label="同比（去年同周期）", value="yoy"),
        RequirementOption(label="环比（上期）", value="mom"),
        RequirementOption(label="与指定基线对比", value="vs_baseline"),
        RequirementOption(label="不对比", value="none"),
    ]


# --- Field-key → options dispatch ------------------------------------------

_OPTIONS_BY_KEY: dict[RequirementFieldKey, callable] = {
    "time_range": time_options,
    "scope": scope_options,
    "metric": metric_options,
    "granularity": granularity_options,
    "comparison": comparison_options,
}


def options_for(key: RequirementFieldKey) -> list[RequirementOption]:
    """Return the controlled options for one field key. Falls back to an
    empty list if the key is unknown (caller should treat that as an
    internal error and log it).
    """
    fn = _OPTIONS_BY_KEY.get(key)
    if fn is None:
        return []
    return fn()


# --- Missing-field builder -------------------------------------------------

def build_missing_field(
    *,
    key: RequirementFieldKey,
    label: str | None = None,
) -> RequirementMissingField:
    """Build a single `RequirementMissingField` with controlled options."""
    options = options_for(key)
    default_label = {
        "time_range": "时间范围",
        "scope": "范围",
        "metric": "指标",
        "granularity": "粒度",
        "comparison": "对比",
    }.get(key, key)
    return RequirementMissingField(
        key=key,
        label=label or default_label,
        kind="single" if key in {"time_range", "granularity", "comparison"} else "multiple",
        options=options,
    )


# --- Assumption builder ----------------------------------------------------

def build_assumption(
    *,
    key: str,
    text: str,
    alternatives: list[RequirementOption] | None = None,
) -> RequirementAssumption:
    """Build a `RequirementAssumption` with `accepted=None` (pending user)."""
    return RequirementAssumption(
        key=key,
        text=text,
        accepted=None,
        alternatives=alternatives or [],
    )
