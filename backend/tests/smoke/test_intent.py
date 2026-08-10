"""工作流式意图分类测试。"""
from __future__ import annotations

import pytest

from app.agent.intent import IntentKind, classify_intent

pytestmark = pytest.mark.smoke


def test_chitchat_keyword():
    assert classify_intent("你好").kind == IntentKind.CHITCHAT
    assert classify_intent("你能做什么").kind == IntentKind.CHITCHAT


def test_interface_strong_keyword():
    assert classify_intent("订单接口的字段").kind == IntentKind.INTERFACE
    assert classify_intent("websocket 推送格式").kind == IntentKind.INTERFACE
    assert classify_intent("长连接如何接入").kind == IntentKind.INTERFACE


def test_report_when_dict_hit():
    # 字段从推送源来（stream）但用户是字段澄清查询 → 应走 REPORT，不误判成接口意图
    assert classify_intent("total_amount 是什么", dict_hit=True).kind == IntentKind.REPORT
    assert classify_intent("各区域销售额", dict_hit=True).kind == IntentKind.REPORT


def test_report_weak_push_word_not_interface():
    # "推送"在报表语境常见（订单推送的数据做报表），不应硬判 INTERFACE
    assert classify_intent("统计订单推送的 amt 总额").kind != IntentKind.INTERFACE


def test_unknown_empty():
    assert classify_intent("").kind == IntentKind.UNKNOWN


def test_llm_fallback_for_ambiguous(monkeypatch):
    """无字典命中 + 无关键词 → LLM 兜底分类。"""
    from app.agent import intent
    monkeypatch.setattr(intent, "call_llm",
                        lambda prompt, **kw: '{"kind": "chitchat", "confidence": 0.9, "reason": "test"}')
    assert classify_intent("帮我写首诗").kind == IntentKind.CHITCHAT

    monkeypatch.setattr(intent, "call_llm",
                        lambda prompt, **kw: '{"kind": "interface", "confidence": 0.8, "reason": "test"}')
    assert classify_intent("把订单实时推给我").kind == IntentKind.INTERFACE


def test_llm_fallback_default_report_on_bad_output(monkeypatch):
    from app.agent import intent
    monkeypatch.setattr(intent, "call_llm",
                        lambda prompt, **kw: 'not json at all')
    assert classify_intent("帮我查一下数据").kind == IntentKind.REPORT