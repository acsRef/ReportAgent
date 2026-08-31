from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.contracts

from app.llm.mock import (
    MockLLMAdapter,
    set_mock_session_scope,
    reset_mock_session_scope,
)


def _write_fixture(tmp_path: Path, case: str, mapping: dict) -> None:
    (tmp_path / f"{case}.json").write_text(json.dumps(mapping), encoding="utf-8")


_PROMPT = "你是 ReportAgent SQL 生成专家。" + " SELECT ..."


def test_mock_llm_session_scope_isolates_counters(tmp_path: Path) -> None:
    """两个 session scope 各自 seq 从 1 起，互不干扰。

    review-prep-r2 Fix 1：fixture cursor 必须按 session scope 隔离，否则同一
    backend process 内多 session 共享 cursor 会导致后续 session fixture miss。
    """
    _write_fixture(
        tmp_path,
        "multi-session",
        {
            "sql_generate:1": {"sql": "SELECT 1"},
            "sql_generate:2": {"sql": "SELECT 2"},
        },
    )
    adapter = MockLLMAdapter(tmp_path, "multi-session")

    token_a = set_mock_session_scope("user:1:session:A")
    try:
        assert adapter.generate(_PROMPT) == {"sql": "SELECT 1"}
        assert adapter.generate(_PROMPT) == {"sql": "SELECT 2"}
    finally:
        reset_mock_session_scope(token_a)

    token_b = set_mock_session_scope("user:1:session:B")
    try:
        # session B 第一次 sql_generate 也应从 :1 起（不被 A 的 :2 污染）
        assert adapter.generate(_PROMPT) == {"sql": "SELECT 1"}
    finally:
        reset_mock_session_scope(token_b)


def test_mock_llm_default_scope_used_when_context_unset(tmp_path: Path) -> None:
    """contextvar 未显式 set → 落 '__default__' scope，向后兼容。

    旧行为（r2 之前）：单 cursor dict，无 scope 概念。r2 改造后默认值
    '__default__' 保证不主动 set 的 caller 仍可工作。
    """
    _write_fixture(tmp_path, "default-scope", {"sql_generate:1": "SELECT default"})
    adapter = MockLLMAdapter(tmp_path, "default-scope")

    # 不 set scope → 落默认
    assert adapter.generate(_PROMPT) == "SELECT default"


def test_mock_llm_session_scope_reset_restores_default(tmp_path: Path) -> None:
    """reset(token) 后回到默认 scope（不同 session 同 fixture 不共享 counter）。

    reset 后再 set 不同 scope 验证独立 + 互不影响。
    """
    _write_fixture(tmp_path, "reset-scope", {"sql_generate:1": "SELECT first"})
    adapter = MockLLMAdapter(tmp_path, "reset-scope")

    token_x = set_mock_session_scope("user:1:session:X")
    try:
        assert adapter.generate(_PROMPT) == "SELECT first"
        # session X 第二次 → fixture miss（cursor 已 :1）
        with pytest.raises(Exception, match="no fixture"):
            adapter.generate(_PROMPT)
    finally:
        reset_mock_session_scope(token_x)

    # reset 后回到默认 scope → 默认 scope 内 cursor 独立（与 X 不共享）
    assert adapter.generate(_PROMPT) == "SELECT first"


def test_mock_llm_same_scope_seq_increments_for_repair(tmp_path: Path) -> None:
    """同 scope 内 seq 仍按 kind:1 → :2 递增（repair 路径不被 r2 破坏）。

    关键回归测试：spec 03 retry 的 sql_generate:1 → :2 必须在同 session
    scope 内递增，不能因为 scope 隔离而让每次都从 :1 起。
    """
    _write_fixture(
        tmp_path,
        "repair",
        {
            "sql_generate:1": "SELECT bad_sql",
            "sql_generate:2": "SELECT good_sql",
        },
    )
    adapter = MockLLMAdapter(tmp_path, "repair")

    token = set_mock_session_scope("user:1:session:REPAIR")
    try:
        assert adapter.generate(_PROMPT) == "SELECT bad_sql"
        assert adapter.generate(_PROMPT) == "SELECT good_sql"
        # 第 3 次越界 → 明确失败
        with pytest.raises(Exception, match="no fixture"):
            adapter.generate(_PROMPT)
    finally:
        reset_mock_session_scope(token)
