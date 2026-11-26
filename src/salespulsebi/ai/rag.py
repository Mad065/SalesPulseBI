"""Source-grounded Financial RAG pipeline: table-aware chunking, hybrid retrieval, citations, and numeric verification."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from salespulsebi.ai.extractor import detect_document_type, extract_document_pages
from salespulsebi.ai.guardrails import (
    detect_prompt_injection,
    verify_numeric_claims,
    wrap_retrieved_record,
    wrap_untrusted_input,
)
from salespulsebi.ai.router import MultiProviderLLMRouter
from salespulsebi.ai.schemas import AgentCitation, StructuredAgentResponse
from salespulsebi.ai.vector_store import DuckDBHybridStore, RAGChunk
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import FilterState
from salespulsebi.storage.parquet_store import sanitize_json


def chunk_financial_page(
    page_text: str,
    *,
    filename: str,
    page_num: int,
    currency: str | None = None,
    as_of_date: str | None = None,
    max_chars: int = 900,
) -> list[tuple[str, str]]:
    """
    Table-aware chunker: preserves pipe/tabular headers on every chunk and avoids splitting table rows mid-line.
    Returns list of `(section_title, chunk_content)`.
    """
    lines = [ln.rstrip() for ln in (page_text or "").splitlines() if ln.strip()]
    if not lines:
        return []

    meta_header = f"[Source: {filename} | p.{page_num}"
    if currency:
        meta_header += f" | Currency: {currency}"
    if as_of_date:
        meta_header += f" | As-Of: {as_of_date}"
    meta_header += "]"

    chunks: list[tuple[str, str]] = []
    cur_section = f"Page {page_num}"
    active_table_header: str | None = None
    buf: list[str] = [meta_header]
    buf_len = len(meta_header)

    for ln in lines:
        # Track heading lines
        if len(ln) <= 80 and (ln.isupper() or ln.endswith(":") or ln.startswith("#")):
            cur_section = ln.lstrip("# ").rstrip(":")
            active_table_header = None

        # Detect pipe-delimited table header row
        if "|" in ln and active_table_header is None:
            active_table_header = ln

        if buf_len + len(ln) + 1 > max_chars and len(buf) > 1:
            chunks.append((cur_section, "\n".join(buf)))
            buf = [meta_header]
            if active_table_header and "|" in ln:
                buf.append(active_table_header)
            buf_len = sum(len(x) + 1 for x in buf)

        buf.append(ln)
        buf_len += len(ln) + 1

    if len(buf) > 1:
        chunks.append((cur_section, "\n".join(buf)))
    return chunks


async def index_document_for_rag(
    *,
    project_id: str,
    file_path: Path,
    filename: str,
    rag_db_path: Path,
    db: MetadataDB,
    router: MultiProviderLLMRouter,
    doc_type: str = "auto",
    as_of_date: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    currency: str | None = None,
    doc_id: str | None = None,
) -> dict[str, Any]:
    """Parse, table-aware chunk, embed, and index a financial document into the local DuckDB RAG store."""
    pages, engine = extract_document_pages(file_path)
    full_text = "\n".join(pages)
    resolved_type = detect_document_type(full_text, doc_type)
    file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()[:16]

    doc_rec = db.upsert_document(
        doc_id=doc_id,
        project_id=project_id,
        filename=filename,
        doc_type=resolved_type,
        file_path=str(file_path),
        file_hash=file_hash,
        page_count=len(pages),
        as_of_date=as_of_date,
        period_start=period_start,
        period_end=period_end,
        currency=currency.upper() if currency else None,
        status="indexed",
        indexed_in_rag=True,
    )
    final_doc_id = str(doc_rec["id"])

    raw_chunks: list[tuple[int, str, str]] = []
    for p_idx, p_text in enumerate(pages, start=1):
        for sec, content in chunk_financial_page(
            p_text,
            filename=filename,
            page_num=p_idx,
            currency=currency,
            as_of_date=as_of_date,
        ):
            raw_chunks.append((p_idx, sec, content))

    texts = [c[2] for c in raw_chunks]
    embeddings, emb_engine = await router.embed_texts(texts)

    rag_chunks = [
        RAGChunk(
            chunk_id=f"{final_doc_id}_c{idx}",
            doc_id=final_doc_id,
            project_id=project_id,
            filename=filename,
            doc_type=resolved_type,
            page_num=p_num,
            section_title=sec,
            content=content,
            embedding=emb,
            as_of_date=as_of_date,
            period_start=period_start,
            period_end=period_end,
            currency=currency.upper() if currency else None,
        )
        for idx, ((p_num, sec, content), emb) in enumerate(
            zip(raw_chunks, embeddings, strict=False), start=1
        )
    ]

    store = DuckDBHybridStore(rag_db_path)
    stored_count = store.upsert_chunks(rag_chunks)

    return sanitize_json(
        {
            "document": doc_rec,
            "reader_engine": engine,
            "embedding_engine": emb_engine,
            "chunks_indexed": stored_count,
        }
    )


async def search_project_rag(
    *,
    query: str,
    project_id: str,
    rag_db_path: Path,
    router: MultiProviderLLMRouter,
    filters: FilterState | None = None,
    top_k: int = 5,
    doc_type: str | None = None,
    currency: str | None = None,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Run hybrid BM25 + Vector search with mandatory project_id and temporal pre-filters."""
    if not rag_db_path.exists():
        return {"hits": [], "embedding_engine": "none"}

    embs, emb_engine = await router.embed_texts([query])
    q_emb = embs[0] if embs else []
    store = DuckDBHybridStore(rag_db_path)
    hits = store.hybrid_search(
        project_id=project_id,
        query=query,
        query_embedding=q_emb,
        top_k=top_k,
        doc_type=doc_type,
        currency=currency,
        as_of_date=as_of_date,
        date_from=filters.date_from if filters else None,
        date_to=filters.date_to if filters else None,
    )
    return sanitize_json({"hits": hits, "embedding_engine": emb_engine})


