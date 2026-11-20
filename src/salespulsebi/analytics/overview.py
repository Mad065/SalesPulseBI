"""Executive Overview analytics: KPIs, comparisons, sparklines, and ticket distribution."""

from __future__ import annotations

from typing import Any

from salespulsebi.analytics.query_builder import (
    AnalyticsContext,
    build_where_clause,
    compute_previous_period,
    get_filter_options,
)
from salespulsebi.storage.models import FilterState
from salespulsebi.storage.parquet_store import ParquetStore, sanitize_json


def _kpi_summary(
    ctx: AnalyticsContext,
    where_sql: str,
    params: list[Any],
    *,
    where_all_statuses: str | None = None,
    params_all_statuses: list[Any] | None = None,
) -> dict[str, Any]:
    cols = ctx.columns
    selects = ["COUNT(*) AS rows_count"]
    if "_revenue" in cols:
        selects.append("COALESCE(SUM(_revenue), 0.0) AS revenue")
        selects.append("AVG(_revenue) AS row_avg_revenue")
    if "_order_id" in cols:
        selects.append("COUNT(DISTINCT _order_id) AS orders")
    if "_quantity" in cols:
        selects.append("COALESCE(SUM(_quantity), 0.0) AS units")
    if "_profit" in cols:
        selects.append("COALESCE(SUM(_profit), 0.0) AS profit")
    if "_customer" in cols:
        selects.append("COUNT(DISTINCT _customer) AS customers")
    if "_discount" in cols:
        selects.append("COALESCE(SUM(_discount), 0.0) AS discount_total")

    row = ParquetStore.query_one(ctx.con, f"SELECT {', '.join(selects)} FROM sales {where_sql}", params)
    revenue = float(row.get("revenue") or 0.0)
    orders = int(row.get("orders") or row.get("rows_count") or 0)
    aov = (revenue / orders) if ("_revenue" in cols and orders > 0) else None
    profit = float(row.get("profit")) if "_profit" in cols and row.get("profit") is not None else None
    margin_pct = ((profit / revenue) * 100.0) if (profit is not None and revenue > 0) else None

    disc_rate_pct = None
    if "_discount" in cols and "_revenue" in cols and revenue > 0:
        disc_tot = float(row.get("discount_total") or 0.0)
        gross = revenue + disc_tot
        disc_rate_pct = (disc_tot / gross * 100.0) if gross > 0 else 0.0

    cancel_rate_pct = None
    if "_is_excluded_status" in cols and where_all_statuses is not None:
        st_row = ParquetStore.query_one(
            ctx.con,
            f"""
            SELECT
                COUNT(*) AS total_rows,
                SUM(CASE WHEN COALESCE(_is_excluded_status, FALSE) THEN 1 ELSE 0 END) AS cancelled_rows
            FROM sales {where_all_statuses}
            """,
            params_all_statuses or [],
        )
        tot_r = int(st_row.get("total_rows") or 0)
        canc_r = int(st_row.get("cancelled_rows") or 0)
        cancel_rate_pct = (canc_r / tot_r * 100.0) if tot_r > 0 else 0.0

    return {
        "rows_count": int(row.get("rows_count") or 0),
        "revenue": revenue if "_revenue" in cols else None,
        "orders": orders,
        "aov": aov,
        "units": float(row.get("units")) if "_quantity" in cols else None,
        "gross_profit": profit,
        "gross_margin_pct": margin_pct,
        "unique_customers": int(row.get("customers")) if "_customer" in cols else None,
        "discount_rate_pct": disc_rate_pct,
        "cancellation_rate_pct": cancel_rate_pct,
    }


def _delta(curr: float | int | None, prev: float | int | None) -> float | None:
    if curr is None or prev is None or prev == 0:
        return None
    return round(((float(curr) - float(prev)) / abs(float(prev))) * 100.0, 2)


