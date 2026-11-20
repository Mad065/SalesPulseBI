"""Parquet persistence and DuckDB query execution helpers."""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd


def sanitize_json(obj: Any) -> Any:
    """Recursively normalize Python/NumPy/Pandas objects for strict JSON serialization."""
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        val = float(obj)
        return None if (math.isnan(val) or math.isinf(val)) else val
    if isinstance(obj, Decimal):
        val = float(obj)
        return None if (math.isnan(val) or math.isinf(val)) else val
    if isinstance(obj, (pd.Timestamp, datetime)):
        if pd.isna(obj):
            return None
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_json(v) for v in obj]
    if hasattr(obj, "item") and callable(obj.item):
        try:
            return sanitize_json(obj.item())
        except Exception:
            pass
    try:
        if pd.isna(obj):
            return None
    except Exception:
        pass
    return obj


def prepare_dataframe_for_duckdb(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce Pandas 3.x / mixed object columns cleanly before DuckDB Parquet export."""
    clean = df.copy()
    for col in clean.columns:
        dtype = clean[col].dtype
        if pd.api.types.is_datetime64_any_dtype(dtype):
            clean[col] = pd.to_datetime(clean[col], errors="coerce").dt.tz_localize(None)
        elif pd.api.types.is_bool_dtype(dtype):
            clean[col] = clean[col].fillna(False).astype(bool)
        elif pd.api.types.is_integer_dtype(dtype):
            clean[col] = pd.to_numeric(clean[col], errors="coerce").astype("Int64")
        elif pd.api.types.is_float_dtype(dtype):
            clean[col] = pd.to_numeric(clean[col], errors="coerce").astype("float64")
        else:
            # Convert object/StringDtype/mixed columns safely to nullable Python str/None
            clean[col] = clean[col].map(
                lambda v: None if v is None or (isinstance(v, float) and math.isnan(v)) or pd.isna(v) else str(v)
            )
    return clean


class ParquetStore:
    """DuckDB-backed Parquet reader/writer requiring zero PyArrow dependency."""

    @staticmethod
    def write_parquet(df: pd.DataFrame, parquet_path: Path) -> int:
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        if df.empty:
            if parquet_path.exists():
                parquet_path.unlink()
            return 0
        prepared = prepare_dataframe_for_duckdb(df)
        con = duckdb.connect(":memory:")
        try:
            con.register("df_in", prepared)
            escaped = str(parquet_path).replace("'", "''")
            con.execute(f"COPY df_in TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        finally:
            con.close()
        return len(prepared)

    @staticmethod
    def connect_parquet(parquet_path: Path) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect(":memory:")
        escaped = str(parquet_path).replace("'", "''")
        con.execute(f"CREATE VIEW sales AS SELECT * FROM read_parquet('{escaped}')")
        return con

    @staticmethod
    def get_columns(parquet_path: Path) -> list[dict[str, str]]:
        if not parquet_path.exists():
            return []
        con = ParquetStore.connect_parquet(parquet_path)
        try:
            rows = con.execute("DESCRIBE sales").fetchall()
            return [{"name": str(r[0]), "type": str(r[1])} for r in rows]
        finally:
            con.close()

    @staticmethod
    def query_dicts(
        con: duckdb.DuckDBPyConnection,
        sql: str,
        params: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        cur = con.execute(sql, params or [])
        cols = [desc[0] for desc in (cur.description or [])]
        rows = cur.fetchall()
        return [sanitize_json(dict(zip(cols, row, strict=False))) for row in rows]

    @staticmethod
    def query_one(
        con: duckdb.DuckDBPyConnection,
        sql: str,
        params: list[Any] | None = None,
    ) -> dict[str, Any]:
        rows = ParquetStore.query_dicts(con, sql, params)
        return rows[0] if rows else {}
