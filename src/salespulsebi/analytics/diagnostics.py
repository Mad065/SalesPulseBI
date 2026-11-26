"""Entity Resolution (duplicate detector), Margin Leakage audit, and Smart Alerts / Executive Briefs."""

from __future__ import annotations

import difflib
import unicodedata
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any

from salespulsebi.analytics.overview import compute_overview
from salespulsebi.analytics.query_builder import AnalyticsContext, build_where_clause
from salespulsebi.ml.anomalies import detect_multivariate_anomalies
from salespulsebi.ml.churn_clv import compute_churn_and_clv
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import FilterState
from salespulsebi.storage.parquet_store import ParquetStore, sanitize_json

Q2 = Decimal("0.01")


def _norm_key(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s.strip().lower())
    ascii_str = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return " ".join("".join(ch if ch.isalnum() or ch.isspace() else " " for ch in ascii_str).split())


def detect_entity_duplicates(
    ctx: AnalyticsContext,
    *,
    threshold: float = 0.86,
    max_pairs: int = 20,
) -> dict[str, Any]:
    """Detect near-duplicate product and customer spellings in Data Studio for cleanup."""
    results: dict[str, list[dict[str, Any]]] = {"products": [], "customers": []}

    for col, bucket_key in (("_product", "products"), ("_customer", "customers")):
        if col not in ctx.columns:
            continue
        rows = ParquetStore.query_dicts(
            ctx.con,
            f"""
            SELECT
                CAST({col} AS VARCHAR) AS name,
                COUNT(*) AS rows_count,
                COALESCE(SUM(_revenue), 0.0) AS revenue
            FROM sales
            WHERE {col} IS NOT NULL
            GROUP BY 1
            ORDER BY revenue DESC
            LIMIT 120
            """,
        )
        items = [(str(r["name"]), _norm_key(str(r["name"])), int(r["rows_count"]), float(r["revenue"])) for r in rows]
        pairs: list[dict[str, Any]] = []
        n = len(items)
        for i in range(n):
            name_a, norm_a, cnt_a, rev_a = items[i]
            if not norm_a:
                continue
            for j in range(i + 1, n):
                name_b, norm_b, cnt_b, rev_b = items[j]
                if not norm_b or name_a == name_b:
                    continue
                if norm_a == norm_b:
                    sim = 1.0
                else:
                    if abs(len(norm_a) - len(norm_b)) > max(4, int(len(norm_a) * 0.35)):
                        continue
                    sim = difflib.SequenceMatcher(None, norm_a, norm_b).ratio()
                if sim >= threshold:
                    pairs.append(
                        {
                            "canonical_suggestion": name_a if cnt_a >= cnt_b else name_b,
                            "variant": name_b if cnt_a >= cnt_b else name_a,
                            "similarity_pct": round(sim * 100.0, 1),
                            "combined_rows": cnt_a + cnt_b,
                            "combined_revenue": round(rev_a + rev_b, 2),
                        }
                    )
                    if len(pairs) >= max_pairs:
                        break
            if len(pairs) >= max_pairs:
                break
        pairs.sort(key=lambda x: (-x["similarity_pct"], -x["combined_revenue"]))
        results[bucket_key] = pairs

    return sanitize_json(
        {
            "total_candidates": len(results["products"]) + len(results["customers"]),
            **results,
        }
    )


def detect_margin_leakage(
    ctx: AnalyticsContext,
    filters: FilterState,
) -> dict[str, Any]:
    """Identify channels, categories, or reps where excessive discounts erode gross margin."""
    if "_revenue" not in ctx.columns or "_profit" not in ctx.columns or "_discount" not in ctx.columns:
        return sanitize_json({"available": False, "reason": "requires_revenue_profit_discount", "pockets": []})

    cols = ctx.columns
    where_sql, params = build_where_clause(filters, cols)
    base = ParquetStore.query_one(
        ctx.con,
        f"""
        SELECT
            COALESCE(SUM(_profit) / NULLIF(SUM(_revenue), 0) * 100.0, 0.0) AS avg_margin_pct,
            COALESCE(SUM(_discount) / NULLIF(SUM(_revenue) + SUM(_discount), 0) * 100.0, 0.0) AS avg_disc_pct
        FROM sales {where_sql}
        """,
        params,
    )
    avg_margin = float(base.get("avg_margin_pct") or 0.0)
    avg_disc = float(base.get("avg_disc_pct") or 0.0)

    pockets: list[dict[str, Any]] = []
    with localcontext() as dctx:
        dctx.prec = 28
        for dim_name, col in (
            ("salesperson", "_salesperson"),
            ("channel", "_channel"),
            ("category", "_category"),
            ("product", "_product"),
        ):
            if col not in cols:
                continue
            rows = ParquetStore.query_dicts(
                ctx.con,
                f"""
                SELECT
                    COALESCE(CAST({col} AS VARCHAR), 'N/A') AS segment,
                    COALESCE(SUM(_revenue), 0.0) AS revenue,
                    COALESCE(SUM(_profit), 0.0) AS profit,
                    COALESCE(SUM(_discount), 0.0) AS discount,
                    COALESCE(SUM(_profit) / NULLIF(SUM(_revenue), 0) * 100.0, 0.0) AS margin_pct,
                    COALESCE(SUM(_discount) / NULLIF(SUM(_revenue) + SUM(_discount), 0) * 100.0, 0.0) AS disc_pct
                FROM sales {where_sql}
                GROUP BY 1
                HAVING SUM(_revenue) > 0
                """,
                params,
            )
            for r in rows:
                m_pct = float(r["margin_pct"])
                d_pct = float(r["disc_pct"])
                if d_pct > avg_disc + 1.5 and m_pct < avg_margin - 2.0:
                    rev_d = Decimal(str(round(float(r["revenue"]), 2)))
                    gap_pp = Decimal(str(round(avg_margin - m_pct, 2)))
                    leak_usd = (rev_d * gap_pp / Decimal("100")).quantize(Q2, rounding=ROUND_HALF_EVEN)
                    pockets.append(
                        {
                            "dimension": dim_name,
                            "segment": str(r["segment"]),
                            "revenue": round(float(r["revenue"]), 2),
                            "discount_pct": round(d_pct, 2),
                            "margin_pct": round(m_pct, 2),
                            "benchmark_margin_pct": round(avg_margin, 2),
                            "estimated_leakage_decimal": format(leak_usd, "f"),
                        }
                    )

    pockets.sort(key=lambda x: Decimal(x["estimated_leakage_decimal"]), reverse=True)
    return sanitize_json(
        {
            "available": True,
            "benchmark_margin_pct": round(avg_margin, 2),
            "benchmark_discount_pct": round(avg_disc, 2),
            "pockets": pockets[:15],
        }
    )


