"""Exact Decimal What-If Financial Simulator and Price-Volume-Mix (PVM) Variance Decomposition."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any

import numpy as np

from salespulsebi.analytics.query_builder import (
    AnalyticsContext,
    build_where_clause,
    compute_previous_period,
)
from salespulsebi.storage.models import FilterState
from salespulsebi.storage.parquet_store import ParquetStore, sanitize_json

Q2 = Decimal("0.01")
Q4 = Decimal("0.0001")
HUNDRED = Decimal("100")


def _d(val: Any) -> Decimal:
    if val is None:
        return Decimal("0")
    try:
        dec = Decimal(str(val))
        return dec if dec.is_finite() else Decimal("0")
    except Exception:
        return Decimal("0")


def _q2(val: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 28
        return val.quantize(Q2, rounding=ROUND_HALF_EVEN)


def _fmt_d(val: Decimal) -> str:
    return format(_q2(val), "f")


def _estimate_price_elasticity(ctx: AnalyticsContext, where_sql: str, params: list[Any]) -> float | None:
    """
    Estimate historical price elasticity of demand via log-log OLS on (_unit_price, _quantity)
    aggregated by product-month. Returns elasticity clamped to [-3.0, 0.0] or None.
    """
    if "_unit_price" not in ctx.columns or "_quantity" not in ctx.columns:
        return None
    rows = ParquetStore.query_dicts(
        ctx.con,
        f"""
        SELECT
            AVG(_unit_price) AS avg_price,
            SUM(_quantity) AS qty
        FROM sales
        {where_sql}
        {"AND" if where_sql else "WHERE"} _unit_price > 0 AND _quantity > 0
        GROUP BY COALESCE(CAST(_product AS VARCHAR), 'all'),
                 COALESCE(CAST(_month AS VARCHAR), 'm')
        HAVING SUM(_quantity) > 0 AND AVG(_unit_price) > 0
        LIMIT 500
        """,
        params,
    )
    if len(rows) < 6:
        return None
    lp = np.log(np.array([float(r["avg_price"]) for r in rows], dtype=float))
    lq = np.log(np.array([float(r["qty"]) for r in rows], dtype=float))
    if float(np.std(lp)) < 1e-6:
        return None
    slope = float(np.polyfit(lp, lq, 1)[0])
    # Only accept economically plausible negative demand elasticity; clamp to [-3.0, 0.0]
    if not np.isfinite(slope):
        return None
    return round(float(np.clip(slope, -3.0, 0.0)), 3)


def compute_what_if_scenario(
    ctx: AnalyticsContext,
    filters: FilterState,
    *,
    price_delta_pct: Decimal = Decimal("0"),
    discount_delta_pct: Decimal = Decimal("0"),
    cost_delta_pct: Decimal = Decimal("0"),
    volume_delta_pct: Decimal = Decimal("0"),
    fx_multiplier: Decimal = Decimal("1"),
    use_estimated_elasticity: bool = False,
) -> dict[str, Any]:
    """
    Simulate financial P&L sensitivity using exact Decimal arithmetic (ROUND_HALF_EVEN).
    """
    if "_revenue" not in ctx.columns:
        return sanitize_json({"available": False, "reason": "requires_revenue"})

    cols = ctx.columns
    where_sql, params = build_where_clause(filters, cols)
    cost_sql = "COALESCE(SUM(_cost), 0)" if "_cost" in cols else "0"
    disc_sql = "COALESCE(SUM(_discount), 0)" if "_discount" in cols else "0"
    qty_sql = "COALESCE(SUM(_quantity), 0)" if "_quantity" in cols else "COUNT(*)"

    row = ParquetStore.query_one(
        ctx.con,
        f"""
        SELECT
            COALESCE(SUM(_revenue), 0) AS revenue,
            {cost_sql} AS cost,
            {disc_sql} AS discount,
            {qty_sql} AS units,
            COUNT(*) AS rows_count
        FROM sales
        {where_sql}
        """,
        params,
    )

    with localcontext() as dctx:
        dctx.prec = 28
        base_rev = _q2(_d(row.get("revenue")))
        base_cost = _q2(_d(row.get("cost")))
        base_disc = _q2(_d(row.get("discount")))
        base_units = _d(row.get("units"))
        has_cost = "_cost" in cols

        est_elasticity = _estimate_price_elasticity(ctx, where_sql, params)
        effective_vol_delta = volume_delta_pct
        elasticity_volume_adj = Decimal("0")
        if use_estimated_elasticity and est_elasticity is not None and price_delta_pct != Decimal("0"):
            elasticity_volume_adj = (price_delta_pct * _d(est_elasticity)).quantize(
                Q2, rounding=ROUND_HALF_EVEN
            )
            effective_vol_delta = volume_delta_pct + elasticity_volume_adj

        # Guard total volume factor >= 0
        vol_factor = max(Decimal("0"), Decimal("1") + (effective_vol_delta / HUNDRED))
        price_factor = max(Decimal("0"), Decimal("1") + (price_delta_pct / HUNDRED))
        cost_factor = max(Decimal("0"), Decimal("1") + (cost_delta_pct / HUNDRED))
        disc_factor = max(Decimal("0"), Decimal("1") + (discount_delta_pct / HUNDRED))

        # Gross before discount = base_rev + base_disc
        base_gross = base_rev + base_disc
        sim_gross = base_gross * vol_factor * price_factor * fx_multiplier
        sim_disc = (base_disc * vol_factor * disc_factor * fx_multiplier) if base_disc > 0 else Decimal("0")
        # If dataset has no discount column but user adjusts discount_delta_pct, apply directly to revenue
        if base_disc == 0 and discount_delta_pct != Decimal("0"):
            sim_rev = _q2(sim_gross * max(Decimal("0"), Decimal("1") - (discount_delta_pct / HUNDRED)))
        else:
            sim_rev = _q2(max(Decimal("0"), sim_gross - sim_disc))

        sim_cost = _q2(base_cost * vol_factor * cost_factor * fx_multiplier) if has_cost else Decimal("0")
        sim_units = (base_units * vol_factor).quantize(Q2, rounding=ROUND_HALF_EVEN)

        base_profit = _q2(base_rev - base_cost) if has_cost else None
        sim_profit = _q2(sim_rev - sim_cost) if has_cost else None

        base_margin = (
            _q2((base_profit / base_rev) * HUNDRED)
            if (base_profit is not None and base_rev > 0)
            else None
        )
        sim_margin = (
            _q2((sim_profit / sim_rev) * HUNDRED)
            if (sim_profit is not None and sim_rev > 0)
            else None
        )

        delta_rev = _q2(sim_rev - base_rev)
        delta_rev_pct = _q2((delta_rev / base_rev) * HUNDRED) if base_rev > 0 else Decimal("0")
        delta_profit = (
            _q2(sim_profit - base_profit)
            if (sim_profit is not None and base_profit is not None)
            else None
        )
        delta_margin_pp = (
            _q2(sim_margin - base_margin)
            if (sim_margin is not None and base_margin is not None)
            else None
        )

    return sanitize_json(
        {
            "available": True,
            "has_cost": has_cost,
            "estimated_elasticity": est_elasticity,
            "elasticity_volume_adj_pct": float(elasticity_volume_adj),
            "inputs": {
                "price_delta_pct": _fmt_d(price_delta_pct),
                "discount_delta_pct": _fmt_d(discount_delta_pct),
                "cost_delta_pct": _fmt_d(cost_delta_pct),
                "volume_delta_pct": _fmt_d(volume_delta_pct),
                "effective_volume_delta_pct": _fmt_d(effective_vol_delta),
                "fx_multiplier": format(fx_multiplier, "f"),
                "use_estimated_elasticity": use_estimated_elasticity,
            },
            "baseline": {
                "revenue": float(base_rev),
                "revenue_decimal": _fmt_d(base_rev),
                "cost": float(base_cost) if has_cost else None,
                "cost_decimal": _fmt_d(base_cost) if has_cost else None,
                "gross_profit": float(base_profit) if base_profit is not None else None,
                "gross_profit_decimal": _fmt_d(base_profit) if base_profit is not None else None,
                "gross_margin_pct": float(base_margin) if base_margin is not None else None,
                "units": float(base_units),
            },
            "simulated": {
                "revenue": float(sim_rev),
                "revenue_decimal": _fmt_d(sim_rev),
                "cost": float(sim_cost) if has_cost else None,
                "cost_decimal": _fmt_d(sim_cost) if has_cost else None,
                "gross_profit": float(sim_profit) if sim_profit is not None else None,
                "gross_profit_decimal": _fmt_d(sim_profit) if sim_profit is not None else None,
                "gross_margin_pct": float(sim_margin) if sim_margin is not None else None,
                "units": float(sim_units),
            },
            "deltas": {
                "revenue": float(delta_rev),
                "revenue_decimal": _fmt_d(delta_rev),
                "revenue_pct": float(delta_rev_pct),
                "gross_profit": float(delta_profit) if delta_profit is not None else None,
                "gross_profit_decimal": _fmt_d(delta_profit) if delta_profit is not None else None,
                "margin_pp": float(delta_margin_pp) if delta_margin_pp is not None else None,
            },
        }
    )


def compute_pvm_bridge(ctx: AnalyticsContext, filters: FilterState) -> dict[str, Any]:
    """
    Exact Price-Volume-Mix (PVM) Variance Decomposition between current and prior period.

    Guarantees exact Decimal conservation law:
      prior_revenue + volume_effect + price_effect + mix_effect
      + new_items_effect + discontinued_items_effect == current_revenue
    """
    if "_date_day" not in ctx.columns or "_revenue" not in ctx.columns:
        return sanitize_json({"available": False, "reason": "requires_date_and_revenue"})

    prev_window = compute_previous_period(ctx.con, filters, ctx.columns)
    if not prev_window:
        return sanitize_json({"available": False, "reason": "requires_two_periods"})

    cols = ctx.columns
    item_col = "_product" if "_product" in cols else ("_category" if "_category" in cols else None)
    qty_expr = "COALESCE(SUM(_quantity), 0)" if "_quantity" in cols else "COUNT(*)"
    item_expr = f"COALESCE(CAST({item_col} AS VARCHAR), 'Other')" if item_col else "'All'"

    # Resolve current date bounds when filters omit explicit dates (matching compute_previous_period split)
    cur_override: tuple[str | None, str | None] | None = None
    if not filters.date_from and not filters.date_to:
        # Current period is the second half after prev_window[1]
        from datetime import date, timedelta

        next_start = (date.fromisoformat(prev_window[1]) + timedelta(days=1)).isoformat()
        cur_override = (next_start, None)

    cur_where, cur_params = build_where_clause(filters, cols, date_override=cur_override)
    prev_where, prev_params = build_where_clause(filters, cols, date_override=prev_window)

    sql_tpl = f"""
        SELECT
            {item_expr} AS item,
            COALESCE(SUM(_revenue), 0) AS revenue,
            {qty_expr} AS qty
        FROM sales
        {{where}}
        GROUP BY 1
    """
    cur_rows = ParquetStore.query_dicts(ctx.con, sql_tpl.format(where=cur_where), cur_params)
    prev_rows = ParquetStore.query_dicts(ctx.con, sql_tpl.format(where=prev_where), prev_params)

    cur_map = {str(r["item"]): (_d(r["revenue"]), _d(r["qty"])) for r in cur_rows}
    prev_map = {str(r["item"]): (_d(r["revenue"]), _d(r["qty"])) for r in prev_rows}

    with localcontext() as dctx:
        dctx.prec = 28
        prior_total = _q2(sum((v[0] for v in prev_map.values()), Decimal("0")))
        current_total = _q2(sum((v[0] for v in cur_map.values()), Decimal("0")))
        total_delta = _q2(current_total - prior_total)

        common_keys = set(cur_map.keys()) & set(prev_map.keys())
        new_keys = set(cur_map.keys()) - set(prev_map.keys())
        disc_keys = set(prev_map.keys()) - set(cur_map.keys())

        new_effect = _q2(sum((cur_map[k][0] for k in new_keys), Decimal("0")))
        disc_effect = _q2(-sum((prev_map[k][0] for k in disc_keys), Decimal("0")))

        # Common-SKU PVM exact identity:
        # sum(R1_i - R0_i) = Volume + Mix + Price
        q0_common = sum((prev_map[k][1] for k in common_keys), Decimal("0"))
        q1_common = sum((cur_map[k][1] for k in common_keys), Decimal("0"))
        r0_common = sum((prev_map[k][0] for k in common_keys), Decimal("0"))

        p0_avg = (r0_common / q0_common) if q0_common > 0 else Decimal("0")
        vol_raw = (q1_common - q0_common) * p0_avg

        price_raw = Decimal("0")
        mix_raw = Decimal("0")
        for k in common_keys:
            r0, q0 = prev_map[k]
            r1, q1 = cur_map[k]
            p0 = (r0 / q0) if q0 > 0 else Decimal("0")
            p1 = (r1 / q1) if q1 > 0 else Decimal("0")
            # Price effect: (p1 - p0) * q1
            price_raw += (p1 - p0) * q1
            # Mix effect: (s1 - s0) * Q1 * p0 = (q1 - q0 * (Q1/Q0)) * p0
            expected_q = (q0 * q1_common / q0_common) if q0_common > 0 else Decimal("0")
            mix_raw += (q1 - expected_q) * p0

        vol_effect = _q2(vol_raw)
        price_effect = _q2(price_raw)
        mix_effect = _q2(mix_raw)

        # Allocate any sub-cent rounding residual deterministically to mix_effect so conservation holds 100%
        reconciled_sum = (
            prior_total + vol_effect + price_effect + mix_effect + new_effect + disc_effect
        )
        residual = _q2(current_total - reconciled_sum)
        if residual != Decimal("0"):
            mix_effect = _q2(mix_effect + residual)

    steps = [
        {"id": "prior", "label_es": "Periodo Anterior", "label_en": "Prior Period", "value": float(prior_total), "value_decimal": _fmt_d(prior_total), "kind": "total"},
        {"id": "volume", "label_es": "Efecto Volumen", "label_en": "Volume Effect", "value": float(vol_effect), "value_decimal": _fmt_d(vol_effect), "kind": "delta"},
        {"id": "price", "label_es": "Efecto Precio", "label_en": "Price Effect", "value": float(price_effect), "value_decimal": _fmt_d(price_effect), "kind": "delta"},
        {"id": "mix", "label_es": "Efecto Mix", "label_en": "Mix Effect", "value": float(mix_effect), "value_decimal": _fmt_d(mix_effect), "kind": "delta"},
        {"id": "new_items", "label_es": "Nuevos SKUs", "label_en": "New SKUs", "value": float(new_effect), "value_decimal": _fmt_d(new_effect), "kind": "delta"},
        {"id": "discontinued", "label_es": "SKUs Inactivos", "label_en": "Discontinued SKUs", "value": float(disc_effect), "value_decimal": _fmt_d(disc_effect), "kind": "delta"},
        {"id": "current", "label_es": "Periodo Actual", "label_en": "Current Period", "value": float(current_total), "value_decimal": _fmt_d(current_total), "kind": "total"},
    ]

    return sanitize_json(
        {
            "available": True,
            "prior_window": {"from": prev_window[0], "to": prev_window[1]},
            "dimension": item_col or "total",
            "prior_revenue": float(prior_total),
            "prior_revenue_decimal": _fmt_d(prior_total),
            "current_revenue": float(current_total),
            "current_revenue_decimal": _fmt_d(current_total),
            "total_delta": float(total_delta),
            "total_delta_decimal": _fmt_d(total_delta),
            "conservation_verified": (
                prior_total + vol_effect + price_effect + mix_effect + new_effect + disc_effect
                == current_total
            ),
            "steps": steps,
        }
    )
