"""Application-wide General Settings endpoints (/api/v1/settings)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from salespulsebi.api.deps import get_db
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import GlobalSettings, GlobalSettingsUpdate

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=GlobalSettings)
def get_global_settings(db: MetadataDB = Depends(get_db)) -> GlobalSettings:
    """Return persisted global application settings."""
    return db.get_global_settings()


@router.patch("", response_model=GlobalSettings)
def update_global_settings(
    payload: GlobalSettingsUpdate,
    db: MetadataDB = Depends(get_db),
) -> GlobalSettings:
    """Update global application settings and optionally apply currency to existing projects."""
    return db.update_global_settings(payload)


@router.post("/reset", response_model=GlobalSettings)
def reset_global_settings(db: MetadataDB = Depends(get_db)) -> GlobalSettings:
    """Reset global application settings to factory defaults."""
    return db.reset_global_settings()
