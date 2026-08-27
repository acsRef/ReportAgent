"""Memory lifecycle 契约类型（P4b Task 1 基座）。

memory-architecture.md §五/§六 冻结：
- Status: candidate → active → superseded / expired（V1 无 promotion pipeline：
  candidate 由 LLM-inferred 写入但**不**被召回，active 由 explicit statement 写入）
- Scope: user（跨 session 长期）| session（绑 session_id，任务结束可过期）
- Confidence: high | medium | low（§五 规则固定，不让 LLM 拍）

这些是 persistence 无关的领域枚举；DB 列用 .value 存 TEXT。
"""
from __future__ import annotations

from enum import Enum


class MemoryStatus(str, Enum):
    CANDIDATE = "candidate"     # LLM-inferred，未进 active，不被召回
    ACTIVE = "active"           # explicit statement/definition，参与召回
    SUPERSEDED = "superseded"   # 被新 active 覆盖
    EXPIRED = "expired"         # session-scope 过期


class MemoryScope(str, Enum):
    USER = "user"               # 跨 session 长期
    SESSION = "session"         # 绑 session_id


class MemoryConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# §五 冻结：confidence 规则固定，不由 LLM 决定
CONFIDENCE_EXPLICIT_STATEMENT = MemoryConfidence.HIGH
CONFIDENCE_EXPLICIT_DEFINITION = MemoryConfidence.HIGH
# LLM-inferred preference → status=candidate（不进 active）；confidence 记 low
CONFIDENCE_LLM_INFERRED = MemoryConfidence.LOW

# 可召回状态：只有 active（candidate/superseded/expired 一律不召回）
RECALLABLE_STATUSES: frozenset[str] = frozenset({MemoryStatus.ACTIVE.value})
