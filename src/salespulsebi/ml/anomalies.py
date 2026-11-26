"""Multivariate anomaly detection combining Isolation Forest and MAD Z-Score with root-cause attribution."""

from __future__ import annotations

from typing import Any

import numpy as np

from salespulsebi.analytics.query_builder import AnalyticsContext, build_where_clause
from salespulsebi.storage.models import FilterState
from salespulsebi.storage.parquet_store import ParquetStore, sanitize_json

try:
    from sklearn.ensemble import IsolationForest  # type: ignore[import-not-found]

    HAS_SKLEARN_IF = True
except Exception:
    HAS_SKLEARN_IF = False


def _harmonic_c(n: int) -> float:
    """Average path length of unsuccessful search in a BST (Liu et al., Isolation Forest 2008)."""
    if n <= 1:
        return 1.0
    if n == 2:
        return 1.0
    return 2.0 * (float(np.log(n - 1)) + 0.5772156649) - (2.0 * (n - 1) / n)


def _numpy_isolation_forest_scores(
    X: np.ndarray,
    *,
    n_trees: int = 80,
    seed: int = 42,
) -> np.ndarray:
    """
    Deterministic pure-NumPy Isolation Forest returning anomaly scores in [0, 1]
    (higher = more anomalous).
    """
    n, d = X.shape
    if n < 4 or d == 0:
        return np.zeros(n, dtype=float)

    rng = np.random.default_rng(seed)
    sub_size = min(n, 256)
    max_depth = int(np.ceil(np.log2(max(sub_size, 2))))
    c_n = _harmonic_c(sub_size)

    def _build_tree(indices: np.ndarray, depth: int) -> dict[str, Any]:
        m = len(indices)
        if depth >= max_depth or m <= 1:
            return {"leaf": True, "size": m}
        sub = X[indices]
        mins = np.min(sub, axis=0)
        maxs = np.max(sub, axis=0)
        valid_dims = np.where((maxs - mins) > 1e-12)[0]
        if len(valid_dims) == 0:
            return {"leaf": True, "size": m}
        feat = int(rng.choice(valid_dims))
        split_val = float(rng.uniform(mins[feat], maxs[feat]))
        left_mask = sub[:, feat] < split_val
        left_idx = indices[left_mask]
        right_idx = indices[~left_mask]
        if len(left_idx) == 0 or len(right_idx) == 0:
            return {"leaf": True, "size": m}
        return {
            "leaf": False,
            "feat": feat,
            "split": split_val,
            "left": _build_tree(left_idx, depth + 1),
            "right": _build_tree(right_idx, depth + 1),
        }

    def _path_len(node: dict[str, Any], row: np.ndarray, depth: int) -> float:
        if node["leaf"]:
            sz = int(node["size"])
            return float(depth) + (_harmonic_c(sz) if sz > 1 else 0.0)
        if row[node["feat"]] < node["split"]:
            return _path_len(node["left"], row, depth + 1)
        return _path_len(node["right"], row, depth + 1)

    path_sums = np.zeros(n, dtype=float)
    all_idx = np.arange(n)
    for _ in range(n_trees):
        sample_idx = rng.choice(all_idx, size=sub_size, replace=False) if n > sub_size else all_idx
        tree = _build_tree(sample_idx, 0)
        for i in range(n):
            path_sums[i] += _path_len(tree, X[i], 0)

    avg_paths = path_sums / float(n_trees)
    scores = np.power(2.0, -avg_paths / max(c_n, 1e-9))
    return scores


def _robust_z(arr: np.ndarray) -> tuple[np.ndarray, float]:
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    if mad > 1e-9:
        return 0.6745 * (arr - med) / mad, med
    std = float(np.std(arr))
    if std > 1e-9:
        return (arr - med) / std, med
    return np.zeros_like(arr), med


