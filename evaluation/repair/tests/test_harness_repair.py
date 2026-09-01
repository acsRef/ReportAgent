"""repair 子包 dispatcher 注册测试（D2 deferred 占位）。"""
from __future__ import annotations

import pytest

from evaluation.checker import check_turn, ObservedTurn, DIM_REGISTRY
from evaluation.repair.harness import assert_repair


def test_repair_registered():
    assert DIM_REGISTRY.get("repair") is assert_repair


def test_repair_p14a_returns_deferred_keys():
    """D2 deferred：(sections=[], deferred_keys=list(exp.keys()))。"""
    obs = ObservedTurn()
    sec, deferred = assert_repair(obs, {"used": True, "retries_max": 2, "succeeded_within_budget": True})
    assert sec == {}
    assert set(deferred) == {"used", "retries_max", "succeeded_within_budget"}


def test_dispatch_through_check_turn_defers_repair_keys():
    obs = ObservedTurn()
    sec, deferred = check_turn(obs, {"repair": {"used": True}})
    assert not any(k.startswith("repair.") for k in sec.keys())
    assert "repair.used" in deferred
