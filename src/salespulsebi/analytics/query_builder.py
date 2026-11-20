"""Safe DuckDB context, capability detection, and parameterized filter builder."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb

from salespulsebi.storage.models import FilterState
from salespulsebi.storage.parquet_store import ParquetStore, sanitize_json


@dataclass
class AnalyticsContext:
    con: duckdb.DuckDBPyConnection
    columns: set[str]
    column_types: dict[str, str]
    capabilities: dict[str, bool]

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> AnalyticsContext:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def detect_capabilities(cols: set[str]) -> dict[str, bool]:
    """Determine which analytics modules are unlocked by the columns present."""
    has_rev = "_revenue" in cols
    has_date = "_date" in cols
    has_prod = "_product" in cols
    has_cat = "_category" in cols
    has_order = "_order_id" in cols
    has_cust = "_customer" in cols
    has_cost = "_cost" in cols and has_rev
    has_qty = "_quantity" in cols
    has_disc = "_discount" in cols
    has_region = "_region" in cols
    has_channel = "_channel" in cols
    has_salesperson = "_salesperson" in cols
    has_payment = "_payment_method" in cols
    has_status = "_status" in cols

    return {
        "revenue": has_rev,
        "date": has_date,
        "timeseries": has_date and has_rev,
        "forecast": has_date and has_rev,
        "anomalies": has_date and has_rev,
        "seasonality": has_date and has_rev,
        "products": has_prod and has_rev,
        "pareto": has_prod and has_rev,
        "categories": has_cat and has_rev,
        "market_basket": has_order and has_prod,
        "orders": has_order,
        "customers": has_cust and has_rev,
        "rfm": has_cust and has_rev and has_date,
        "cohorts": has_cust and has_date and has_rev,
        "profit": has_cost,
        "quantity": has_qty,
        "discount": has_disc,
        "region": has_region,
        "channel": has_channel,
        "salesperson": has_salesperson,
        "payment_method": has_payment,
        "status": has_status,
        "dimensions": any(
            [has_region, has_channel, has_salesperson, has_payment, has_disc, "_source_folder" in cols]
        ),
        "adhoc": len(cols) >= 2,
    }


def open_context(parquet_path: Path) -> AnalyticsContext:
    if not parquet_path.exists():
        raise FileNotFoundError(f"Dataset parquet not found: {parquet_path}")
    con = ParquetStore.connect_parquet(parquet_path)
    desc = con.execute("DESCRIBE sales").fetchall()
    col_types = {str(r[0]): str(r[1]) for r in desc}
    cols = set(col_types.keys())
    return AnalyticsContext(
        con=con,
        columns=cols,
        column_types=col_types,
        capabilities=detect_capabilities(cols),
    )


def safe_ident(col_name: str, allowed_cols: set[str]) -> str:
    """Strictly validate a column identifier against the real Parquet schema."""
    if col_name not in allowed_cols:
        raise ValueError(f"Invalid column identifier: {col_name}")
    escaped = col_name.replace('"', '""')
    return f'"{escaped}"'


def build_where_clause(
    filters: FilterState,
    cols: set[str],
    *,
    date_override: tuple[str | None, str | None] | None = None,
) -> tuple[str, list[Any]]:
    """Build a parameterized WHERE clause for DuckDB queries."""
    clauses: list[str] = []
    params: list[Any] = []

    if "_is_excluded_status" in cols and not filters.include_excluded_statuses:
        clauses.append("COALESCE(_is_excluded_status, FALSE) = FALSE")

    d_from, d_to = date_override if date_override is not None else (filters.date_from, filters.date_to)
    if "_date_day" in cols:
        if d_from:
            clauses.append("_date_day >= ?")
            params.append(d_from[:10])
        if d_to:
            clauses.append("_date_day <= ?")
            params.append(d_to[:10])

    multi_filters = [
        ("_category", filters.categories),
        ("_region", filters.regions),
        ("_channel", filters.channels),
        ("_salesperson", filters.salespersons),
        ("_payment_method", filters.payment_methods),
        ("_status", filters.statuses),
        ("_source_file", filters.source_files),
        ("_source_folder", filters.source_folders),
        ("_product", filters.products),
        ("_customer", filters.customers),
    ]
    for col, values in multi_filters:
        if col in cols and values:
            placeholders = ", ".join("?" for _ in values)
            clauses.append(f"{col} IN ({placeholders})")
            params.extend(values)

    if filters.search and filters.search.strip():
        q = f"%{filters.search.strip()}%"
        searchable = [
            c
            for c in (
                "_product",
                "_category",
                "_customer",
                "_order_id",
                "_region",
                "_channel",
                "_salesperson",
                "_source_file",
            )
            if c in cols
        ]
        if searchable:
            sub = " OR ".join(f"CAST({c} AS VARCHAR) ILIKE ?" for c in searchable)
            clauses.append(f"({sub})")
            params.extend([q] * len(searchable))

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where_sql, params


def compute_previous_period(
    con: duckdb.DuckDBPyConnection,
    filters: FilterState,
    cols: set[str],
) -> tuple[str, str] | None:
    """Compute an equal-length preceding date window `(prev_from, prev_to)`."""
    if "_date_day" not in cols:
        return None
    d_from_str, d_to_str = filters.date_from, filters.date_to
    if not d_from_str or not d_to_str:
        where_no_date, params = build_where_clause(filters, cols, date_override=(None, None))
        row = ParquetStore.query_one(
            con,
            f"SELECT MIN(_date_day) AS min_d, MAX(_date_day) AS max_d FROM sales {where_no_date}",
            params,
        )
        d_from_str = d_from_str or row.get("min_d")
        d_to_str = d_to_str or row.get("max_d")

    if not d_from_str or not d_to_str:
        return None
    try:
        d_from = date.fromisoformat(d_from_str[:10])
        d_to = date.fromisoformat(d_to_str[:10])
    except ValueError:
        return None
    span_days = max((d_to - d_from).days + 1, 1)
    # If full dataset span is used without explicit bounds, split dataset in half for comparison
    if not filters.date_from and not filters.date_to and span_days >= 14:
        half = span_days // 2
        prev_from = d_from
        prev_to = d_from + timedelta(days=half - 1)
        return prev_from.isoformat(), prev_to.isoformat()

    prev_to = d_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=span_days - 1)
    return prev_from.isoformat(), prev_to.isoformat()


def get_filter_options(con: duckdb.DuckDBPyConnection, cols: set[str]) -> dict[str, Any]:
    """Return distinct option lists and date bounds for the global UI filter bar."""
    options: dict[str, Any] = {
        "date_min": None,
        "date_max": None,
        "categories": [],
        "regions": [],
        "channels": [],
        "salespersons": [],
        "payment_methods": [],
        "statuses": [],
        "source_files": [],
        "source_folders": [],
    }
    if "_date_day" in cols:
        row = ParquetStore.query_one(
            con, "SELECT MIN(_date_day) AS dmin, MAX(_date_day) AS dmax FROM sales"
        )
        options["date_min"] = row.get("dmin")
        options["date_max"] = row.get("dmax")

    mapping = [
        ("_category", "categories"),
        ("_region", "regions"),
        ("_channel", "channels"),
        ("_salesperson", "salespersons"),
        ("_payment_method", "payment_methods"),
        ("_status", "statuses"),
        ("_source_file", "source_files"),
        ("_source_folder", "source_folders"),
    ]
    for col, key in mapping:
        if col in cols:
            rows = ParquetStore.query_dicts(
                con,
                f"SELECT DISTINCT {col} AS val FROM sales WHERE {col} IS NOT NULL ORDER BY 1 LIMIT 200",
            )
            options[key] = [r["val"] for r in rows if r["val"] is not None]

    return sanitize_json(options)
