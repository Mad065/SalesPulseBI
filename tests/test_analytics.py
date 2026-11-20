"""Tests for adaptive DuckDB analytics, Pareto ABC, Market Basket, RFM, Cohorts, Forecast, MAD, and Exports."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from salespulsebi.analytics.customers import compute_customers
from salespulsebi.analytics.dimensions import compute_adhoc, compute_dimensions
from salespulsebi.analytics.insights import generate_insights
from salespulsebi.analytics.overview import compute_overview
from salespulsebi.analytics.products import compute_products
from salespulsebi.analytics.quality import compute_quality
from salespulsebi.analytics.query_builder import open_context
from salespulsebi.analytics.reports import export_csv_bytes, export_html_report, export_xlsx_bytes
from salespulsebi.analytics.timeseries import compute_timeseries
from salespulsebi.ingestion.pipeline import ProjectIngestionPipeline
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import AdHocRequest, FilterState, ProjectCreate


def _seed_synthetic_analytics_project(
    tmp_path: Path, db: MetadataDB, pipeline: ProjectIngestionPipeline
) -> str:
    rows = []
    start = date(2026, 1, 1)
    for d_off in range(30):
        d = (start + timedelta(days=d_off)).isoformat()
        # Normal daily baseline ~150
        rows.append(
            {
                "order_id": f"O-{d_off}-1",
                "date": d,
                "product": "Laptop Pro",
                "category": "Electronics",
                "quantity": 1,
                "cost": 60.0,
                "discount": 5.0,
                "total": 100.0,
                "customer": f"Cust-{(d_off % 5) + 1}",
                "region": "North",
                "channel": "Web",
                "status": "Completed",
            }
        )
        rows.append(
            {
                "order_id": f"O-{d_off}-1",
                "date": d,
                "product": "USB Hub",
                "category": "Accessories",
                "quantity": 1,
                "cost": 20.0,
                "discount": 0.0,
                "total": 50.0,
                "customer": f"Cust-{(d_off % 5) + 1}",
                "region": "North",
                "channel": "Web",
                "status": "Completed",
            }
        )
    # Inject massive anomaly spike on day 20
    rows.append(
        {
            "order_id": "O-SPIKE",
            "date": (start + timedelta(days=20)).isoformat(),
            "product": "Server Rack",
            "category": "Enterprise",
            "quantity": 1,
            "cost": 2000.0,
            "discount": 0.0,
            "total": 5000.0,
            "customer": "Cust-1",
            "region": "South",
            "channel": "B2B",
            "status": "Completed",
        }
    )
    csv_path = tmp_path / "analytics_test.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)

    proj = db.create_project(ProjectCreate(name="Analytics Math", currency="USD"))
    pipeline.ingest_uploaded_files(proj.id, [("analytics_test.csv", csv_path.read_bytes())])
    return proj.id


def test_full_analytics_suite(
    tmp_path: Path, db: MetadataDB, pipeline: ProjectIngestionPipeline
) -> None:
    pid = _seed_synthetic_analytics_project(tmp_path, db, pipeline)
    ppath = pipeline.settings.project_parquet_path(pid)
    proj = db.get_project(pid)
    assert proj is not None

    filters = FilterState(granularity="day", forecast_horizon=14, lang="es")
    with open_context(ppath) as ctx:
        ov = compute_overview(ctx, filters)
        assert ov["kpis"]["revenue"]["value"] == 30 * 150.0 + 5000.0
        assert ov["kpis"]["orders"]["value"] == 31
        assert ov["kpis"]["unique_customers"]["value"] == 5

        ts = compute_timeseries(ctx, filters)
        assert ts["available"] is True
        assert len(ts["forecast"]) == 14
        assert all(f["forecast"] >= 0 and f["upper_95"] >= f["lower_95"] for f in ts["forecast"])
        anomaly_dates = [a["date"] for a in ts["anomalies"]]
        assert "2026-01-21" in anomaly_dates
        spike = next(a for a in ts["anomalies"] if a["date"] == "2026-01-21")
        assert spike["direction"] == "spike"
        assert spike["root_cause"] == "Server Rack"

        pr = compute_products(ctx, filters)
        assert pr["available"] is True
        assert len(pr["market_basket"]) >= 1
        assert pr["market_basket"][0]["co_count"] == 30

        cu = compute_customers(ctx, filters)
        assert cu["available"] is True
        assert cu["summary"]["total_customers"] == 5
        assert len(cu["rfm_segments"]) >= 1

        dm = compute_dimensions(ctx, filters)
        assert "regions" in dm["dimensions"]
        adhoc = compute_adhoc(ctx, AdHocRequest(x_column="_region", y_column="_revenue", aggregation="sum"))
        assert len(adhoc["rows"]) == 2

        ql = compute_quality(ctx, pipeline.settings.project_quality_path(pid))
        assert 0 <= ql["health_score"] <= 100

        ins_es = generate_insights(overview=ov, timeseries=ts, products=pr, customers=cu, quality=ql, lang="es")
        ins_en = generate_insights(overview=ov, timeseries=ts, products=pr, customers=cu, quality=ql, lang="en")
        assert len(ins_es) >= 3 and len(ins_en) >= 3

        csv_b = export_csv_bytes(ctx, filters)
        xlsx_b = export_xlsx_bytes(ctx, filters, proj, ov)
        html_rep = export_html_report(project=proj, overview=ov, insights=ins_es, products=pr, quality=ql, lang="es")
        assert len(csv_b) > 100
        assert xlsx_b[:2] == b"PK"
        assert "SalesPulseBI" in html_rep
