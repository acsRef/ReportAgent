"""Checkpoint adapter —— v1 → v2 schema migration（P3 Task 2）。

P3 plan §2.4 钉住：
- 不造 saver wrapper（review #8）；migrate 通过 graph 入口节点单点注入（(γ)）
- 显式 legacy detection（review #7）：缺 schema_version + active_sub_agent +
  original_query 并存；未知 shape 抛 MigrationError 不误判
- deterministic rename 表（review P1 #3）：仅 active_sub_agent → active_agent /
  insight_text → insight；其他字段保留 state_dict 顶层
"""
from __future__ import annotations

LEGACY_SCHEMA_VERSION = "v1"
CURRENT_SCHEMA_VERSION = "v2"

# (v1 源字段, v2 目标字段) —— deterministic 1:1 rename only（review P1 #3）
_RENAME_MAP: tuple[tuple[str, str], ...] = (
    ("active_sub_agent", "active_agent"),
    ("insight_text", "insight"),
)

# legacy 检测标志字段组合（review #7：缺 schema_version 但二者并存 → legacy）
_LEGACY_MARKER_FIELDS: frozenset[str] = frozenset(
    {"active_sub_agent", "original_query"}
)


class MigrationError(RuntimeError):
    """checkpoint 既不是已知 v1 shape 也不是 v2 shape，拒绝自动迁移。"""


def is_legacy_checkpoint(checkpoint: dict) -> bool:
    """显式判据：缺 schema_version 且含全部标志字段。

    第三方 / 测试 fixture 缺标志组合 → 返回 False，由 migrate_checkpoint 拒收
    raise MigrationError。
    """
    if "schema_version" in checkpoint:
        return False
    return _LEGACY_MARKER_FIELDS.issubset(checkpoint.keys())


def migrate_checkpoint(checkpoint: dict) -> dict:
    """三分支迁移：

    - schema_version == CURRENT_SCHEMA_VERSION → 透传（idempotent）
    - schema_version == LEGACY_SCHEMA_VERSION
      或（缺 + is_legacy_checkpoint） → rename → v2
    - 其他 → raise MigrationError
    """
    version = checkpoint.get("schema_version")
    if version == CURRENT_SCHEMA_VERSION:
        return checkpoint
    if version == LEGACY_SCHEMA_VERSION or (
        version is None and is_legacy_checkpoint(checkpoint)
    ):
        return _apply_legacy_rename(checkpoint)
    raise MigrationError(
        f"unknown checkpoint shape: schema_version={version!r}, "
        f"keys_sample={sorted(checkpoint.keys())[:5]}"
    )


def _apply_legacy_rename(checkpoint: dict) -> dict:
    """对 legacy checkpoint 应用 deterministic rename，返回 v2 shape dict。

    - v1 名（active_sub_agent / insight_text）从结果中移除（rename 不丢名是预期）
    - v2 名（active_agent / insight）写入
    - 其他字段（mapped 同名 / unmapped）原样保留
    - 顶层注入 schema_version=CURRENT_SCHEMA_VERSION
    """
    result = dict(checkpoint)  # shallow copy，不修改入参
    for src, dst in _RENAME_MAP:
        if src in result:
            result[dst] = result.pop(src)
    result["schema_version"] = CURRENT_SCHEMA_VERSION
    return result


def inject_schema_version(checkpoint: dict) -> dict:
    """写入侧 helper：在 checkpoint 顶层注入 schema_version=CURRENT_SCHEMA_VERSION。

    idempotent：已有 v2 marker 时只覆盖同值。
    """
    result = dict(checkpoint)
    result["schema_version"] = CURRENT_SCHEMA_VERSION
    return result
