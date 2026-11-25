"""Tests for SQLite metadata persistence, saved views, duplication, and API endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import ProjectCreate, SavedViewCreate


def test_storage_crud_and_views(db: MetadataDB) -> None:
    p = db.create_project(ProjectCreate(name="Store Alpha", tags=["retail"], currency="MXN"))
    assert db.get_project(p.id) is not None

    view = db.create_saved_view(
        p.id,
        SavedViewCreate(name="Q1 North", filters={"regions": ["North"]}, active_tab="products"),
    )
    assert len(db.list_saved_views(p.id)) == 1

    dup = db.duplicate_project(p.id)
    assert dup is not None and dup.id != p.id
    assert len(db.list_saved_views(dup.id)) == 1

    assert db.delete_saved_view(p.id, view.id) is True
    assert db.delete_project(p.id) is True
    assert db.get_project(p.id) is None


def test_api_end_to_end_and_demo(client: TestClient, heterogeneous_folder: Path) -> None:
    r_health = client.get("/api/v1/health")
    assert r_health.status_code == 200
    assert r_health.json()["app"] == "SalesPulseBI"

    # Seed demos via API
    r_demo = client.post("/api/v1/demo/seed")
    assert r_demo.status_code == 200
    demos = r_demo.json()
    assert len(demos) == 2

    # Verify both OmniRetail and Café Aurora analytics work cleanly
    for proj in demos:
        pid = proj["id"]
        ov = client.post(f"/api/v1/projects/{pid}/analytics/overview", json={})
        assert ov.status_code == 200
        tbl = client.post(f"/api/v1/projects/{pid}/analytics/table", json={"page": 1, "page_size": 10})
        assert tbl.status_code == 200
        assert tbl.json()["total"] > 0

    # Link heterogeneous folder & export bundle + reports
    r_new = client.post("/api/v1/projects", json={"name": "API Folder", "currency": "USD"})
    assert r_new.status_code == 201
    new_id = r_new.json()["id"]

    r_link = client.post(
        f"/api/v1/projects/{new_id}/link-folder",
        json={"path": str(heterogeneous_folder), "recursive": True},
    )
    assert r_link.status_code == 200
    assert r_link.json()["total_rows"] == 7

    r_zip = client.get(f"/api/v1/projects/{new_id}/export-bundle")
    assert r_zip.status_code == 200
    assert r_zip.content[:2] == b"PK"

    r_html = client.post(f"/api/v1/projects/{new_id}/export/html-report", json={"lang": "en"})
    assert r_html.status_code == 200
    assert b"Executive Sales Intelligence Report" in r_html.content

    r_404 = client.get("/api/v1/projects/non-existent-id")
    assert r_404.status_code == 404


def test_settings_currency_fx_upload_and_custom_panels_api(
    client: TestClient,
    tmp_path: Path,
) -> None:
    # 1. Create project and upload initial CSV
    r_create = client.post("/api/v1/projects", json={"name": "FX & Custom BI", "currency": "USD"})
    assert r_create.status_code == 201
    pid = r_create.json()["id"]

    csv_part1 = (
        "order_id,date,product,category,quantity,cost,total,customer,channel,status\n"
        "A1,2026-01-10,Desk,Furniture,1,60.00,100.00,C1,Web,Completed\n"
        "A2,2026-01-12,Chair,Furniture,2,80.00,200.00,C2,Store,Completed\n"
    ).encode("utf-8")
    r_up1 = client.post(
        f"/api/v1/projects/{pid}/upload",
        files=[("files", ("jan_part1.csv", csv_part1, "text/csv"))],
    )
    assert r_up1.status_code == 200
    assert r_up1.json()["total_rows"] == 2
    assert r_up1.json()["total_files"] == 1

    # 2. Add MORE files after project creation
    csv_part2 = (
        "order_id,date,product,category,quantity,cost,total,customer,channel,status\n"
        "A3,2026-02-05,Lamp,Lighting,1,20.00,50.00,C1,Web,Completed\n"
    ).encode("utf-8")
    r_up2 = client.post(
        f"/api/v1/projects/{pid}/upload",
        files=[("files", ("feb_part2.csv", csv_part2, "text/csv"))],
    )
    assert r_up2.status_code == 200
    assert r_up2.json()["total_rows"] == 3
    assert r_up2.json()["total_files"] == 2

    base_ov = client.post(f"/api/v1/projects/{pid}/analytics/overview", json={}).json()
    assert base_ov["kpis"]["revenue"]["value"] == 350.0
    base_margin = base_ov["kpis"]["gross_margin_pct"]["value"]

    # 3. Reject invalid FX rates and invalid currency codes (422)
    for bad_payload in (
        {"currency": "123"},
        {"settings": {"exchange_rate": "0"}},
        {"settings": {"exchange_rate": "-19.5"}},
        {"settings": {"exchange_rate": "NaN"}},
        {"unexpected_extra": True},
    ):
        r_bad = client.patch(f"/api/v1/projects/{pid}", json=bad_payload)
        assert r_bad.status_code == 422

    # 4. Configure MXN currency + exact 20.15 exchange rate + custom panels + layout order
    settings_payload = {
        "base_currency": "USD",
        "exchange_rate": "20.15",
        "layouts": {
            "overview": {
                "panel_order": ["cp_channel_donut", "kpis", "trend"],
                "hidden_panels": ["ticket_dist"],
                "panel_spans": {"cp_channel_donut": 2},
                "kpi_order": ["orders", "revenue"],
                "hidden_kpis": ["rows_count"],
            }
        },
        "custom_panels": [
            {
                "id": "cp_channel_donut",
                "title": "Ventas por Canal",
                "panel_type": "donut",
                "x_column": "_channel",
                "y_column": "_revenue",
                "aggregation": "sum",
                "format_kind": "auto",
                "limit": 10,
                "span": 2,
            },
            {
                "id": "cp_max_kpi",
                "title": "Venta Máxima",
                "panel_type": "kpi",
                "y_column": "_revenue",
                "aggregation": "max",
                "format_kind": "currency",
                "limit": 10,
                "span": 1,
            },
            {
                "id": "cp_unique_cust_tbl",
                "title": "Clientes Únicos por Categoría",
                "panel_type": "table",
                "x_column": "_category",
                "y_column": "_customer",
                "aggregation": "count_distinct",
                "format_kind": "number",
                "limit": 5,
                "span": 1,
            },
        ],
    }
    r_patch = client.patch(
        f"/api/v1/projects/{pid}",
        json={"currency": "mxn", "settings": settings_payload},
    )
    assert r_patch.status_code == 200
    patched = r_patch.json()
    assert patched["currency"] == "MXN"
    assert patched["settings"]["exchange_rate"] == "20.15"
    # 350.00 * 20.15 == 7052.50 exact
    assert patched["total_revenue"] == 7052.5

    # 5. Verify FX conversion propagates to overview, exports, duplication, and zip bundle without drift
    fx_ov = client.post(f"/api/v1/projects/{pid}/analytics/overview", json={}).json()
    assert fx_ov["kpis"]["revenue"]["value"] == 7052.5
    assert fx_ov["kpis"]["gross_margin_pct"]["value"] == base_margin

    r_cp = client.post(f"/api/v1/projects/{pid}/analytics/custom-panels", json={})
    assert r_cp.status_code == 200
    cp_data = r_cp.json()["panels"]
    assert cp_data["cp_max_kpi"]["value"] == 4030.0  # 200 * 20.15
    assert cp_data["cp_channel_donut"]["cross_filter_key"] == "channels"
    assert sum(r["value"] for r in cp_data["cp_channel_donut"]["rows"]) == 7052.5
    assert cp_data["cp_unique_cust_tbl"]["rows"][0]["value"] == 2.0

    # Preview invalid column returns 400
    r_bad_cp = client.post(
        f"/api/v1/projects/{pid}/analytics/custom-panels",
        json={
            "preview_panel": {
                "id": "bad_1",
                "title": "Bad",
                "panel_type": "bar",
                "x_column": "non_existent_col",
                "aggregation": "count",
            }
        },
    )
    assert r_bad_cp.status_code == 400

    # Duplicate and export/import bundle preserve settings and do NOT double-multiply total_revenue
    r_dup = client.post(f"/api/v1/projects/{pid}/duplicate")
    assert r_dup.status_code == 201
    assert r_dup.json()["total_revenue"] == 7052.5
    assert len(r_dup.json()["settings"]["custom_panels"]) == 3

    r_bundle = client.get(f"/api/v1/projects/{pid}/export-bundle")
    assert r_bundle.status_code == 200
    r_imp = client.post(
        "/api/v1/projects/import-bundle",
        files={"file": ("proj.zip", r_bundle.content, "application/zip")},
    )
    assert r_imp.status_code == 201
    assert r_imp.json()["currency"] == "MXN"
    assert r_imp.json()["total_revenue"] == 7052.5
    assert len(r_imp.json()["settings"]["custom_panels"]) == 3

    # Revert back to 1:1 USD restores exact original revenue
    settings_payload["exchange_rate"] = "1"
    r_rev = client.patch(
        f"/api/v1/projects/{pid}",
        json={"currency": "USD", "settings": settings_payload},
    )
    assert r_rev.status_code == 200
    assert r_rev.json()["total_revenue"] == 350.0


def test_global_settings_crud_inheritance_and_bulk_apply(client: TestClient) -> None:
    # 1. Default global settings
    r_get = client.get("/api/v1/settings")
    assert r_get.status_code == 200
    initial = r_get.json()
    assert initial["default_currency"] == "USD"
    assert initial["style_preset"] == "precision"
    assert initial["theme"] == "dark"
    assert initial["default_granularity"] == "month"

    # 2. Invalid payloads rejected with HTTP 422
    for bad in (
        {"default_currency": "123"},
        {"style_preset": "unknown_preset"},
        {"theme": "neon"},
        {"default_exchange_rate": "0"},
        {"default_exchange_rate": "-5"},
        {"default_forecast_horizon": 2},
        {"extra_unknown_field": True},
    ):
        r_bad = client.patch("/api/v1/settings", json=bad)
        assert r_bad.status_code == 422

    # 3. Update global defaults (currency, FX, style, theme, analytics, cleaning rules)
    r_patch = client.patch(
        "/api/v1/settings",
        json={
            "default_currency": "mxn",
            "default_base_currency": "usd",
            "default_exchange_rate": "19.50",
            "style_preset": "terminal",
            "theme": "system",
            "locale": "en",
            "compact_numbers": False,
            "default_granularity": "week",
            "default_forecast_horizon": 60,
            "default_compare_previous": False,
            "default_include_excluded_statuses": True,
            "default_deduplicate": False,
            "default_dedup_mode": "order_product",
            "default_exclude_negative_revenue": True,
            "default_date_dayfirst": True,
        },
    )
    assert r_patch.status_code == 200
    updated = r_patch.json()
    assert updated["default_currency"] == "MXN"
    assert updated["default_base_currency"] == "USD"
    assert updated["default_exchange_rate"] == "19.5"
    assert updated["style_preset"] == "terminal"
    assert updated["theme"] == "system"
    assert updated["locale"] == "en"
    assert updated["compact_numbers"] is False
    assert updated["default_granularity"] == "week"
    assert updated["default_forecast_horizon"] == 60
    assert updated["default_deduplicate"] is False
    assert updated["default_dedup_mode"] == "order_product"
    assert updated["default_exclude_negative_revenue"] is True
    assert updated["default_date_dayfirst"] is True

    # 4. Creating a project without explicit currency/locale inherits global settings & cleaning rules
    r_proj = client.post("/api/v1/projects", json={"name": "Inherited Defaults Project"})
    assert r_proj.status_code == 201
    proj = r_proj.json()
    assert proj["currency"] == "MXN"
    assert proj["locale"] == "en"
    assert proj["settings"]["base_currency"] == "USD"
    assert proj["settings"]["exchange_rate"] == "19.5"

    r_detail = client.get(f"/api/v1/projects/{proj['id']}")
    assert r_detail.status_code == 200
    rules = r_detail.json()["mapping"]["cleaning_rules"]
    assert rules["deduplicate"] is False
    assert rules["dedup_mode"] == "order_product"
    assert rules["exclude_negative_revenue"] is True
    assert rules["date_dayfirst"] is True

    # 5. Bulk-apply new default currency + exchange rate to existing projects
    r_apply = client.patch(
        "/api/v1/settings",
        json={
            "default_currency": "EUR",
            "default_base_currency": "USD",
            "default_exchange_rate": "0.92",
            "apply_currency_to_existing": True,
        },
    )
    assert r_apply.status_code == 200
    r_after = client.get(f"/api/v1/projects/{proj['id']}")
    assert r_after.status_code == 200
    assert r_after.json()["currency"] == "EUR"
    assert r_after.json()["settings"]["exchange_rate"] == "0.92"

    # 6. Reset to factory defaults
    r_reset = client.post("/api/v1/settings/reset")
    assert r_reset.status_code == 200
    reset_cfg = r_reset.json()
    assert reset_cfg["default_currency"] == "USD"
    assert reset_cfg["style_preset"] == "precision"
    assert reset_cfg["theme"] == "dark"
    assert reset_cfg["default_exchange_rate"] == "1"

