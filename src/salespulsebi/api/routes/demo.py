"""1-click demo seeding endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from salespulsebi.api.deps import get_app_settings, get_db, get_pipeline
from salespulsebi.config import Settings
from salespulsebi.demo.generator import seed_demo_projects
from salespulsebi.ingestion.pipeline import ProjectIngestionPipeline
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import ProjectSummary

router = APIRouter(prefix="/demo", tags=["demo"])


@router.post("/seed", response_model=list[ProjectSummary])
def seed_demos(
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    pipeline: ProjectIngestionPipeline = Depends(get_pipeline),
) -> list[ProjectSummary]:
    return seed_demo_projects(db, settings, pipeline)
