"""Phase 1 tests: AutoML forecasting (leakage-free), multivariate anomalies, PVM conservation, What-If."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from salespulsebi.analytics.query_builder import open_context
from salespulsebi.config import Settings
from salespulsebi.ingestion.pipeline import ProjectIngestionPipeline
from salespulsebi.ml.anomalies import detect_multivariate_anomalies
from salespulsebi.ml.automl_forecast import run_automl_forecast
from salespulsebi.ml.features import build_supervised_lag_matrix
from salespulsebi.ml.scenarios import compute_pvm_bridge, compute_what_if_scenario
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import FilterState, ProjectCreate


def _seed_rich_sales_project(
    tmp_path: Path, db: MetadataDB, pipeline: ProjectIngestionPipeline
) -> str:
    """Create a synthetic 90-day multi-product dataset with trend, seasonality, leap day, and anomalies."""
    proj = db.create_project(ProjectCreate(name="ML Test Retail", currency="USD"))
    rows = []
    start = date(2024, 1, 1)  # Leap year (includes 2024-02-29)
    for i in range(90):
        d = start + timedelta(days=i)
        dow_factor = 1.35 if d.weekday() in (4, 5) else 1.0
        base_qty = 4 + (i % 3)
        price_a = 100.0 if i < 45 else 112.0  # Clear price increase in second half
        cost_a = 55.0
        disc_a = 5.0
        # Inject an anomalous day with severe margin compression & huge discount
        if i == 60:
            disc_a = 70.0
            cost_a = 95.0
        # Inject a massive revenue spike day
        if i == 75:
            base_qty = 35
        rev_a = round(base_qty * price_a * dow_factor - disc_a, 2)
        rows.append(
            {
                "Order_ID": f"ORD-{i}-A",
                "Order_Date": d.isoformat(),
                "Product": "Widget Alpha",
                "Category": "Core",
                "Quantity": base_qty,
                "Unit_Price": price_a,
                "Discount": disc_a,
                "Cost": round(base_qty * cost_a, 2),
                "Revenue": rev_a,
                "Customer": f"Cust-{(i % 15) + 1}",
                "Region": "North" if i % 2 == 0 else "South",
                "Status": "Completed",
            }
        )
        # Second product for PVM mix effect (plus discontinued/new items)
        prod_b = "Legacy Item" if i < 45 else "New Launch"
        rows.append(
            {
                "Order_ID": f"ORD-{i}-B",
                "Order_Date": d.isoformat(),
                "Product": prod_b,
                "Category": "Expansion",
                "Quantity": 2,
                "Unit_Price": 60.0,
                "Discount": 0.0,
                "Cost": 30.0,
                "Revenue": 120.0,
                "Customer": f"Cust-{(i % 10) + 1}",
                "Region": "North",
                "Status": "Completed",
            }
        )
    csv_bytes = pd.DataFrame(rows).to_csv(index=False).encode("utf-8")
    pipeline.ingest_uploaded_files(proj.id, [("sales_90d.csv", csv_bytes)])
    return proj.id


def test_lag_features_have_zero_lookahead_bias() -> None:
    """Changing values at index >= t MUST NEVER change feature row for target t."""
    periods = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(25)]
    vals_a = np.arange(100.0, 125.0, dtype=float)
    vals_b = vals_a.copy()
    # Mutate future value at t=20 drastically
    vals_b[20:] = 99999.0

    Xa, ya, _, _ = build_supervised_lag_matrix(periods, vals_a, "day")
    Xb, yb, _, _ = build_supervised_lag_matrix(periods, vals_b, "day")

    # Row predicting target index 20 (which corresponds to start_idx + row_idx == 20)
    # Must be IDENTICAL between vals_a and vals_b because index 20 is not in vals[:20]
    target_pos = int(np.where(ya == vals_a[20])[0][0])
    np.testing.assert_allclose(Xa[: target_pos + 1], Xb[: target_pos + 1], rtol=0, atol=0)


def test_automl_forecast_tournament_and_invalidation(
    tmp_path: Path, db: MetadataDB, pipeline: ProjectIngestionPipeline, isolated_settings: Settings
) -> None:
    pid = _seed_rich_sales_project(tmp_path, db, pipeline)
    ppath = isolated_settings.project_parquet_path(pid)
    models_dir = isolated_settings.project_models_dir(pid)

    with open_context(ppath) as ctx:
        res = run_automl_forecast(
            ctx,
            FilterState(granularity="day", forecast_horizon=14),
            project_id=pid,
            db=db,
            models_dir=models_dir,
            parquet_path=ppath,
            target_metric="revenue",
            granularity="day",
            horizon_days=14,
        )

    assert res["available"] is True
    assert len(res["leaderboard"]) >= 4
    assert res["winner_model"] == res["leaderboard"][0]["model"]
    assert len(res["forecast"]) == 14
    for pt in res["forecast"]:
        assert 0.0 <= pt["lower_95"] <= pt["lower_80"] <= pt["forecast"] <= pt["upper_80"] <= pt["upper_95"]
    assert db.get_ml_model(pid, "revenue", "day") is not None

    # Syncing project invalidates stale model pointers automatically
    pipeline.sync_project(pid)
    assert db.get_ml_model(pid, "revenue", "day") is None


def test_automl_edge_cases_constant_and_tiny_series(
    tmp_path: Path, db: MetadataDB, pipeline: ProjectIngestionPipeline, isolated_settings: Settings
) -> None:
    # Single-row dataset -> clean insufficient_history response (no crash, no div-by-zero)
    p1 = db.create_project(ProjectCreate(name="Tiny", currency="USD"))
    df1 = pd.DataFrame([{"Date": "2026-01-01", "Revenue": 100.0}])
    pipeline.ingest_uploaded_files(p1.id, [("tiny.csv", df1.to_csv(index=False).encode())])
    with open_context(isolated_settings.project_parquet_path(p1.id)) as ctx:
        res1 = run_automl_forecast(ctx, FilterState(granularity="day"), granularity="day")
    assert res1["available"] is False
    assert res1["reason"] == "insufficient_history"

    # Constant series (std == 0)
    p2 = db.create_project(ProjectCreate(name="Flat", currency="USD"))
    df2 = pd.DataFrame(
        [{"Date": (date(2026, 1, 1) + timedelta(days=i)).isoformat(), "Revenue": 500.0} for i in range(15)]
    )
    pipeline.ingest_uploaded_files(p2.id, [("flat.csv", df2.to_csv(index=False).encode())])
    with open_context(isolated_settings.project_parquet_path(p2.id)) as ctx:
        res2 = run_automl_forecast(ctx, FilterState(granularity="day"), granularity="day", horizon_days=7)
    assert res2["available"] is True
    assert all(np.isfinite(pt["forecast"]) for pt in res2["forecast"])


def test_multivariate_anomalies_detects_spike_and_margin_drop(
    tmp_path: Path, db: MetadataDB, pipeline: ProjectIngestionPipeline, isolated_settings: Settings
) -> None:
    pid = _seed_rich_sales_project(tmp_path, db, pipeline)
    with open_context(isolated_settings.project_parquet_path(pid)) as ctx:
        res = detect_multivariate_anomalies(ctx, FilterState(), contamination=0.10, limit=15)

    assert res["available"] is True
    dates = {a["date"] for a in res["anomalies"]}
    # Day 60 (2024-03-01) has severe margin compression; Day 75 (2024-03-16) has huge spike
    assert (date(2024, 1, 1) + timedelta(days=75)).isoformat() in dates
    assert (date(2024, 1, 1) + timedelta(days=60)).isoformat() in dates


def test_pvm_bridge_exact_decimal_conservation_and_what_if(
    tmp_path: Path, db: MetadataDB, pipeline: ProjectIngestionPipeline, isolated_settings: Settings
) -> None:
    pid = _seed_rich_sales_project(tmp_path, db, pipeline)
    with open_context(isolated_settings.project_parquet_path(pid)) as ctx:
        pvm = compute_pvm_bridge(ctx, FilterState())
        wi = compute_what_if_scenario(
            ctx,
            FilterState(),
            price_delta_pct=Decimal("10"),
            cost_delta_pct=Decimal("5"),
        )

    assert pvm["available"] is True
    assert pvm["conservation_verified"] is True
    step_map = {s["id"]: Decimal(s["value_decimal"]) for s in pvm["steps"]}
    reconciled = (
        step_map["prior"]
        + step_map["volume"]
        + step_map["price"]
        + step_map["mix"]
        + step_map["new_items"]
        + step_map["discontinued"]
    )
    assert reconciled == step_map["current"]

    assert wi["available"] is True
    assert Decimal(wi["simulated"]["revenue_decimal"]) > Decimal(wi["baseline"]["revenue_decimal"])


def test_ml_endpoints_and_strict_422_validation(client: TestClient) -> None:
    resp = client.post("/api/v1/demo/seed")
    assert resp.status_code == 200
    payload = resp.json()
    pid = payload[0]["id"] if isinstance(payload, list) else payload["projects"][0]["id"]

    r_fc = client.post(f"/api/v1/projects/{pid}/ml/forecast", json={"target_metric": "revenue"})
    assert r_fc.status_code == 200
    assert r_fc.json()["available"] is True

    r_an = client.post(f"/api/v1/projects/{pid}/ml/anomalies", json={})
    assert r_an.status_code == 200
    assert r_an.json()["available"] is True

    r_pvm = client.post(f"/api/v1/projects/{pid}/ml/pvm-bridge", json={})
    assert r_pvm.status_code == 200
    assert r_pvm.json()["conservation_verified"] is True

    r_wi = client.post(
        f"/api/v1/projects/{pid}/ml/what-if",
        json={"price_delta_pct": "10", "discount_delta_pct": "-5"},
    )
    assert r_wi.status_code == 200
    assert r_wi.json()["available"] is True

    # Strict extra="forbid" & bounds validation -> 422
    r_bad = client.post(
        f"/api/v1/projects/{pid}/ml/what-if",
        json={"price_delta_pct": "999", "unexpected_field": 1},
    )
    assert r_bad.status_code == 422
