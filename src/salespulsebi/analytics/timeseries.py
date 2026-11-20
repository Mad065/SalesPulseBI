"""Time series trends, seasonality heatmaps, Holt-Winters forecast, and MAD anomaly detection."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from salespulsebi.analytics.query_builder import AnalyticsContext, build_where_clause
from salespulsebi.storage.models import FilterState
from salespulsebi.storage.parquet_store import ParquetStore, sanitize_json


def _granularity_sql(granularity: str) -> str:
    mapping = {
        "day": "strftime(_date, '%Y-%m-%d')",
        "week": "strftime(DATE_TRUNC('week', _date), '%Y-%m-%d')",
        "month": "strftime(DATE_TRUNC('month', _date), '%Y-%m')",
        "quarter": "CAST(YEAR(_date) AS VARCHAR) || '-Q' || CAST(QUARTER(_date) AS VARCHAR)",
        "year": "CAST(YEAR(_date) AS VARCHAR)",
    }
    return mapping.get(granularity, mapping["month"])


def _holt_winters_forecast(
    dates: list[str],
    values: list[float],
    horizon: int,
    granularity: str,
) -> list[dict[str, Any]]:
    """
    Deterministic statistical forecast:
    - Additive Holt-Winters (period=7) when daily series has >=21 points
    - Damped Holt linear trend otherwise
    Returns forecast points with 80% and 95% confidence bounds.
    """
    n = len(values)
    if n < 3 or horizon <= 0:
        return []

    y = np.asarray(values, dtype=float)
    use_seasonal = granularity == "day" and n >= 21
    season_len = 7

    if use_seasonal:
        alpha, beta, gamma = 0.30, 0.08, 0.25
        seasonals = np.zeros(season_len, dtype=float)
        init_mean = float(np.mean(y[:season_len]))
        for i in range(season_len):
            seasonals[i] = y[i] - init_mean
        level = init_mean
        trend = float((np.mean(y[season_len : 2 * season_len]) - init_mean) / season_len)
        fitted = np.zeros(n, dtype=float)
        for i in range(n):
            s_idx = i % season_len
            prev_s = seasonals[s_idx]
            fitted[i] = level + trend + prev_s
            new_level = alpha * (y[i] - prev_s) + (1.0 - alpha) * (level + trend)
            new_trend = beta * (new_level - level) + (1.0 - beta) * trend
            seasonals[s_idx] = gamma * (y[i] - new_level) + (1.0 - gamma) * prev_s
            level, trend = new_level, new_trend
        residuals = y - fitted
        rmse = float(np.sqrt(np.mean(residuals**2))) if n > 1 else float(np.std(y))
        preds = [level + (h + 1) * trend + seasonals[(n + h) % season_len] for h in range(horizon)]
    else:
        alpha, beta, phi = 0.35, 0.12, 0.90
        level = float(y[0])
        trend = float(y[1] - y[0]) if n > 1 else 0.0
        fitted = np.zeros(n, dtype=float)
        for i in range(n):
            fitted[i] = level + phi * trend
            new_level = alpha * y[i] + (1.0 - alpha) * (level + phi * trend)
            new_trend = beta * (new_level - level) + (1.0 - beta) * phi * trend
            level, trend = new_level, new_trend
        residuals = y - fitted
        rmse = float(np.sqrt(np.mean(residuals**2))) if n > 1 else float(np.std(y))
        preds = []
        phi_sum = 0.0
        for h in range(1, horizon + 1):
            phi_sum += phi**h
            preds.append(level + phi_sum * trend)

    # Generate future date labels based on granularity
    last_label = dates[-1]
    future_labels: list[str] = []
    try:
        if granularity in ("day", "week"):
            base_d = date.fromisoformat(last_label[:10])
            step_days = 1 if granularity == "day" else 7
            future_labels = [
                (base_d + timedelta(days=step_days * (h + 1))).isoformat()
                for h in range(horizon)
            ]
        elif granularity == "month":
            base_p = pd.Period(last_label[:7], freq="M")
            future_labels = [str(base_p + h + 1) for h in range(horizon)]
        elif granularity == "quarter":
            base_p = pd.Period(last_label, freq="Q")
            future_labels = [str(base_p + h + 1) for h in range(horizon)]
        else:
            yr = int(last_label[:4])
            future_labels = [str(yr + h + 1) for h in range(horizon)]
    except Exception:
        future_labels = [f"+{h + 1}" for h in range(horizon)]

    out: list[dict[str, Any]] = []
    for h, (lbl, pred) in enumerate(zip(future_labels, preds, strict=False), start=1):
        pred_val = max(0.0, float(pred))
        sigma = rmse * float(np.sqrt(h))
        out.append(
            {
                "period": lbl,
                "forecast": round(pred_val, 2),
                "lower_80": round(max(0.0, pred_val - 1.2816 * sigma), 2),
                "upper_80": round(pred_val + 1.2816 * sigma, 2),
                "lower_95": round(max(0.0, pred_val - 1.96 * sigma), 2),
                "upper_95": round(pred_val + 1.96 * sigma, 2),
            }
        )
    return out


def _detect_anomalies_mad(
    ctx: AnalyticsContext,
    where_sql: str,
    params: list[Any],
    daily_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect statistical spikes/drops on daily revenue via Modified Z-Score (MAD)."""
    if len(daily_rows) < 7:
        return []
    vals = np.array([float(r["revenue"]) for r in daily_rows], dtype=float)
    median = float(np.median(vals))
    mad = float(np.median(np.abs(vals - median)))
    if mad == 0.0:
        std = float(np.std(vals))
        if std == 0.0:
            return []
        z_scores = (vals - median) / std
    else:
        z_scores = 0.6745 * (vals - median) / mad

    anomalies: list[dict[str, Any]] = []
    for r, z in zip(daily_rows, z_scores, strict=False):
        if abs(z) >= 3.0:
            anomalies.append(
                {
                    "date": r["period"],
                    "revenue": round(float(r["revenue"]), 2),
                    "expected": round(median, 2),
                    "z_score": round(float(z), 2),
                    "direction": "spike" if z > 0 else "drop",
                    "root_cause": None,
                }
            )

    anomalies.sort(key=lambda item: abs(item["z_score"]), reverse=True)
    anomalies = anomalies[:12]

    # Enrich top anomalies with root-cause attribution (top product or region on that day)
    attr_col = "_product" if "_product" in ctx.columns else ("_category" if "_category" in ctx.columns else "_region" if "_region" in ctx.columns else None)
    if attr_col:
        for item in anomalies[:6]:
            extra_clause = f"{'AND' if where_sql else 'WHERE'} _date_day = ?"
            top = ParquetStore.query_one(
                ctx.con,
                f"""
                SELECT COALESCE({attr_col}, 'N/A') AS driver, SUM(_revenue) AS rev
                FROM sales {where_sql} {extra_clause}
                GROUP BY 1
                ORDER BY rev DESC
                LIMIT 1
                """,
                params + [item["date"]],
            )
            if top:
                item["root_cause"] = top.get("driver")

    return anomalies


