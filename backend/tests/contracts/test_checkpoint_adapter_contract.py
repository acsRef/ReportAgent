"""Checkpoint adapter 钉子（P3 Task 2）。

P3 plan §2.4 + review #7 钉住：
- LEGACY_SCHEMA_VERSION = "v1" / CURRENT_SCHEMA_VERSION = "v2"
- MigrationError 异常
- is_legacy_checkpoint 显式判据（缺 schema_version 但 active_sub_agent
  + original_query 并存；未知 shape 不误判为 legacy）
- migrate_checkpoint 三分支：v2 透传 / legacy 走映射 / unknown raise
- deterministic 映射表（review P1 #3：仅同名/同类型 rename，**不**做语义伪映射）
- inject_schema_version 写入侧 helper
"""
from __future__ import annotations

import pytest

from app.state.checkpoint_adapter import (
    CURRENT_SCHEMA_VERSION,
    LEGACY_SCHEMA_VERSION,
    MigrationError,
    inject_schema_version,
    is_legacy_checkpoint,
    migrate_checkpoint,
)


class TestSchemaVersionConstants:
    def test_versions_defined(self):
        assert LEGACY_SCHEMA_VERSION == "v1"
        assert CURRENT_SCHEMA_VERSION == "v2"


class TestIsLegacyCheckpoint:
    def test_legacy_shape_detected_by_active_sub_agent_and_original_query(self):
        # review #7：显式判据，缺 schema_version 但 active_sub_agent +
        # original_query 并存 → 判 legacy
        legacy = {
            "original_query": "2024 销售",
            "active_sub_agent": "execution",
            "session_id": "s1",
        }
        assert is_legacy_checkpoint(legacy) is True

    def test_v2_checkpoint_not_legacy(self):
        v2 = {"schema_version": CURRENT_SCHEMA_VERSION, "session_id": "s1"}
        assert is_legacy_checkpoint(v2) is False

    def test_unknown_shape_not_legacy(self):
        # 缺 schema_version + 不含标志组合 → 不算 legacy
        # （review #7 防第三方/测试 fixture 被误判）
        unknown = {"session_id": "s1", "user_id": 1}
        assert is_legacy_checkpoint(unknown) is False

    def test_only_active_sub_agent_not_enough(self):
        partial = {"active_sub_agent": "execution"}
        assert is_legacy_checkpoint(partial) is False

    def test_only_original_query_not_enough(self):
        partial = {"original_query": "2024"}
        assert is_legacy_checkpoint(partial) is False


class TestMigrateCheckpointIdempotent:
    def test_v2_passthrough_unchanged(self):
        v2 = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "session_id": "s1",
            "user_id": 1,
        }
        result = migrate_checkpoint(v2)
        assert result == v2


class TestMigrateCheckpointLegacyToV2:
    def test_legacy_shape_renames_active_sub_agent(self):
        legacy = {
            "original_query": "2024 销售",
            "current_query": "2024 销售趋势",
            "session_id": "s1",
            "user_id": 42,
            "trace_id": "t-1",
            "active_sub_agent": "execution",
            "memory_context": "ctx",
            "insight_text": "华东领先",
        }
        v2 = migrate_checkpoint(legacy)
        assert v2["schema_version"] == CURRENT_SCHEMA_VERSION
        # rename：active_sub_agent → active_agent（v1 名移除，不丢名是预期）
        assert "active_sub_agent" not in v2
        assert v2["active_agent"] == "execution"
        # rename：insight_text → insight
        assert "insight_text" not in v2
        assert v2["insight"] == "华东领先"
        # 同名字段保留
        assert v2["session_id"] == "s1"
        assert v2["user_id"] == 42
        assert v2["trace_id"] == "t-1"
        assert v2["memory_context"] == "ctx"
        assert v2["original_query"] == "2024 销售"
        assert v2["current_query"] == "2024 销售趋势"

    def test_explicit_v1_marker_also_migrates(self):
        legacy = {
            "schema_version": LEGACY_SCHEMA_VERSION,
            "active_sub_agent": "x",
            "original_query": "q",  # 加标志，但其实显式 v1 marker 已足够
        }
        v2 = migrate_checkpoint(legacy)
        assert v2["schema_version"] == CURRENT_SCHEMA_VERSION
        assert "active_sub_agent" not in v2
        assert v2["active_agent"] == "x"

    def test_unmapped_fields_preserved(self):
        # review P1 #3：unmapped 字段（intent / retry_counters / security_score）
        # 保留在 state_dict 顶层不强行 rename
        legacy = {
            "original_query": "x",
            "active_sub_agent": "y",
            "intent": "report",
            "retry_counters": {"repair": 2},
            "security_score": 0,
        }
        v2 = migrate_checkpoint(legacy)
        assert v2["intent"] == "report"
        assert v2["retry_counters"] == {"repair": 2}
        assert v2["security_score"] == 0
        # rename 行生效
        assert v2["active_agent"] == "y"
        assert "active_sub_agent" not in v2


class TestMigrateCheckpointUnknownShape:
    def test_raises_migration_error_on_unknown(self):
        # review #7：缺 schema_version 又不是 legacy shape → raise MigrationError
        unknown = {"foo": "bar"}
        with pytest.raises(MigrationError):
            migrate_checkpoint(unknown)

    def test_raises_migration_error_on_wrong_version(self):
        bad = {"schema_version": "v999"}
        with pytest.raises(MigrationError):
            migrate_checkpoint(bad)


class TestInjectSchemaVersion:
    def test_injects_v2(self):
        result = inject_schema_version({"foo": "bar"})
        assert result["schema_version"] == CURRENT_SCHEMA_VERSION
        assert result["foo"] == "bar"

    def test_idempotent_when_already_v2(self):
        v2 = {"schema_version": CURRENT_SCHEMA_VERSION, "foo": "bar"}
        result = inject_schema_version(v2)
        assert result["schema_version"] == CURRENT_SCHEMA_VERSION
