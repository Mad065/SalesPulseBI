"""FastAPI application factory serving REST API + self-contained SPA."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from salespulsebi import __app_name__, __version__
from salespulsebi.api.routes import (
    ai,
    analytics,
    demo,
    documents,
    exports,
    mapping,
    market,
    ml,
    projects,
    sources,
)
from salespulsebi.api.routes import settings as settings_routes
from salespulsebi.config import Settings, get_settings
from salespulsebi.ingestion.pipeline import ProjectIngestionPipeline
from salespulsebi.storage.database import MetadataDB

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
STATIC_DIR = WEB_DIR / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()
    cfg.ensure_dirs()

    app = FastAPI(
        title=__app_name__,
        version=__version__,
        description="Local-first open-source Sales Intelligence API & Web App",
    )

    db = MetadataDB(cfg.db_path, default_currency=cfg.default_currency)
    pipeline = ProjectIngestionPipeline(db=db, settings=cfg)
    app.state.settings = cfg
    app.state.db = db
    app.state.pipeline = pipeline

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1",
            "http://localhost",
            f"http://127.0.0.1:{cfg.port}",
            f"http://localhost:{cfg.port}",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    api_prefix = "/api/v1"
    app.include_router(settings_routes.router, prefix=api_prefix)
    app.include_router(projects.router, prefix=api_prefix)
    app.include_router(sources.router, prefix=api_prefix)
    app.include_router(mapping.router, prefix=api_prefix)
    app.include_router(analytics.router, prefix=api_prefix)
    app.include_router(ml.router, prefix=api_prefix)
    app.include_router(ai.router, prefix=api_prefix)
    app.include_router(documents.router, prefix=api_prefix)
    app.include_router(market.router, prefix=api_prefix)
    app.include_router(exports.router, prefix=api_prefix)
    app.include_router(demo.router, prefix=api_prefix)

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "app": __app_name__, "version": __version__}

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def serve_index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    return app
