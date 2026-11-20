"""Customer Intelligence: RFM Segmentation, New vs Returning, Cohorts, and Concentration Risk."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from salespulsebi.analytics.query_builder import AnalyticsContext, build_where_clause
from salespulsebi.storage.models import FilterState
from salespulsebi.storage.parquet_store import ParquetStore, sanitize_json


def _score_quintiles(series: pd.Series, *, ascending: bool) -> pd.Series:
    """Robustly assign 1..5 quintile scores even on small or heavily tied series."""
    if len(series) <= 1 or series.nunique() <= 1:
        return pd.Series(3, index=series.index, dtype=int)
    ranked = series.rank(method="first", ascending=ascending)
    bins = min(5, len(ranked))
    labels = list(range(1, bins + 1))
    scored = pd.qcut(ranked, q=bins, labels=labels)
    if bins < 5:
        # Scale up cleanly to 1..5 range
        return np.ceil(scored.astype(float) * 5.0 / bins).astype(int)
    return scored.astype(int)


def _classify_rfm(r: int, f: int, m: int) -> str:
    fm = (f + m) / 2.0
    if r >= 4 and fm >= 4.0:
        return "Champions"
    if r >= 3 and fm >= 3.5:
        return "Loyal"
    if r >= 4 and fm >= 2.0:
        return "Potential Loyalist"
    if r == 5 and f <= 2:
        return "New"
    if r >= 3 and fm >= 2.0:
        return "Promising"
    if r <= 2 and fm >= 3.5:
        return "At Risk"
    if r <= 2 and fm >= 2.0:
        return "Need Attention"
    return "Hibernating"


def compute_customers(ctx: AnalyticsContext, filters: FilterState) -> dict[str, Any]:
    """Compute RFM segmentation, New vs Returning, Cohort retention matrix, and concentration."""
    if not ctx.capabilities.get("customers"):
        return sanitize_json({"available": False, "reason": "requires_customer_and_revenue"})

    cols = ctx.columns
    where_sql, params = build_where_clause(filters, cols)
    orders_expr = "COUNT(DISTINCT _order_id)" if "_order_id" in cols else "COUNT(*)"
    has_date = "_date_day" in cols

    cust_rows = ParquetStore.query_dicts(
        ctx.con,
        f"""
        SELECT
            _customer AS customer,
            COALESCE(SUM(_revenue), 0.0) AS monetary,
            {orders_expr} AS frequency
            {', MAX(_date_day) AS last_date, MIN(_date_day) AS first_date' if has_date else ''}
        FROM sales
        {where_sql}
        {"AND" if where_sql else "WHERE"} _customer IS NOT NULL
        GROUP BY _customer
        ORDER BY monetary DESC
        """,
        params,
    )

    if not cust_rows:
        return sanitize_json({"available": False, "reason": "no_customer_rows"})

    cdf = pd.DataFrame(cust_rows)
    total_customers = len(cdf)
    total_rev = float(cdf["monetary"].sum())
    repeat_customers = int((cdf["frequency"] > 1).sum())

    # Concentration risk (top 1%, 5%, 10% share)
    sorted_m = cdf["monetary"].sort_values(ascending=False).reset_index(drop=True)
    def top_pct_share(pct: float) -> float:
        k = max(1, int(np.ceil(total_customers * pct)))
        return round(float(sorted_m.head(k).sum()) / total_rev * 100.0, 1) if total_rev > 0 else 0.0

    concentration = {
        "top_1_pct_share": top_pct_share(0.01),
        "top_5_pct_share": top_pct_share(0.05),
        "top_10_pct_share": top_pct_share(0.10),
    }

    # RFM Scoring
    rfm_segments_summary: list[dict[str, Any]] = []
    if has_date:
        max_date = pd.to_datetime(cdf["last_date"], errors="coerce").max()
        last_dt = pd.to_datetime(cdf["last_date"], errors="coerce")
        cdf["recency_days"] = (max_date - last_dt).dt.days.fillna(999).astype(int)
        cdf["r_score"] = _score_quintiles(cdf["recency_days"], ascending=False)
    else:
        cdf["recency_days"] = 0
        cdf["r_score"] = 3

    cdf["f_score"] = _score_quintiles(cdf["frequency"], ascending=True)
    cdf["m_score"] = _score_quintiles(cdf["monetary"], ascending=True)
    cdf["segment"] = [
        _classify_rfm(int(r), int(f), int(m))
        for r, f, m in zip(cdf["r_score"], cdf["f_score"], cdf["m_score"], strict=False)
    ]
    cdf["avg_ticket"] = np.where(cdf["frequency"] > 0, cdf["monetary"] / cdf["frequency"], 0.0)

    seg_grp = (
        cdf.groupby("segment", as_index=False)
        .agg(
            customers=("customer", "count"),
            revenue=("monetary", "sum"),
            avg_frequency=("frequency", "mean"),
            avg_recency=("recency_days", "mean"),
        )
        .sort_values("revenue", ascending=False)
    )
    for _, row in seg_grp.iterrows():
        rfm_segments_summary.append(
            {
                "segment": row["segment"],
                "customers": int(row["customers"]),
                "customers_pct": round(int(row["customers"]) / total_customers * 100.0, 1),
                "revenue": round(float(row["revenue"]), 2),
                "revenue_pct": round(float(row["revenue"]) / total_rev * 100.0, 1) if total_rev > 0 else 0.0,
                "avg_frequency": round(float(row["avg_frequency"]), 2),
                "avg_recency": round(float(row["avg_recency"]), 1),
            }
        )

    # New vs Returning & Cohort Retention Matrix
    new_vs_returning: list[dict[str, Any]] = []
    cohorts: dict[str, Any] = {"months": [], "rows": []}
    if has_date and "_month" in cols:
        nvr_rows = ParquetStore.query_dicts(
            ctx.con,
            f"""
            WITH first_seen AS (
                SELECT _customer AS cust, MIN(_month) AS cohort_month
                FROM sales
                {where_sql}
                {"AND" if where_sql else "WHERE"} _customer IS NOT NULL AND _month IS NOT NULL
                GROUP BY 1
            ),
            monthly AS (
                SELECT
                    s._month AS period,
                    s._customer AS cust,
                    f.cohort_month,
                    SUM(s._revenue) AS rev
                FROM sales s
                JOIN first_seen f ON s._customer = f.cust
                {where_sql}
                {"AND" if where_sql else "WHERE"} s._customer IS NOT NULL AND s._month IS NOT NULL
                GROUP BY 1, 2, 3
            )
            SELECT
                period,
                SUM(CASE WHEN period = cohort_month THEN rev ELSE 0 END) AS new_revenue,
                SUM(CASE WHEN period > cohort_month THEN rev ELSE 0 END) AS returning_revenue,
                COUNT(DISTINCT CASE WHEN period = cohort_month THEN cust END) AS new_customers,
                COUNT(DISTINCT CASE WHEN period > cohort_month THEN cust END) AS returning_customers
            FROM monthly
            GROUP BY 1
            ORDER BY 1
            """,
            params + params,
        )
        new_vs_returning = nvr_rows

        cohort_raw = ParquetStore.query_dicts(
            ctx.con,
            f"""
            WITH first_seen AS (
                SELECT _customer AS cust, MIN(DATE_TRUNC('month', _date)) AS cohort_dt
                FROM sales
                {where_sql}
                {"AND" if where_sql else "WHERE"} _customer IS NOT NULL AND _date IS NOT NULL
                GROUP BY 1
            ),
            activity AS (
                SELECT DISTINCT
                    s._customer AS cust,
                    f.cohort_dt,
                    DATE_TRUNC('month', s._date) AS active_dt
                FROM sales s
                JOIN first_seen f ON s._customer = f.cust
                {where_sql}
                {"AND" if where_sql else "WHERE"} s._customer IS NOT NULL AND s._date IS NOT NULL
            )
            SELECT
                strftime(cohort_dt, '%Y-%m') AS cohort,
                CAST(DATE_DIFF('month', cohort_dt, active_dt) AS INTEGER) AS month_offset,
                COUNT(DISTINCT cust) AS active_customers
            FROM activity
            WHERE DATE_DIFF('month', cohort_dt, active_dt) BETWEEN 0 AND 11
            GROUP BY 1, 2
            ORDER BY 1, 2
            """,
            params + params,
        )
        cohort_names = sorted({r["cohort"] for r in cohort_raw})[-12:]
        max_offset = max([int(r["month_offset"]) for r in cohort_raw], default=0)
        cohort_lookup = {(r["cohort"], int(r["month_offset"])): int(r["active_customers"]) for r in cohort_raw}
        cohort_matrix_rows: list[dict[str, Any]] = []
        for cname in cohort_names:
            base = cohort_lookup.get((cname, 0), 0)
            retention = []
            for off in range(max_offset + 1):
                cnt = cohort_lookup.get((cname, off))
                pct = round(cnt / base * 100.0, 1) if (cnt is not None and base > 0) else None
                retention.append(pct)
            cohort_matrix_rows.append({"cohort": cname, "size": base, "retention": retention})
        cohorts = {"max_offset": max_offset, "rows": cohort_matrix_rows}

    top_customers = cdf.head(100).to_dict(orient="records")

    return sanitize_json(
        {
            "available": True,
            "summary": {
                "total_customers": total_customers,
                "repeat_customers": repeat_customers,
                "repeat_rate_pct": round(repeat_customers / total_customers * 100.0, 1),
                "avg_revenue_per_customer": round(total_rev / total_customers, 2),
                "concentration": concentration,
            },
            "rfm_segments": rfm_segments_summary,
            "new_vs_returning": new_vs_returning,
            "cohorts": cohorts,
            "top_customers": top_customers,
        }
    )
