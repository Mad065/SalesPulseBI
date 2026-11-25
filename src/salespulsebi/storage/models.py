"""Pydantic v2 domain and API models."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

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


PanelType = Literal["bar", "hbar", "line", "area", "donut", "kpi", "table"]
AggregationType = Literal["sum", "avg", "count", "count_distinct", "min", "max"]
ValueFormatKind = Literal["auto", "currency", "number", "percent"]
StylePreset = Literal["precision", "terminal", "editorial", "bento"]
ThemeMode = Literal["dark", "light", "system"]
LocaleCode = Literal["es", "en"]
GranularityType = Literal["day", "week", "month", "quarter", "year"]
DedupMode = Literal["exact", "order_product"]


def _format_decimal_plain(v: Decimal) -> str:
    s = format(v, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _validate_iso_currency(v: Any, field_name: str = "currency") -> str:
    if not isinstance(v, str):
        raise ValueError(f"{field_name} must be a string")
    cleaned = v.strip().upper()
    if not cleaned.isalpha() or not (3 <= len(cleaned) <= 8):
        raise ValueError(f"{field_name} must be a 3-8 letter ISO currency code")
    return cleaned


def _validate_iso_currency_opt(v: Any, field_name: str = "currency") -> str | None:
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError(f"{field_name} must be a string")
    cleaned = v.strip().upper()
    if not cleaned:
        return None
    if not cleaned.isalpha() or not (3 <= len(cleaned) <= 8):
        raise ValueError(f"{field_name} must be a 3-8 letter ISO currency code")
    return cleaned


def _validate_positive_fx(v: Any) -> Decimal:
    if isinstance(v, bool) or v is None:
        raise ValueError("exchange_rate must be a positive number")
    try:
        dec = v if isinstance(v, Decimal) else Decimal(str(v).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Invalid decimal exchange_rate") from exc
    if not dec.is_finite() or dec <= Decimal("0") or dec > Decimal("1000000"):
        raise ValueError("exchange_rate must be finite, > 0 and <= 1,000,000")
    with localcontext() as ctx:
        ctx.prec = 28
        return dec.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)


ExchangeRateDecimal = Annotated[
    Decimal,
    PlainSerializer(_format_decimal_plain, return_type=str, when_used="json"),
]


class GlobalSettings(BaseModel):
    """Application-wide default preferences for currency, UI style, analytics, and ingestion."""

    model_config = ConfigDict(extra="forbid")

    default_currency: str = Field(default="USD", min_length=3, max_length=8)
    default_base_currency: str | None = Field(default=None, min_length=3, max_length=8)
    default_exchange_rate: ExchangeRateDecimal = Decimal("1")
    style_preset: StylePreset = "precision"
    theme: ThemeMode = "dark"
    locale: LocaleCode = "es"
    compact_numbers: bool = True
    default_granularity: GranularityType = "month"
    default_forecast_horizon: Annotated[int, Field(ge=7, le=180)] = 30
    default_compare_previous: bool = True
    default_include_excluded_statuses: bool = False
    default_deduplicate: bool = True
    default_dedup_mode: DedupMode = "exact"
    default_exclude_negative_revenue: bool = False
    default_date_dayfirst: bool | None = None
    updated_at: str | None = None

    @field_validator("default_currency", mode="before")
    @classmethod
    def _val_def_cur(cls, v: Any) -> str:
        return _validate_iso_currency(v, "default_currency")

    @field_validator("default_base_currency", mode="before")
    @classmethod
    def _val_def_base_cur(cls, v: Any) -> str | None:
        return _validate_iso_currency_opt(v, "default_base_currency")

    @field_validator("default_exchange_rate", mode="before")
    @classmethod
    def _val_def_fx(cls, v: Any) -> Decimal:
        return _validate_positive_fx(v)


class GlobalSettingsUpdate(BaseModel):
    """Partial update payload for application-wide general settings."""

    model_config = ConfigDict(extra="forbid")

    default_currency: str | None = Field(default=None, min_length=3, max_length=8)
    default_base_currency: str | None = Field(default=None, max_length=8)
    default_exchange_rate: ExchangeRateDecimal | None = None
    style_preset: StylePreset | None = None
    theme: ThemeMode | None = None
    locale: LocaleCode | None = None
    compact_numbers: bool | None = None
    default_granularity: GranularityType | None = None
    default_forecast_horizon: Annotated[int, Field(ge=7, le=180)] | None = None
    default_compare_previous: bool | None = None
    default_include_excluded_statuses: bool | None = None
    default_deduplicate: bool | None = None
    default_dedup_mode: DedupMode | None = None
    default_exclude_negative_revenue: bool | None = None
    default_date_dayfirst: bool | None = None
    apply_currency_to_existing: bool = False

    @field_validator("default_currency", mode="before")
    @classmethod
    def _val_def_cur_opt(cls, v: Any) -> str | None:
        return _validate_iso_currency_opt(v, "default_currency")

    @field_validator("default_base_currency", mode="before")
    @classmethod
    def _val_def_base_cur_opt(cls, v: Any) -> str | None:
        return _validate_iso_currency_opt(v, "default_base_currency")

    @field_validator("default_exchange_rate", mode="before")
    @classmethod
    def _val_def_fx_opt(cls, v: Any) -> Decimal | None:
        if v is None:
            return None
        return _validate_positive_fx(v)


class CustomPanelSpec(BaseModel):
    """User-defined custom panel specification for the Overview dashboard."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=80)
    panel_type: PanelType = "bar"
    x_column: str | None = Field(default=None, max_length=120)
    y_column: str | None = Field(default=None, max_length=120)
    aggregation: AggregationType = "sum"
    format_kind: ValueFormatKind = "auto"
    limit: Annotated[int, Field(ge=1, le=100)] = 12
    span: Literal[1, 2] = 1


