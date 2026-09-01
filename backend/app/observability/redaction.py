"""PII redaction：所有进 tracer / Langfuse / log 的数据统一过 mask_pii。

CLAUDE.md § 宪法区 12 / 伞形 plan §十三（P13）：redaction 层复用
app.utils.pii.mask_pii 正则族，在 Adapter 前一层统一做。
"""
from __future__ import annotations

from typing import Any

from app.utils.pii import mask_pii


def redact(value: Any) -> Any:
    """递归 mask dict / list / str 中的 PII；其余类型透传。"""
    if isinstance(value, str):
        return mask_pii(value)
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def redact_user_query(query: str | None) -> str:
    """user_query 入口 redaction（空串 / None 安全）。"""
    return mask_pii(query or "")