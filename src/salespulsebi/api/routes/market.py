"""FastAPI endpoints for Market Sentiment & News, Smart Alerts, Entity Resolution, and Margin Leakage."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from salespulsebi.ai.router import MultiProviderLLMRouter
from salespulsebi.ai.sentiment import fetch_and_score_market_sentiment
from salespulsebi.analytics.diagnostics import (
    detect_entity_duplicates,
    detect_margin_leakage,
    evaluate_and_store_alerts,
)
from salespulsebi.analytics.query_builder import open_context
from salespulsebi.api.deps import get_app_settings, get_db
from salespulsebi.config import Settings
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import FilterState
from salespulsebi.storage.parquet_store import ParquetStore

router = APIRouter(prefix="/projects/{project_id}/market", tags=["market"])


class MarketSentimentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, max_length=200)
    custom_headlines: list[dict[str, str]] | None = None
    lang: Literal["es", "en"] = "es"
    use_cache: bool = True


@router.post("/sentiment")
async def project_market_sentiment(
    project_id: str,
    payload: MarketSentimentRequest = MarketSentimentRequest(),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj = db.get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    # Derive sector/entity keywords automatically from the project's top categories & name
    keywords: list[str] = [proj.name]
    ppath = settings.project_parquet_path(project_id)
    if ppath.exists():
        def _top_cats() -> list[str]:
            with open_context(ppath, fx_rate=proj.settings.exchange_rate) as ctx:
                if "_category" not in ctx.columns:
                    return []
                rows = ParquetStore.query_dicts(
                    ctx.con,
                    "SELECT DISTINCT CAST(_category AS VARCHAR) AS c FROM sales WHERE _category IS NOT NULL LIMIT 6",
                )
                return [str(r["c"]) for r in rows if r.get("c")]

        keywords.extend(await asyncio.to_thread(_top_cats))

    eff_query = (payload.query or " ".join(keywords[:3]) or "retail sales economy").strip()
    llm_router = MultiProviderLLMRouter(settings, db)
    return await fetch_and_score_market_sentiment(
        query=eff_query,
        entity_keywords=keywords,
        db=db,
        router=llm_router,
        custom_headlines=payload.custom_headlines,
        lang=payload.lang,
        use_cache=payload.use_cache,
    )


@router.get("/alerts")
async def project_alerts(
    project_id: str,
    refresh: bool = Query(default=False),
    lang: Literal["es", "en"] = Query(default="es"),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj = db.get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    existing = db.list_project_alerts(project_id)
    ppath = settings.project_parquet_path(project_id)
    if (refresh or not existing) and ppath.exists():
        def _eval() -> dict[str, Any]:
            with open_context(ppath, fx_rate=proj.settings.exchange_rate) as ctx:
                return evaluate_and_store_alerts(
                    project_id=project_id,
                    currency=proj.currency,
                    ctx=ctx,
                    filters=FilterState(lang=lang),
                    db=db,
                    lang=lang,
                )

        return await asyncio.to_thread(_eval)
    return {"alerts": existing, "generated_count": 0}


@router.post("/alerts/{alert_id}/ack")
def ack_project_alert(
    project_id: str,
    alert_id: str,
    db: MetadataDB = Depends(get_db),
) -> dict[str, bool]:
    if not db.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    ok = db.acknowledge_alert(project_id, alert_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"acknowledged": True}


@router.post("/diagnostics")
async def project_diagnostics(
    project_id: str,
    filters: FilterState = FilterState(),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj = db.get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    ppath = settings.project_parquet_path(project_id)
    if not ppath.exists():
        raise HTTPException(status_code=404, detail="Project has no ingested data yet")

    def _run() -> dict[str, Any]:
        with open_context(ppath, fx_rate=proj.settings.exchange_rate) as ctx:
            return {
                "entity_resolution": detect_entity_duplicates(ctx),
                "margin_leakage": detect_margin_leakage(ctx, filters),
            }

    return await asyncio.to_thread(_run)
