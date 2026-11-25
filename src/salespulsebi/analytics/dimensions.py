"""Dimensional breakdowns (Region, Channel, Team, Payment, Source Folder), Discount Impact, and Ad-Hoc Explorer."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from salespulsebi.analytics.query_builder import (
    AnalyticsContext,
    build_where_clause,
    compute_previous_period,
    safe_ident,
)
from salespulsebi.storage.models import AdHocRequest, CustomPanelSpec, FilterState
from salespulsebi.storage.parquet_store import MONETARY_COLUMNS, ParquetStore, sanitize_json

TEMPORAL_COLUMNS: frozenset[str] = frozenset({"_date", "_date_day", "_month", "_quarter", "_year"})

CROSS_FILTER_BY_COLUMN: dict[str, str] = {
    "_category": "categories",
    "_region": "regions",
    "_channel": "channels",
    "_salesperson": "salespersons",
    "_payment_method": "payment_methods",
    "_status": "statuses",
    "_source_file": "source_files",
    "_source_folder": "source_folders",
    "_product": "products",
    "_customer": "customers",
}


def get_available_columns(ctx: AnalyticsContext) -> list[dict[str, Any]]:
    """Return selectable dataset columns and numeric flags for Ad-Hoc & Custom Panels."""
    numeric_types = ("INT", "BIGINT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "HUGEINT")
    return [
        {
            "name": c,
            "type": ctx.column_types[c],
            "is_numeric": any(t in ctx.column_types[c].upper() for t in numeric_types),
            "is_temporal": c in TEMPORAL_COLUMNS or "DATE" in ctx.column_types[c].upper() or "TIME" in ctx.column_types[c].upper(),
            "is_monetary": c in MONETARY_COLUMNS,
            "cross_filter_key": CROSS_FILTER_BY_COLUMN.get(c),
        }
        for c in sorted(ctx.columns)
        if not c.startswith("_is_")
    ]


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

    available_columns = get_available_columns(ctx)

    return sanitize_json(
        {
            "available": True,
            "dimensions": dims,
            "discount_impact": discount_impact,
            "available_columns": available_columns,
        }
    )


def _build_metric_sql(
    ctx: AnalyticsContext,
    *,
    aggregation: str,
    y_column: str | None,
    fallback_col: str | None = None,
    format_kind: str = "auto",
) -> str:
    cols = ctx.columns
    agg_map = {
        "sum": "SUM",
        "avg": "AVG",
        "min": "MIN",
        "max": "MAX",
    }
    if aggregation == "count":
        return "CAST(COUNT(*) AS DECIMAL(28, 4))"
    if aggregation == "count_distinct":
        target = y_column or fallback_col
        if not target:
            return "CAST(COUNT(*) AS DECIMAL(28, 4))"
        ident = safe_ident(target, cols)
        return f"CAST(COUNT(DISTINCT {ident}) AS DECIMAL(28, 4))"

    if not y_column:
        return "CAST(COUNT(*) AS DECIMAL(28, 4))"

    agg_fn = agg_map[aggregation]
    y_ident = safe_ident(y_column, cols)
    base_expr = f"TRY_CAST({y_ident} AS DECIMAL(28, 8))"
    if format_kind == "currency" and y_column not in MONETARY_COLUMNS and ctx.fx_rate != Decimal("1"):
        fx_lit = format(ctx.fx_rate, "f")
        if re.fullmatch(r"\d+(\.\d+)?", fx_lit):
            base_expr = f"({base_expr} * DECIMAL '{fx_lit}')"
    return f"COALESCE(ROUND(CAST({agg_fn}({base_expr}) AS DECIMAL(28, 6)), 4), 0)"


def _resolve_value_format(spec: CustomPanelSpec) -> str:
    if spec.format_kind != "auto":
        return spec.format_kind
    if spec.aggregation in ("count", "count_distinct"):
        return "number"
    if spec.y_column in MONETARY_COLUMNS:
        return "currency"
    if spec.y_column and ("pct" in spec.y_column or "margin" in spec.y_column):
        return "percent"
    return "number"


def _delta_pct(curr: float | int | None, prev: float | int | None) -> float | None:
    if curr is None or prev is None or prev == 0:
        return None
    return round(((float(curr) - float(prev)) / abs(float(prev))) * 100.0, 2)


def compute_adhoc(ctx: AnalyticsContext, req: AdHocRequest) -> dict[str, Any]:
    """Run an interactive dynamic aggregation over any column in the dataset."""
    cols = ctx.columns
    where_sql, params = build_where_clause(req.filters, cols)
    metric_expr = _build_metric_sql(
        ctx,
        aggregation=req.aggregation,
        y_column=req.y_column,
        fallback_col=req.x_column,
    )

    if not req.x_column:
        one = ParquetStore.query_one(
            ctx.con,
            f"SELECT {metric_expr} AS value, COUNT(*) AS row_count FROM sales {where_sql}",
            params,
        )
        return sanitize_json(
            {
                "x_column": None,
                "y_column": req.y_column,
                "aggregation": req.aggregation,
                "rows": [
                    {
                        "label": "Total",
                        "value": one.get("value", 0.0),
                        "row_count": int(one.get("row_count") or 0),
                    }
                ],
            }
        )

    x_ident = safe_ident(req.x_column, cols)
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
        ORDER BY value DESC, label ASC
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


def compute_custom_panels(
    ctx: AnalyticsContext,
    panels: list[CustomPanelSpec],
    filters: FilterState,
    *,
    strict_columns: bool = False,
) -> dict[str, Any]:
    """Evaluate user-configured custom panels in a single DuckDB session."""
    cols = ctx.columns
    where_sql, params = build_where_clause(filters, cols)
    prev_window = (
        compute_previous_period(ctx.con, filters, cols)
        if filters.compare_previous and any(p.panel_type == "kpi" for p in panels)
        else None
    )

    results: dict[str, Any] = {}
    for spec in panels:
        req_cols = [
            c
            for c in (
                spec.x_column if spec.panel_type != "kpi" else None,
                spec.y_column if spec.aggregation != "count" else None,
            )
            if c
        ]
        missing = [c for c in req_cols if c not in cols]
        if missing:
            if strict_columns:
                raise ValueError(f"Invalid column identifier: {missing[0]}")
            results[spec.id] = {
                "id": spec.id,
                "available": False,
                "reason": "missing_column",
                "missing_columns": missing,
            }
            continue

        if spec.panel_type != "kpi" and not spec.x_column:
            if strict_columns:
                raise ValueError("x_column is required for chart and table panels")
            results[spec.id] = {
                "id": spec.id,
                "available": False,
                "reason": "missing_x_column",
            }
            continue

        metric_expr = _build_metric_sql(
            ctx,
            aggregation=spec.aggregation,
            y_column=spec.y_column,
            fallback_col=spec.x_column,
            format_kind=spec.format_kind,
        )
        value_format = _resolve_value_format(spec)

        if spec.panel_type == "kpi":
            curr_row = ParquetStore.query_one(
                ctx.con,
                f"SELECT {metric_expr} AS val, COUNT(*) AS row_count FROM sales {where_sql}",
                params,
            )
            row_cnt = int(curr_row.get("row_count") or 0)
            curr_val = (
                None
                if row_cnt == 0 and spec.aggregation in ("avg", "min", "max")
                else curr_row.get("val", 0)
            )
            prev_val = None
            if prev_window:
                p_where, p_params = build_where_clause(filters, cols, date_override=prev_window)
                prev_row = ParquetStore.query_one(
                    ctx.con,
                    f"SELECT {metric_expr} AS val, COUNT(*) AS row_count FROM sales {p_where}",
                    p_params,
                )
                p_cnt = int(prev_row.get("row_count") or 0)
                if p_cnt > 0 or spec.aggregation in ("sum", "count", "count_distinct"):
                    prev_val = prev_row.get("val", 0)

            results[spec.id] = {
                "id": spec.id,
                "available": True,
                "panel_type": "kpi",
                "value_format": value_format,
                "value": curr_val,
                "previous_value": prev_val,
                "delta_pct": _delta_pct(curr_val, prev_val),
                "row_count": row_cnt,
            }
            continue

        assert spec.x_column is not None
        x_ident = safe_ident(spec.x_column, cols)
        is_chrono = spec.panel_type in ("line", "area") and (
            spec.x_column in TEMPORAL_COLUMNS
            or "DATE" in ctx.column_types.get(spec.x_column, "").upper()
            or "TIME" in ctx.column_types.get(spec.x_column, "").upper()
        )
        extra_non_null = f"{'AND' if where_sql else 'WHERE'} {x_ident} IS NOT NULL" if is_chrono else ""
        order_sql = "ORDER BY 1 ASC" if is_chrono else "ORDER BY value DESC, label ASC"

        rows = ParquetStore.query_dicts(
            ctx.con,
            f"""
            SELECT
                COALESCE(CAST({x_ident} AS VARCHAR), '—') AS label,
                {metric_expr} AS value,
                COUNT(*) AS row_count
            FROM sales
            {where_sql}
            {extra_non_null}
            GROUP BY 1
            {order_sql}
            LIMIT {int(spec.limit)}
            """,
            params,
        )
        total_pos = sum(max(0.0, float(r.get("value") or 0.0)) for r in rows)
        for r in rows:
            val_f = float(r.get("value") or 0.0)
            r["share_pct"] = round(max(0.0, val_f) / total_pos * 100.0, 1) if total_pos > 0 else 0.0

        results[spec.id] = {
            "id": spec.id,
            "available": True,
            "panel_type": spec.panel_type,
            "value_format": value_format,
            "x_column": spec.x_column,
            "y_column": spec.y_column,
            "aggregation": spec.aggregation,
            "cross_filter_key": CROSS_FILTER_BY_COLUMN.get(spec.x_column),
            "rows": rows,
        }

    return sanitize_json(
        {
            "panels": results,
            "available_columns": get_available_columns(ctx),
        }
    )
