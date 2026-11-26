"""AI Copilot SSE streaming, multi-provider status, connection testing, and telemetry endpoints."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from salespulsebi.ai.agent import stream_copilot_response
from salespulsebi.ai.router import MultiProviderLLMRouter
from salespulsebi.ai.schemas import ChatStreamRequest, ConnectionTestRequest
from salespulsebi.ai.tools import BoundToolContext
from salespulsebi.api.deps import get_app_settings, get_db
from salespulsebi.config import Settings
from salespulsebi.storage.database import MetadataDB

router = APIRouter(tags=["ai"])


@router.get("/ai/status")
def get_ai_status(
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    return MultiProviderLLMRouter(settings, db).get_public_status()


@router.get("/ai/telemetry")
def get_ai_telemetry(
    project_id: str | None = Query(default=None),
    limit: int = Query(default=40, ge=1, le=200),
    db: MetadataDB = Depends(get_db),
) -> dict[str, Any]:
    return db.get_ai_telemetry_summary(project_id=project_id, limit=limit)


@router.post("/ai/test-connection")
async def test_ai_connection(
    payload: ConnectionTestRequest = ConnectionTestRequest(),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    llm_router = MultiProviderLLMRouter(settings, db)
    return await llm_router.test_connection(
        provider=payload.provider,
        base_url=payload.base_url,
        model=payload.model,
    )


@router.post("/projects/{project_id}/ai/chat/stream")
async def chat_stream(
    project_id: str,
    payload: ChatStreamRequest,
    raw_request: Request,
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> StreamingResponse:
    proj = db.get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    ppath = settings.project_parquet_path(project_id)
    if not ppath.exists():
        raise HTTPException(status_code=404, detail="Project has no ingested data yet")

    bound = BoundToolContext(
        project=proj,
        parquet_path=ppath,
        models_dir=settings.project_models_dir(project_id),
        rag_db_path=settings.project_rag_db_path(project_id),
        db=db,
        filters=payload.filters,
        fx_rate=proj.settings.exchange_rate,
    )
    llm_router = MultiProviderLLMRouter(settings, db)

    # Optional RAG search hook (activates automatically once Phase 3 rag module exists)
    rag_search_fn = None
    try:
        from salespulsebi.ai.rag import search_project_rag

        async def _rag_hook(**kwargs: Any) -> dict[str, Any]:
            return await search_project_rag(
                rag_db_path=bound.rag_db_path,
                router=llm_router,
                **kwargs,
            )

        rag_search_fn = _rag_hook
    except Exception:
        rag_search_fn = None

    async def event_generator() -> AsyncIterator[str]:
        gen = stream_copilot_response(
            request=payload,
            bound=bound,
            router=llm_router,
            rag_search_fn=rag_search_fn,
        )
        try:
            async for frame in gen:
                if await raw_request.is_disconnected():
                    break
                yield frame
        except asyncio.CancelledError:
            return
        finally:
            await gen.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
