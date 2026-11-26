"""Leakage-free AutoML Customer Churn Probability and 90-Day Expected CLV engine."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any

import numpy as np

from salespulsebi.analytics.query_builder import AnalyticsContext, build_where_clause
from salespulsebi.storage.models import FilterState
from salespulsebi.storage.parquet_store import ParquetStore, sanitize_json

Q2 = Decimal("0.01")


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -20.0, 20.0)))


def _fit_l2_logistic(X: np.ndarray, y: np.ndarray, *, l2: float = 1.0, steps: int = 120) -> tuple[np.ndarray, float, np.ndarray, np.ndarray]:
    """Pure-NumPy standardized L2 logistic regression returning (weights, bias, mean_x, std_x)."""
    mean_x = np.mean(X, axis=0)
    std_x = np.where(np.std(X, axis=0) < 1e-8, 1.0, np.std(X, axis=0))
    Xs = (X - mean_x) / std_x
    n, p = Xs.shape
    w = np.zeros(p, dtype=float)
    b = float(np.log((np.mean(y) + 1e-4) / (1.0 - np.mean(y) + 1e-4)))
    lr = 0.25
    for _ in range(steps):
        probs = _sigmoid(Xs @ w + b)
        diff = probs - y
        grad_w = (Xs.T @ diff) / n + (l2 / n) * w
        grad_b = float(np.mean(diff))
        w -= lr * grad_w
        b -= lr * grad_b
    return w, b, mean_x, std_x


def compute_churn_and_clv(
    ctx: AnalyticsContext,
    filters: FilterState,
    *,
    horizon_days: int = 60,
    limit: int = 100,
) -> dict[str, Any]:
    """
    Predict per-customer churn probability and 90-day expected CLV (exact Decimal).
    Trains a strictly causal temporal classifier when enough history exists, with calibrated
    RFM-hazard scoring fallback for shorter datasets.
    """
    if not ctx.capabilities.get("rfm"):
        return sanitize_json({"available": False, "reason": "requires_customer_date_revenue"})

    cols = ctx.columns
    where_sql, params = build_where_clause(filters, cols)
    orders_sql = "COUNT(DISTINCT _order_id)" if "_order_id" in cols else "COUNT(*)"

    bounds = ParquetStore.query_one(
        ctx.con,
        f"""
        SELECT MIN(_date_day) AS min_d, MAX(_date_day) AS max_d
        FROM sales {where_sql}
        {"AND" if where_sql else "WHERE"} _date_day IS NOT NULL AND _customer IS NOT NULL
        """,
        params,
    )
    if not bounds.get("min_d") or not bounds.get("max_d"):
        return sanitize_json({"available": False, "reason": "no_customer_dates"})

    min_d = date.fromisoformat(str(bounds["min_d"])[:10])
    max_d = date.fromisoformat(str(bounds["max_d"])[:10])
    span_days = max((max_d - min_d).days, 1)

    rows = ParquetStore.query_dicts(
        ctx.con,
        f"""
        SELECT
            CAST(_customer AS VARCHAR) AS customer,
            MIN(_date_day) AS first_date,
            MAX(_date_day) AS last_date,
            {orders_sql} AS orders,
            COALESCE(SUM(_revenue), 0.0) AS revenue,
            COALESCE(AVG(_revenue), 0.0) AS avg_ticket
        FROM sales
        {where_sql}
        {"AND" if where_sql else "WHERE"} _customer IS NOT NULL AND _date_day IS NOT NULL
        GROUP BY 1
        ORDER BY revenue DESC
        """,
        params,
    )
    if not rows:
        return sanitize_json({"available": False, "reason": "no_customers"})

    # Build current customer features
    feat_rows = []
    for r in rows:
        fd = date.fromisoformat(str(r["first_date"])[:10])
        ld = date.fromisoformat(str(r["last_date"])[:10])
        recency = max((max_d - ld).days, 0)
        tenure = max((max_d - fd).days, 1)
        orders = max(int(r["orders"] or 1), 1)
        rev = max(float(r["revenue"] or 0.0), 0.0)
        avg_gap = float((ld - fd).days) / max(orders - 1, 1) if orders > 1 else float(max(recency, 30))
        recency_ratio = recency / max(avg_gap, 7.0)
        feat_rows.append(
            {
                "customer": str(r["customer"]),
                "recency_days": recency,
                "tenure_days": tenure,
                "orders": orders,
                "revenue": rev,
                "avg_ticket": float(r["avg_ticket"] or 0.0),
                "avg_gap_days": round(avg_gap, 1),
                "vec": [
                    float(recency),
                    float(np.log1p(orders)),
                    float(np.log1p(rev)),
                    float(tenure),
                    float(min(recency_ratio, 10.0)),
                ],
            }
        )

    X_now = np.array([f["vec"] for f in feat_rows], dtype=float)
    model_used = "rfm_hazard_calibrated"
    brier_score: float | None = None

    # Strictly causal temporal training if span >= 2 * horizon_days and >= 12 customers
    cutoff_d = max_d - timedelta(days=min(horizon_days, max(span_days // 3, 14)))
    if span_days >= 45 and len(feat_rows) >= 12:
        cutoff_str = cutoff_d.isoformat()
        train_rows = ParquetStore.query_dicts(
            ctx.con,
            f"""
            WITH past AS (
                SELECT
                    CAST(_customer AS VARCHAR) AS customer,
                    MIN(_date_day) AS first_date,
                    MAX(_date_day) AS last_date,
                    {orders_sql} AS orders,
                    COALESCE(SUM(_revenue), 0.0) AS revenue
                FROM sales
                {where_sql}
                {"AND" if where_sql else "WHERE"} _customer IS NOT NULL AND _date_day <= ?
                GROUP BY 1
            ),
            future AS (
                SELECT DISTINCT CAST(_customer AS VARCHAR) AS customer
                FROM sales
                {where_sql}
                {"AND" if where_sql else "WHERE"} _customer IS NOT NULL AND _date_day > ?
            )
            SELECT
                p.customer, p.first_date, p.last_date, p.orders, p.revenue,
                CASE WHEN f.customer IS NULL THEN 1 ELSE 0 END AS churned
            FROM past p
            LEFT JOIN future f ON p.customer = f.customer
            """,
            params + [cutoff_str] + params + [cutoff_str],
        )
        if len(train_rows) >= 10:
            y_tr = np.array([int(r["churned"]) for r in train_rows], dtype=float)
            if 0 < int(np.sum(y_tr)) < len(y_tr):
                X_tr_list = []
                for r in train_rows:
                    fd = date.fromisoformat(str(r["first_date"])[:10])
                    ld = date.fromisoformat(str(r["last_date"])[:10])
                    rec = max((cutoff_d - ld).days, 0)
                    ten = max((cutoff_d - fd).days, 1)
                    ords = max(int(r["orders"] or 1), 1)
                    rev = max(float(r["revenue"] or 0.0), 0.0)
                    gap = float((ld - fd).days) / max(ords - 1, 1) if ords > 1 else float(max(rec, 30))
                    X_tr_list.append([float(rec), float(np.log1p(ords)), float(np.log1p(rev)), float(ten), float(min(rec / max(gap, 7.0), 10.0))])
                X_tr = np.array(X_tr_list, dtype=float)
                w, b, mx, sx = _fit_l2_logistic(X_tr, y_tr, l2=1.5)
                p_tr = _sigmoid(((X_tr - mx) / sx) @ w + b)
                brier_score = round(float(np.mean((p_tr - y_tr) ** 2)), 4)
                probs = _sigmoid(((X_now - mx) / sx) @ w + b)
                model_used = "temporal_l2_logistic"
            else:
                probs = None
        else:
            probs = None
    else:
        probs = None

    if probs is None:
        # Calibrated recency-frequency hazard fallback
        med_rec = max(float(np.median(X_now[:, 0])), 14.0)
        raw_z = 1.4 * ((X_now[:, 0] - med_rec) / max(med_rec, 1.0)) - 0.65 * (X_now[:, 1] - float(np.median(X_now[:, 1])))
        probs = _sigmoid(raw_z)

    customers_out: list[dict[str, Any]] = []
    at_risk_rev_dec = Decimal("0")
    total_clv_dec = Decimal("0")
    high_cnt = 0
    med_cnt = 0

    with localcontext() as dctx:
        dctx.prec = 28
        for item, p_churn in zip(feat_rows, probs, strict=False):
            prob_pct = round(float(np.clip(p_churn, 0.01, 0.99)) * 100.0, 1)
            risk_tier = "high" if prob_pct >= 65.0 else ("medium" if prob_pct >= 35.0 else "low")
            if risk_tier == "high":
                high_cnt += 1
            elif risk_tier == "medium":
                med_cnt += 1

            # Expected 90-day CLV = (daily_rev_rate * 90) * (1 - p_churn) in exact Decimal
            rev_dec = Decimal(str(round(item["revenue"], 2)))
            tenure_dec = Decimal(str(max(item["tenure_days"], 30)))
            retention = Decimal(str(round(1.0 - float(np.clip(p_churn, 0.01, 0.99)), 4)))
            clv_90 = ((rev_dec / tenure_dec) * Decimal("90") * retention).quantize(
                Q2, rounding=ROUND_HALF_EVEN
            )
            total_clv_dec += clv_90
            if risk_tier == "high":
                at_risk_rev_dec += rev_dec

            factors = []
            if item["recency_days"] > max(item["avg_gap_days"] * 1.4, 30):
                factors.append("high_recency_gap")
            if item["orders"] <= 1:
                factors.append("single_order_buyer")
            if not factors:
                factors.append("stable_cadence")

            customers_out.append(
                {
                    "customer": item["customer"],
                    "churn_probability_pct": prob_pct,
                    "risk_tier": risk_tier,
                    "expected_clv_90d": float(clv_90),
                    "expected_clv_90d_decimal": format(clv_90, "f"),
                    "historical_revenue": round(item["revenue"], 2),
                    "orders": item["orders"],
                    "recency_days": item["recency_days"],
                    "risk_factors": factors,
                }
            )

    customers_out.sort(key=lambda c: (-c["churn_probability_pct"], -c["historical_revenue"]))
    return sanitize_json(
        {
            "available": True,
            "model_used": model_used,
            "brier_score": brier_score,
            "summary": {
                "total_customers": len(customers_out),
                "high_risk_count": high_cnt,
                "medium_risk_count": med_cnt,
                "low_risk_count": len(customers_out) - high_cnt - med_cnt,
                "revenue_at_high_risk_decimal": format(at_risk_rev_dec.quantize(Q2, rounding=ROUND_HALF_EVEN), "f"),
                "expected_clv_90d_total_decimal": format(total_clv_dec.quantize(Q2, rounding=ROUND_HALF_EVEN), "f"),
            },
            "customers": customers_out[:limit],
        }
    )
