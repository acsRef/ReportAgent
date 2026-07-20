from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class Span(BaseModel):
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    span_name: str
    span_type: str = "NODE"
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_ms: Optional[int] = None
    status: str = "RUNNING"
    input: Optional[Any] = None
    output: Optional[Any] = None
    error: Optional[str] = None


class LLMCall(BaseModel):
    span_id: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    cost: float = 0.0


class Trace(BaseModel):
    trace_id: str
    session_id: Optional[str] = None
    user_query: Optional[str] = None
    status: str = "RUNNING"
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    total_duration_ms: Optional[int] = None
