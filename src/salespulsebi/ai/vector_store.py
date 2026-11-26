"""100% Local-First DuckDB Hybrid Vector + BM25 Store with hard temporal/project pre-filtering."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import duckdb
import numpy as np

TOKEN_RE = re.compile(r"[A-Za-z0-9_\-.$%]+")


def tokenize_financial_text(text: str) -> list[str]:
    """Preserve exact financial identifiers (SKUs, Q1/Q4, 10-K, decimals, percentages) for BM25."""
    return [tok.lower() for tok in TOKEN_RE.findall(text or "") if len(tok) >= 2]


@dataclass
class RAGChunk:
    chunk_id: str
    doc_id: str
    project_id: str
    filename: str
    doc_type: str
    page_num: int
    section_title: str
    content: str
    embedding: list[float]
    as_of_date: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    currency: str | None = None


class VectorStoreBackend(Protocol):
    """Decoupled interface allowing DuckDB local store by default and optional external stores later."""

    def upsert_chunks(self, chunks: list[RAGChunk]) -> int: ...

    def delete_document(self, project_id: str, doc_id: str) -> None: ...

    def hybrid_search(
        self,
        *,
        project_id: str,
        query: str,
        query_embedding: list[float],
        top_k: int = 5,
        doc_type: str | None = None,
        currency: str | None = None,
        as_of_date: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]: ...


class DuckDBHybridStore:
    """
    Zero-server, 100% local hybrid retrieval engine backed by DuckDB (`project_rag.duckdb`).
    Enforces hard SQL metadata pre-filters BEFORE BM25 + Cosine Vector Reciprocal Rank Fusion (RRF).
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.db_path))

    def _init_schema(self) -> None:
        con = self._connect()
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS doc_chunks (
                    chunk_id VARCHAR PRIMARY KEY,
                    doc_id VARCHAR NOT NULL,
                    project_id VARCHAR NOT NULL,
                    filename VARCHAR NOT NULL,
                    doc_type VARCHAR NOT NULL,
                    as_of_date VARCHAR,
                    period_start VARCHAR,
                    period_end VARCHAR,
                    currency VARCHAR,
                    page_num INTEGER NOT NULL,
                    section_title VARCHAR NOT NULL DEFAULT '',
                    content VARCHAR NOT NULL,
                    tokens_json VARCHAR NOT NULL DEFAULT '[]',
                    embedding_json VARCHAR NOT NULL DEFAULT '[]'
                )
                """
            )
        finally:
            con.close()

    def upsert_chunks(self, chunks: list[RAGChunk]) -> int:
        if not chunks:
            return 0
        con = self._connect()
        try:
            doc_ids = {(c.project_id, c.doc_id) for c in chunks}
            for pid, did in doc_ids:
                con.execute(
                    "DELETE FROM doc_chunks WHERE project_id = ? AND doc_id = ?",
                    [pid, did],
                )
            for c in chunks:
                toks = tokenize_financial_text(f"{c.section_title} {c.content}")
                con.execute(
                    """
                    INSERT INTO doc_chunks (
                        chunk_id, doc_id, project_id, filename, doc_type,
                        as_of_date, period_start, period_end, currency,
                        page_num, section_title, content, tokens_json, embedding_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        c.chunk_id,
                        c.doc_id,
                        c.project_id,
                        c.filename,
                        c.doc_type,
                        c.as_of_date,
                        c.period_start,
                        c.period_end,
                        c.currency,
                        c.page_num,
                        c.section_title,
                        c.content,
                        json.dumps(toks),
                        json.dumps(c.embedding),
                    ],
                )
            return len(chunks)
        finally:
            con.close()

    def delete_document(self, project_id: str, doc_id: str) -> None:
        con = self._connect()
        try:
            con.execute(
                "DELETE FROM doc_chunks WHERE project_id = ? AND doc_id = ?",
                [project_id, doc_id],
            )
        finally:
            con.close()

    def hybrid_search(
        self,
        *,
        project_id: str,
        query: str,
        query_embedding: list[float],
        top_k: int = 5,
        doc_type: str | None = None,
        currency: str | None = None,
        as_of_date: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        """Apply strict project + temporal metadata pre-filters, then rank via BM25 + Cosine RRF."""
        clauses = ["project_id = ?"]
        params: list[Any] = [project_id]

        if doc_type:
            clauses.append("doc_type = ?")
            params.append(doc_type)
        if currency:
            clauses.append("(currency IS NULL OR currency = ?)")
            params.append(currency.upper())
        if as_of_date:
            clauses.append("(as_of_date IS NULL OR as_of_date <= ?)")
            params.append(as_of_date[:10])
        if date_from:
            clauses.append("(period_end IS NULL OR period_end >= ?)")
            params.append(date_from[:10])
        if date_to:
            clauses.append("(period_start IS NULL OR period_start <= ?)")
            params.append(date_to[:10])

        where_sql = " AND ".join(clauses)
        con = self._connect()
        try:
            rows = con.execute(
                f"""
                SELECT
                    chunk_id, doc_id, filename, doc_type, as_of_date,
                    period_start, period_end, currency, page_num,
                    section_title, content, tokens_json, embedding_json
                FROM doc_chunks
                WHERE {where_sql}
                """,
                params,
            ).fetchall()
        finally:
            con.close()

        if not rows:
            return []

        q_tokens = tokenize_financial_text(query)
        q_vec = np.asarray(query_embedding, dtype=np.float32)
        q_norm = float(np.linalg.norm(q_vec))

        docs_tokens: list[list[str]] = []
        parsed_rows: list[dict[str, Any]] = []
        for r in rows:
            toks = json.loads(r[11] or "[]")
            emb = json.loads(r[12] or "[]")
            docs_tokens.append(toks)
            parsed_rows.append(
                {
                    "chunk_id": r[0],
                    "doc_id": r[1],
                    "filename": r[2],
                    "doc_type": r[3],
                    "as_of_date": r[4],
                    "period_start": r[5],
                    "period_end": r[6],
                    "currency": r[7],
                    "page_num": int(r[8]),
                    "section_title": r[9],
                    "content": r[10],
                    "_embedding": emb,
                }
            )

        # 1. Exact BM25 lexical scoring
        n_docs = len(docs_tokens)
        avg_dl = sum(len(t) for t in docs_tokens) / max(n_docs, 1)
        df_counts: Counter[str] = Counter()
        for toks in docs_tokens:
            for u in set(toks):
                df_counts[u] += 1

        bm25_scores: list[float] = []
        k1, b = 1.5, 0.75
        for toks in docs_tokens:
            tf = Counter(toks)
            dl = len(toks)
            score = 0.0
            for qt in q_tokens:
                f = tf.get(qt, 0)
                if f == 0:
                    continue
                df_val = df_counts.get(qt, 0)
                idf = math.log(1.0 + (n_docs - df_val + 0.5) / (df_val + 0.5))
                score += idf * (f * (k1 + 1.0)) / (f + k1 * (1.0 - b + b * dl / max(avg_dl, 1.0)))
            bm25_scores.append(score)

        # 2. Vector cosine similarity
        vec_scores: list[float] = []
        for item in parsed_rows:
            emb = item.pop("_embedding")
            if q_norm > 1e-9 and emb and len(emb) == len(q_vec):
                ev = np.asarray(emb, dtype=np.float32)
                en = float(np.linalg.norm(ev))
                cos = float(np.dot(q_vec, ev) / (q_norm * en)) if en > 1e-9 else 0.0
            else:
                cos = 0.0
            vec_scores.append(cos)

        # 3. Reciprocal Rank Fusion (RRF)
        bm25_order = {
            idx: rank
            for rank, idx in enumerate(
                sorted(range(n_docs), key=lambda i: bm25_scores[i], reverse=True), start=1
            )
        }
        vec_order = {
            idx: rank
            for rank, idx in enumerate(
                sorted(range(n_docs), key=lambda i: vec_scores[i], reverse=True), start=1
            )
        }

        scored: list[dict[str, Any]] = []
        for i, item in enumerate(parsed_rows):
            rrf = (1.0 / (60.0 + bm25_order[i])) + (1.0 / (60.0 + vec_order[i]))
            item["bm25_score"] = round(bm25_scores[i], 4)
            item["vector_score"] = round(vec_scores[i], 4)
            item["rrf_score"] = round(rrf, 5)
            scored.append(item)

        scored.sort(
            key=lambda x: (x["bm25_score"] > 0 or x["vector_score"] > 0.12, x["rrf_score"], x["bm25_score"]),
            reverse=True,
        )
        # Filter out completely unrelated zero-overlap chunks
        relevant = [s for s in scored if s["bm25_score"] > 0.0 or s["vector_score"] >= 0.15]
        return relevant[:top_k]
