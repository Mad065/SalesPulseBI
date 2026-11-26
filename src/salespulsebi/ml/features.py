"""Leakage-free time-series regularization and deterministic lag/calendar feature engineering."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from salespulsebi.analytics.query_builder import AnalyticsContext, build_where_clause
from salespulsebi.storage.models import FilterState
from salespulsebi.storage.parquet_store import ParquetStore

TARGET_METRIC_SQL: dict[str, tuple[str, str | None]] = {
    "revenue": ("COALESCE(SUM(_revenue), 0.0)", "_revenue"),
    "profit": ("COALESCE(SUM(_profit), 0.0)", "_profit"),
    "units": ("COALESCE(SUM(_quantity), 0.0)", "_quantity"),
    "orders": ("COUNT(DISTINCT _order_id)", "_order_id"),
}


def _granularity_bucket_sql(granularity: str) -> str:
    mapping = {
        "day": "strftime(_date, '%Y-%m-%d')",
        "week": "strftime(DATE_TRUNC('week', _date), '%Y-%m-%d')",
        "month": "strftime(DATE_TRUNC('month', _date), '%Y-%m')",
        "quarter": "CAST(YEAR(_date) AS VARCHAR) || '-Q' || CAST(QUARTER(_date) AS VARCHAR)",
        "year": "CAST(YEAR(_date) AS VARCHAR)",
    }
    return mapping.get(granularity, mapping["month"])


def extract_regular_series(
    ctx: AnalyticsContext,
    filters: FilterState,
    *,
    target_metric: str = "revenue",
    granularity: str | None = None,
) -> pd.DataFrame:
    """
    Extract a chronologically sorted, regular-frequency time series from DuckDB.

    Follows strict financial time-series hygiene:
    - Sorted ascending by period before any lag/rolling operation.
    - Missing calendar buckets for flow variables (revenue, profit, units, orders)
      are filled with 0.0 (never backward-filled).
    """
    gran = granularity or filters.granularity or "month"
    metric = target_metric if target_metric in TARGET_METRIC_SQL else "revenue"
    agg_sql, req_col = TARGET_METRIC_SQL[metric]
    if metric == "orders" and "_order_id" not in ctx.columns:
        agg_sql = "COUNT(*)"
    elif req_col and req_col not in ctx.columns:
        return pd.DataFrame(columns=["period", "value", "orders", "discount_rate"])

    if "_date" not in ctx.columns:
        return pd.DataFrame(columns=["period", "value", "orders", "discount_rate"])

    where_sql, params = build_where_clause(filters, ctx.columns)
    bucket_sql = _granularity_bucket_sql(gran)
    orders_sql = "COUNT(DISTINCT _order_id)" if "_order_id" in ctx.columns else "COUNT(*)"
    disc_sql = (
        "COALESCE(SUM(_discount) / NULLIF(SUM(_revenue) + SUM(_discount), 0), 0.0)"
        if "_discount" in ctx.columns and "_revenue" in ctx.columns
        else "0.0"
    )

    rows = ParquetStore.query_dicts(
        ctx.con,
        f"""
        SELECT
            {bucket_sql} AS period,
            {agg_sql} AS value,
            {orders_sql} AS orders,
            {disc_sql} AS discount_rate
        FROM sales
        {where_sql}
        {"AND" if where_sql else "WHERE"} _date IS NOT NULL
        GROUP BY 1
        ORDER BY 1 ASC
        """,
        params,
    )
    if not rows:
        return pd.DataFrame(columns=["period", "value", "orders", "discount_rate"])

    df = pd.DataFrame(rows)
    df["period"] = df["period"].astype(str)
    df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0.0).astype(float)
    df["orders"] = pd.to_numeric(df["orders"], errors="coerce").fillna(0.0).astype(float)
    df["discount_rate"] = (
        pd.to_numeric(df["discount_rate"], errors="coerce").fillna(0.0).astype(float)
    )
    df = df.drop_duplicates(subset=["period"], keep="last").sort_values("period").reset_index(drop=True)

    # Regularize calendar gaps with 0.0 for daily/weekly/monthly series (avoiding giant empty spans)
    if len(df) >= 2 and gran in ("day", "week", "month"):
        try:
            if gran == "day":
                start_d = date.fromisoformat(df["period"].iloc[0][:10])
                end_d = date.fromisoformat(df["period"].iloc[-1][:10])
                span = (end_d - start_d).days
                if 0 < span <= 1500:
                    full_periods = [
                        (start_d + timedelta(days=i)).isoformat() for i in range(span + 1)
                    ]
                    df = (
                        df.set_index("period")
                        .reindex(full_periods, fill_value=0.0)
                        .reset_index()
                        .rename(columns={"index": "period"})
                    )
            elif gran == "month":
                p0 = pd.Period(df["period"].iloc[0][:7], freq="M")
                p1 = pd.Period(df["period"].iloc[-1][:7], freq="M")
                span = (p1 - p0).n
                if 0 < span <= 180:
                    full_periods = [str(p0 + i) for i in range(span + 1)]
                    df = (
                        df.set_index("period")
                        .reindex(full_periods, fill_value=0.0)
                        .reset_index()
                        .rename(columns={"index": "period"})
                    )
        except Exception:
            pass

    return df


def season_length_for(granularity: str) -> int:
    return {"day": 7, "week": 4, "month": 12, "quarter": 4, "year": 1}.get(granularity, 12)


def generate_future_periods(last_label: str, horizon: int, granularity: str) -> list[str]:
    """Generate monotonically increasing future period labels matching granularity."""
    if horizon <= 0:
        return []
    try:
        if granularity in ("day", "week"):
            base_d = date.fromisoformat(last_label[:10])
            step_days = 1 if granularity == "day" else 7
            return [(base_d + timedelta(days=step_days * (h + 1))).isoformat() for h in range(horizon)]
        if granularity == "month":
            base_p = pd.Period(last_label[:7], freq="M")
            return [str(base_p + h + 1) for h in range(horizon)]
        if granularity == "quarter":
            base_p = pd.Period(last_label, freq="Q")
            return [str(base_p + h + 1) for h in range(horizon)]
        yr = int(last_label[:4])
        return [str(yr + h + 1) for h in range(horizon)]
    except Exception:
        return [f"+{h + 1}" for h in range(horizon)]


def calendar_features_for_label(label: str, granularity: str, step_idx: int) -> dict[str, float]:
    """Deterministic cyclical calendar features for a period label (strictly known in advance)."""
    dow = 0
    month = 1
    is_month_end = 0.0
    try:
        if granularity in ("day", "week"):
            d = date.fromisoformat(label[:10])
            dow = d.weekday()
            month = d.month
            is_month_end = 1.0 if (d + timedelta(days=1)).month != d.month else 0.0
        elif granularity == "month":
            p = pd.Period(label[:7], freq="M")
            month = p.month
            is_month_end = 1.0
        elif granularity == "quarter":
            q = int(label.split("-Q")[-1])
            month = q * 3
    except Exception:
        month = (step_idx % 12) + 1
        dow = step_idx % 7

    return {
        "dow_sin": float(np.sin(2.0 * np.pi * dow / 7.0)),
        "dow_cos": float(np.cos(2.0 * np.pi * dow / 7.0)),
        "month_sin": float(np.sin(2.0 * np.pi * (month - 1) / 12.0)),
        "month_cos": float(np.cos(2.0 * np.pi * (month - 1) / 12.0)),
        "is_month_end": is_month_end,
    }


def build_lag_feature_row(
    history: list[float] | np.ndarray,
    label: str,
    granularity: str,
    step_idx: int,
    lags: tuple[int, ...],
) -> tuple[np.ndarray, list[str]]:
    """
    Build a strictly causal (leakage-free) feature vector for forecasting the next point
    using ONLY values strictly prior to the target timestamp.
    """
    n = len(history)
    last_val = float(history[-1]) if n > 0 else 0.0
    feats: list[float] = []
    names: list[str] = []

    for lag in lags:
        val = float(history[-lag]) if n >= lag else last_val
        feats.append(val)
        names.append(f"lag_{lag}")

    # Strictly past rolling statistics
    for win in (3, 7):
        slice_vals = history[-min(n, win) :] if n > 0 else [0.0]
        feats.append(float(np.mean(slice_vals)))
        names.append(f"rolling_mean_{win}")
        feats.append(float(np.std(slice_vals)) if len(slice_vals) > 1 else 0.0)
        names.append(f"rolling_std_{win}")

    # Past short-term momentum (lag_1 - lag_2)
    diff1 = float(history[-1] - history[-2]) if n >= 2 else 0.0
    feats.append(diff1)
    names.append("momentum_1")

    # Normalized time index
    feats.append(float(step_idx) / max(float(step_idx + 1), 10.0))
    names.append("trend_index")

    cal = calendar_features_for_label(label, granularity, step_idx)
    for k, v in cal.items():
        feats.append(v)
        names.append(k)

    return np.asarray(feats, dtype=float), names


def build_supervised_lag_matrix(
    periods: list[str],
    values: np.ndarray,
    granularity: str,
    lags: tuple[int, ...] | None = None,
) -> tuple[np.ndarray, np.ndarray, list[str], tuple[int, ...]]:
    """
    Construct supervised `(X, y)` matrix strictly without look-ahead bias:
    row `t` predicts `values[t]` using ONLY `values[:t]` and calendar features of `periods[t]`.
    """
    n = len(values)
    if lags is None:
        base_lags = (1, 2, 3, 7, 14) if granularity == "day" else (1, 2, 3, 4, 6)
        lags = tuple(lg for lg in base_lags if lg < max(n - 2, 2)) or (1,)

    start_idx = max(max(lags), 2)
    if n <= start_idx + 1:
        return np.empty((0, 0)), np.empty((0,)), [], lags

    X_rows: list[np.ndarray] = []
    y_rows: list[float] = []
    feat_names: list[str] = []

    for t in range(start_idx, n):
        # Strictly causal prefix values[:t]
        row_vec, feat_names = build_lag_feature_row(
            values[:t], periods[t], granularity, t, lags
        )
        X_rows.append(row_vec)
        y_rows.append(float(values[t]))

    return np.vstack(X_rows), np.asarray(y_rows, dtype=float), feat_names, lags


def summarize_feature_name(name: str, lang: str = "es") -> str:
    """Human-readable label for lag/calendar feature importance."""
    es = lang == "es"
    if name.startswith("lag_"):
        k = name.split("_")[1]
        return f"Valor rezago t-{k}" if es else f"Lag t-{k} value"
    mapping = {
        "rolling_mean_3": ("Media móvil (3p)" if es else "Rolling mean (3p)"),
        "rolling_std_3": ("Volatilidad reciente (3p)" if es else "Recent volatility (3p)"),
        "rolling_mean_7": ("Media móvil (7p)" if es else "Rolling mean (7p)"),
        "rolling_std_7": ("Volatilidad (7p)" if es else "Volatility (7p)"),
        "momentum_1": ("Impulso periodo previo" if es else "Prior period momentum"),
        "trend_index": ("Tendencia secular" if es else "Long-term trend"),
        "dow_sin": ("Estacionalidad semanal (sen)" if es else "Weekly seasonality (sin)"),
        "dow_cos": ("Estacionalidad semanal (cos)" if es else "Weekly seasonality (cos)"),
        "month_sin": ("Estacionalidad mensual (sen)" if es else "Monthly seasonality (sin)"),
        "month_cos": ("Estacionalidad mensual (cos)" if es else "Monthly seasonality (cos)"),
        "is_month_end": ("Efecto cierre de mes" if es else "Month-end effect"),
    }
    return mapping.get(name, name)


def compute_dataset_hash(parquet_path: Any) -> str:
    """Fast fingerprint of dataset.parquet (size + mtime_ns) for model cache invalidation."""
    try:
        st = parquet_path.stat()
        return f"{st.st_size}:{st.st_mtime_ns}"
    except Exception:
        return "none"
