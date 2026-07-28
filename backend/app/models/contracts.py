from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.models.requirement import RequirementCard


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
    # 错误类别（psycopg2 → 上层决策）。None = 未知（沿用旧路径）。
    # 取值：timeout / syntax / object / connection / permission / other
    kind: Optional[str] = None


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
    # True when rows were truncated by execute_sql's MAX_RESULT_ROWS cap;
    # total/true lets the LLM ask the user to narrow scope instead of
    # silently dropping the tail.
    truncated: bool = False


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


# ---------------------------------------------------------------------------
# Conversational Workbench v2 — added in Phase 1
# ---------------------------------------------------------------------------

ChatMode = Literal["new", "supplement", "adjust", "legacy"]


class ChatRequest(BaseModel):
    """Body for `POST /api/v1/chat`.

    `mode` defaults to `new`. `legacy` keeps the old 2-stage intent card
    flow alive for clients that haven't migrated; see Phase 8 of the
    workbench plan for the retirement rule.
    """

    user_query: str
    session_id: str | None = None
    mode: ChatMode = "new"
    base_report_version: int | None = None
    metadata: dict | None = None


class PatchRequirementRequest(BaseModel):
    """Body for `PATCH /api/v1/sessions/{sid}/requirement`."""

    requirement: RequirementCard


class ReportVersionSummary(BaseModel):
    """Summary row from `agent.report_version` — used in `SessionSummary`."""

    version: int
    title: str
    status: Literal["generating", "done", "error"] = "done"
    created_at: datetime
    favorite: bool = False


class SessionSummary(BaseModel):
    """Response of `GET /api/v1/sessions`."""

    session_id: str
    title: str = ""
    phase: Literal[
        "idle", "parsing", "awaiting_missing", "awaiting_confirm",
        "generating", "adjusting", "report_ready", "error",
    ] = "idle"
    msg_count: int = 0
    updated_at: datetime
    report_versions: list[ReportVersionSummary] = Field(default_factory=list)


class SessionSnapshot(BaseModel):
    """Response of `GET /api/v1/sessions/{sid}`."""

    session: SessionSummary
    messages: list[ConversationMessage] = Field(default_factory=list)
    current_requirement: RequirementCard | None = None
    latest_report: Optional[dict] = None
    last_failed_action: Literal["new", "supplement", "confirm", "adjust", "retry"] | None = None


class TemplateRequest(BaseModel):
    """Body for `POST /api/v1/templates`."""

    name: str
    description: str = ""
    requirement_payload: dict

