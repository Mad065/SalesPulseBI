"""Semantic column mapping & cleaning rule endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from salespulsebi.api.deps import get_db, get_pipeline
from salespulsebi.ingestion.pipeline import ProjectIngestionPipeline
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import MappingState, MappingUpdate, ProjectUpdate, SyncResult

router = APIRouter(prefix="/projects/{project_id}/mapping", tags=["mapping"])


@router.get("", response_model=MappingState)
def get_mapping(project_id: str, db: MetadataDB = Depends(get_db)) -> MappingState:
    if not db.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return db.get_mapping(project_id)


@router.put("", response_model=SyncResult)
def update_mapping(
    project_id: str,
    payload: MappingUpdate,
    db: MetadataDB = Depends(get_db),
    pipeline: ProjectIngestionPipeline = Depends(get_pipeline),
) -> SyncResult:
    if not db.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    if payload.currency:
        db.update_project(project_id, ProjectUpdate(currency=payload.currency))
    return pipeline.rebuild_with_mapping(
        project_id,
        role_map=payload.role_map,
        cleaning_rules=payload.cleaning_rules,
    )
