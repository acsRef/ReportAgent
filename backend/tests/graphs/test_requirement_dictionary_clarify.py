"""dictionary_context 注入 + field_meaning 澄清规则：prompt 契约与 assumption 透传。

B4 任务目标：
1. 当上层（dictionary_context 来自 RAG 桥）传入字典片段时，必须进入 LLM prompt；
2. LLM 输出的 `field_meaning:<field>` assumption 必须原样透传到 RequirementCard；
3. 未提供 dictionary_context 时 prompt 必须不出现该占位（no-op）。
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.graphs


def _patch_llm(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    """Force `app.llm.call_llm` to return a fixed JSON string."""
    import app.agent.requirement_parser as parser_mod
    import app.llm as llm_mod

    text = json.dumps(payload, ensure_ascii=False)
    monkeypatch.setattr(parser_mod, "call_llm", lambda *a, **k: text)
    # Also patch the module-level import in case anything reaches in directly.
    monkeypatch.setattr(llm_mod, "call_llm", lambda *a, **k: text)


def test_dictionary_context_injected_into_prompt(monkeypatch) -> None:
    import app.agent.requirement_parser as rp

    captured: dict = {}

    def fake_llm(prompt: str, **kwargs) -> str:
        captured["prompt"] = prompt
        return json.dumps({
            "summary": "查询销售额",
            "target_metrics": ["销售额"],
            "time_range": "今年",
            "scope": [],
            "dimensions": ["时间"],
            "analysis_methods": ["trend_analysis"],
            "confidence": 0.9,
            "missing_fields": [],
            "assumptions": [],
        }, ensure_ascii=False)

    monkeypatch.setattr(rp, "call_llm", fake_llm)

    rp.parse_requirement(
        user_query="统计订单推送的 amt 字段总额",
        schema_context=None,
        dictionary_context="- dict-api_orders-push.md: amt = 实付金额（元）",
    )

    prompt = captured["prompt"]
    # 数据字典参考块必须在 prompt 中
    assert "【数据字典参考】" in prompt
    assert "amt = 实付金额" in prompt
    # 字段释义规则必须在 prompt 中（field_meaning 关键词提示 LLM 用此 key）
    assert "field_meaning" in prompt


def test_field_meaning_assumption_passthrough(monkeypatch) -> None:
    """LLM 在字典无释义/歧义时输出 field_meaning:<field> assumption，
    parser 必须原样保留 key/text/alternatives；accepted=None 让 gate 拦截。"""
    import app.agent.requirement_parser as rp

    _patch_llm(monkeypatch, {
        "summary": "查询金额",
        "target_metrics": ["金额"],
        "time_range": None,
        "scope": [],
        "dimensions": [],
        "analysis_methods": [],
        "confidence": 0.6,
        "missing_fields": ["time_range"],
        "assumptions": [
            {
                "key": "field_meaning:amt",
                "text": "字段 amt 推测为实付金额（元），请确认",
                "alternatives": [
                    {"label": "应付金额", "value": "应付金额（元）"},
                ],
            },
        ],
    })

    card = rp.parse_requirement(user_query="amt 总额", schema_context=None)

    keys = [a.key for a in card.assumptions]
    assert "field_meaning:amt" in keys
    target = next(a for a in card.assumptions if a.key == "field_meaning:amt")
    assert target.accepted is None  # 待用户确认 → gate 拦截
    assert "amt" in target.text
    assert len(target.alternatives) == 1
    assert target.alternatives[0].value == "应付金额（元）"
    # field_meaning assumption 不应让 status 变 complete（unresolved → missing）
    assert card.status == "missing"


def test_no_dictionary_context_is_noop(monkeypatch) -> None:
    """未传 dictionary_context 时，prompt 必须不出现「数据字典参考」块。"""
    import app.agent.requirement_parser as rp

    captured: dict = {}

    def fake_llm(prompt: str, **kwargs) -> str:
        captured["prompt"] = prompt
        return json.dumps({
            "summary": "s",
            "target_metrics": [],
            "time_range": None,
            "scope": [],
            "dimensions": [],
            "analysis_methods": [],
            "confidence": 0.5,
            "missing_fields": [],
            "assumptions": [],
        }, ensure_ascii=False)

    monkeypatch.setattr(rp, "call_llm", fake_llm)

    rp.parse_requirement(user_query="今年销售额", schema_context=None)
    assert "【数据字典参考】" not in captured["prompt"]