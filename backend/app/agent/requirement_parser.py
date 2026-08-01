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

from app.llm import call_llm
from app.models.contracts import SchemaContext
from app.models.requirement import (
    RequirementAssumption,
    RequirementCard,
    RequirementFieldKey,
    RequirementFieldKind,
    RequirementMissingField,
    RequirementStatus,
)
from app.utils.text import safe_json_parse

from app.agent import requirement_options as opts

logger = logging.getLogger(__name__)


# --- LLM call --------------------------------------------------------------


_PARSE_PROMPT = """你是 ReportAgent 需求解析器。给定用户的中文业务问题与可用表结构，
判断每个维度（time / scope / metric / granularity / comparison）是否明确，
并输出结构化 JSON。

用户问题: {user_query}

可用表结构:
{schema_text}

维度判断规则：
- time_range: 是否包含明确的时间范围或可推断的相对时间词（本月/上月/今年/最近30天 等）
- scope: 是否包含明确的区域/产品/客户范围；可缺省（默认 ALL）
- metric: 是否包含明确指标（销售额/订单数/退货率 等）
- granularity: 日/周/月/季度；不指定时默认月
- comparison: 是否要对比（同比/环比/指定基线）；不需要则 none

输出 JSON（禁止解释，禁止 markdown，禁止换行）：
{{
  "summary": "一句话业务目标",
  "target_metrics": ["指标1", "指标2"],
  "time_range": "今年" | null,
  "scope": ["华东"] | [],
  "dimensions": ["时间", "区域"],
  "analysis_methods": ["trend_analysis", "group_compare"],
  "confidence": 0.85,
  "missing_fields": ["time_range", "metric"],
  "assumptions": [
    {{"key": "scope_default", "text": "未指定范围，使用全部区域", "alternatives": [...]}}
  ]
}}
"""


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


def _call_llm_for_parse(
    user_query: str,
    schema: SchemaContext | None,
    conversation_context: str | None = None,
) -> dict:
    """Call the LLM and parse the JSON response. Returns {} on parse failure."""
    prompt = _PARSE_PROMPT.format(user_query=user_query, schema_text=_schema_text(schema))
    # 分层对话上下文前置：让需求解析感知先前轮次（如「再按产品细分」隐含的范围）。
    if conversation_context:
        prompt = f"<对话上下文>\n{conversation_context}\n</对话上下文>\n\n{prompt}"
    raw = call_llm(prompt, max_tokens=1500)  # reasoning model may write a long <think> block first
    logger.warning("parse_requirement LLM raw for user_query=%r:\n%s", user_query, raw[:2000])
    parsed = safe_json_parse(raw)
    logger.warning("parse_requirement parsed: %s", parsed)
    return parsed if isinstance(parsed, dict) else {}


# --- Public entry point ---------------------------------------------------


def parse_requirement(
    *,
    user_query: str,
    schema_context: SchemaContext | None,
    prior_card: RequirementCard | None = None,
    conversation_context: str | None = None,
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
    parsed = _call_llm_for_parse(user_query, schema_context, conversation_context)
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
