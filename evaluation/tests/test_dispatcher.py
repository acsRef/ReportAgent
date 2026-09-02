"""Dispatcher 注册与 dispatch 行为测试。

D1 边界：requirement / execution / report / behavior 走 Phase 1 legacy；
         memory / retrieval / tool_selection / sql / repair / frontend / e2e 走 Phase 2 active dispatch。
"""
from __future__ import annotations

import pytest


def test_dim_registry_contains_expected_dims():
    """DIM_REGISTRY 9 keys 必须全部注册（含 requirement / report 等兼容注册位）。"""
    from evaluation.checker import DIM_REGISTRY

    expected = {
        "requirement", "memory", "retrieval",
        "tool_selection", "sql", "repair", "report",
        "frontend", "e2e",
    }
    missing = expected - set(DIM_REGISTRY.keys())
    assert not missing, f"missing dims: {missing}"


def test_check_turn_phase1_legacy_compat_requirement():
    """Phase 1 legacy 唯一负责 requirement 段——section key 带 requirement. 前缀。"""
    from evaluation.checker import check_turn, ObservedTurn

    obs = ObservedTurn(
        card_status="complete",
        target_metrics=["销售额", "订单"],
        time_range="2024年",
    )
    sec, _ = check_turn(obs, {
        "requirement": {
            "status": "complete",
            "target_metrics_contains": ["销售额"],
            "time_range_equals": "2024年",
        }
    })
    assert sec["requirement.status"] == "pass"
    assert sec["requirement.target_metrics"] == "pass"
    assert sec["requirement.time_range_equals"] == "pass"


def test_check_turn_phase2_dispatch_to_sql_dim():
    """Phase 2 active dispatch：sql dim 不在 LEGACY_KEYS，走 assert_sql harness。

    sql 子包实装（D2 实装分支），dispatch 后 section key 带 `sql.` 前缀。
    """
    from evaluation.checker import check_turn, ObservedTurn

    obs = ObservedTurn(sql="SELECT 1", row_count=10)
    sec, _ = check_turn(obs, {"sql": {"sql_nonempty": True, "rows_gt": 5}})
    assert "sql.sql_nonempty" in sec
    assert sec["sql.sql_nonempty"] == "pass"
    assert "sql.rows_gt" in sec
    assert sec["sql.rows_gt"] == "pass"


def test_check_turn_phase2_dispatch_to_memory_dim_deferred():
    """Phase 2 dispatch：memory dim deferred（D2 占位：sections 空、deferred 包含 expected keys）。"""
    from evaluation.checker import check_turn, ObservedTurn

    obs = ObservedTurn()
    sec, deferred = check_turn(obs, {"memory": {"recalled": True, "types_any_of": ["conversation"]}})
    # memory 子包 placeholder 返回 ( {}, list(exp.keys()) )
    # dispatcher 加 'memory.' 前缀到 deferred keys
    assert not any(k.startswith("memory.") for k in sec.keys()), (
        "memory placeholder 不应写 sections"
    )
    assert "memory.recalled" in deferred
    assert "memory.types_any_of" in deferred


def test_check_turn_phase2_skips_requirement_dim():
    """D1 边界：requirement 在 LEGACY_KEYS 内，Phase 2 dispatcher 跳过——只通过 Phase 1 legacy 处理。

    即使用户调用 check_turn 时显式传 requirement 子包函数也只跑一次（Phase 1），不会重复 dispatch。
    """
    from evaluation.checker import check_turn, ObservedTurn

    obs = ObservedTurn(card_status="complete", target_metrics=["销售额"])
    sec, _ = check_turn(obs, {"requirement": {"status": "complete"}})
    # 仅一项 section（Phase 1 legacy 写入）
    requirement_keys = [k for k in sec if k.startswith("requirement.")]
    assert len(requirement_keys) == 1
    assert "requirement.status" in sec


def test_frontend_noop_returns_empty_not_deferred():
    """frontend 子包真 no-op：无 expectation schema → 不计 deferred（dim_results[frontend] 全 0）。"""
    from evaluation.checker import check_turn, ObservedTurn

    obs = ObservedTurn()
    sec, deferred = check_turn(obs, {"frontend": {"any_key": True}})
    assert not any(k.startswith("frontend.") for k in sec.keys())
    assert not any(k.startswith("frontend.") for k in deferred)
