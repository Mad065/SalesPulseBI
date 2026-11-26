"""Deterministic Zero-Trust DuckDB/ML tools bound to server-side project context (zero LLM arithmetic)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from salespulsebi.ai.guardrails import validate_readonly_sql
from salespulsebi.ai.schemas import BreakdownToolInput, SQLToolInput, ToolExecutionTrace
from salespulsebi.analytics.overview import compute_overview
from salespulsebi.analytics.query_builder import build_where_clause, open_context
from salespulsebi.ml.anomalies import detect_multivariate_anomalies
from salespulsebi.ml.automl_forecast import run_automl_forecast
from salespulsebi.ml.scenarios import compute_pvm_bridge
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import CustomPanelSpec, FilterState, ProjectSummary
from salespulsebi.storage.parquet_store import ParquetStore, sanitize_json

DIM_COL_MAP: dict[str, str] = {
    "product": "_product",
    "category": "_category",
    "region": "_region",
    "channel": "_channel",
    "customer": "_customer",
    "salesperson": "_salesperson",
    "payment_method": "_payment_method",
}

METRIC_SQL_MAP: dict[str, tuple[str, str | None]] = {
    "revenue": ("COALESCE(SUM(_revenue), 0)", "_revenue"),
    "profit": ("COALESCE(SUM(_profit), 0)", "_profit"),
    "units": ("COALESCE(SUM(_quantity), 0)", "_quantity"),
    "orders": ("COUNT(DISTINCT _order_id)", "_order_id"),
}


@dataclass
class BoundToolContext:
    """Server-side bound security context — NEVER supplied by LLM tool arguments."""

    project: ProjectSummary
    parquet_path: Path
    models_dir: Path
    rag_db_path: Path
    db: MetadataDB
    filters: FilterState
    fx_rate: Decimal


class DeterministicToolRunner:
    """Executes strictly validated, read-only DuckDB & ML tools with zero LLM arithmetic."""

    def __init__(self, bound: BoundToolContext) -> None:
        self.bound = bound

    def _open(self):
        return open_context(self.bound.parquet_path, fx_rate=self.bound.fx_rate)

    def get_schema_summary(self) -> dict[str, Any]:
        if not self.bound.parquet_path.exists():
            return {"columns": [], "capabilities": {}}
        with self._open() as ctx:
            return {
                "columns": sorted(ctx.columns),
                "column_types": ctx.column_types,
                "capabilities": ctx.capabilities,
            }

    def run_tool(
        self, tool_name: str, args: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], ToolExecutionTrace]:
        t0 = time.perf_counter()
        raw_args = args or {}
        try:
            if tool_name == "get_kpi_overview":
                res = self._tool_kpi_overview()
                return res, ToolExecutionTrace(
                    tool_name=tool_name,
                    args_summary={"currency": self.bound.project.currency},
                    ok=True,
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                )

            if tool_name == "get_dimension_breakdown":
                inp = BreakdownToolInput.model_validate(raw_args)
                res = self._tool_dimension_breakdown(inp)
                return res, ToolExecutionTrace(
                    tool_name=tool_name,
                    args_summary=inp.model_dump(),
                    ok=res.get("ok", True),
                    row_count=len(res.get("rows", [])),
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                )

            if tool_name == "get_period_trend_and_forecast":
                metric = str(raw_args.get("target_metric", "revenue"))
                res = self._tool_forecast(metric)
                return res, ToolExecutionTrace(
                    tool_name=tool_name,
                    args_summary={"target_metric": metric, "granularity": self.bound.filters.granularity},
                    ok=bool(res.get("available")),
                    row_count=len(res.get("forecast", [])),
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                )

            if tool_name == "get_anomalies_and_pvm":
                res = self._tool_anomalies_pvm()
                return res, ToolExecutionTrace(
                    tool_name=tool_name,
                    args_summary={},
                    ok=True,
                    row_count=len(res.get("anomalies", [])),
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                )

            if tool_name == "run_readonly_sql":
                inp = SQLToolInput.model_validate(raw_args)
                res, executed_sql = self._tool_readonly_sql(inp)
                return res, ToolExecutionTrace(
                    tool_name=tool_name,
                    args_summary={"limit": inp.limit},
                    ok=bool(res.get("ok")),
                    row_count=len(res.get("rows", [])),
                    sql=executed_sql,
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                    error_code=res.get("error_code"),
                )

            if tool_name == "propose_dashboard_panel":
                res = self._tool_propose_panel(raw_args)
                return res, ToolExecutionTrace(
                    tool_name=tool_name,
                    args_summary={"panel_type": raw_args.get("panel_type", "bar")},
                    ok=bool(res.get("ok")),
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                )

            return (
                {"ok": False, "error_code": "unknown_tool", "hint": f"Unknown tool: {tool_name}"},
                ToolExecutionTrace(
                    tool_name=tool_name,
                    ok=False,
                    error_code="unknown_tool",
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                ),
            )
        except Exception as exc:
            return (
                {"ok": False, "error_code": "tool_execution_error", "hint": type(exc).__name__},
                ToolExecutionTrace(
                    tool_name=tool_name,
                    ok=False,
                    error_code=type(exc).__name__,
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                ),
            )

    def _tool_kpi_overview(self) -> dict[str, Any]:
        with self._open() as ctx:
            ov = compute_overview(ctx, self.bound.filters)
        return sanitize_json(
            {
                "ok": True,
                "currency": self.bound.project.currency,
                "kpis": ov.get("kpis", {}),
                "top_categories": ov.get("top_categories", [])[:5],
                "top_products": ov.get("top_products", [])[:5],
            }
        )

    def _tool_dimension_breakdown(self, inp: BreakdownToolInput) -> dict[str, Any]:
        with self._open() as ctx:
            col = DIM_COL_MAP.get(inp.dimension)
            if not col or col not in ctx.columns:
                return {"ok": False, "error_code": "dimension_unavailable", "rows": []}
            agg_sql, req_col = METRIC_SQL_MAP[inp.metric]
            if inp.metric == "orders" and "_order_id" not in ctx.columns:
                agg_sql = "COUNT(*)"
            elif req_col and req_col not in ctx.columns:
                return {"ok": False, "error_code": "metric_unavailable", "rows": []}
            where_sql, params = build_where_clause(self.bound.filters, ctx.columns)
            direction = "ASC" if inp.order == "asc" else "DESC"
            rows = ParquetStore.query_dicts(
                ctx.con,
                f"""
                SELECT
                    COALESCE(CAST({col} AS VARCHAR), 'N/A') AS label,
                    {agg_sql} AS value,
                    COUNT(*) AS rows_count
                FROM sales
                {where_sql}
                GROUP BY 1
                ORDER BY value {direction} NULLS LAST
                LIMIT ?
                """,
                params + [inp.limit],
            )
        return sanitize_json(
            {
                "ok": True,
                "dimension": inp.dimension,
                "metric": inp.metric,
                "currency": self.bound.project.currency,
                "rows": rows,
            }
        )

    def _tool_forecast(self, metric: str) -> dict[str, Any]:
        safe_metric = metric if metric in ("revenue", "profit", "units", "orders") else "revenue"
        with self._open() as ctx:
            fc = run_automl_forecast(
                ctx,
                self.bound.filters,
                project_id=self.bound.project.id,
                db=self.bound.db,
                models_dir=self.bound.models_dir,
                parquet_path=self.bound.parquet_path,
                target_metric=safe_metric,
                granularity=self.bound.filters.granularity,
                horizon_days=self.bound.filters.forecast_horizon,
                lang=self.bound.filters.lang,
            )
        return sanitize_json(
            {
                "available": fc.get("available", False),
                "target_metric": safe_metric,
                "winner_model": fc.get("winner_model"),
                "winner_metrics": fc.get("winner_metrics"),
                "forecast_total": fc.get("forecast_total"),
                "forecast_total_decimal": fc.get("forecast_total_decimal"),
                "forecast": (fc.get("forecast") or [])[:12],
                "feature_importance": (fc.get("feature_importance") or [])[:5],
            }
        )

    def _tool_anomalies_pvm(self) -> dict[str, Any]:
        with self._open() as ctx:
            an = detect_multivariate_anomalies(ctx, self.bound.filters, limit=8)
            pvm = compute_pvm_bridge(ctx, self.bound.filters)
        return sanitize_json(
            {
                "ok": True,
                "anomalies": an.get("anomalies", [])[:6],
                "anomalies_summary": an.get("summary", {}),
                "pvm": {
                    "available": pvm.get("available", False),
                    "total_delta": pvm.get("total_delta"),
                    "steps": pvm.get("steps", []),
                },
            }
        )

    def _tool_readonly_sql(self, inp: SQLToolInput) -> tuple[dict[str, Any], str]:
        ok, clean_sql, reason = validate_readonly_sql(
            inp.sql, allowed_tables={"sales"}, max_limit=min(inp.limit, 500)
        )
        if not ok:
            return {"ok": False, "error_code": reason or "invalid_sql", "rows": []}, ""
        with self._open() as ctx:
            rows = ParquetStore.query_dicts(ctx.con, clean_sql)
        return sanitize_json({"ok": True, "sql": clean_sql, "rows": rows[:100]}), clean_sql

    def _tool_propose_panel(self, raw_args: dict[str, Any]) -> dict[str, Any]:
        with self._open() as ctx:
            x_col = raw_args.get("x_column")
            y_col = raw_args.get("y_column")
            if x_col and x_col not in ctx.columns:
                x_col = next(
                    (c for c in ("_category", "_product", "_region", "_channel", "_date_day") if c in ctx.columns),
                    None,
                )
            if y_col and y_col not in ctx.columns:
                y_col = "_revenue" if "_revenue" in ctx.columns else None
            spec = CustomPanelSpec(
                id=str(raw_args.get("id") or f"ai_panel_{int(time.time())}"),
                title=str(raw_args.get("title") or "AI Panel")[:80],
                panel_type=raw_args.get("panel_type", "bar"),
                x_column=x_col,
                y_column=y_col,
                aggregation=raw_args.get("aggregation", "sum"),
                format_kind=raw_args.get("format_kind", "currency" if y_col == "_revenue" else "auto"),
                limit=int(raw_args.get("limit", 10)),
                span=2 if int(raw_args.get("span", 1)) == 2 else 1,
            )
        return {"ok": True, "panel_spec": spec.model_dump()}
