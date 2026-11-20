"""Data Quality & Health Auditor: 0-100 health score, column profiling, gaps, duplicates, drift."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from salespulsebi.analytics.query_builder import AnalyticsContext, safe_ident
from salespulsebi.storage.parquet_store import ParquetStore, sanitize_json


def compute_quality(
    ctx: AnalyticsContext,
    quality_cache_path: Path | None = None,
) -> dict[str, Any]:
    """Compute live data health score, column profiles, date gaps, and ingestion diagnostics."""
    cols = ctx.columns
    total_row = ParquetStore.query_one(ctx.con, "SELECT COUNT(*) AS total FROM sales")
    total = int(total_row.get("total") or 0)

    cached: dict[str, Any] = {}
    if quality_cache_path and quality_cache_path.exists():
        try:
            cached = json.loads(quality_cache_path.read_text(encoding="utf-8"))
        except Exception:
            cached = {}

    column_health: list[dict[str, Any]] = []
    for c in sorted(cols):
        if c.startswith("_is_") or c in {"_year", "_month", "_quarter", "_dow", "_hour", "_date_day"}:
            continue
        ident = safe_ident(c, cols)
        stat = ParquetStore.query_one(
            ctx.con,
            f"""
            SELECT
                SUM(CASE WHEN {ident} IS NULL THEN 1 ELSE 0 END) AS nulls,
                COUNT(DISTINCT {ident}) AS distinct_cnt
            FROM sales
            """,
        )
        nulls = int(stat.get("nulls") or 0)
        column_health.append(
            {
                "column": c,
                "type": ctx.column_types[c],
                "null_count": nulls,
                "null_pct": round(nulls / max(total, 1) * 100.0, 2),
                "distinct_count": int(stat.get("distinct_cnt") or 0),
            }
        )

    negative_revenue_count = 0
    zero_revenue_count = 0
    if "_revenue" in cols:
        rev_chk = ParquetStore.query_one(
            ctx.con,
            """
            SELECT
                SUM(CASE WHEN _revenue < 0 THEN 1 ELSE 0 END) AS neg_cnt,
                SUM(CASE WHEN _revenue = 0 THEN 1 ELSE 0 END) AS zero_cnt
            FROM sales
            """,
        )
        negative_revenue_count = int(rev_chk.get("neg_cnt") or 0)
        zero_revenue_count = int(rev_chk.get("zero_cnt") or 0)

    # Date gap detection (missing days between min and max)
    date_gaps: list[str] = []
    date_span_days = 0
    if "_date_day" in cols and total > 0:
        d_rows = ParquetStore.query_dicts(
            ctx.con,
            "SELECT DISTINCT _date_day AS d FROM sales WHERE _date_day IS NOT NULL ORDER BY 1",
        )
        present = {r["d"] for r in d_rows if r["d"]}
        if len(present) >= 2:
            d_min = pd.to_datetime(min(present))
            d_max = pd.to_datetime(max(present))
            date_span_days = int((d_max - d_min).days) + 1
            if date_span_days <= 730:
                full_range = {d.strftime("%Y-%m-%d") for d in pd.date_range(d_min, d_max, freq="D")}
                missing = sorted(full_range - present)
                date_gaps = missing[:30]

    health_score = cached.get("health_score")
    if health_score is None:
        health_score = 100.0
        if total > 0 and "_revenue" in cols:
            health_score -= min(15.0, negative_revenue_count / total * 100.0)
        core_nulls = [c["null_pct"] for c in column_health if c["column"] in {"_date", "_revenue", "_product", "_customer"}]
        if core_nulls:
            health_score -= min(25.0, sum(core_nulls) / len(core_nulls) * 0.8)
        health_score = round(max(0.0, min(100.0, health_score)), 1)

    return sanitize_json(
        {
            "health_score": health_score,
            "total_rows": total,
            "negative_revenue_count": negative_revenue_count,
            "zero_revenue_count": zero_revenue_count,
            "date_span_days": date_span_days,
            "date_gap_count": len(date_gaps),
            "date_gaps_sample": date_gaps[:12],
            "ingestion_stats": cached.get("stats", {}),
            "schema_variants": cached.get("schema_variants", []),
            "warnings": cached.get("warnings", []),
            "column_health": column_health,
        }
    )
