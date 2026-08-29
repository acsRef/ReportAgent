from __future__ import annotations

import pytest

pytestmark = pytest.mark.contracts

from app.tools import register_all_tools
from app.tools.registry import registry


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


def test_14_fields_present(registered):
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


def test_description_four_questions(registered):
    for name, meta in registry.all_tools().items():
        assert "什么时候调" in meta.description, f"{name}: missing 什么时候调"
        assert "什么时候不调" in meta.description, f"{name}: missing 什么时候不调"
        assert "调用前" in meta.description, f"{name}: missing 调用前"
        assert "调用后" in meta.description, f"{name}: missing 调用后"


def test_permission_alias_synced(registered):
    for name, meta in registry.all_tools().items():
        assert meta.permission == meta.permission_required, f"{name}: permission alias not synced"


def test_registry_validate_empty(registered):
    errors = registry.validate()
    assert not errors, "registry.validate failed:\n" + "\n".join(errors)


def test_examples_non_empty_strings(registered):
    for name, meta in registry.all_tools().items():
        for ex in meta.examples:
            assert isinstance(ex, str) and ex.strip(), f"{name}: example empty string"


def test_risk_level_permission_source_consistency(registered):
    for name, meta in registry.all_tools().items():
        if meta.risk_level == "high":
            assert meta.permission or meta.permission_required, f"{name}: high risk must have permission"
        if meta.source == "mcp":
            assert "mcp" in meta.source
