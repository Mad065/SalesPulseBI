"""Pydantic v2 domain and API models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SemanticRole = Literal[
    "date",
    "revenue",
    "quantity",
    "unit_price",
    "cost",
    "discount",
    "tax",
    "product",
    "category",
    "customer",
    "order_id",
    "region",
    "channel",
    "salesperson",
    "payment_method",
    "status",
]

ALL_SEMANTIC_ROLES: tuple[SemanticRole, ...] = (
    "date",
    "revenue",
    "quantity",
    "unit_price",
    "cost",
    "discount",
    "tax",
    "product",
    "category",
    "customer",
    "order_id",
    "region",
    "channel",
    "salesperson",
    "payment_method",
    "status",
)

DEFAULT_EXCLUDED_STATUSES: list[str] = [
    "cancelled",
    "canceled",
    "cancelado",
    "cancelada",
    "returned",
    "devuelto",
    "devuelta",
    "refunded",
    "reembolsado",
    "void",
    "anulado",
    "anulada",
]


class CleaningRules(BaseModel):
    """Per-project data normalization and cleaning rules."""

    deduplicate: bool = True
    dedup_mode: Literal["exact", "order_product"] = "exact"
    exclude_statuses: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDED_STATUSES))
    exclude_negative_revenue: bool = False
    date_dayfirst: bool | None = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    tags: list[str] = Field(default_factory=list)
    currency: str = Field(default="USD", min_length=1, max_length=8)
    locale: str = Field(default="es")
    is_favorite: bool = False


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    tags: list[str] | None = None
    currency: str | None = Field(default=None, min_length=1, max_length=8)
    locale: str | None = None
    is_favorite: bool | None = None


class ProjectSummary(BaseModel):
    id: str
    name: str
    description: str
    tags: list[str]
    is_favorite: bool
    currency: str
    locale: str
    created_at: str
    updated_at: str
    last_synced_at: str | None = None
    row_count: int = 0
    file_count: int = 0
    total_revenue: float | None = None
    health_score: float | None = None


class SourceRecord(BaseModel):
    id: str
    project_id: str
    source_type: Literal["uploaded_file", "linked_folder", "linked_file"]
    path: str
    recursive: bool = True
    file_glob: str = "*"
    file_hash: str | None = None
    file_size: int = 0
    row_count: int = 0
    status: str = "ready"
    error_message: str | None = None
    last_seen_at: str | None = None


class ColumnProfile(BaseModel):
    name: str
    dtype: str
    null_count: int = 0
    null_pct: float = 0.0
    distinct_count: int = 0
    sample_values: list[str] = Field(default_factory=list)
    detected_role: str | None = None
    confidence: float = 0.0


class MappingState(BaseModel):
    role_map: dict[str, str] = Field(default_factory=dict)
    detected_schema: list[ColumnProfile] = Field(default_factory=list)
    cleaning_rules: CleaningRules = Field(default_factory=CleaningRules)
    updated_at: str | None = None


class MappingUpdate(BaseModel):
    role_map: dict[str, str] = Field(default_factory=dict)
    cleaning_rules: CleaningRules | None = None
    currency: str | None = None


class SavedViewCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    filters: dict[str, Any] = Field(default_factory=dict)
    active_tab: str = "overview"


class SavedViewRecord(BaseModel):
    id: str
    project_id: str
    name: str
    filters: dict[str, Any]
    active_tab: str
    created_at: str


class SyncResult(BaseModel):
    project_id: str
    files_added: int = 0
    files_updated: int = 0
    files_removed: int = 0
    total_files: int = 0
    total_rows: int = 0
    duration_ms: int = 0
    warnings: list[str] = Field(default_factory=list)


class SyncHistoryRecord(BaseModel):
    id: str
    project_id: str
    triggered_at: str
    files_added: int
    files_updated: int
    files_removed: int
    total_rows: int
    duration_ms: int
    warnings: list[str]


class ProjectDetail(ProjectSummary):
    sources: list[SourceRecord] = Field(default_factory=list)
    mapping: MappingState = Field(default_factory=MappingState)
    saved_views: list[SavedViewRecord] = Field(default_factory=list)
    sync_history: list[SyncHistoryRecord] = Field(default_factory=list)
    capabilities: dict[str, bool] = Field(default_factory=dict)


class LinkFolderRequest(BaseModel):
    path: str = Field(min_length=1)
    recursive: bool = True
    file_glob: str = "*"


class FilterState(BaseModel):
    date_from: str | None = None
    date_to: str | None = None
    search: str | None = None
    categories: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    salespersons: list[str] = Field(default_factory=list)
    payment_methods: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    source_files: list[str] = Field(default_factory=list)
    source_folders: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    customers: list[str] = Field(default_factory=list)
    include_excluded_statuses: bool = False
    compare_previous: bool = True
    granularity: Literal["day", "week", "month", "quarter", "year"] = "month"
    forecast_horizon: int = Field(default=30, ge=7, le=180)
    lang: Literal["es", "en"] = "es"


class AdHocRequest(BaseModel):
    filters: FilterState = Field(default_factory=FilterState)
    x_column: str
    y_column: str | None = None
    aggregation: Literal["sum", "avg", "count", "min", "max"] = "sum"
    limit: int = Field(default=20, ge=1, le=200)


class TableRequest(BaseModel):
    filters: FilterState = Field(default_factory=FilterState)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=10, le=250)
    sort_by: str | None = None
    sort_dir: Literal["asc", "desc"] = "desc"
