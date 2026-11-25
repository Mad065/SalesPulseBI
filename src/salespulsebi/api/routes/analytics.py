"""Adaptive analytics & paginated data explorer endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from salespulsebi.analytics.customers import compute_customers
from salespulsebi.analytics.dimensions import (
    compute_adhoc,
    compute_custom_panels,
    compute_dimensions,
)
from salespulsebi.analytics.insights import generate_insights
from salespulsebi.analytics.overview import compute_overview
from salespulsebi.analytics.products import compute_products
from salespulsebi.analytics.quality import compute_quality
from salespulsebi.analytics.query_builder import build_where_clause, open_context, safe_ident
from salespulsebi.analytics.timeseries import compute_timeseries
from salespulsebi.api.deps import get_app_settings, get_db
from salespulsebi.config import Settings
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import (
    AdHocRequest,
    CustomPanelsBatchRequest,
    FilterState,
    ProjectSummary,
    TableRequest,
)
from salespulsebi.storage.parquet_store import ParquetStore, sanitize_json

router = APIRouter(prefix="/projects/{project_id}/analytics", tags=["analytics"])


def _require_project_and_parquet(
    project_id: str, db: MetadataDB, settings: Settings
) -> tuple[ProjectSummary, Any]:
    proj = db.get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    parquet_path = settings.project_parquet_path(project_id)
    if not parquet_path.exists():
        raise HTTPException(status_code=404, detail="Project has no ingested data yet")
    return proj, parquet_path


@router.post("/overview")
def analytics_overview(
    project_id: str,
    filters: FilterState = FilterState(),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj, ppath = _require_project_and_parquet(project_id, db, settings)
    with open_context(ppath, fx_rate=proj.settings.exchange_rate) as ctx:
        return compute_overview(ctx, filters)


@router.post("/timeseries")
def analytics_timeseries(
    project_id: str,
    filters: FilterState = FilterState(),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj, ppath = _require_project_and_parquet(project_id, db, settings)
    with open_context(ppath, fx_rate=proj.settings.exchange_rate) as ctx:
        return compute_timeseries(ctx, filters)


@router.post("/products")
def analytics_products(
    project_id: str,
    filters: FilterState = FilterState(),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj, ppath = _require_project_and_parquet(project_id, db, settings)
    with open_context(ppath, fx_rate=proj.settings.exchange_rate) as ctx:
        return compute_products(ctx, filters)


@router.post("/customers")
def analytics_customers(
    project_id: str,
    filters: FilterState = FilterState(),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj, ppath = _require_project_and_parquet(project_id, db, settings)
    with open_context(ppath, fx_rate=proj.settings.exchange_rate) as ctx:
        return compute_customers(ctx, filters)


@router.post("/dimensions")
def analytics_dimensions(
    project_id: str,
    filters: FilterState = FilterState(),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj, ppath = _require_project_and_parquet(project_id, db, settings)
    with open_context(ppath, fx_rate=proj.settings.exchange_rate) as ctx:
        return compute_dimensions(ctx, filters)


@router.post("/quality")
def analytics_quality(
    project_id: str,
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj, ppath = _require_project_and_parquet(project_id, db, settings)
    with open_context(ppath, fx_rate=proj.settings.exchange_rate) as ctx:
        return compute_quality(ctx, settings.project_quality_path(project_id))


@router.post("/insights")
def analytics_insights(
    project_id: str,
    filters: FilterState = FilterState(),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj, ppath = _require_project_and_parquet(project_id, db, settings)
    with open_context(ppath, fx_rate=proj.settings.exchange_rate) as ctx:
        ov = compute_overview(ctx, filters)
        ts = compute_timeseries(ctx, filters)
        pr = compute_products(ctx, filters)
        cu = compute_customers(ctx, filters)
        ql = compute_quality(ctx, settings.project_quality_path(project_id))
        items = generate_insights(
            overview=ov,
            timeseries=ts,
            products=pr,
            customers=cu,
            quality=ql,
            currency=proj.currency,
            lang=filters.lang,
        )
    return {"insights": items}


@router.post("/adhoc")
def analytics_adhoc(
    project_id: str,
    req: AdHocRequest,
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj, ppath = _require_project_and_parquet(project_id, db, settings)
    with open_context(ppath, fx_rate=proj.settings.exchange_rate) as ctx:
        try:
            return compute_adhoc(ctx, req)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/custom-panels")
def analytics_custom_panels(
    project_id: str,
    req: CustomPanelsBatchRequest = CustomPanelsBatchRequest(),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj, ppath = _require_project_and_parquet(project_id, db, settings)
    is_preview = req.preview_panel is not None
    panels = [req.preview_panel] if req.preview_panel is not None else list(proj.settings.custom_panels)
    with open_context(ppath, fx_rate=proj.settings.exchange_rate) as ctx:
        try:
            return compute_custom_panels(ctx, panels, req.filters, strict_columns=is_preview)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/table")
def analytics_table(
    project_id: str,
    req: TableRequest,
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj, ppath = _require_project_and_parquet(project_id, db, settings)
    with open_context(ppath, fx_rate=proj.settings.exchange_rate) as ctx:
        where_sql, params = build_where_clause(req.filters, ctx.columns)
        count_row = ParquetStore.query_one(
            ctx.con, f"SELECT COUNT(*) AS total FROM sales {where_sql}", params
        )
        total = int(count_row.get("total") or 0)

        display_cols = [
            c
            for c in ctx.columns
            if c not in {"_year", "_month", "_quarter", "_dow", "_hour", "_date_day", "_is_excluded_status"}
        ]
        # Order canonical columns first
        priority = [
            "_date",
            "_order_id",
            "_product",
            "_category",
            "_quantity",
            "_unit_price",
            "_discount",
            "_revenue",
            "_cost",
            "_profit",
            "_margin_pct",
            "_customer",
            "_region",
            "_channel",
            "_salesperson",
            "_payment_method",
            "_status",
            "_source_folder",
            "_source_file",
        ]
        ordered_cols = [c for c in priority if c in display_cols] + sorted(
            c for c in display_cols if c not in priority
        )
        select_list = ", ".join(safe_ident(c, ctx.columns) for c in ordered_cols)

        order_clause = ""
        if req.sort_by and req.sort_by in ctx.columns:
            sort_ident = safe_ident(req.sort_by, ctx.columns)
            direction = "ASC" if req.sort_dir == "asc" else "DESC"
            order_clause = f"ORDER BY {sort_ident} {direction} NULLS LAST"
        elif "_date" in ctx.columns:
            order_clause = "ORDER BY _date DESC NULLS LAST"

        offset = (req.page - 1) * req.page_size
        rows = ParquetStore.query_dicts(
            ctx.con,
            f"SELECT {select_list} FROM sales {where_sql} {order_clause} LIMIT ? OFFSET ?",
            params + [req.page_size, offset],
        )
        return sanitize_json(
            {
                "total": total,
                "page": req.page,
                "page_size": req.page_size,
                "columns": ordered_cols,
                "rows": rows,
            }
        )
