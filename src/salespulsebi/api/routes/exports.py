"""Data & Executive Report export endpoints (CSV, XLSX, Parquet, HTML/PDF Report)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from salespulsebi.analytics.customers import compute_customers
from salespulsebi.analytics.insights import generate_insights
from salespulsebi.analytics.overview import compute_overview
from salespulsebi.analytics.products import compute_products
from salespulsebi.analytics.quality import compute_quality
from salespulsebi.analytics.query_builder import open_context
from salespulsebi.analytics.reports import (
    export_csv_bytes,
    export_html_report,
    export_parquet_bytes,
    export_xlsx_bytes,
)
from salespulsebi.analytics.timeseries import compute_timeseries
from salespulsebi.api.deps import get_app_settings, get_db
from salespulsebi.config import Settings
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import FilterState

router = APIRouter(prefix="/projects/{project_id}/export", tags=["exports"])


def _safe_slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40] or "salespulsebi"


@router.post("/csv")
def export_csv(
    project_id: str,
    filters: FilterState = FilterState(),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    proj = db.get_project(project_id)
    ppath = settings.project_parquet_path(project_id)
    if not proj or not ppath.exists():
        raise HTTPException(status_code=404, detail="Project dataset not found")
    with open_context(ppath) as ctx:
        data = export_csv_bytes(ctx, filters)
    slug = _safe_slug(proj.name)
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{slug}_cleaned.csv"'},
    )


@router.post("/xlsx")
def export_xlsx(
    project_id: str,
    filters: FilterState = FilterState(),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    proj = db.get_project(project_id)
    ppath = settings.project_parquet_path(project_id)
    if not proj or not ppath.exists():
        raise HTTPException(status_code=404, detail="Project dataset not found")
    with open_context(ppath) as ctx:
        ov = compute_overview(ctx, filters)
        data = export_xlsx_bytes(ctx, filters, proj, ov)
    slug = _safe_slug(proj.name)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{slug}_report.xlsx"'},
    )


@router.post("/parquet")
def export_parquet(
    project_id: str,
    filters: FilterState = FilterState(),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    proj = db.get_project(project_id)
    ppath = settings.project_parquet_path(project_id)
    if not proj or not ppath.exists():
        raise HTTPException(status_code=404, detail="Project dataset not found")
    with open_context(ppath) as ctx:
        data = export_parquet_bytes(ctx, filters, settings.project_dir(project_id))
    slug = _safe_slug(proj.name)
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{slug}_dataset.parquet"'},
    )


@router.post("/html-report")
def export_html(
    project_id: str,
    filters: FilterState = FilterState(),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    proj = db.get_project(project_id)
    ppath = settings.project_parquet_path(project_id)
    if not proj or not ppath.exists():
        raise HTTPException(status_code=404, detail="Project dataset not found")
    with open_context(ppath) as ctx:
        ov = compute_overview(ctx, filters)
        ts = compute_timeseries(ctx, filters)
        pr = compute_products(ctx, filters)
        cu = compute_customers(ctx, filters)
        ql = compute_quality(ctx, settings.project_quality_path(project_id))
        ins = generate_insights(
            overview=ov,
            timeseries=ts,
            products=pr,
            customers=cu,
            quality=ql,
            currency=proj.currency,
            lang=filters.lang,
        )
        html_str = export_html_report(
            project=proj,
            overview=ov,
            insights=ins,
            products=pr,
            quality=ql,
            lang=filters.lang,
        )
    slug = _safe_slug(proj.name)
    return Response(
        content=html_str.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{slug}_executive_report.html"'},
    )
