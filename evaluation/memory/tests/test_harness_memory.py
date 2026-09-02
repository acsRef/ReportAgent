"""memory 子包 dispatcher 注册测试（D2 deferred 占位；P14b 实装）。"""
from __future__ import annotations

import pytest

from evaluation.checker import check_turn, ObservedTurn, DIM_REGISTRY
from evaluation.memory.harness import assert_memory


def test_memory_registered():
    assert DIM_REGISTRY.get("memory") is assert_memory


def test_memory_p14a_returns_deferred_keys():
    """D2 边界：placeholder 返回 (sections=[], deferred_keys=list(exp.keys()))。

    与 frontend/e2e 真 no-op 区分——memory 是「schema 已定、observation 暂不可用」，
    deferred 列表里每个 expected key 都出现，dim_results[memory]['deferred'] 会等于期望数。
    """
    obs = ObservedTurn()
    sec, deferred = assert_memory(obs, {"recalled": True, "types_any_of": ["conversation"]})
    assert sec == {}
    assert set(deferred) == {"recalled", "types_any_of"}


def test_dispatch_through_check_turn_defers_memory_keys():
    """通过 dispatcher 跑 memory exp → sections 不写值，deferred 加 prefix 包装 memory.*。"""
    obs = ObservedTurn()
    sec, deferred = check_turn(obs, {"memory": {"recalled": True, "types_any_of": ["session"]}})
    # sections 不写 memory.*（placeholder sections=[]）
    assert not any(k.startswith("memory.") for k in sec.keys())
    # deferred 列表带 'memory.' prefix
    assert "memory.recalled" in deferred
    assert "memory.types_any_of" in deferred
