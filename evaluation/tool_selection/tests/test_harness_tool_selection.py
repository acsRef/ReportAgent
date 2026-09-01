"""tool_selection 子包 dispatcher 注册测试（D2 deferred 占位）。"""
from __future__ import annotations

import pytest

from evaluation.checker import check_turn, ObservedTurn, DIM_REGISTRY
from evaluation.tool_selection.harness import assert_tool_selection


def test_tool_selection_registered():
    assert DIM_REGISTRY.get("tool_selection") is assert_tool_selection


def test_tool_selection_p14a_returns_deferred_keys():
    """D2 deferred：empty exp 返回空 deferred；非空 exp 返回 keys 列表。"""
    obs = ObservedTurn()
    # empty exp 边界（list({}) = []）
    sec, deferred = assert_tool_selection(obs, {})
    assert sec == {}
    assert deferred == []
    # 非空 exp → deferred = list(exp.keys())
    sec2, deferred2 = assert_tool_selection(obs, {"tool_chosen": "search_schema"})
    assert sec2 == {}
    assert deferred2 == ["tool_chosen"]


def test_dispatch_through_check_turn_defers_tool_selection_keys():
    obs = ObservedTurn()
    sec, deferred = check_turn(obs, {"tool_selection": {"tool_chosen": "search_schema"}})
    assert not any(k.startswith("tool_selection.") for k in sec.keys())
    assert "tool_selection.tool_chosen" in deferred
