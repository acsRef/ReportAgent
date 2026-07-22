from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel


class ColumnSchema(BaseModel):
    name: str
    type: str
    description: str = ""


class TableSchema(BaseModel):
    name: str
    description: str = ""
    columns: list[ColumnSchema] = []
    relationships: list[dict] = []


class ErrorDetail(BaseModel):
    code: str
    message: str


class SchemaContext(BaseModel):
    version: str = "1.0"
    source: str = ""
    tables: list[TableSchema] = []
    confidence: float = 0.0
    status: Literal["SUCCESS", "FAILED"] = "SUCCESS"
    error: Optional[ErrorDetail] = None


class QueryPlan(BaseModel):
    version: str = "1.0"
    target_metric: str = ""
    dimensions: list[str] = []
    filters: list[dict] = []
    aggregation: str = ""
    time_range: Optional[str] = None


class QueryResult(BaseModel):
    version: str = "1.0"
    sql: str = ""
    columns: list[dict] = []
    rows: list[dict] = []
    row_count: int = 0
    status: Literal["SUCCESS", "FAILED", "EMPTY"] = "SUCCESS"
    error: Optional[ErrorDetail] = None


class ComponentSpec(BaseModel):
    id: str = ""
    type: str = ""
    title: str = ""
    layout: dict = {}
    data_binding: dict = {}
    visual_config: dict = {}


class ReportSpec(BaseModel):
    version: str = "1.0"
    components: list[ComponentSpec] = []
    insight: str = ""


class ClarificationRequest(BaseModel):
    question: str
    resume_point: str = "sql"
    alternatives: list[str] = []


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    user_id: int
    username: str


class ConversationMessage(BaseModel):
    id: int
    role: str
    content: str | None
    message_type: str
    metadata: dict | None
    created_at: str


class SessionSummary(BaseModel):
    session_id: str
    msg_count: int
    first_message: str
    last_message: str