def evaluate_and_store_alerts(
    *,
    project_id: str,
    currency: str,
    ctx: AnalyticsContext,
    filters: FilterState,
    db: MetadataDB,
    lang: str = "es",
) -> dict[str, Any]:
    """Generate automated ML + financial alerts and an Executive Brief persisted in SQLite."""
    is_es = lang == "es"
    ov = compute_overview(ctx, filters)
    anoms = detect_multivariate_anomalies(ctx, filters, limit=5)
    churn = compute_churn_and_clv(ctx, filters, limit=10)
    leak = detect_margin_leakage(ctx, filters)

    generated: list[dict[str, object]] = []
    kpis = ov.get("kpis", {})

    rev_k = kpis.get("revenue")
    if rev_k and rev_k.get("delta_pct") is not None and float(rev_k["delta_pct"]) <= -5.0:
        dp = float(rev_k["delta_pct"])
        generated.append(
            {
                "alert_type": "revenue_drop",
                "severity": "high",
                "title": (f"Caída de ingresos de {dp:.1f}%" if is_es else f"Revenue declined {dp:.1f}%"),
                "body": (
                    f"Los ingresos netos ({currency} {rev_k['value']:,.2f}) están por debajo del periodo previo."
                    if is_es
                    else f"Net revenue ({currency} {rev_k['value']:,.2f}) is below the prior period."
                ),
                "metric_value": f"{dp:.1f}%",
            }
        )

    high_anoms = [a for a in anoms.get("anomalies", []) if a.get("severity") == "high"]
    if high_anoms:
        top_a = high_anoms[0]
        generated.append(
            {
                "alert_type": "ml_anomaly",
                "severity": "high",
                "title": (
                    f"Anomalía ML ({top_a['anomaly_type']}) el {top_a['date']}"
                    if is_es
                    else f"ML Anomaly ({top_a['anomaly_type']}) on {top_a['date']}"
                ),
                "body": (
                    f"Score Isolation Forest {top_a['if_score']} · Ingreso {currency} {top_a['revenue']:,.2f} (driver: {top_a.get('root_cause') or 'N/A'})."
                    if is_es
                    else f"Isolation Forest score {top_a['if_score']} · Revenue {currency} {top_a['revenue']:,.2f} (driver: {top_a.get('root_cause') or 'N/A'})."
                ),
                "metric_value": str(top_a["if_score"]),
            }
        )

    if churn.get("available") and churn["summary"]["high_risk_count"] > 0:
        hr = churn["summary"]["high_risk_count"]
        risk_rev = churn["summary"]["revenue_at_high_risk_decimal"]
        generated.append(
            {
                "alert_type": "churn_risk",
                "severity": "warning",
                "title": (
                    f"{hr} clientes en alto riesgo de abandono"
                    if is_es
                    else f"{hr} customers at high churn risk"
                ),
                "body": (
                    f"Cartera histórica expuesta: {currency} {risk_rev}."
                    if is_es
                    else f"Historical revenue exposed: {currency} {risk_rev}."
                ),
                "metric_value": f"{currency} {risk_rev}",
            }
        )

    if leak.get("available") and leak.get("pockets"):
        top_l = leak["pockets"][0]
        generated.append(
            {
                "alert_type": "margin_leakage",
                "severity": "warning",
                "title": (
                    f"Fuga de margen en {top_l['dimension']}: {top_l['segment']}"
                    if is_es
                    else f"Margin leakage in {top_l['dimension']}: {top_l['segment']}"
                ),
                "body": (
                    f"Descuento {top_l['discount_pct']}% con margen {top_l['margin_pct']}% (fuga estimada {currency} {top_l['estimated_leakage_decimal']})."
                    if is_es
                    else f"Discount {top_l['discount_pct']}% with {top_l['margin_pct']}% margin (est. leakage {currency} {top_l['estimated_leakage_decimal']})."
                ),
                "metric_value": f"{currency} {top_l['estimated_leakage_decimal']}",
            }
        )

    stored = db.replace_project_alerts(project_id, generated)
    return sanitize_json({"alerts": stored, "generated_count": len(generated)})
