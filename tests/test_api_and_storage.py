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
