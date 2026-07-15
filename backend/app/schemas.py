from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    user_query: str
    session_id: str


class ColumnDef(BaseModel):
    key: str
    title: str


class TableData(BaseModel):
    columns: list[ColumnDef]
    rows: list[dict[str, Any]]


class ChartConfig(BaseModel):
    type: str
    config: dict[str, Any]


class Answer(BaseModel):
    text: str
    table: Optional[TableData] = None
    chart: Optional[ChartConfig] = None
    insight: Optional[str] = None


class TraceStepOut(BaseModel):
    step: str
    status: str
    detail: str
    duration: str


class ChatResponse(BaseModel):
    answer: Answer
    trace: list[TraceStepOut]


class SSEEvent(BaseModel):
    event: str
    data: str
