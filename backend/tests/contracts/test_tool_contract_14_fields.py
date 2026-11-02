from __future__ import annotations

import pytest

pytestmark = pytest.mark.contracts

from app.tools import register_all_tools
from app.tools.registry import ToolContractError, registry


@pytest.fixture
def registered():
    snap_tools = dict(registry._tools)
    snap_instances = dict(registry._instances)
    try:
        registry._tools.clear()
        registry._instances.clear()
        register_all_tools()
        yield
    finally:
        registry._tools.clear()
        registry._tools.update(snap_tools)
        registry._instances.clear()
        registry._instances.update(snap_instances)


def test_description_four_questions(registered):
    """Tool Metadata 契约：description 必须回答四问（§7 的唯一钉子）。"""
    for name, meta in registry.all_tools().items():
        assert "什么时候调" in meta.description, f"{name}: missing 什么时候调"
        assert "什么时候不调" in meta.description, f"{name}: missing 什么时候不调"
        assert "调用前" in meta.description, f"{name}: missing 调用前"
        assert "调用后" in meta.description, f"{name}: missing 调用后"


def test_all_registered_tools_metadata_complete(registered):
    """每个注册工具 14 字段齐全 + 权限别名同步 + examples 非空 + 高风险必有权限。"""
    for name, meta in registry.all_tools().items():
        assert meta.name, f"{name}: name empty"
        assert meta.purpose, f"{name}: purpose empty"
        assert meta.when_to_use, f"{name}: when_to_use empty"
        assert meta.when_not_to_use, f"{name}: when_not_to_use empty"
        assert isinstance(meta.input_schema, dict), f"{name}: input_schema not dict"
        assert isinstance(meta.output_schema, dict), f"{name}: output_schema not dict"
        assert meta.preconditions, f"{name}: preconditions empty"
        assert meta.postconditions, f"{name}: postconditions empty"
        assert meta.failure_policy, f"{name}: failure_policy empty"
        assert meta.side_effects, f"{name}: side_effects empty"
        assert meta.examples, f"{name}: examples empty"
        assert meta.risk_level in {"low", "medium", "high"}, f"{name}: risk_level invalid"
        assert meta.source in {"local", "mcp"}, f"{name}: source invalid"
        assert isinstance(meta.permission, list), f"{name}: permission not list"
        assert isinstance(meta.permission_required, list), f"{name}: permission_required not list"

        # permission 是 permission_required 的兼容别名，两者必须同步
        assert meta.permission == meta.permission_required, f"{name}: permission alias not synced"

        # examples 是给人看的示例，空串等于没写
        for ex in meta.examples:
            assert isinstance(ex, str) and ex.strip(), f"{name}: example empty string"

        # 有牙齿的一条：高风险工具必须声明权限（当前只命中 execute_sql）
        if meta.risk_level == "high":
            assert meta.permission or meta.permission_required, (
                f"{name}: high risk must have permission"
            )


def test_register_all_tools_fails_fast_on_incomplete_metadata(monkeypatch, registered):
    """注册结束即校验：Metadata 缺失必须**启动时抛错**，不能只靠测试兜。

    2026-11-02 之前 `registry.validate()` 没有任何生产 caller——新增工具漏写
    `preconditions` 之类会静默注册成功，Agent 拿不到边界信息也可能乱调。
    """
    monkeypatch.setattr(
        registry, "validate", lambda: ["fake_tool: preconditions empty"],
    )
    with pytest.raises(ToolContractError) as exc:
        register_all_tools()
    assert "preconditions empty" in str(exc.value)
