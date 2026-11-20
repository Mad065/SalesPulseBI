"""Dimensional breakdowns (Region, Channel, Team, Payment, Source Folder), Discount Impact, and Ad-Hoc Explorer."""

from __future__ import annotations

from typing import Any

from salespulsebi.analytics.query_builder import (
    AnalyticsContext,
    build_where_clause,
    safe_ident,
)
from salespulsebi.storage.models import AdHocRequest, FilterState
from salespulsebi.storage.parquet_store import ParquetStore, sanitize_json


def _dimension_breakdown(
    ctx: AnalyticsContext,
    where_sql: str,
    params: list[Any],
    col: str,
    limit: int = 15,
) -> list[dict[str, Any]]:
    cols = ctx.columns
    orders_expr = "COUNT(DISTINCT _order_id)" if "_order_id" in cols else "COUNT(*)"
    rev_expr = "COALESCE(SUM(_revenue), 0.0)" if "_revenue" in cols else "COUNT(*)::DOUBLE"
    profit_select = ", COALESCE(SUM(_profit), 0.0) AS profit" if "_profit" in cols else ""

    rows = ParquetStore.query_dicts(
        ctx.con,
        f"""
        SELECT
            COALESCE(CAST({col} AS VARCHAR), '—') AS name,
            {rev_expr} AS revenue,
            {orders_expr} AS orders
            {profit_select}
        FROM sales
        {where_sql}
        GROUP BY 1
        ORDER BY revenue DESC
        LIMIT {limit}
        """,
        params,
    )
    total_rev = sum(max(0.0, float(r["revenue"] or 0.0)) for r in rows)
    for r in rows:
        rev = float(r["revenue"] or 0.0)
        ords = int(r["orders"] or 0)
        r["aov"] = round(rev / ords, 2) if ords > 0 else 0.0
        r["share_pct"] = round(max(0.0, rev) / total_rev * 100.0, 1) if total_rev > 0 else 0.0
        if r.get("profit") is not None and rev > 0:
            r["margin_pct"] = round(float(r["profit"]) / rev * 100.0, 2)
        else:
            r["margin_pct"] = None
    return rows


def compute_dimensions(ctx: AnalyticsContext, filters: FilterState) -> dict[str, Any]:
    """Compute breakdown metrics for all available operational dimensions and discount tiers."""
    cols = ctx.columns
    where_sql, params = build_where_clause(filters, cols)

    dims: dict[str, list[dict[str, Any]]] = {}
    dim_map = [
        ("_region", "regions"),
        ("_channel", "channels"),
        ("_salesperson", "salespersons"),
        ("_payment_method", "payment_methods"),
        ("_source_folder", "source_folders"),
        ("_source_file", "source_files"),
    ]
    for col, key in dim_map:
        if col in cols:
            dims[key] = _dimension_breakdown(ctx, where_sql, params, col)

    discount_impact: list[dict[str, Any]] = []
    if "_discount" in cols and "_revenue" in cols:
        profit_sel = ", COALESCE(SUM(_profit), 0.0) AS profit" if "_profit" in cols else ""
        d_rows = ParquetStore.query_dicts(
            ctx.con,
            f"""
            WITH disc AS (
                SELECT
                    _revenue,
                    {'_profit,' if '_profit' in cols else ''}
                    CASE
                        WHEN COALESCE(_discount, 0) <= 0 THEN '0%'
                        WHEN (_discount / NULLIF(_revenue + _discount, 0)) <= 0.10 THEN '1-10%'
                        WHEN (_discount / NULLIF(_revenue + _discount, 0)) <= 0.25 THEN '11-25%'
                        ELSE '>25%'
                    END AS tier
                FROM sales {where_sql}
            )
            SELECT
                tier,
                COUNT(*) AS rows_count,
                COALESCE(SUM(_revenue), 0.0) AS revenue,
                AVG(_revenue) AS avg_ticket
                {profit_sel}
            FROM disc
            GROUP BY tier
            ORDER BY CASE tier WHEN '0%' THEN 1 WHEN '1-10%' THEN 2 WHEN '11-25%' THEN 3 ELSE 4 END
            """,
            params,
        )
        for r in d_rows:
            rev = float(r["revenue"] or 0.0)
            r["margin_pct"] = (
                round(float(r["profit"]) / rev * 100.0, 2)
                if r.get("profit") is not None and rev > 0
                else None
            )
        discount_impact = d_rows

    # Provide selectable columns for the Ad-Hoc Explorer
    numeric_types = ("INT", "BIGINT", "DOUBLE", "FLOAT", "DECIMAL")
    available_columns = [
        {
            "name": c,
            "type": ctx.column_types[c],
            "is_numeric": any(t in ctx.column_types[c].upper() for t in numeric_types),
        }
        for c in sorted(cols)
        if not c.startswith("_is_")
    ]

    return sanitize_json(
        {
            "available": True,
            "dimensions": dims,
            "discount_impact": discount_impact,
            "available_columns": available_columns,
        }
    )


def compute_adhoc(ctx: AnalyticsContext, req: AdHocRequest) -> dict[str, Any]:
    """Run an interactive dynamic aggregation over any column in the dataset."""
    cols = ctx.columns
    x_ident = safe_ident(req.x_column, cols)
    where_sql, params = build_where_clause(req.filters, cols)

    agg_map = {
        "sum": "SUM",
        "avg": "AVG",
        "count": "COUNT",
        "min": "MIN",
        "max": "MAX",
    }
    agg_fn = agg_map[req.aggregation]

    if req.aggregation == "count" or not req.y_column:
        metric_expr = "COUNT(*)::DOUBLE"
    else:
        y_ident = safe_ident(req.y_column, cols)
        metric_expr = f"COALESCE({agg_fn}(TRY_CAST({y_ident} AS DOUBLE)), 0.0)"

    rows = ParquetStore.query_dicts(
        ctx.con,
        f"""
        SELECT
            COALESCE(CAST({x_ident} AS VARCHAR), '—') AS label,
            {metric_expr} AS value,
            COUNT(*) AS row_count
        FROM sales
        {where_sql}
        GROUP BY 1
        ORDER BY value DESC
        LIMIT {int(req.limit)}
        """,
        params,
    )
    return sanitize_json(
        {
            "x_column": req.x_column,
            "y_column": req.y_column,
            "aggregation": req.aggregation,
            "rows": rows,
        }
    )
