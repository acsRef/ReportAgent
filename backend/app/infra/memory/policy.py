from __future__ import annotations

import re
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class MemoryType(str, Enum):
    SEMANTIC = "semantic"
    QUERY_TEMPLATE = "query_template"
    USER_PREFERENCE = "user_preference"
    PATTERN = "pattern"


class MemoryEntry(BaseModel):
    type: MemoryType
    key: str
    value: str
    user_id: str = ""
    metadata: dict = {}
    created_at: str = ""


_PREFERENCE_KEYWORDS = ["以后", "默认", "每次", " always", " default", " prefer"]
_TEMPORAL_KEYWORDS = ["昨天", "今天", "明天", "上周", "本月", "yesterday", "today", "last week"]


class MemoryPolicy:
    def should_remember(self, text: str, memory_type: Optional[MemoryType] = None) -> bool:
        if memory_type == MemoryType.QUERY_TEMPLATE:
            return True

        if memory_type == MemoryType.USER_PREFERENCE:
            return any(kw in text for kw in _PREFERENCE_KEYWORDS)

        if memory_type == MemoryType.SEMANTIC:
            if any(kw in text for kw in _TEMPORAL_KEYWORDS):
                return False
            return True

        return False

    def extract_preference(self, text: str) -> Optional[MemoryEntry]:
        patterns = [
            (r"(?:以后|默认|每次)\s*(.*?)(?:显示|用|使用|以)\s*(.*?)(?:为单位|来显示|$)", "display"),
            (r"我(?:喜欢|想|要)\s*(?:看|用)?\s*(.*?)(?:的)?\s*(?:图表|格式|方式)", "preference"),
        ]
        for pattern, pref_type in patterns:
            m = re.search(pattern, text)
            if m:
                return MemoryEntry(
                    type=MemoryType.USER_PREFERENCE,
                    key=f"preference.{pref_type}",
                    value=m.group(0).strip(),
                    metadata={"pattern": pattern, "groups": list(m.groups())},
                )
        return None

    def classify_memory_type(self, event_type: str, text: str = "") -> MemoryType:
        if event_type == "sql_success":
            return MemoryType.QUERY_TEMPLATE
        if any(kw in text for kw in _PREFERENCE_KEYWORDS):
            return MemoryType.USER_PREFERENCE
        if event_type in ("insight", "analysis_result"):
            return MemoryType.SEMANTIC
        return MemoryType.SEMANTIC
