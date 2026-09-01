"""requirement 子包 dispatcher 单测。

注：requirement 在 LEGACY_KEYS 内，本子包函数 Phase 2 dispatcher 不调用（兼容注册位）。
测试直接调 assert_requirement() 函数体，不验证 dispatcher 行为（dispatcher 行为
见 test_dispatcher.py::test_check_turn_phase2_skips_requirement_dim）。
"""
from __future__ import annotations

import pytest

from evaluation.checker import ObservedTurn, check_turn
from evaluation.requirement.harness import assert_requirement


def test_assert_requirement_status_complete_pass():
    obs = ObservedTurn(card_status="complete")
    sec, _ = assert_requirement(obs, {"status": "complete"})
    assert sec == {"status": "pass"}


def test_assert_requirement_status_missing_fail():
    obs = ObservedTurn(card_status="complete")
    sec, _ = assert_requirement(obs, {"status": "missing"})
    assert sec == {"status": "fail"}


def test_assert_requirement_min_missing_fields():
    obs = ObservedTurn(missing_fields_count=3)
    sec, _ = assert_requirement(obs, {"min_missing_fields": 1})
    assert sec["min_missing_fields"] == "pass"


def test_assert_requirement_target_metrics_contains_hit():
    obs = ObservedTurn(target_metrics=["销售额", "订单"])
    sec, _ = assert_requirement(obs, {"target_metrics_contains": ["销售额"]})
    assert sec["target_metrics"] == "pass"


def test_check_turn_phase1_legacy_only_one_section_per_key():
    """走 dispatcher 时 section key 带 dim prefix；requirement 在 LEGACY_KEYS 内只走 Phase 1。"""
    obs = ObservedTurn(card_status="complete", target_metrics=["销售额"])
    sec, _ = check_turn(obs, {"requirement": {"status": "complete", "target_metrics_contains": ["销售额"]}})
    # Phase 1 唯一写入 requirement.* section；Phase 2 dispatcher 跳过 requirement
    # （LEGACY_KEYS 过滤，见 D1 边界）——所以 requirement.* 只出现一次
    requirement_section_keys = [k for k in sec if k.startswith("requirement.")]
    assert "requirement.status" in sec
    assert sec["requirement.status"] == "pass"
    # D1 边界：requirement 不会被 Phase 2 dispatcher 重写——仅一份 section
    assert requirement_section_keys.count("requirement.status") == 1