def compute_timeseries(ctx: AnalyticsContext, filters: FilterState) -> dict[str, Any]:
    """Compute detailed time series, moving averages, YoY cumulative, seasonality, forecast, and anomalies."""
    if not ctx.capabilities.get("timeseries"):
        return sanitize_json({"available": False, "reason": "requires_date_and_revenue"})

    cols = ctx.columns
    where_sql, params = build_where_clause(filters, cols)
    bucket_expr = _granularity_sql(filters.granularity)
    orders_expr = "COUNT(DISTINCT _order_id)" if "_order_id" in cols else "COUNT(*)"

    rows = ParquetStore.query_dicts(
        ctx.con,
        f"""
        SELECT
            {bucket_expr} AS period,
            COALESCE(SUM(_revenue), 0.0) AS revenue,
            {orders_expr} AS orders
            {', COALESCE(SUM(_profit), 0.0) AS profit' if '_profit' in cols else ''}
            {', COALESCE(SUM(_quantity), 0.0) AS units' if '_quantity' in cols else ''}
        FROM sales
        {where_sql}
        {"AND" if where_sql else "WHERE"} _date IS NOT NULL
        GROUP BY 1
        ORDER BY 1
        """,
        params,
    )

    # Compute 7-period moving average & period-over-period growth %
    revs = [float(r["revenue"]) for r in rows]
    for i, r in enumerate(rows):
        win = revs[max(0, i - 6) : i + 1]
        r["moving_avg"] = round(sum(win) / len(win), 2)
        if i > 0 and revs[i - 1] != 0:
            r["growth_pct"] = round(((revs[i] - revs[i - 1]) / abs(revs[i - 1])) * 100.0, 2)
        else:
            r["growth_pct"] = None

    # Daily series for anomaly detection & accurate daily forecast when granularity == day
    daily_rows = ParquetStore.query_dicts(
        ctx.con,
        f"""
        SELECT
            _date_day AS period,
            COALESCE(SUM(_revenue), 0.0) AS revenue
        FROM sales
        {where_sql}
        {"AND" if where_sql else "WHERE"} _date_day IS NOT NULL
        GROUP BY 1
        ORDER BY 1
        """,
        params,
    )

    # Forecast (adjust horizon steps naturally by granularity so chart looks crisp)
    horizon_steps = filters.forecast_horizon
    if filters.granularity == "week":
        horizon_steps = max(4, min(26, filters.forecast_horizon // 7))
    elif filters.granularity == "month":
        horizon_steps = max(3, min(12, max(1, filters.forecast_horizon // 30) * 3))
    elif filters.granularity in ("quarter", "year"):
        horizon_steps = 4

    forecast = _holt_winters_forecast(
        [str(r["period"]) for r in rows],
        [float(r["revenue"]) for r in rows],
        horizon=horizon_steps,
        granularity=filters.granularity,
    )

    anomalies = _detect_anomalies_mad(ctx, where_sql, params, daily_rows)

    # Seasonality heatmap: Day of Week (0..6) x Month (1..12)
    seasonality = ParquetStore.query_dicts(
        ctx.con,
        f"""
        SELECT
            CAST(_dow AS INTEGER) AS dow,
            MONTH(_date) AS month_num,
            COALESCE(SUM(_revenue), 0.0) AS revenue,
            COUNT(*) AS count
        FROM sales
        {where_sql}
        {"AND" if where_sql else "WHERE"} _date IS NOT NULL AND _dow IS NOT NULL
        GROUP BY 1, 2
        ORDER BY 2, 1
        """,
        params,
    )

    # Cumulative YoY comparison
    yoy_rows = ParquetStore.query_dicts(
        ctx.con,
        f"""
        SELECT
            _year AS year,
            MONTH(_date) AS month_num,
            COALESCE(SUM(_revenue), 0.0) AS revenue
        FROM sales
        {where_sql}
        {"AND" if where_sql else "WHERE"} _date IS NOT NULL AND _year IS NOT NULL
        GROUP BY 1, 2
        ORDER BY 1, 2
        """,
        params,
    )
    yoy_by_year: dict[str, list[float | None]] = {}
    for yr in sorted({int(r["year"]) for r in yoy_rows}):
        monthly_map = {int(r["month_num"]): float(r["revenue"]) for r in yoy_rows if int(r["year"]) == yr}
        cum = 0.0
        arr: list[float | None] = []
        for m in range(1, 13):
            if m in monthly_map:
                cum += monthly_map[m]
                arr.append(round(cum, 2))
            else:
                arr.append(None)
        yoy_by_year[str(yr)] = arr

    return sanitize_json(
        {
            "available": True,
            "granularity": filters.granularity,
            "series": rows,
            "forecast": forecast,
            "anomalies": anomalies,
            "seasonality": seasonality,
            "yoy_cumulative": yoy_by_year,
        }
    )