class TabLayoutConfig(BaseModel):
    """Per-view panel order, visibility, width spans, and KPI strip preferences."""

    model_config = ConfigDict(extra="forbid")

    panel_order: list[str] = Field(default_factory=list)
    hidden_panels: list[str] = Field(default_factory=list)
    panel_spans: dict[str, Literal[1, 2]] = Field(default_factory=dict)
    kpi_order: list[str] = Field(default_factory=list)
    hidden_kpis: list[str] = Field(default_factory=list)


class ProjectSettings(BaseModel):
    """Persisted dashboard customization, FX conversion, and custom panels."""

    model_config = ConfigDict(extra="forbid")

    base_currency: str | None = Field(default=None, min_length=3, max_length=8)
    exchange_rate: ExchangeRateDecimal = Decimal("1")
    layouts: dict[str, TabLayoutConfig] = Field(default_factory=dict)
    custom_panels: list[CustomPanelSpec] = Field(default_factory=list)

    @field_validator("base_currency", mode="before")
    @classmethod
    def _validate_base_currency(cls, v: Any) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("base_currency must be a string")
        cleaned = v.strip().upper()
        if not cleaned:
            return None
        if not cleaned.isalpha() or not (3 <= len(cleaned) <= 8):
            raise ValueError("base_currency must be a 3-8 letter currency code")
        return cleaned

    @field_validator("exchange_rate", mode="before")
    @classmethod
    def _validate_exchange_rate(cls, v: Any) -> Decimal:
        if isinstance(v, bool) or v is None:
            raise ValueError("exchange_rate must be a positive number")
        try:
            dec = v if isinstance(v, Decimal) else Decimal(str(v).strip())
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("Invalid decimal exchange_rate") from exc
        if not dec.is_finite() or dec <= Decimal("0") or dec > Decimal("1000000"):
            raise ValueError("exchange_rate must be finite, > 0 and <= 1,000,000")
        with localcontext() as ctx:
            ctx.prec = 28
            return dec.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    tags: list[str] = Field(default_factory=list)
    currency: str | None = Field(default=None, min_length=3, max_length=8)
    locale: str | None = Field(default=None)
    is_favorite: bool = False
    settings: ProjectSettings | None = None

    @field_validator("currency", mode="before")
    @classmethod
    def _validate_currency(cls, v: Any) -> str | None:
        return _validate_iso_currency_opt(v, "currency")


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    tags: list[str] | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=8)
    locale: str | None = None
    is_favorite: bool | None = None
    settings: ProjectSettings | None = None

    @field_validator("currency", mode="before")
    @classmethod
    def _validate_currency_opt(cls, v: Any) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("currency must be a string")
        cleaned = v.strip().upper()
        if not cleaned.isalpha() or not (3 <= len(cleaned) <= 8):
            raise ValueError("currency must be a 3-8 letter ISO currency code")
        return cleaned


class ProjectSummary(BaseModel):
    id: str
    name: str
    description: str
    tags: list[str]
    is_favorite: bool
    currency: str
    locale: str
    settings: ProjectSettings = Field(default_factory=ProjectSettings)
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
    model_config = ConfigDict(extra="forbid")

    filters: FilterState = Field(default_factory=FilterState)
    x_column: str | None = None
    y_column: str | None = None
    aggregation: AggregationType = "sum"
    limit: int = Field(default=20, ge=1, le=200)


class CustomPanelsBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: FilterState = Field(default_factory=FilterState)
    preview_panel: CustomPanelSpec | None = None


class TableRequest(BaseModel):
    filters: FilterState = Field(default_factory=FilterState)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=10, le=250)
    sort_by: str | None = None
    sort_dir: Literal["asc", "desc"] = "desc"
