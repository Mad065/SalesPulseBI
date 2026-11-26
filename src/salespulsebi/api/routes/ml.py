"""FastAPI endpoints for AutoML forecasting, multivariate anomalies, What-If scenarios, and PVM bridge."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from salespulsebi.analytics.query_builder import open_context
from salespulsebi.api.deps import get_app_settings, get_db
from salespulsebi.config import Settings
from salespulsebi.ml.anomalies import detect_multivariate_anomalies
from salespulsebi.ml.automl_forecast import run_automl_forecast
from salespulsebi.ml.churn_clv import compute_churn_and_clv
from salespulsebi.ml.scenarios import compute_pvm_bridge, compute_what_if_scenario
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import FilterState, GranularityType, ProjectSummary

router = APIRouter(prefix="/projects/{project_id}/ml", tags=["ml"])


class AutoMLForecastRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: FilterState = Field(default_factory=FilterState)
    target_metric: Literal["revenue", "profit", "units", "orders"] = "revenue"
    granularity: GranularityType | None = None
    horizon_days: Annotated[int | None, Field(ge=7, le=180)] = None
    force_retrain: bool = False


class MultivariateAnomaliesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: FilterState = Field(default_factory=FilterState)
    contamination: Annotated[float, Field(ge=0.01, le=0.25)] = 0.08
    limit: Annotated[int, Field(ge=1, le=50)] = 15


class WhatIfRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: FilterState = Field(default_factory=FilterState)
    price_delta_pct: Annotated[Decimal, Field(ge=Decimal("-90"), le=Decimal("300"))] = Decimal("0")
    discount_delta_pct: Annotated[Decimal, Field(ge=Decimal("-100"), le=Decimal("200"))] = Decimal("0")
    cost_delta_pct: Annotated[Decimal, Field(ge=Decimal("-90"), le=Decimal("300"))] = Decimal("0")
    volume_delta_pct: Annotated[Decimal, Field(ge=Decimal("-95"), le=Decimal("500"))] = Decimal("0")
    fx_multiplier: Annotated[Decimal, Field(gt=Decimal("0"), le=Decimal("100"))] = Decimal("1")
    use_estimated_elasticity: bool = False


def _require_project_parquet(
    project_id: str, db: MetadataDB, settings: Settings
) -> tuple[ProjectSummary, Any]:
    proj = db.get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    ppath = settings.project_parquet_path(project_id)
    if not ppath.exists():
        raise HTTPException(status_code=404, detail="Project has no ingested data yet")
    return proj, ppath


@router.get("/models")
def list_project_ml_models(
    project_id: str,
    db: MetadataDB = Depends(get_db),
) -> dict[str, Any]:
    if not db.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return {"models": db.list_ml_models(project_id)}


@router.post("/forecast")
@router.post("/forecast/train")
async def ml_automl_forecast(
    project_id: str,
    req: AutoMLForecastRequest = AutoMLForecastRequest(),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj, ppath = _require_project_parquet(project_id, db, settings)

    def _run() -> dict[str, Any]:
        with open_context(ppath, fx_rate=proj.settings.exchange_rate) as ctx:
            return run_automl_forecast(
                ctx,
                req.filters,
                project_id=project_id,
                db=db,
                models_dir=settings.project_models_dir(project_id),
                parquet_path=ppath,
                target_metric=req.target_metric,
                granularity=req.granularity or req.filters.granularity,
                horizon_days=req.horizon_days or req.filters.forecast_horizon,
                force_retrain=req.force_retrain,
                lang=req.filters.lang,
            )

    return await asyncio.to_thread(_run)


@router.post("/anomalies")
async def ml_multivariate_anomalies(
    project_id: str,
    req: MultivariateAnomaliesRequest = MultivariateAnomaliesRequest(),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj, ppath = _require_project_parquet(project_id, db, settings)

    def _run() -> dict[str, Any]:
        with open_context(ppath, fx_rate=proj.settings.exchange_rate) as ctx:
            return detect_multivariate_anomalies(
                ctx,
                req.filters,
                contamination=req.contamination,
                limit=req.limit,
            )

    return await asyncio.to_thread(_run)


@router.post("/what-if")
async def ml_what_if(
    project_id: str,
    req: WhatIfRequest = WhatIfRequest(),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj, ppath = _require_project_parquet(project_id, db, settings)

    def _run() -> dict[str, Any]:
        with open_context(ppath, fx_rate=proj.settings.exchange_rate) as ctx:
            return compute_what_if_scenario(
                ctx,
                req.filters,
                price_delta_pct=req.price_delta_pct,
                discount_delta_pct=req.discount_delta_pct,
                cost_delta_pct=req.cost_delta_pct,
                volume_delta_pct=req.volume_delta_pct,
                fx_multiplier=req.fx_multiplier,
                use_estimated_elasticity=req.use_estimated_elasticity,
            )

    return await asyncio.to_thread(_run)


@router.post("/pvm-bridge")
async def ml_pvm_bridge(
    project_id: str,
    filters: FilterState = FilterState(),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj, ppath = _require_project_parquet(project_id, db, settings)

    def _run() -> dict[str, Any]:
        with open_context(ppath, fx_rate=proj.settings.exchange_rate) as ctx:
            return compute_pvm_bridge(ctx, filters)

    return await asyncio.to_thread(_run)


class ChurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: FilterState = Field(default_factory=FilterState)
    horizon_days: Annotated[int, Field(ge=14, le=180)] = 60
    limit: Annotated[int, Field(ge=5, le=500)] = 100


@router.post("/churn")
async def ml_customer_churn(
    project_id: str,
    req: ChurnRequest = ChurnRequest(),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj, ppath = _require_project_parquet(project_id, db, settings)

    def _run() -> dict[str, Any]:
        with open_context(ppath, fx_rate=proj.settings.exchange_rate) as ctx:
            return compute_churn_and_clv(
                ctx,
                req.filters,
                horizon_days=req.horizon_days,
                limit=req.limit,
            )

    return await asyncio.to_thread(_run)
