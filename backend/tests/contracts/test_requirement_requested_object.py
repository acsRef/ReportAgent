"""P15 e2e T3：点名对象缺失 → requested_object assumption 澄清，不静默替换。

产品修复是 prompt 规则（LLM 遵守），此处钉两层确定性底座：
 1. parse prompt 必须包含「点名对象缺失 → requested_object assumption，禁止静默替换」指令
    （防止未来某次 prompt 重构把规则删了，回归静默软化）。
 2. parser 机制：LLM 产出 requested_object assumption（accepted=None）→ 卡 status=missing
    且 assumption 保留、summary 不丢原对象——「已满足/complete」不会在无澄清时发生。
"""
from __future__ import annotations

import json

import pytest

from app.agent.prompts import build_requirement_parse_prompt
from app.agent.requirement_parser import parse_requirement

pytestmark = pytest.mark.contracts


def test_parse_prompt_contains_no_silent_replace_rule():
    prompt = build_requirement_parse_prompt(
        user_query="查询 unicorn_data 表", schema_text="无可用表结构", dictionary_block="",
    )
    assert "requested_object:" in prompt, "prompt 必须含 requested_object assumption 规则"
    assert "禁止" in prompt and "静默" in prompt, "prompt 必须显式禁止静默替换"


def test_requested_object_assumption_keeps_card_missing(monkeypatch):
    out = {
        "summary": "查询 unicorn_data 表的所有数据", "target_metrics": [],
        "time_range": None, "scope": [], "dimensions": [], "analysis_methods": [],
        "confidence": 0.6, "missing_fields": [],
        "assumptions": [
            {
                "key": "requested_object:unicorn_data",
                "text": "用户点名的「unicorn_data」不在当前可用表结构中；请确认改为可用对象",
                "alternatives": [{"label": "fact_orders", "value": "fact_orders"}],
                "accepted": None,
            }
        ],
    }
    monkeypatch.setattr(
        "app.agent.requirement_parser.call_llm",
        lambda prompt, max_tokens=0: json.dumps(out, ensure_ascii=False),
    )
    card = parse_requirement(user_query="查询 unicorn_data 表的所有数据", schema_context=None)
    assert card.status == "missing", "未消歧的 requested_object assumption 必须使卡停在 missing"
    assert any(a.key.startswith("requested_object:") for a in card.assumptions), "assumption 应保留"
    assert any("unicorn_data" in a.key for a in card.assumptions)
    assert "unicorn_data" in card.summary, "summary 不应丢掉用户点名对象"