def detect_multivariate_anomalies(
    ctx: AnalyticsContext,
    filters: FilterState,
    *,
    contamination: float = 0.08,
    limit: int = 15,
) -> dict[str, Any]:
    """
    Detect daily multivariate anomalies across revenue, order count, AOV,
    gross margin %, discount rate %, and cancellation rate %.
    """
    if "_date_day" not in ctx.columns or "_revenue" not in ctx.columns:
        return sanitize_json({"available": False, "reason": "requires_date_and_revenue"})

    cols = ctx.columns
    where_sql, params = build_where_clause(filters, cols)
    orders_sql = "COUNT(DISTINCT _order_id)" if "_order_id" in cols else "COUNT(*)"
    margin_sql = (
        "COALESCE(SUM(_profit) / NULLIF(SUM(_revenue), 0) * 100.0, 0.0)"
        if "_profit" in cols
        else "NULL"
    )
    disc_sql = (
        "COALESCE(SUM(_discount) / NULLIF(SUM(_revenue) + SUM(_discount), 0) * 100.0, 0.0)"
        if "_discount" in cols
        else "NULL"
    )
    canc_sql = (
        "AVG(CASE WHEN COALESCE(_is_excluded_status, FALSE) THEN 100.0 ELSE 0.0 END)"
        if "_is_excluded_status" in cols
        else "NULL"
    )

    rows = ParquetStore.query_dicts(
        ctx.con,
        f"""
        SELECT
            _date_day AS date,
            COALESCE(SUM(_revenue), 0.0) AS revenue,
            {orders_sql} AS orders,
            COALESCE(SUM(_revenue) / NULLIF({orders_sql}, 0), 0.0) AS aov,
            {margin_sql} AS margin_pct,
            {disc_sql} AS discount_pct,
            {canc_sql} AS cancellation_pct
        FROM sales
        {where_sql}
        {"AND" if where_sql else "WHERE"} _date_day IS NOT NULL
        GROUP BY 1
        ORDER BY 1 ASC
        """,
        params,
    )

    n = len(rows)
    if n < 7:
        return sanitize_json(
            {
                "available": True,
                "engine": "sklearn_isolation_forest" if HAS_SKLEARN_IF else "numpy_isolation_forest",
                "observations": n,
                "active_features": [],
                "anomalies": [],
                "summary": {"total": 0, "high": 0, "medium": 0},
            }
        )

    candidate_features = ["revenue", "orders", "aov"]
    if "_profit" in cols:
        candidate_features.append("margin_pct")
    if "_discount" in cols:
        candidate_features.append("discount_pct")
    if "_is_excluded_status" in cols and filters.include_excluded_statuses:
        candidate_features.append("cancellation_pct")

    raw_mat = np.column_stack(
        [np.array([float(r.get(f) or 0.0) for r in rows], dtype=float) for f in candidate_features]
    )

    # Standardize robustly using per-feature median/IQR or std
    z_by_feat: dict[str, np.ndarray] = {}
    med_by_feat: dict[str, float] = {}
    scaled_cols: list[np.ndarray] = []
    for idx, f in enumerate(candidate_features):
        z, med = _robust_z(raw_mat[:, idx])
        z_by_feat[f] = z
        med_by_feat[f] = med
        scaled_cols.append(np.clip(z, -10.0, 10.0))
    X_scaled = np.column_stack(scaled_cols)

    # Isolation Forest scoring
    if HAS_SKLEARN_IF:
        iso = IsolationForest(
            n_estimators=100,
            contamination=min(max(contamination, 0.01), 0.25),
            random_state=42,
        )
        iso.fit(X_scaled)
        # decision_function: negative = outlier; map to [0, 1] where higher = more anomalous
        raw_dec = -iso.score_samples(X_scaled)
        if_scores = np.clip(raw_dec, 0.0, 1.0)
        flags_if = iso.predict(X_scaled) == -1
    else:
        if_scores = _numpy_isolation_forest_scores(X_scaled, n_trees=80, seed=42)
        thresh = max(0.58, float(np.quantile(if_scores, 1.0 - min(max(contamination, 0.02), 0.25))))
        flags_if = if_scores >= thresh

    anomalies: list[dict[str, Any]] = []
    for i, r in enumerate(rows):
        rev_z = float(z_by_feat["revenue"][i])
        max_abs_z = max(abs(float(z_by_feat[f][i])) for f in candidate_features)
        is_if_outlier = bool(flags_if[i])
        is_mad_outlier = max_abs_z >= 2.8

        if not (is_if_outlier or is_mad_outlier):
            continue

        # Determine dominant anomaly dimension and type
        dom_feat = max(candidate_features, key=lambda f: abs(float(z_by_feat[f][i])))
        dom_z = float(z_by_feat[dom_feat][i])

        if dom_feat == "margin_pct" and dom_z < 0:
            anom_type = "margin_compression"
        elif dom_feat == "discount_pct" and dom_z > 0:
            anom_type = "discount_anomaly"
        elif dom_feat == "cancellation_pct" and dom_z > 0:
            anom_type = "cancellation_surge"
        elif rev_z >= 0:
            anom_type = "revenue_spike"
        else:
            anom_type = "revenue_drop"

        score = float(if_scores[i])
        severity = "high" if (score >= 0.68 or max_abs_z >= 3.5) else "medium"
        anomalies.append(
            {
                "date": str(r["date"]),
                "anomaly_type": anom_type,
                "severity": severity,
                "if_score": round(score, 3),
                "z_score": round(dom_z, 2),
                "dominant_metric": dom_feat,
                "revenue": round(float(r["revenue"]), 2),
                "expected_revenue": round(med_by_feat["revenue"], 2),
                "orders": int(r["orders"] or 0),
                "margin_pct": round(float(r["margin_pct"]), 2) if r.get("margin_pct") is not None else None,
                "discount_pct": round(float(r["discount_pct"]), 2) if r.get("discount_pct") is not None else None,
                "root_cause": None,
            }
        )

    anomalies.sort(key=lambda item: (item["severity"] != "high", -item["if_score"], -abs(item["z_score"])))
    anomalies = anomalies[:limit]

    # Root-cause attribution in DuckDB for top anomalies
    attr_col = (
        "_product"
        if "_product" in cols
        else ("_category" if "_category" in cols else "_region" if "_region" in cols else None)
    )
    if attr_col:
        for item in anomalies[:10]:
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

    high_cnt = sum(1 for a in anomalies if a["severity"] == "high")
    return sanitize_json(
        {
            "available": True,
            "engine": "sklearn_isolation_forest" if HAS_SKLEARN_IF else "numpy_isolation_forest",
            "observations": n,
            "active_features": candidate_features,
            "anomalies": anomalies,
            "summary": {
                "total": len(anomalies),
                "high": high_cnt,
                "medium": len(anomalies) - high_cnt,
            },
        }
    )