def compute_overview(ctx: AnalyticsContext, filters: FilterState) -> dict[str, Any]:
    """Compute Executive Overview payload."""
    cols = ctx.columns
    where_sql, params = build_where_clause(filters, cols)

    # All-status filter for cancellation rate
    all_status_filters = filters.model_copy(update={"include_excluded_statuses": True})
    where_all_st, params_all_st = build_where_clause(all_status_filters, cols)

    current = _kpi_summary(
        ctx,
        where_sql,
        params,
        where_all_statuses=where_all_st,
        params_all_statuses=params_all_st,
    )

    previous: dict[str, Any] = {}
    prev_window = compute_previous_period(ctx.con, filters, cols) if filters.compare_previous else None
    if prev_window:
        p_where, p_params = build_where_clause(filters, cols, date_override=prev_window)
        p_all_where, p_all_params = build_where_clause(
            all_status_filters, cols, date_override=prev_window
        )
        previous = _kpi_summary(
            ctx,
            p_where,
            p_params,
            where_all_statuses=p_all_where,
            params_all_statuses=p_all_params,
        )

    # Monthly/daily compact trend for overview & sparklines
    trend: list[dict[str, Any]] = []
    if "_date_day" in cols and "_revenue" in cols:
        orders_expr = "COUNT(DISTINCT _order_id)" if "_order_id" in cols else "COUNT(*)"
        bucket = "_month" if "_month" in cols else "_date_day"
        trend = ParquetStore.query_dicts(
            ctx.con,
            f"""
            SELECT
                {bucket} AS period,
                COALESCE(SUM(_revenue), 0.0) AS revenue,
                {orders_expr} AS orders
                {', COALESCE(SUM(_profit), 0.0) AS profit' if '_profit' in cols else ''}
            FROM sales
            {where_sql}
            {"AND" if where_sql else "WHERE"} {bucket} IS NOT NULL
            GROUP BY 1
            ORDER BY 1
            """,
            params,
        )

    spark_rev = [r["revenue"] for r in trend[-16:]]
    spark_ord = [r["orders"] for r in trend[-16:]]

    kpi_defs = [
        ("revenue", spark_rev),
        ("orders", spark_ord),
        ("aov", []),
        ("units", []),
        ("gross_profit", []),
        ("gross_margin_pct", []),
        ("unique_customers", []),
        ("cancellation_rate_pct", []),
        ("discount_rate_pct", []),
        ("rows_count", []),
    ]
    kpis: dict[str, Any] = {}
    for key, spark in kpi_defs:
        val = current.get(key)
        if val is None:
            continue
        prev_val = previous.get(key) if previous else None
        kpis[key] = {
            "value": round(val, 2) if isinstance(val, float) else val,
            "previous_value": round(prev_val, 2) if isinstance(prev_val, float) else prev_val,
            "delta_pct": _delta(val, prev_val),
            "sparkline": spark,
        }

    # Top categories
    top_categories: list[dict[str, Any]] = []
    if "_category" in cols and "_revenue" in cols:
        top_categories = ParquetStore.query_dicts(
            ctx.con,
            f"""
            SELECT
                COALESCE(_category, 'Uncategorized') AS name,
                COALESCE(SUM(_revenue), 0.0) AS revenue,
                COUNT(*) AS rows_count
            FROM sales {where_sql}
            GROUP BY 1
            ORDER BY revenue DESC
            LIMIT 10
            """,
            params,
        )

    # Top products
    top_products: list[dict[str, Any]] = []
    if "_product" in cols and "_revenue" in cols:
        top_products = ParquetStore.query_dicts(
            ctx.con,
            f"""
            SELECT
                COALESCE(_product, 'Unknown') AS name,
                COALESCE(SUM(_revenue), 0.0) AS revenue,
                COUNT(*) AS rows_count
            FROM sales {where_sql}
            GROUP BY 1
            ORDER BY revenue DESC
            LIMIT 10
            """,
            params,
        )

    # Ticket distribution & percentiles
    ticket_dist: dict[str, Any] = {"percentiles": {}, "histogram": []}
    if "_revenue" in cols:
        ticket_expr = "SUM(_revenue)" if "_order_id" in cols else "_revenue"
        group_sql = "GROUP BY _order_id" if "_order_id" in cols else ""
        pct = ParquetStore.query_one(
            ctx.con,
            f"""
            WITH tickets AS (
                SELECT {ticket_expr} AS val
                FROM sales {where_sql}
                {group_sql}
            )
            SELECT
                MIN(val) AS min_val,
                QUANTILE_CONT(val, 0.25) AS p25,
                QUANTILE_CONT(val, 0.50) AS p50,
                QUANTILE_CONT(val, 0.75) AS p75,
                QUANTILE_CONT(val, 0.90) AS p90,
                QUANTILE_CONT(val, 0.99) AS p99,
                MAX(val) AS max_val
            FROM tickets
            WHERE val IS NOT NULL AND val >= 0
            """,
            params,
        )
        ticket_dist["percentiles"] = pct
        min_v = float(pct.get("min_val") or 0.0)
        max_v = float(pct.get("p99") or pct.get("max_val") or 0.0)
        if max_v > min_v:
            bins = 12
            step = (max_v - min_v) / bins
            hist_rows = ParquetStore.query_dicts(
                ctx.con,
                f"""
                WITH tickets AS (
                    SELECT {ticket_expr} AS val
                    FROM sales {where_sql}
                    {group_sql}
                ),
                bucketed AS (
                    SELECT LEAST(FLOOR((val - ?) / ?)::INTEGER, {bins - 1}) AS b
                    FROM tickets
                    WHERE val >= ? AND val <= ?
                )
                SELECT b, COUNT(*) AS cnt FROM bucketed GROUP BY b ORDER BY b
                """,
                params + [min_v, step, min_v, max_v],
            )
            cnt_map = {int(r["b"]): int(r["cnt"]) for r in hist_rows}
            ticket_dist["histogram"] = [
                {
                    "label": f"{round(min_v + i * step, 0):.0f}–{round(min_v + (i + 1) * step, 0):.0f}",
                    "count": cnt_map.get(i, 0),
                }
                for i in range(bins)
            ]

    return sanitize_json(
        {
            "capabilities": ctx.capabilities,
            "filter_options": get_filter_options(ctx.con, cols),
            "previous_window": prev_window,
            "kpis": kpis,
            "trend": trend,
            "top_categories": top_categories,
            "top_products": top_products,
            "ticket_distribution": ticket_dist,
        }
    )
