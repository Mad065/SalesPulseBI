"""AutoML Forecasting Tournament with walk-forward CV, conformal bands, and feature attribution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path
from typing import Any

import numpy as np

from salespulsebi.analytics.query_builder import AnalyticsContext
from salespulsebi.ml.features import (
    build_lag_feature_row,
    build_supervised_lag_matrix,
    compute_dataset_hash,
    extract_regular_series,
    generate_future_periods,
    season_length_for,
    summarize_feature_name,
)
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import FilterState
from salespulsebi.storage.parquet_store import sanitize_json

try:  # Optional scikit-learn acceleration (graceful fallback on Python 3.15 if absent)
    from sklearn.ensemble import HistGradientBoostingRegressor  # type: ignore[import-not-found]
    from sklearn.linear_model import Ridge  # type: ignore[import-not-found]

    HAS_SKLEARN = True
except Exception:
    HAS_SKLEARN = False


def _dec2(val: float) -> str:
    with localcontext() as ctx:
        ctx.prec = 28
        d = Decimal(str(val if np.isfinite(val) else 0.0))
        return format(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN), "f")


def _compute_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    naive_scale: float,
) -> dict[str, float]:
    """Compute WAPE %, sMAPE %, MASE, and RMSE with strict zero-denominator guards."""
    if len(y_true) == 0:
        return {"wape": 0.0, "smape": 0.0, "mase": 1.0, "rmse": 0.0}
    err = np.abs(y_true - y_pred)
    mae = float(np.mean(err))
    rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
    denom_sum = float(np.sum(np.abs(y_true)))
    wape = (float(np.sum(err)) / denom_sum * 100.0) if denom_sum > 1e-9 else 0.0
    smape_denom = np.abs(y_true) + np.abs(y_pred)
    with np.errstate(divide="ignore", invalid="ignore"):
        smape_terms = np.where(smape_denom > 1e-9, 2.0 * err / smape_denom, 0.0)
    smape = float(np.mean(smape_terms) * 100.0)
    mase = (mae / naive_scale) if naive_scale > 1e-9 else (0.0 if mae <= 1e-9 else 1.0)
    return {
        "wape": round(wape, 2),
        "smape": round(smape, 2),
        "mase": round(mase, 3),
        "rmse": round(rmse, 2),
    }


# ── Individual Candidate Forecasters ─────────────────────────────────────────


def _forecast_seasonal_naive(
    values: np.ndarray, horizon: int, season_len: int
) -> np.ndarray:
    n = len(values)
    if n == 0:
        return np.zeros(horizon, dtype=float)
    if n >= season_len and season_len > 1:
        tail = values[-season_len:]
        return np.array([tail[h % season_len] for h in range(horizon)], dtype=float)
    return np.full(horizon, float(values[-1]), dtype=float)


def _fit_predict_holt_winters(
    values: np.ndarray,
    horizon: int,
    granularity: str,
    *,
    alpha: float = 0.30,
    beta: float = 0.08,
    gamma: float = 0.25,
    phi: float = 0.90,
) -> np.ndarray:
    n = len(values)
    if n == 0:
        return np.zeros(horizon, dtype=float)
    if n == 1:
        return np.full(horizon, float(values[0]), dtype=float)

    s_len = season_length_for(granularity)
    use_seasonal = granularity == "day" and n >= 2 * s_len and s_len > 1
    y = values.astype(float)

    if use_seasonal:
        seasonals = np.zeros(s_len, dtype=float)
        init_mean = float(np.mean(y[:s_len]))
        for i in range(s_len):
            seasonals[i] = y[i] - init_mean
        level = init_mean
        trend = float((np.mean(y[s_len : 2 * s_len]) - init_mean) / s_len)
        for i in range(n):
            idx = i % s_len
            prev_s = seasonals[idx]
            new_level = alpha * (y[i] - prev_s) + (1.0 - alpha) * (level + phi * trend)
            new_trend = beta * (new_level - level) + (1.0 - beta) * phi * trend
            seasonals[idx] = gamma * (y[i] - new_level) + (1.0 - gamma) * prev_s
            level, trend = new_level, new_trend
        preds = []
        phi_sum = 0.0
        for h in range(1, horizon + 1):
            phi_sum += phi**h
            preds.append(level + phi_sum * trend + seasonals[(n + h - 1) % s_len])
        return np.asarray(preds, dtype=float)

    level = float(y[0])
    trend = float(y[1] - y[0])
    for i in range(n):
        new_level = alpha * y[i] + (1.0 - alpha) * (level + phi * trend)
        new_trend = beta * (new_level - level) + (1.0 - beta) * phi * trend
        level, trend = new_level, new_trend
    preds = []
    phi_sum = 0.0
    for h in range(1, horizon + 1):
        phi_sum += phi**h
        preds.append(level + phi_sum * trend)
    return np.asarray(preds, dtype=float)


def _forecast_holt_tuned(
    values: np.ndarray, horizon: int, granularity: str
) -> np.ndarray:
    """Grid-search Holt/Holt-Winters parameters using only the training prefix."""
    n = len(values)
    if n < 6:
        return _fit_predict_holt_winters(values, horizon, granularity)
    val_h = max(1, min(3, n // 4))
    tr, va = values[:-val_h], values[-val_h:]
    best_err = float("inf")
    best_cfg = (0.30, 0.08, 0.25, 0.90)
    for alpha in (0.15, 0.35, 0.55):
        for beta in (0.04, 0.14):
            for phi in (0.80, 0.94):
                pred = _fit_predict_holt_winters(
                    tr, val_h, granularity, alpha=alpha, beta=beta, gamma=0.20, phi=phi
                )
                err = float(np.mean(np.abs(va - pred)))
                if err < best_err:
                    best_err = err
                    best_cfg = (alpha, beta, 0.20, phi)
    a, b, g, p = best_cfg
    return _fit_predict_holt_winters(values, horizon, granularity, alpha=a, beta=b, gamma=g, phi=p)


def _forecast_theta(values: np.ndarray, horizon: int) -> np.ndarray:
    """Classic Theta method (SES on 2*y - linear_trend + damped drift)."""
    n = len(values)
    if n < 3:
        return np.full(horizon, float(values[-1]) if n else 0.0, dtype=float)
    t = np.arange(n, dtype=float)
    slope, intercept = np.polyfit(t, values, 1)
    # SES with alpha=0.25
    alpha = 0.25
    level = float(values[0])
    for v in values[1:]:
        level = alpha * float(v) + (1.0 - alpha) * level
    preds = []
    for h in range(1, horizon + 1):
        drift = 0.5 * slope * (h + (1.0 - (1.0 - alpha) ** n) / alpha)
        preds.append(level + 0.5 * (slope * (n - 1 + h) + intercept - level) * 0.35 + drift * 0.35)
    return np.asarray(preds, dtype=float)


class _NumpyRidgeModel:
    """Standardized Ridge regression with exact closed-form Cholesky/solve (zero external deps)."""

    def __init__(self, alpha: float = 1.0) -> None:
        self.alpha = alpha
        self.mean_x: np.ndarray | None = None
        self.std_x: np.ndarray | None = None
        self.mean_y: float = 0.0
        self.coef_: np.ndarray | None = None

    def fit(self, X: np.ndarray, y: np.ndarray) -> _NumpyRidgeModel:
        self.mean_x = np.mean(X, axis=0)
        self.std_x = np.std(X, axis=0)
        self.std_x = np.where(self.std_x < 1e-8, 1.0, self.std_x)
        Xs = (X - self.mean_x) / self.std_x
        self.mean_y = float(np.mean(y))
        yc = y - self.mean_y
        p = Xs.shape[1]
        A = Xs.T @ Xs + self.alpha * np.eye(p, dtype=float)
        b = Xs.T @ yc
        try:
            self.coef_ = np.linalg.solve(A, b)
        except np.linalg.LinAlgError:
            self.coef_ = np.linalg.lstsq(A, b, rcond=None)[0]
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        assert self.mean_x is not None and self.std_x is not None and self.coef_ is not None
        Xs = (X - self.mean_x) / self.std_x
        return self.mean_y + Xs @ self.coef_


class _NumpyGradientBoostedStumps:
    """Deterministic Gradient-Boosted Decision Stumps in pure NumPy (fallback when sklearn absent)."""

    def __init__(self, n_estimators: int = 35, lr: float = 0.12) -> None:
        self.n_estimators = n_estimators
        self.lr = lr
        self.init_val: float = 0.0
        self.stumps: list[tuple[int, float, float, float]] = []
        self.feature_importances_: np.ndarray | None = None

    def fit(self, X: np.ndarray, y: np.ndarray) -> _NumpyGradientBoostedStumps:
        n, p = X.shape
        self.init_val = float(np.mean(y))
        pred = np.full(n, self.init_val, dtype=float)
        self.stumps = []
        gains = np.zeros(p, dtype=float)
        for _ in range(self.n_estimators):
            resid = y - pred
            base_loss = float(np.sum(resid**2))
            if base_loss < 1e-10:
                break
            best_loss = base_loss
            best_stump: tuple[int, float, float, float] | None = None
            for j in range(p):
                col = X[:, j]
                qs = np.unique(np.quantile(col, [0.2, 0.4, 0.5, 0.6, 0.8]))
                for thr in qs:
                    left_mask = col <= thr
                    if not np.any(left_mask) or np.all(left_mask):
                        continue
                    l_val = float(np.mean(resid[left_mask]))
                    r_val = float(np.mean(resid[~left_mask]))
                    step = np.where(left_mask, l_val, r_val)
                    loss = float(np.sum((resid - step) ** 2))
                    if loss < best_loss:
                        best_loss = loss
                        best_stump = (j, float(thr), l_val, r_val)
            if best_stump is None:
                break
            j, thr, l_val, r_val = best_stump
            gains[j] += max(0.0, base_loss - best_loss)
            step = np.where(X[:, j] <= thr, l_val, r_val)
            pred += self.lr * step
            self.stumps.append(best_stump)
        total_gain = float(np.sum(gains))
        self.feature_importances_ = gains / total_gain if total_gain > 1e-12 else np.ones(p) / max(p, 1)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        out = np.full(X.shape[0], self.init_val, dtype=float)
        for j, thr, l_val, r_val in self.stumps:
            out += self.lr * np.where(X[:, j] <= thr, l_val, r_val)
        return out


def _forecast_ml_recursive(
    periods: list[str],
    values: np.ndarray,
    horizon: int,
    granularity: str,
    model_kind: str,
) -> tuple[np.ndarray, list[tuple[str, float]]]:
    """Train a strictly causal lag-based ML regressor and forecast recursively."""
    X, y, feat_names, lags = build_supervised_lag_matrix(periods, values, granularity)
    if X.shape[0] < 5:
        return _forecast_seasonal_naive(values, horizon, season_length_for(granularity)), []

    if model_kind == "ridge_lags":
        if HAS_SKLEARN:
            mean_x = np.mean(X, axis=0)
            std_x = np.where(np.std(X, axis=0) < 1e-8, 1.0, np.std(X, axis=0))
            Xs = (X - mean_x) / std_x
            reg = Ridge(alpha=2.0)
            reg.fit(Xs, y)

            def _pred_fn(row: np.ndarray) -> float:
                return float(reg.predict((row.reshape(1, -1) - mean_x) / std_x)[0])

            raw_imp = np.abs(reg.coef_)
        else:
            reg = _NumpyRidgeModel(alpha=2.0).fit(X, y)

            def _pred_fn(row: np.ndarray) -> float:
                return float(reg.predict(row.reshape(1, -1))[0])

            raw_imp = np.abs(reg.coef_) if reg.coef_ is not None else np.zeros(X.shape[1])
    else:
        if HAS_SKLEARN and X.shape[0] >= 18:
            gbr = HistGradientBoostingRegressor(
                max_iter=60, max_depth=3, learning_rate=0.08, min_samples_leaf=4, random_state=42
            )
            gbr.fit(X, y)

            def _pred_fn(row: np.ndarray) -> float:
                return float(gbr.predict(row.reshape(1, -1))[0])

            # Lightweight permutation-free correlation importance
            base_p = gbr.predict(X)
            raw_imp = np.array(
                [
                    abs(float(np.corrcoef(X[:, j], base_p)[0, 1]))
                    if np.std(X[:, j]) > 1e-9 and np.std(base_p) > 1e-9
                    else 0.0
                    for j in range(X.shape[1])
                ]
            )
        else:
            gbr = _NumpyGradientBoostedStumps(n_estimators=40, lr=0.12).fit(X, y)

            def _pred_fn(row: np.ndarray) -> float:
                return float(gbr.predict(row.reshape(1, -1))[0])

            raw_imp = (
                gbr.feature_importances_
                if gbr.feature_importances_ is not None
                else np.zeros(X.shape[1])
            )

    future_labels = generate_future_periods(periods[-1], horizon, granularity)
    hist = list(values.astype(float))
    preds: list[float] = []
    base_idx = len(values)
    for h, lbl in enumerate(future_labels):
        row_vec, _ = build_lag_feature_row(hist, lbl, granularity, base_idx + h, lags)
        p_val = _pred_fn(row_vec)
        preds.append(p_val)
        hist.append(p_val)

    total_imp = float(np.sum(raw_imp))
    norm_imp = raw_imp / total_imp if total_imp > 1e-12 else np.zeros_like(raw_imp)
    importance = sorted(
        zip(feat_names, norm_imp.tolist(), strict=False),
        key=lambda item: item[1],
        reverse=True,
    )
    return np.asarray(preds, dtype=float), importance


def _run_candidate(
    model_name: str,
    periods: list[str],
    values: np.ndarray,
    horizon: int,
    granularity: str,
) -> tuple[np.ndarray, list[tuple[str, float]]]:
    s_len = season_length_for(granularity)
    if model_name == "seasonal_naive":
        return _forecast_seasonal_naive(values, horizon, s_len), []
    if model_name == "holt_winters_tuned":
        return _forecast_holt_tuned(values, horizon, granularity), []
    if model_name == "theta_damped":
        return _forecast_theta(values, horizon), []
    if model_name in ("ridge_lags", "boosted_trees_lags"):
        return _forecast_ml_recursive(periods, values, horizon, granularity, model_name)
    return _forecast_seasonal_naive(values, horizon, s_len), []


@dataclass
class AutoMLForecastResult:
    available: bool
    payload: dict[str, Any]


def resolve_horizon_steps(horizon_days: int, granularity: str) -> int:
    if granularity == "day":
        return max(3, min(180, horizon_days))
    if granularity == "week":
        return max(4, min(26, max(horizon_days // 7, 4)))
    if granularity == "month":
        return max(3, min(12, max(1, horizon_days // 30) * 3))
    return 4


def run_automl_forecast(
    ctx: AnalyticsContext,
    filters: FilterState,
    *,
    project_id: str | None = None,
    db: MetadataDB | None = None,
    models_dir: Path | None = None,
    parquet_path: Path | None = None,
    target_metric: str = "revenue",
    granularity: str | None = None,
    horizon_days: int | None = None,
    force_retrain: bool = False,
    lang: str = "es",
) -> dict[str, Any]:
    """
    Run the local-first AutoML Forecasting Tournament:
    - Walk-Forward / Rolling-Origin Cross-Validation without look-ahead bias
    - Automatic model selection (lowest WAPE, tie-broken by MASE)
    - Top-2 Inverse-WAPE Ensemble candidate
    - Conformal Prediction 80% / 95% confidence bands
    - Feature importance explanation + local model persistence
    """
    gran = granularity or filters.granularity or "month"
    h_days = horizon_days if horizon_days is not None else filters.forecast_horizon
    horizon_steps = resolve_horizon_steps(h_days, gran)
    allow_negative = target_metric == "profit"

    df = extract_regular_series(ctx, filters, target_metric=target_metric, granularity=gran)
    n = len(df)
    if n < 3:
        return sanitize_json(
            {
                "available": False,
                "reason": "insufficient_history",
                "target_metric": target_metric,
                "granularity": gran,
                "observations": n,
            }
        )

    periods = df["period"].astype(str).tolist()
    values = df["value"].to_numpy(dtype=float)
    s_len = season_length_for(gran)

    # Scale for MASE computed strictly from in-sample naive differences
    if n > s_len:
        naive_scale = float(np.mean(np.abs(values[s_len:] - values[:-s_len])))
    else:
        naive_scale = float(np.mean(np.abs(np.diff(values)))) if n > 1 else 0.0

    # Candidate roster adapts to series length
    candidate_names = ["seasonal_naive", "holt_winters_tuned", "theta_damped"]
    if n >= 10:
        candidate_names.extend(["ridge_lags", "boosted_trees_lags"])

    # Walk-forward CV folds (strictly causal)
    cv_h = max(1, min(horizon_steps, max(1, n // 5), 6))
    min_train = max(4, n - 3 * cv_h)
    fold_origins: list[int] = []
    origin = min_train
    while origin + cv_h <= n:
        fold_origins.append(origin)
        origin += cv_h
    if not fold_origins and n >= 4:
        fold_origins = [n - 1]
        cv_h = 1

    cv_preds_by_model: dict[str, list[np.ndarray]] = {m: [] for m in candidate_names}
    cv_actuals: list[np.ndarray] = []

    for orig in fold_origins:
        tr_p = periods[:orig]
        tr_v = values[:orig]
        va_v = values[orig : orig + cv_h]
        cv_actuals.append(va_v)
        for m in candidate_names:
            p_hat, _ = _run_candidate(m, tr_p, tr_v, len(va_v), gran)
            if not allow_negative:
                p_hat = np.maximum(0.0, p_hat)
            cv_preds_by_model[m].append(p_hat)

    y_cv_all = np.concatenate(cv_actuals) if cv_actuals else np.empty((0,), dtype=float)
    leaderboard: list[dict[str, Any]] = []
    residuals_by_model: dict[str, np.ndarray] = {}

    for m in candidate_names:
        if cv_preds_by_model[m]:
            p_all = np.concatenate(cv_preds_by_model[m])
            metrics = _compute_metrics(y_cv_all, p_all, naive_scale)
            residuals_by_model[m] = np.abs(y_cv_all - p_all)
        else:
            metrics = {"wape": 0.0, "smape": 0.0, "mase": 1.0, "rmse": 0.0}
            residuals_by_model[m] = np.array([float(np.std(values))], dtype=float)
        leaderboard.append({"model": m, **metrics})

    # Add inverse-WAPE Top-2 Ensemble candidate if we have >=2 non-naive candidates with CV
    non_naive = sorted(
        [row for row in leaderboard if row["model"] != "seasonal_naive"],
        key=lambda r: (r["wape"], r["mase"], r["rmse"]),
    )
    top2_models: list[str] = []
    top2_weights: list[float] = []
    if len(non_naive) >= 2 and cv_actuals:
        m1, m2 = non_naive[0]["model"], non_naive[1]["model"]
        w1_raw = 1.0 / max(float(non_naive[0]["wape"]), 0.5)
        w2_raw = 1.0 / max(float(non_naive[1]["wape"]), 0.5)
        w_sum = w1_raw + w2_raw
        w1, w2 = w1_raw / w_sum, w2_raw / w_sum
        top2_models = [m1, m2]
        top2_weights = [w1, w2]
        p_ens = w1 * np.concatenate(cv_preds_by_model[m1]) + w2 * np.concatenate(cv_preds_by_model[m2])
        ens_metrics = _compute_metrics(y_cv_all, p_ens, naive_scale)
        residuals_by_model["top2_ensemble"] = np.abs(y_cv_all - p_ens)
        leaderboard.append(
            {
                "model": "top2_ensemble",
                "components": top2_models,
                "weights": [round(w1, 3), round(w2, 3)],
                **ens_metrics,
            }
        )

    leaderboard.sort(key=lambda r: (r["wape"], r["mase"], r["rmse"]))
    winner = leaderboard[0]
    winner_name = str(winner["model"])

    # Fit winner on full series and collect feature importance from best ML model
    importance_pairs: list[tuple[str, float]] = []
    if winner_name == "top2_ensemble" and len(top2_models) == 2:
        p1, imp1 = _run_candidate(top2_models[0], periods, values, horizon_steps, gran)
        p2, imp2 = _run_candidate(top2_models[1], periods, values, horizon_steps, gran)
        final_preds = top2_weights[0] * p1 + top2_weights[1] * p2
        importance_pairs = imp1 or imp2
    else:
        final_preds, importance_pairs = _run_candidate(winner_name, periods, values, horizon_steps, gran)

    if not importance_pairs and n >= 10:
        _, importance_pairs = _run_candidate("ridge_lags", periods, values, 1, gran)

    if not allow_negative:
        final_preds = np.maximum(0.0, final_preds)

    # Conformal Prediction calibrated intervals (80% & 95%)
    abs_res = residuals_by_model.get(winner_name, np.array([float(np.std(values))], dtype=float))
    if len(abs_res) == 0 or float(np.max(abs_res)) <= 1e-9:
        fallback_sigma = max(float(np.std(values)) * 0.15, float(np.mean(np.abs(values))) * 0.05)
        q80 = 1.2816 * fallback_sigma
        q95 = 1.96 * fallback_sigma
    else:
        q80 = float(np.quantile(abs_res, 0.80))
        q95 = float(np.quantile(abs_res, 0.95))
        if q95 < q80:
            q95 = q80 * 1.35

    future_labels = generate_future_periods(periods[-1], horizon_steps, gran)
    forecast_points: list[dict[str, Any]] = []
    total_forecast_dec = Decimal("0")

    for h, (lbl, raw_p) in enumerate(zip(future_labels, final_preds, strict=False), start=1):
        scale = float(np.sqrt(1.0 + 0.18 * (h - 1)))
        val = float(raw_p)
        lo80 = val - q80 * scale
        hi80 = val + q80 * scale
        lo95 = val - q95 * scale
        hi95 = val + q95 * scale
        if not allow_negative:
            val = max(0.0, val)
            lo80 = max(0.0, lo80)
            lo95 = max(0.0, lo95)
        with localcontext() as dctx:
            dctx.prec = 28
            total_forecast_dec += Decimal(_dec2(val))
        forecast_points.append(
            {
                "period": lbl,
                "forecast": round(val, 2),
                "forecast_decimal": _dec2(val),
                "lower_80": round(lo80, 2),
                "upper_80": round(hi80, 2),
                "lower_95": round(lo95, 2),
                "upper_95": round(hi95, 2),
            }
        )

    feature_importance = [
        {
            "feature": fname,
            "label": summarize_feature_name(fname, lang=lang),
            "importance_pct": round(score * 100.0, 1),
        }
        for fname, score in importance_pairs[:8]
        if score > 0.005
    ]

    series_out = [
        {"period": p, "value": round(float(v), 2)}
        for p, v in zip(periods, values, strict=False)
    ]

    result: dict[str, Any] = sanitize_json(
        {
            "available": True,
            "engine": "sklearn+numpy" if HAS_SKLEARN else "numpy_native",
            "target_metric": target_metric,
            "granularity": gran,
            "observations": n,
            "cv_folds": len(fold_origins),
            "winner_model": winner_name,
            "winner_metrics": {
                "wape": winner["wape"],
                "smape": winner["smape"],
                "mase": winner["mase"],
                "rmse": winner["rmse"],
            },
            "leaderboard": leaderboard,
            "feature_importance": feature_importance,
            "forecast_total": float(total_forecast_dec),
            "forecast_total_decimal": format(total_forecast_dec, "f"),
            "series": series_out,
            "forecast": forecast_points,
        }
    )

    # Persist winning model metadata & artifact when project context is provided
    if project_id and db is not None:
        data_hash = compute_dataset_hash(parquet_path) if parquet_path else ""
        artifact_path_str: str | None = None
        if models_dir is not None:
            models_dir.mkdir(parents=True, exist_ok=True)
            art_file = models_dir / f"forecast_{target_metric}_{gran}.json"
            art_file.write_text(
                json.dumps(
                    {
                        "winner_model": winner_name,
                        "winner_metrics": result["winner_metrics"],
                        "leaderboard": leaderboard,
                        "feature_importance": feature_importance,
                        "data_hash": data_hash,
                        "observations": n,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            artifact_path_str = str(art_file)

        saved = db.upsert_ml_model(
            project_id=project_id,
            target_metric=target_metric,
            granularity=gran,
            winner_model=winner_name,
            candidates=leaderboard,
            metrics=result["winner_metrics"],
            feature_importance=feature_importance,
            data_hash=data_hash,
            artifact_path=artifact_path_str,
        )
        result["trained_at"] = saved.get("trained_at")

    return result
