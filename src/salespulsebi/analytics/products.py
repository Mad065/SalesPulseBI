"""Product & Portfolio Intelligence: Pareto ABC (80/20), BCG Performance Matrix, and Market Basket."""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np

from salespulsebi.analytics.query_builder import AnalyticsContext, build_where_clause
from salespulsebi.storage.models import FilterState
from salespulsebi.storage.parquet_store import ParquetStore, sanitize_json


def compute_products(ctx: AnalyticsContext, filters: FilterState) -> dict[str, Any]:
    """Compute Pareto ABC classification, Performance Matrix, and Cross-Sell Market Basket."""
    if not ctx.capabilities.get("products"):
        return sanitize_json({"available": False, "reason": "requires_product_and_revenue"})

    cols = ctx.columns
    where_sql, params = build_where_clause(filters, cols)
    orders_expr = "COUNT(DISTINCT _order_id)" if "_order_id" in cols else "COUNT(*)"
    units_expr = "COALESCE(SUM(_quantity), 0.0)" if "_quantity" in cols else "COUNT(*)::DOUBLE"
    cat_expr = "COALESCE(MAX(_category), '—')" if "_category" in cols else "'—'"
    profit_expr = "COALESCE(SUM(_profit), 0.0)" if "_profit" in cols else "NULL"

    prod_rows = ParquetStore.query_dicts(
        ctx.con,
        f"""
        SELECT
            COALESCE(_product, 'Unknown') AS product,
            {cat_expr} AS category,
            COALESCE(SUM(_revenue), 0.0) AS revenue,
            {units_expr} AS units,
            {orders_expr} AS orders,
            {profit_expr} AS profit
        FROM sales
        {where_sql}
        {"AND" if where_sql else "WHERE"} _product IS NOT NULL
        GROUP BY _product
        ORDER BY revenue DESC
        """,
        params,
    )

    total_rev = sum(max(0.0, float(r["revenue"] or 0.0)) for r in prod_rows)
    cum = 0.0
    abc_counts = {"A": 0, "B": 0, "C": 0}
    abc_rev = {"A": 0.0, "B": 0.0, "C": 0.0}

    for r in prod_rows:
        rev = max(0.0, float(r["revenue"] or 0.0))
        share = (rev / total_rev * 100.0) if total_rev > 0 else 0.0
        cum += share
        if cum <= 80.0 or abc_counts["A"] == 0:
            tier = "A"
        elif cum <= 95.0:
            tier = "B"
        else:
            tier = "C"
        r["share_pct"] = round(share, 2)
        r["cum_share_pct"] = round(min(100.0, cum), 2)
        r["abc_tier"] = tier
        if r.get("profit") is not None and rev > 0:
            r["margin_pct"] = round(float(r["profit"]) / rev * 100.0, 2)
        else:
            r["margin_pct"] = None
        abc_counts[tier] += 1
        abc_rev[tier] += rev

    abc_summary = [
        {
            "tier": t,
            "product_count": abc_counts[t],
            "product_pct": round(abc_counts[t] / max(len(prod_rows), 1) * 100.0, 1),
            "revenue": round(abc_rev[t], 2),
            "revenue_pct": round(abc_rev[t] / total_rev * 100.0, 1) if total_rev > 0 else 0.0,
        }
        for t in ("A", "B", "C")
    ]

    # BCG / Performance Matrix: X = Revenue, Y = Margin % (or Period Growth % if no cost)
    matrix_mode = "margin" if "_profit" in cols else ("growth" if "_date_day" in cols else "orders")
    growth_map: dict[str, float] = {}
    if matrix_mode == "growth":
        bounds = ParquetStore.query_one(
            ctx.con,
            f"SELECT MIN(_date_day) AS min_d, MAX(_date_day) AS max_d FROM sales {where_sql}",
            params,
        )
        if bounds.get("min_d") and bounds.get("max_d") and bounds["min_d"] != bounds["max_d"]:
            d1 = date.fromisoformat(str(bounds["min_d"])[:10])
            d2 = date.fromisoformat(str(bounds["max_d"])[:10])
            mid_d = (d1 + (d2 - d1) // 2).isoformat()
            if mid_d:
                half_rows = ParquetStore.query_dicts(
                    ctx.con,
                    f"""
                    SELECT
                        _product AS product,
                        SUM(CASE WHEN _date_day < ? THEN _revenue ELSE 0 END) AS rev_h1,
                        SUM(CASE WHEN _date_day >= ? THEN _revenue ELSE 0 END) AS rev_h2
                    FROM sales
                    {where_sql}
                    {"AND" if where_sql else "WHERE"} _product IS NOT NULL AND _date_day IS NOT NULL
                    GROUP BY 1
                    """,
                    [mid_d, mid_d] + params,
                )
                for hr in half_rows:
                    h1 = float(hr["rev_h1"] or 0.0)
                    h2 = float(hr["rev_h2"] or 0.0)
                    if h1 > 0:
                        growth_map[str(hr["product"])] = round((h2 - h1) / h1 * 100.0, 2)
                    elif h2 > 0:
                        growth_map[str(hr["product"])] = 100.0

    top_for_matrix = prod_rows[:40]
    matrix_points: list[dict[str, Any]] = []
    y_vals: list[float] = []
    x_vals = [float(r["revenue"]) for r in top_for_matrix]
    for r in top_for_matrix:
        if matrix_mode == "margin":
            y_v = float(r["margin_pct"] if r["margin_pct"] is not None else 0.0)
        elif matrix_mode == "growth":
            y_v = float(growth_map.get(r["product"], 0.0))
        else:
            y_v = float(r["orders"])
        y_vals.append(y_v)
        matrix_points.append(
            {
                "product": r["product"],
                "category": r["category"],
                "revenue": round(float(r["revenue"]), 2),
                "units": round(float(r["units"]), 2),
                "y_value": round(y_v, 2),
                "abc_tier": r["abc_tier"],
            }
        )

    x_med = float(np.median(x_vals)) if x_vals else 0.0
    y_med = float(np.median(y_vals)) if y_vals else 0.0
    for pt in matrix_points:
        high_x = pt["revenue"] >= x_med
        high_y = pt["y_value"] >= y_med
        if high_x and high_y:
            pt["quadrant"] = "star"
        elif high_x and not high_y:
            pt["quadrant"] = "cash_cow"
        elif not high_x and high_y:
            pt["quadrant"] = "question_mark"
        else:
            pt["quadrant"] = "dog"

    # Market Basket / Cross-Sell Affinity
    market_basket: list[dict[str, Any]] = []
    if ctx.capabilities.get("market_basket"):
        basket_rows = ParquetStore.query_dicts(
            ctx.con,
            f"""
            WITH filtered AS (
                SELECT DISTINCT _order_id AS oid, _product AS prod
                FROM sales
                {where_sql}
                {"AND" if where_sql else "WHERE"} _order_id IS NOT NULL AND _product IS NOT NULL
            ),
            tot AS (SELECT COUNT(DISTINCT oid) AS total_orders FROM filtered),
            prod_cnt AS (SELECT prod, COUNT(*) AS cnt FROM filtered GROUP BY prod),
            pairs AS (
                SELECT a.prod AS product_a, b.prod AS product_b, COUNT(*) AS co_count
                FROM filtered a
                JOIN filtered b ON a.oid = b.oid AND a.prod < b.prod
                GROUP BY 1, 2
                HAVING COUNT(*) >= 2
            )
            SELECT
                p.product_a,
                p.product_b,
                p.co_count,
                t.total_orders,
                pa.cnt AS count_a,
                pb.cnt AS count_b,
                (p.co_count::DOUBLE / t.total_orders) * 100.0 AS support_pct,
                (p.co_count::DOUBLE / pa.cnt) * 100.0 AS confidence_a_to_b_pct,
                (p.co_count::DOUBLE * t.total_orders) / NULLIF(pa.cnt * pb.cnt, 0) AS lift
            FROM pairs p
            CROSS JOIN tot t
            JOIN prod_cnt pa ON pa.prod = p.product_a
            JOIN prod_cnt pb ON pb.prod = p.product_b
            ORDER BY p.co_count DESC, lift DESC
            LIMIT 20
            """,
            params,
        )
        for br in basket_rows:
            br["support_pct"] = round(float(br.get("support_pct") or 0.0), 2)
            br["confidence_a_to_b_pct"] = round(float(br.get("confidence_a_to_b_pct") or 0.0), 2)
            br["lift"] = round(float(br.get("lift") or 0.0), 2)
        market_basket = basket_rows

    # Category mix breakdown
    categories: list[dict[str, Any]] = []
    if "_category" in cols:
        categories = ParquetStore.query_dicts(
            ctx.con,
            f"""
            SELECT
                COALESCE(_category, 'Uncategorized') AS category,
                COALESCE(SUM(_revenue), 0.0) AS revenue,
                {units_expr} AS units,
                {orders_expr} AS orders
                {', COALESCE(SUM(_profit), 0.0) AS profit' if '_profit' in cols else ''}
            FROM sales {where_sql}
            GROUP BY 1
            ORDER BY revenue DESC
            LIMIT 20
            """,
            params,
        )

    return sanitize_json(
        {
            "available": True,
            "total_products": len(prod_rows),
            "abc_summary": abc_summary,
            "pareto_curve": prod_rows[:35],
            "products_table": prod_rows[:100],
            "matrix": {
                "mode": matrix_mode,
                "x_median": round(x_med, 2),
                "y_median": round(y_med, 2),
                "points": matrix_points,
            },
            "market_basket": market_basket,
            "categories": categories,
        }
    )
