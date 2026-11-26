"""Strict Pydantic v2 schemas for LLM structured outputs, Copilot SSE, and AI telemetry."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from salespulsebi.storage.models import AIProviderType, CustomPanelSpec, FilterState

AgentStatus = Literal["ok", "insufficient_data", "ambiguous_query", "blocked"]
ConfidenceLevel = Literal["high", "medium", "low"]


class ChatTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ChatStreamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000)
    filters: FilterState = Field(default_factory=FilterState)
    history: list[ChatTurn] = Field(default_factory=list, max_length=10)
    include_documents: bool = True
    lang: Literal["es", "en"] | None = None


class AgentCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=140)
    kind: Literal["metric", "sql", "ml", "document", "panel"] = "metric"
    target_tab: str | None = Field(default=None, max_length=40)
    page_num: int | None = None
    doc_id: str | None = None


class ToolExecutionTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    args_summary: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    row_count: int | None = None
    sql: str | None = None
    duration_ms: int = 0
    error_code: str | None = None


class StructuredAgentResponse(BaseModel):
    """Strict output schema for programmatic LLM synthesis (reasoning before values)."""

    model_config = ConfigDict(extra="forbid")

    rationale: str = Field(default="", max_length=600)
    status: AgentStatus = "ok"
    confidence: ConfidenceLevel = "high"
    answer_markdown: str = Field(min_length=1, max_length=8000)
    citations: list[AgentCitation] = Field(default_factory=list)
    proposed_panel: CustomPanelSpec | None = None
    follow_ups: list[str] = Field(default_factory=list, max_length=5)
    warnings: list[str] = Field(default_factory=list)


class ConnectionTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: AIProviderType | None = None
    base_url: str | None = Field(default=None, max_length=255)
    model: str | None = Field(default=None, max_length=120)


class SQLToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=8, max_length=2000)
    limit: Annotated[int, Field(ge=1, le=500)] = 100


class BreakdownToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: Literal[
        "product",
        "category",
        "region",
        "channel",
        "customer",
        "salesperson",
        "payment_method",
    ] = "category"
    metric: Literal["revenue", "profit", "units", "orders"] = "revenue"
    order: Literal["desc", "asc"] = "desc"
    limit: Annotated[int, Field(ge=1, le=50)] = 10
