"""Shared FastAPI dependency accessors attached to app.state."""

from __future__ import annotations

from fastapi import Request

from salespulsebi.config import Settings
from salespulsebi.ingestion.pipeline import ProjectIngestionPipeline
from salespulsebi.storage.database import MetadataDB


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> MetadataDB:
    return request.app.state.db


def get_pipeline(request: Request) -> ProjectIngestionPipeline:
    return request.app.state.pipeline