async def ask_project_documents(
    *,
    project_id: str,
    question: str,
    rag_db_path: Path,
    router: MultiProviderLLMRouter,
    filters: FilterState | None = None,
    doc_type: str | None = None,
    currency: str | None = None,
    as_of_date: str | None = None,
    lang: str = "es",
) -> dict[str, Any]:
    """Source-grounded Q&A over indexed financial documents with strict citations and abstention."""
    is_es = lang == "es"
    blocked, reason = detect_prompt_injection(question)
    if blocked:
        return sanitize_json(
            {
                "status": "blocked",
                "confidence": "low",
                "answer_markdown": (
                    "Consulta bloqueada por guardrails de seguridad."
                    if is_es
                    else "Query blocked by security guardrails."
                ),
                "citations": [],
                "hits": [],
                "grounded": True,
                "reason": reason,
            }
        )

    search_res = await search_project_rag(
        query=question,
        project_id=project_id,
        rag_db_path=rag_db_path,
        router=router,
        filters=filters,
        top_k=5,
        doc_type=doc_type,
        currency=currency,
        as_of_date=as_of_date,
    )
    hits = search_res.get("hits") or []

    # Mandatory abstention when no relevant records match the project/temporal filter scope
    if not hits:
        return sanitize_json(
            {
                "status": "insufficient_data",
                "confidence": "low",
                "answer_markdown": (
                    "No se encontró información en los documentos indexados de este proyecto para el alcance o periodo consultado."
                    if is_es
                    else "No supporting records were found in this project's indexed documents for the requested scope or period."
                ),
                "citations": [],
                "hits": [],
                "grounded": True,
            }
        )

    citations = [
        AgentCitation(
            id=f"{h['doc_id']}:p{h['page_num']}",
            label=f"{h['filename']} · p.{h['page_num']}",
            kind="document",
            target_tab="ai_ml",
            page_num=int(h["page_num"]),
            doc_id=str(h["doc_id"]),
        )
        for h in hits[:4]
    ]

    # Try LLM synthesis when provider enabled, else deterministic grounded excerpt answer
    cfg = router.get_config()
    if cfg.is_enabled:
        records_blob = "\n\n".join(
            wrap_retrieved_record(f"{h['filename']}:p.{h['page_num']}", h["content"])
            for h in hits[:4]
        )
        parsed, _ = await router.complete_structured(
            schema_cls=StructuredAgentResponse,
            system_prompt=(
                "Answer ONLY using facts and exact numbers from the <retrieved_record> blocks. "
                "Cite every factual claim with [filename · p.X]. "
                "If the retrieved records do not answer the question, set status='insufficient_data'. "
                f"Respond in {'Spanish' if is_es else 'English'}."
            ),
            user_prompt=f"{wrap_untrusted_input(question)}\n\nRetrieved Records:\n{records_blob}",
            tier="reasoning",
            project_id=project_id,
            feature_name="financial_rag_qa",
        )
        if parsed is not None:
            check = verify_numeric_claims(parsed.answer_markdown, hits)
            return sanitize_json(
                {
                    "status": parsed.status,
                    "confidence": parsed.confidence,
                    "answer_markdown": parsed.answer_markdown,
                    "citations": [c.model_dump() for c in (parsed.citations or citations)],
                    "hits": hits,
                    "grounded": check["grounded"],
                    "unverified_numbers": check["unverified_numbers"],
                }
            )

    # 100% deterministic grounded response (exact excerpts + page citations)
    excerpts = []
    for h in hits[:3]:
        body = "\n".join(
            ln for ln in str(h["content"]).splitlines() if not ln.startswith("[Source:")
        ).strip()
        excerpts.append(f"**[{h['filename']} · p.{h['page_num']}]** {body[:380]}")
    answer_md = (
        ("Evidencia encontrada en tus documentos:\n\n" if is_es else "Supporting evidence from your documents:\n\n")
        + "\n\n".join(excerpts)
    )
    return sanitize_json(
        {
            "status": "ok",
            "confidence": "high" if hits[0]["bm25_score"] > 0 else "medium",
            "answer_markdown": answer_md,
            "citations": [c.model_dump() for c in citations],
            "hits": hits,
            "grounded": True,
            "unverified_numbers": [],
        }
    )
