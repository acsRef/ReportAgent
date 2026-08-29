"""LLM-driven requirement parser.

`parse_requirement` takes a Chinese natural-language query plus the
available `SchemaContext` and produces a `RequirementCard`. The LLM is
asked only to identify which dimensions are present / missing /
ambiguous; the option labels themselves are server-side controlled
(see `requirement_options.py`).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from app.context import format_context_block
from app.llm import call_llm
from app.utils.text import safe_json_parse
from app.models.contracts import SchemaContext
from app.models.requirement import (
    RequirementAssumption,
    RequirementCard,
    RequirementFieldKey,
    RequirementFieldKind,
    RequirementMissingField,
    RequirementStatus,
)
from app.agent import requirement_options as opts
from app.agent.prompts import build_requirement_parse_prompt

logger = logging.getLogger(__name__)


# --- LLM call --------------------------------------------------------------


# C-7: prompt 里的 schema 文本必须有界。表/列描述随业务增长可膨胀到几十 K，
# 直接挤爆 LLM 上下文窗口。单表描述截到 160 字，整体再套一个硬上限。
MAX_SCHEMA_CHARS = 8000
_MAX_TABLE_DESC = 160


def _schema_text(ctx: SchemaContext | None) -> str:
    if not ctx or not ctx.tables:
        return "无可用表结构"
    lines = [
        f"- {t.name}: {(t.description or '')[:_MAX_TABLE_DESC]}"
        for t in ctx.tables
    ]
    text = "\n".join(lines)
    if len(text) > MAX_SCHEMA_CHARS:
        text = text[:MAX_SCHEMA_CHARS] + "\n...（schema 已截断）"
    return text


# C-7 同款边界：字典片段来自外部 RAG，长度不受信任。
# 超过此上限的尾部会被截断，避免撑爆 LLM 上下文窗口。
MAX_DICTIONARY_CHARS = 4000


def _call_llm_for_parse(
    user_query: str,
    schema: SchemaContext | None,
    conversation_context: str | None = None,
    dictionary_context: str | None = None,
    assembled_context: str | None = None,  # P4c: 优先于 conversation_context，含 selective recall
) -> dict:
    """Call the LLM and parse the JSON response. Returns {} on parse failure."""
    dictionary_block = ""
    if dictionary_context:
        bounded = dictionary_context[:MAX_DICTIONARY_CHARS]
        dictionary_block = f"【数据字典参考】\n{bounded}"
    prompt = build_requirement_parse_prompt(
        user_query=user_query,
        schema_text=_schema_text(schema),
        dictionary_block=dictionary_block,
    )
    # 分层对话上下文前置：让需求解析感知先前轮次（如「再按产品细分」隐含的范围）。
    # P4c 优先用 assembled_context（含 recall + conversation 全景），否则 fallback 到 conversation_context。
    injected = assembled_context or conversation_context
    if injected:
        prompt = f"{format_context_block(injected)}\n\n{prompt}"
    try:
        raw = call_llm(prompt, max_tokens=1500)
    except Exception as exc:
        logger.warning("parse_requirement LLM failed: %s query=%r", exc, user_query)
        return {}
    parsed = safe_json_parse(raw) if isinstance(raw, str) else raw
    logger.warning("parse_requirement parsed: %s", parsed)
    return parsed if isinstance(parsed, dict) else {}


# --- Public entry point ---------------------------------------------------


def parse_requirement(
    *,
    user_query: str,
    schema_context: SchemaContext | None,
    prior_card: RequirementCard | None = None,
    conversation_context: str | None = None,
    dictionary_context: str | None = None,
    assembled_context: str | None = None,  # P4c: 由 Requirement Agent 传（ContextBundle.assembled_context）
) -> RequirementCard:
    """Parse a user query into a RequirementCard.

    Strategy:
    1. Call LLM to get a structured analysis (summary, metrics, missing
       fields, assumptions, confidence).
    2. For each missing field, build a server-side controlled
       `RequirementMissingField` with the canonical options.
    3. For each assumption, build a `RequirementAssumption` with
       `accepted=None`.
    4. If `prior_card` is supplied, merge the new analysis on top of it
       (for `mode=supplement`). For now, the new analysis wins.
    5. If there are no missing fields and assumptions are all resolved,
       status='complete'; else 'missing'.
    """
    parsed = _call_llm_for_parse(
        user_query, schema_context,
        conversation_context, dictionary_context,
        assembled_context,
    )
    if not parsed:
        # LLM gave nothing usable; fall back to a 'missing' card with all
        # dimensions empty so the frontend can render a complete form.
        return _fallback_missing_card(user_query)

    missing_keys: list[RequirementFieldKey] = []
    raw_missing = parsed.get("missing_fields") or []
    for k in raw_missing:
        if k in ("time_range", "scope", "metric", "granularity", "comparison"):
            missing_keys.append(k)  # type: ignore[arg-type]

    missing_fields: list[RequirementMissingField] = [
        opts.build_missing_field(key=k) for k in missing_keys
    ]

    assumptions: list[RequirementAssumption] = []
    for raw in parsed.get("assumptions") or []:
        if not isinstance(raw, dict):
            continue
        alts: list = []
        for opt in raw.get("alternatives") or []:
            if isinstance(opt, dict) and "label" in opt and "value" in opt:
                from app.models.requirement import RequirementOption
                alts.append(RequirementOption(
                    label=str(opt["label"])[:60],
                    value=str(opt["value"])[:60],
                ))
        # Trust the LLM's explicit `accepted` flag (True/False). If the
        # LLM omits it or sends null, leave the assumption unresolved
        # so the frontend prompts the user.
        raw_accepted = raw.get("accepted", None)
        if raw_accepted is True:
            accepted: bool | None = True
        elif raw_accepted is False:
            accepted = False
        else:
            accepted = None
        assumptions.append(RequirementAssumption(
            key=str(raw.get("key", "assumption"))[:64],
            text=str(raw.get("text", ""))[:300],
            accepted=accepted,
            alternatives=alts,
        ))

    target_metrics = [str(m)[:64] for m in parsed.get("target_metrics") or []]
    scope = [str(s)[:64] for s in parsed.get("scope") or []]
    dimensions = [str(d)[:64] for d in parsed.get("dimensions") or []]
    analysis_methods = [str(a)[:64] for a in parsed.get("analysis_methods") or []]

    confidence_raw = parsed.get("confidence", 0.0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    summary = str(parsed.get("summary") or user_query)[:300]
    time_range = parsed.get("time_range")
    if time_range is not None:
        time_range = str(time_range)[:64]

    # status: complete requires NO missing fields AND all assumptions
    # explicitly accepted. If the LLM left assumptions unresolved
    # (accepted=None), the frontend must prompt the user to accept/reject
    # them first, so the card stays in 'missing' state from the
    # server's perspective.
    unresolved = [a for a in assumptions if a.accepted is None]
    if missing_fields or unresolved:
        status: RequirementStatus = "missing"
    else:
        status = "complete"

    card = RequirementCard(
        id=f"draft-{int(datetime.now().timestamp() * 1000)}",
        version=(prior_card.version if prior_card else 0) + 1,
        status=status,
        summary=summary,
        target_metrics=target_metrics,
        time_range=time_range,
        scope=scope,
        dimensions=dimensions,
        analysis_methods=analysis_methods,
        expected_blocks=[],
        missing_fields=missing_fields,
        assumptions=assumptions,
        confidence=confidence,
        confirmed_at=None,
    )
    return card


def _fallback_missing_card(user_query: str) -> RequirementCard:
    """When the LLM is unavailable or its output cannot be parsed, return a
    card that asks the user to fill in EVERY dimension. This is a safer
    default than guessing.
    """
    all_keys: list[RequirementFieldKey] = [
        "time_range", "scope", "metric", "granularity", "comparison",
    ]
    return RequirementCard(
        id=f"draft-{int(datetime.now().timestamp() * 1000)}",
        version=1,
        status="missing",
        summary=user_query[:300],
        missing_fields=[opts.build_missing_field(key=k) for k in all_keys],
        assumptions=[],
        confidence=0.0,
        confirmed_at=None,
    )
