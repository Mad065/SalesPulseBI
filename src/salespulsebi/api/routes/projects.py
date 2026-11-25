"""Project CRUD, duplication, bundle export/import, and saved views endpoints."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile

from salespulsebi.analytics.query_builder import detect_capabilities
from salespulsebi.api.deps import get_app_settings, get_db, get_pipeline
from salespulsebi.config import Settings
from salespulsebi.ingestion.pipeline import ProjectIngestionPipeline
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import (
    ProjectCreate,
    ProjectDetail,
    ProjectSettings,
    ProjectSummary,
    ProjectUpdate,
    SavedViewCreate,
    SavedViewRecord,
)
from salespulsebi.storage.parquet_store import ParquetStore

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectSummary])
def list_projects(db: MetadataDB = Depends(get_db)) -> list[ProjectSummary]:
    return db.list_projects()


@router.post("", response_model=ProjectSummary, status_code=201)
def create_project(
    payload: ProjectCreate,
    db: MetadataDB = Depends(get_db),
) -> ProjectSummary:
    return db.create_project(payload)


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project_detail(
    project_id: str,
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ProjectDetail:
    proj = db.get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    parquet_path = settings.project_parquet_path(project_id)
    cols = {c["name"] for c in ParquetStore.get_columns(parquet_path)}
    return ProjectDetail(
        **proj.model_dump(),
        sources=db.list_sources(project_id),
        mapping=db.get_mapping(project_id),
        saved_views=db.list_saved_views(project_id),
        sync_history=db.list_sync_history(project_id),
        capabilities=detect_capabilities(cols),
    )


@router.patch("/{project_id}", response_model=ProjectSummary)
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    db: MetadataDB = Depends(get_db),
) -> ProjectSummary:
    updated = db.update_project(project_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Project not found")
    return updated


@router.delete("/{project_id}")
def delete_project(
    project_id: str,
    db: MetadataDB = Depends(get_db),
    pipeline: ProjectIngestionPipeline = Depends(get_pipeline),
) -> dict[str, bool]:
    deleted = db.delete_project(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")
    pipeline.delete_project_files(project_id)
    return {"deleted": True}


@router.post("/{project_id}/duplicate", response_model=ProjectSummary, status_code=201)
def duplicate_project(
    project_id: str,
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ProjectSummary:
    import shutil

    dup = db.duplicate_project(project_id)
    if not dup:
        raise HTTPException(status_code=404, detail="Project not found")
    src_dir = settings.projects_root / project_id
    dst_dir = settings.projects_root / dup.id
    if src_dir.exists():
        if dst_dir.exists():
            shutil.rmtree(dst_dir, ignore_errors=True)
        shutil.copytree(src_dir, dst_dir)
    return dup


@router.get("/{project_id}/export-bundle")
def export_project_bundle(
    project_id: str,
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    proj = db.get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    mapping = db.get_mapping(project_id)
    views = db.list_saved_views(project_id)
    bundle_meta = {
        "project": proj.model_dump(mode="json"),
        "mapping": mapping.model_dump(mode="json"),
        "saved_views": [v.model_dump(mode="json") for v in views],
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(bundle_meta, indent=2))
        pdir = settings.projects_root / project_id
        for sub in ("dataset.parquet", "quality_report.json"):
            fp = pdir / sub
            if fp.exists():
                zf.write(fp, arcname=sub)
        raw_dir = pdir / "raw"
        if raw_dir.exists():
            for f in raw_dir.rglob("*"):
                if f.is_file():
                    zf.write(f, arcname=f"raw/{f.relative_to(raw_dir).as_posix()}")

    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in proj.name)[:40]
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_bundle.zip"'},
    )


@router.post("/import-bundle", response_model=ProjectSummary, status_code=201)
async def import_project_bundle(
    file: UploadFile = File(...),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    pipeline: ProjectIngestionPipeline = Depends(get_pipeline),
) -> ProjectSummary:
    content = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid ZIP bundle") from exc

    with zf:
        if "manifest.json" not in zf.namelist():
            raise HTTPException(status_code=400, detail="Missing manifest.json in bundle")
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        p_data = manifest["project"]
        imported_settings = ProjectSettings.model_validate(p_data.get("settings") or {})
        new_proj = db.create_project(
            ProjectCreate(
                name=f"{p_data['name']} (Imported)",
                description=p_data.get("description", ""),
                tags=p_data.get("tags", []),
                currency=p_data.get("currency", "USD"),
                locale=p_data.get("locale", "es"),
                settings=imported_settings,
            )
        )
        m_data = manifest.get("mapping", {})
        if m_data:
            from salespulsebi.storage.models import CleaningRules, ColumnProfile

            db.save_mapping(
                new_proj.id,
                role_map=m_data.get("role_map", {}),
                detected_schema=[
                    ColumnProfile.model_validate(c) for c in m_data.get("detected_schema", [])
                ],
                cleaning_rules=CleaningRules.model_validate(m_data.get("cleaning_rules", {})),
            )
        for v in manifest.get("saved_views", []):
            db.create_saved_view(
                new_proj.id,
                SavedViewCreate(
                    name=v["name"],
                    filters=v.get("filters", {}),
                    active_tab=v.get("active_tab", "overview"),
                ),
            )

        pdir = settings.project_dir(new_proj.id)
        raw_dir = settings.project_raw_dir(new_proj.id)
        has_raw = False
        for member in zf.namelist():
            if member.startswith("raw/") and not member.endswith("/"):
                rel = Path(member).relative_to("raw")
                if ".." in rel.parts:
                    continue
                dest = (raw_dir / rel).resolve()
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(member))
                has_raw = True
            elif member in ("dataset.parquet", "quality_report.json"):
                (pdir / member).write_bytes(zf.read(member))

        if has_raw:
            pipeline.sync_project(new_proj.id)
        else:
            from decimal import Decimal, ROUND_HALF_EVEN, localcontext

            raw_rev = p_data.get("total_revenue")
            if raw_rev is not None and imported_settings.exchange_rate != Decimal("1"):
                with localcontext() as ctx:
                    ctx.prec = 28
                    raw_rev = float(
                        (Decimal(str(raw_rev)) / imported_settings.exchange_rate).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_EVEN
                        )
                    )
            db.update_project_stats(
                new_proj.id,
                row_count=p_data.get("row_count", 0),
                file_count=p_data.get("file_count", 0),
                total_revenue=raw_rev,
                health_score=p_data.get("health_score"),
                synced=True,
            )

    res = db.get_project(new_proj.id)
    assert res is not None
    return res


@router.get("/{project_id}/views", response_model=list[SavedViewRecord])
def list_saved_views(project_id: str, db: MetadataDB = Depends(get_db)) -> list[SavedViewRecord]:
    return db.list_saved_views(project_id)


@router.post("/{project_id}/views", response_model=SavedViewRecord, status_code=201)
def create_saved_view(
    project_id: str,
    payload: SavedViewCreate,
    db: MetadataDB = Depends(get_db),
) -> SavedViewRecord:
    if not db.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return db.create_saved_view(project_id, payload)


@router.delete("/{project_id}/views/{view_id}")
def delete_saved_view(
    project_id: str,
    view_id: str,
    db: MetadataDB = Depends(get_db),
) -> dict[str, bool]:
    ok = db.delete_saved_view(project_id, view_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Saved view not found")
    return {"deleted": True}
