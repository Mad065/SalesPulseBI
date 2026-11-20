"""Robust tabular readers for CSV/TSV/TXT, Excel (.xlsx/.xls), JSON/JSONL, and Parquet."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {".csv", ".tsv", ".txt", ".xlsx", ".xls", ".json", ".jsonl", ".ndjson", ".parquet"}
)


def _detect_encoding_and_read_sample(path: Path, sample_bytes: int = 32768) -> tuple[str, str]:
    raw = path.read_bytes()[:sample_bytes]
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            return enc, text
        except UnicodeDecodeError:
            continue
    return "latin-1", raw.decode("latin-1", errors="replace")


def _detect_delimiter(sample_text: str, suffix: str) -> str:
    if suffix == ".tsv":
        return "\t"
    lines = [ln for ln in sample_text.splitlines() if ln.strip()][:20]
    snippet = "\n".join(lines)
    if not snippet:
        return ","
    try:
        dialect = csv.Sniffer().sniff(snippet, delimiters=",;\t|")
        return dialect.delimiter
    except csv.Error:
        counts = {sep: snippet.count(sep) for sep in (",", ";", "\t", "|")}
        best = max(counts, key=counts.get)  # type: ignore[arg-type]
        return best if counts[best] > 0 else ","


def _clean_headers(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(how="all").copy()
    if df.empty:
        return df
    cols: list[str] = []
    seen: dict[str, int] = {}
    for idx, col in enumerate(df.columns):
        name = str(col).strip() if col is not None else f"col_{idx}"
        if not name or name.lower().startswith("unnamed:"):
            name = f"col_{idx}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        cols.append(name)
    df.columns = cols
    return df


def _read_delimited(path: Path) -> list[tuple[str, pd.DataFrame]]:
    encoding, sample_text = _detect_encoding_and_read_sample(path)
    delimiter = _detect_delimiter(sample_text, path.suffix.lower())
    df = pd.read_csv(
        path,
        sep=delimiter,
        encoding=encoding,
        dtype=str,
        keep_default_na=True,
        na_values=["", "NA", "N/A", "null", "NULL", "None", "none", "-"],
        on_bad_lines="skip",
    )
    df = _clean_headers(df)
    return [("csv", df)] if not df.empty else []


def _read_excel(path: Path) -> list[tuple[str, pd.DataFrame]]:
    suffix = path.suffix.lower()
    engine = "openpyxl" if suffix == ".xlsx" else "xlrd"
    results: list[tuple[str, pd.DataFrame]] = []
    with pd.ExcelFile(path, engine=engine) as xls:
        for sheet_name in xls.sheet_names:
            df = pd.read_excel(
                xls,
                sheet_name=sheet_name,
                dtype=str,
                keep_default_na=True,
            )
            df = _clean_headers(df)
            if not df.empty and len(df.columns) >= 2:
                results.append((str(sheet_name), df))
    return results


def _extract_json_records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item if isinstance(item, dict) else {"value": item} for item in data]
    if isinstance(data, dict):
        for key in ("data", "records", "sales", "rows", "items", "orders", "results"):
            val = data.get(key)
            if isinstance(val, list):
                return [item if isinstance(item, dict) else {"value": item} for item in val]
        for val in data.values():
            if isinstance(val, list) and val and isinstance(val[0], dict):
                return val
        return [data]
    return []


def _read_json(path: Path) -> list[tuple[str, pd.DataFrame]]:
    encoding, _ = _detect_encoding_and_read_sample(path)
    text = path.read_text(encoding=encoding)
    data = json.loads(text)
    records = _extract_json_records(data)
    if not records:
        return []
    df = pd.json_normalize(records, max_level=1)
    df = _clean_headers(df)
    return [("json", df)] if not df.empty else []


def _read_jsonl(path: Path) -> list[tuple[str, pd.DataFrame]]:
    encoding, _ = _detect_encoding_and_read_sample(path)
    records: list[dict[str, Any]] = []
    with path.open("r", encoding=encoding) as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            obj = json.loads(stripped)
            records.append(obj if isinstance(obj, dict) else {"value": obj})
    if not records:
        return []
    df = pd.json_normalize(records, max_level=1)
    df = _clean_headers(df)
    return [("jsonl", df)] if not df.empty else []


def _read_parquet(path: Path) -> list[tuple[str, pd.DataFrame]]:
    con = duckdb.connect(":memory:")
    try:
        escaped = str(path).replace("'", "''")
        df = con.execute(f"SELECT * FROM read_parquet('{escaped}')").df()
    finally:
        con.close()
    df = _clean_headers(df)
    return [("parquet", df)] if not df.empty else []


def read_tabular_file(path: Path) -> list[tuple[str, pd.DataFrame]]:
    """Read a tabular file into one or more `(sheet_or_kind, DataFrame)` pairs."""
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv", ".txt"}:
        return _read_delimited(path)
    if suffix in {".xlsx", ".xls"}:
        return _read_excel(path)
    if suffix == ".json":
        return _read_json(path)
    if suffix in {".jsonl", ".ndjson"}:
        return _read_jsonl(path)
    if suffix == ".parquet":
        return _read_parquet(path)
    raise ValueError(f"Unsupported file format: {suffix}")
