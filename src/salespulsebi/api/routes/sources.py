"""File/folder upload, local folder linking, incremental sync, and safe directory browser."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from salespulsebi.api.deps import get_db, get_pipeline
from salespulsebi.ingestion.pipeline import ProjectIngestionPipeline
from salespulsebi.ingestion.readers import SUPPORTED_EXTENSIONS
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import LinkFolderRequest, SyncHistoryRecord, SyncResult

router = APIRouter(tags=["sources"])


@router.post("/projects/{project_id}/upload", response_model=SyncResult)
async def upload_files(
    project_id: str,
    files: list[UploadFile] = File(...),
    relative_paths: str | None = Form(default=None),
    db: MetadataDB = Depends(get_db),
    pipeline: ProjectIngestionPipeline = Depends(get_pipeline),
) -> SyncResult:
    if not db.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")

    rel_list: list[str] = []
    if relative_paths:
        try:
            parsed = json.loads(relative_paths)
            if isinstance(parsed, list):
                rel_list = [str(x) for x in parsed]
        except json.JSONDecodeError:
            rel_list = []

    items: list[tuple[str, bytes]] = []
    for idx, uf in enumerate(files):
        name = rel_list[idx] if idx < len(rel_list) and rel_list[idx] else (uf.filename or f"file_{idx}.csv")
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            continue
        content = await uf.read()
        items.append((name, content))

    if not items:
        raise HTTPException(status_code=400, detail="No supported sales files provided")

    return pipeline.ingest_uploaded_files(project_id, items)


@router.post("/projects/{project_id}/link-folder", response_model=SyncResult)
def link_folder(
    project_id: str,
    payload: LinkFolderRequest,
    db: MetadataDB = Depends(get_db),
    pipeline: ProjectIngestionPipeline = Depends(get_pipeline),
) -> SyncResult:
    if not db.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return pipeline.link_local_path(
            project_id,
            payload.path,
            recursive=payload.recursive,
            file_glob=payload.file_glob,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/sync", response_model=SyncResult)
def sync_project(
    project_id: str,
    db: MetadataDB = Depends(get_db),
    pipeline: ProjectIngestionPipeline = Depends(get_pipeline),
) -> SyncResult:
    if not db.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return pipeline.sync_project(project_id)


@router.delete("/projects/{project_id}/sources/{source_id}", response_model=SyncResult)
def remove_source(
    project_id: str,
    source_id: str,
    db: MetadataDB = Depends(get_db),
    pipeline: ProjectIngestionPipeline = Depends(get_pipeline),
) -> SyncResult:
    if not db.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return pipeline.remove_source(project_id, source_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/projects/{project_id}/sync-history", response_model=list[SyncHistoryRecord])
def get_sync_history(
    project_id: str,
    db: MetadataDB = Depends(get_db),
) -> list[SyncHistoryRecord]:
    return db.list_sync_history(project_id)


@router.get("/fs/browse")
def browse_filesystem(path: str | None = Query(default=None)) -> dict[str, Any]:
    """Browse local directories safely so users can pick a folder in the UI without typing paths."""
    target = Path(path).expanduser().resolve() if path else Path.home().resolve()
    if not target.exists() or not target.is_dir():
        target = Path.home().resolve()

    dirs: list[dict[str, Any]] = []
    data_files: list[str] = []
    try:
        for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if item.name.startswith("."):
                continue
            if item.is_dir():
                dirs.append({"name": item.name, "path": str(item)})
            elif item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS:
                data_files.append(item.name)
    except PermissionError:
        pass

    return {
        "current_path": str(target),
        "parent_path": str(target.parent) if target.parent != target else None,
        "directories": dirs[:200],
        "data_files_count": len(data_files),
        "data_files_sample": data_files[:10],
    }
