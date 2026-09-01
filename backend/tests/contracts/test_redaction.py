from __future__ import annotations

import pytest

pytestmark = pytest.mark.contracts

from app.observability.redaction import redact, redact_user_query


def test_redact_mask_phone_in_string():
    """字符串中的手机号被 mask。"""
    out = redact("联系电话 13800138000")
    assert "13800138000" not in out
    assert "[PHONE]" in out or "***" in out


def test_redact_recursive_mask_dict_and_list():
    """递归 mask dict/list/str 嵌套。"""
    out = redact({
        "user_query": "我的邮箱 test@example.com",
        "spans": [{"name": "intent", "input": "电话 13900139000"}],
        "metadata": "id_card 110101199001011234",
    })
    assert "test@example.com" not in out["user_query"]
    assert "13900139000" not in out["spans"][0]["input"]
    assert "110101199001011234" not in out["metadata"]


def test_redact_non_string_passthrough():
    """非 str / dict / list 透传（int / float / bool / None）。"""
    assert redact(42) == 42
    assert redact(3.14) == 3.14
    assert redact(True) is True
    assert redact(None) is None


def test_redact_user_query_mask():
    """redact_user_query 直接 mask user_query 字符串。"""
    out = redact_user_query("id_card 110101199001011234")
    assert "110101199001011234" not in out


def test_redact_user_query_empty():
    """redact_user_query 空串 / None → 返回空串。"""
    assert redact_user_query("") == ""
    assert redact_user_query(None) == ""