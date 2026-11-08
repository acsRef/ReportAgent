"""用户可见错误文案按来源分叉（plan 2026-11-08-top-period-annotate-and-timeout-copy A2）。

病根：`_USER_MESSAGES` 只按 kind 查表，而 `classify_llm_exception` 把模型超时也归成
`kind="timeout"` → 用户拿到的建议是「缩小时间范围或维度」，对模型超时完全白试。
`ErrorCode.LLM_TIMEOUT` 早就在 envelope 里，只是文案层没用它。

本组用例钉住：
1. `LLM_TIMEOUT` / `MCP_TIMEOUT` 有专用文案；
2. 其他一切（不传 code / SQL_TIMEOUT / 未知 code / 未知 kind）文案**逐字不变**（QUERY_* 契约）；
3. `classify_llm_exception` → `user_message` 这条真实组合能跑对。
"""
from __future__ import annotations

import pytest

from app.reliability import errors
from app.reliability.errors import (
    classify_llm_exception,
    normalize_kind,
    user_code,
    user_message,
)

pytestmark = pytest.mark.contracts


class TestCodeOverrides:
    def test_模型超时不再说查询超时(self) -> None:
        message = user_message("timeout", "LLM_TIMEOUT")

        assert "模型" in message
        assert "缩小时间范围" not in message

    def test_检索超时有自己的说法(self) -> None:
        message = user_message("timeout", "MCP_TIMEOUT")

        assert "检索" in message
        assert message != user_message("timeout")

    def test_没有覆盖的_code_走原表(self) -> None:
        assert user_message("timeout", "SQL_TIMEOUT") == "查询超时,请缩小时间范围或维度后重试"
        assert user_message("timeout", "SOMETHING_NEW") == user_message("timeout")
        assert user_message("timeout", None) == "查询超时,请缩小时间范围或维度后重试"
        assert user_message("timeout", "") == user_message("timeout")

    def test_空_code_不抛(self) -> None:
        assert user_message("other", "")


class TestContractUnchanged:
    @pytest.mark.parametrize("kind,expected", [
        ("timeout", "查询超时,请缩小时间范围或维度后重试"),
        ("connection", "数据库连接失败,请稍后重试"),
        ("permission", "权限不足,无法执行该查询"),
        ("syntax", "SQL 语法错误,请调整查询条件后重试"),
        ("object", "查询引用的表/列不存在,请检查维度后重试"),
        ("object_not_found", "查询引用的表或字段不存在,请检查维度后重试"),
        ("object_ambiguous", "字段名存在歧义,请明确指定要查询的列后重试"),
        ("other", "查询执行失败,请稍后重试或调整需求"),
        ("weird", "查询执行失败,请稍后重试或调整需求"),
    ])
    def test_kind_文案逐字不变(self, kind: str, expected: str) -> None:
        assert user_message(kind) == expected

    def test_用户码不受影响(self) -> None:
        assert user_code("timeout") == "QUERY_TIMEOUT"
        assert user_code("object_ambiguous") == "QUERY_OBJECT"


class TestRealCombination:
    def test_llm_超时异常走专用文案(self) -> None:
        from app.reliability.retry import LLMTimeoutError

        envelope = classify_llm_exception(LLMTimeoutError("read timed out"))
        message = user_message(envelope.kind, envelope.code)

        assert envelope.code == "LLM_TIMEOUT"
        assert envelope.kind == "timeout"
        assert "模型" in message

    def test_sql_超时仍走原文案(self) -> None:
        envelope = errors.classify_sql_kind("timeout", "canceling statement due to timeout")
        message = user_message(envelope.kind, envelope.code)

        assert envelope.code == "SQL_TIMEOUT"
        assert "缩小时间范围" in message

    def test_覆盖表每个_code_都说得通(self) -> None:
        for code, message in errors._CODE_MESSAGES.items():
            assert code.isupper()
            assert message and "缩小时间范围" not in message


def test_normalize_kind_未被改动() -> None:
    assert normalize_kind(None) == "other"
    assert normalize_kind("timeout") == "timeout"
