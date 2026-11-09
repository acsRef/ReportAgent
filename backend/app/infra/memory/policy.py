from __future__ import annotations

import re
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class MemoryType(str, Enum):
    STABLE_PREFERENCE = "stable_preference"
    TEMPORARY_PREFERENCE = "temporary_preference"
    INSIGHT = "insight"
    QUERY_TEMPLATE = "query_template"


class MemoryEntry(BaseModel):
    type: MemoryType
    key: str
    value: str
    user_id: str = ""
    metadata: dict = {}
    created_at: str = ""
    importance_score: float = 0.3
    memory_type: str = "insight"


_STABLE_KEYWORDS = ["以后", "默认", "每次", " always", " default", "每次都用"]
_TEMPORAL_KEYWORDS = ["昨天", "今天", "明天", "上周", "本月", "这个月", "这个季度",
                      "最近", "当前", "yesterday", "today", "last week", "this month"]


class MemoryPolicy:
    def should_remember(self, text: str, memory_type: Optional[MemoryType] = None) -> bool:
        if memory_type == MemoryType.QUERY_TEMPLATE:
            return True
        if memory_type == MemoryType.INSIGHT:
            return not any(kw in text for kw in _TEMPORAL_KEYWORDS)
        if memory_type is not None:
            mtype, _ = self._classify(text)
            return mtype == memory_type
        return bool(re.search(r"(?:喜欢|偏好|习惯|倾向|通常|总是)", text))

    def extract_preference(self, text: str) -> Optional[MemoryEntry]:
        patterns = [
            (
                r"(?:以后|默认|每次)\s*(.*?)(?:显示|用|使用|以)\s*(.*?)(?:为单位|来显示|$)",
                "display",
            ),
            (
                r"我(?:喜欢|想|要)\s*(?:看|用)?\s*(.*?)(?:的)?\s*(?:图表|格式|方式)",
                "preference",
            ),
            # 2026-11-09（plan memory-admission-and-consolidation）：会话级说法也要能被抽取。
            # 少这一条时，「这次先按周看」在 extract_preference 就返回 None，
            # 后面的准入闸根本没机会把它判成 session 级 —— 也就是
            # `temporary_preference` 永不产生的**真正起点**在这里，不只是 manager 的硬编码。
            (
                r"(?:这次|本次|这一回|这一轮|暂时|临时|先)\s*(.*?)(?:显示|用|使用|按|以)\s*"
                r"(.*?)(?:为单位|来显示|来看|看|$)",
                "display",
            ),
        ]
        for pattern, pref_type in patterns:
            m = re.search(pattern, text)
            if m:
                memory_type, importance = self._classify(text)
                return MemoryEntry(
                    type=MemoryType.STABLE_PREFERENCE,
                    key=f"preference.{pref_type}",
                    value=m.group(0).strip(),
                    metadata={"pattern": pattern, "groups": list(m.groups())},
                    importance_score=importance,
                    memory_type=memory_type.value,
                )
        return None

    def _classify(self, text: str) -> tuple[MemoryType, float]:
        if any(kw in text for kw in _STABLE_KEYWORDS):
            return MemoryType.STABLE_PREFERENCE, 0.8
        if any(kw in text for kw in _TEMPORAL_KEYWORDS):
            return MemoryType.TEMPORARY_PREFERENCE, 0.5
        return MemoryType.INSIGHT, 0.3

    def classify_memory_type(self, event_type: str, text: str = "") -> MemoryType:
        if event_type == "sql_success":
            return MemoryType.QUERY_TEMPLATE
        if text:
            return self._classify(text)[0]
        if event_type in ("insight", "analysis_result"):
            return MemoryType.INSIGHT
        return MemoryType.INSIGHT
