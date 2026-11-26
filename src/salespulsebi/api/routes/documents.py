"""FastAPI routes for structured OCR document extraction, local DuckDB hybrid RAG Q&A, and reconciliation."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from salespulsebi.ai.extractor import commit_document_to_dataset, extract_structured_document
from salespulsebi.ai.rag import ask_project_documents, index_document_for_rag
from salespulsebi.ai.router import MultiProviderLLMRouter
from salespulsebi.ai.vector_store import DuckDBHybridStore
from salespulsebi.analytics.query_builder import open_context
from salespulsebi.analytics.reconciliation import reconcile_documents_with_sales
from salespulsebi.api.deps import get_app_settings, get_db, get_pipeline
from salespulsebi.config import Settings
from salespulsebi.ingestion.pipeline import ProjectIngestionPipeline
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import FilterState

router = APIRouter(prefix="/projects/{project_id}/documents", tags=["documents"])

ALLOWED_DOC_EXTS = frozenset({".pdf", ".txt", ".md"})


class DocumentCommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extracted: dict[str, Any] | None = None


class DocumentAskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=2, max_length=2000)
    filters: FilterState | None = None
    doc_type: str | None = None
    currency: str | None = Field(default=None, max_length=8)
    as_of_date: str | None = Field(default=None, max_length=16)
    lang: Literal["es", "en"] = "es"


class ReconcileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_tolerance_days: Annotated[int, Field(ge=0, le=30)] = 3
    amount_tolerance: Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("100"))] = Decimal("0.01")


def _safe_doc_dest(docs_dir: Path, filename: str) -> Path:
    base = Path(filename).name
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in base)[:120] or "document.pdf"
    dest = (docs_dir / safe).resolve()
    if not str(dest).startswith(str(docs_dir.resolve())):
        raise HTTPException(status_code=400, detail="Invalid filename")
    return dest


@router.get("")
def list_project_documents(
    project_id: str,
    db: MetadataDB = Depends(get_db),
) -> dict[str, Any]:
    if not db.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return {"documents": db.list_documents(project_id)}


@router.post("/extract")
async def extract_document_endpoint(
    project_id: str,
    file: UploadFile = File(...),
    doc_type: str = Form(default="auto"),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj = db.get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    fname = file.filename or "document.pdf"
    if Path(fname).suffix.lower() not in ALLOWED_DOC_EXTS:
        raise HTTPException(status_code=400, detail="Supported document types: .pdf, .txt, .md")

    content = await file.read()
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Document exceeds upload size limit")

    dest = _safe_doc_dest(settings.project_docs_dir(project_id), fname)
    dest.write_bytes(content)

    llm_router = MultiProviderLLMRouter(settings, db)
    return await extract_structured_document(
        project_id=project_id,
        file_path=dest,
        filename=dest.name,
        requested_type=doc_type,
        default_currency=proj.currency,
        db=db,
        router=llm_router,
    )


@router.post("/{doc_id}/commit")
async def commit_document_endpoint(
    project_id: str,
    doc_id: str,
    payload: DocumentCommitRequest = DocumentCommitRequest(),
    db: MetadataDB = Depends(get_db),
    pipeline: ProjectIngestionPipeline = Depends(get_pipeline),
) -> dict[str, Any]:
    doc = db.get_document(project_id, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        return await asyncio.to_thread(
            commit_document_to_dataset,
            project_id=project_id,
            doc_record=doc,
            override_extracted=payload.extracted,
            db=db,
            pipeline=pipeline,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/index")
async def index_rag_document_endpoint(
    project_id: str,
    file: UploadFile = File(...),
    doc_type: str = Form(default="auto"),
    as_of_date: str | None = Form(default=None),
    period_start: str | None = Form(default=None),
    period_end: str | None = Form(default=None),
    currency: str | None = Form(default=None),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj = db.get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    fname = file.filename or "report.pdf"
    if Path(fname).suffix.lower() not in ALLOWED_DOC_EXTS:
        raise HTTPException(status_code=400, detail="Supported document types: .pdf, .txt, .md")

    content = await file.read()
    dest = _safe_doc_dest(settings.project_docs_dir(project_id), fname)
    dest.write_bytes(content)

    llm_router = MultiProviderLLMRouter(settings, db)
    return await index_document_for_rag(
        project_id=project_id,
        file_path=dest,
        filename=dest.name,
        rag_db_path=settings.project_rag_db_path(project_id),
        db=db,
        router=llm_router,
        doc_type=doc_type,
        as_of_date=as_of_date,
        period_start=period_start,
        period_end=period_end,
        currency=currency or proj.currency,
    )


@router.post("/ask")
async def ask_documents_endpoint(
    project_id: str,
    payload: DocumentAskRequest,
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    if not db.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    llm_router = MultiProviderLLMRouter(settings, db)
    return await ask_project_documents(
        project_id=project_id,
        question=payload.question,
        rag_db_path=settings.project_rag_db_path(project_id),
        router=llm_router,
        filters=payload.filters,
        doc_type=payload.doc_type,
        currency=payload.currency,
        as_of_date=payload.as_of_date,
        lang=payload.lang,
    )


@router.post("/reconcile")
async def reconcile_documents_endpoint(
    project_id: str,
    payload: ReconcileRequest = ReconcileRequest(),
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    proj = db.get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    ppath = settings.project_parquet_path(project_id)
    if not ppath.exists():
        raise HTTPException(status_code=404, detail="Project has no ingested data yet")
    docs = db.list_documents(project_id)

    def _run() -> dict[str, Any]:
        with open_context(ppath, fx_rate=proj.settings.exchange_rate) as ctx:
            return reconcile_documents_with_sales(
                ctx,
                docs,
                date_tolerance_days=payload.date_tolerance_days,
                amount_tolerance=payload.amount_tolerance,
            )

    return await asyncio.to_thread(_run)


@router.delete("/{doc_id}")
def delete_document_endpoint(
    project_id: str,
    doc_id: str,
    db: MetadataDB = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, bool]:
    doc = db.get_document(project_id, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    rag_path = settings.project_rag_db_path(project_id)
    if rag_path.exists():
        DuckDBHybridStore(rag_path).delete_document(project_id, doc_id)
    fp = Path(str(doc["file_path"]))
    docs_dir = settings.project_docs_dir(project_id).resolve()
    if fp.exists() and str(fp.resolve()).startswith(str(docs_dir)):
        fp.unlink()
    db.delete_document(project_id, doc_id)
    return {"deleted": True}
