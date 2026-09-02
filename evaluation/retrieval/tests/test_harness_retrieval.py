"""retrieval 子包 dispatcher 注册测试（D2 deferred 占位）。"""
from __future__ import annotations

import pytest

from evaluation.checker import check_turn, ObservedTurn, DIM_REGISTRY
from evaluation.retrieval.harness import assert_retrieval


def test_retrieval_registered():
    assert DIM_REGISTRY.get("retrieval") is assert_retrieval


def test_retrieval_p14a_returns_deferred_keys():
    """D2 deferred：placeholder 返回 (sections=[], deferred_keys=list(exp.keys()))。"""
    obs = ObservedTurn()
    sec, deferred = assert_retrieval(obs, {"recalled": True, "k_min": 1})
    assert sec == {}
    assert set(deferred) == {"recalled", "k_min"}


def test_dispatch_through_check_turn_defers_retrieval_keys():
    obs = ObservedTurn()
    sec, deferred = check_turn(obs, {"retrieval": {"recalled": True}})
    assert not any(k.startswith("retrieval.") for k in sec.keys())
    assert "retrieval.recalled" in deferred
