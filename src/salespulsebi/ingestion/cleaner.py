"""Data normalization, currency/date parsing, derived metrics, and deduplication."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from salespulsebi.storage.models import ALL_SEMANTIC_ROLES, CleaningRules

SPANISH_MONTHS = {
    "ene": "jan",
    "feb": "feb",
    "mar": "mar",
    "abr": "apr",
    "may": "may",
    "jun": "jun",
    "jul": "jul",
    "ago": "aug",
    "sep": "sep",
    "sept": "sep",
    "oct": "oct",
    "nov": "nov",
    "dic": "dec",
}

CURRENCY_CODE_RE = re.compile(r"\b(USD|MXN|EUR|COP|ARS|CLP|PEN|GBP|CAD|BRL)\b", re.IGNORECASE)
CURRENCY_SYMBOL_RE = re.compile(r"[$€£¥₹]")


@dataclass
class CleaningStats:
    rows_raw: int = 0
    rows_clean: int = 0
    duplicates_removed: int = 0
    invalid_dates: int = 0
    invalid_amounts: int = 0
    derived_revenue_used: bool = False
    warnings: list[str] = field(default_factory=list)


def _parse_single_currency(val: Any) -> float:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return np.nan
    if isinstance(val, (int, float, np.integer, np.floating)):
        return float(val)
    text = str(val).strip()
    if not text or text.lower() in {"nan", "none", "null", "na", "n/a", "-"}:
        return np.nan

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()

    text = CURRENCY_SYMBOL_RE.sub("", text)
    text = CURRENCY_CODE_RE.sub("", text)
    text = text.replace("%", "").replace("\u00a0", "").replace(" ", "").strip()
    if not text:
        return np.nan

    if text.startswith("-"):
        negative = True
        text = text[1:]
    elif text.startswith("+"):
        text = text[1:]

    # Determine decimal vs thousands separator
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            # European: 1.234,56
            text = text.replace(".", "").replace(",", ".")
        else:
            # US: 1,234.56
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            # Likely decimal comma: 1234,56
            text = text.replace(",", ".")
        else:
            # Thousands comma: 1,234 or 1,234,567
            text = text.replace(",", "")
    elif text.count(".") > 1:
        # European thousands dots without decimal: 1.234.567
        text = text.replace(".", "")

    try:
        num = float(text)
        return -num if negative else num
    except ValueError:
        return np.nan


def parse_currency_series(series: pd.Series) -> pd.Series:
    """Parse messy currency/numeric series into float64."""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce").astype("float64")
    return series.map(_parse_single_currency).astype("float64")


def _replace_spanish_months(text: str) -> str:
    lowered = text.lower()
    for es_m, en_m in SPANISH_MONTHS.items():
        lowered = re.sub(rf"\b{es_m}\b", en_m, lowered)
    return lowered


def _infer_dayfirst(sample_strings: pd.Series) -> bool:
    """Infer whether slash/dash dates like 13/05/2025 vs 05/13/2025 are day-first."""
    slash_matches = sample_strings.str.extract(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})")[
        [0, 1]
    ].dropna()
    if slash_matches.empty:
        return True
    first = pd.to_numeric(slash_matches[0], errors="coerce")
    second = pd.to_numeric(slash_matches[1], errors="coerce")
    if (first > 12).any() and not (second > 12).any():
        return True
    if (second > 12).any() and not (first > 12).any():
        return False
    return True


def parse_date_series(series: pd.Series, dayfirst: bool | None = None) -> pd.Series:
    """Parse mixed date formats including ISO, slash/dash, Spanish months, and Excel serials."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce").dt.tz_localize(None)

    s = series.copy()
    raw_str = s.astype(str).str.strip()
    is_numeric = pd.to_numeric(raw_str, errors="coerce")
    excel_mask = (is_numeric >= 25000) & (is_numeric <= 65000)

    result = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    if excel_mask.any():
        result.loc[excel_mask] = pd.to_datetime(
            is_numeric.loc[excel_mask], unit="D", origin="1899-12-30", errors="coerce"
        )

    remaining_mask = ~excel_mask & s.notna() & (raw_str != "") & (raw_str.str.lower() != "nan")
    if remaining_mask.any():
        rem_str = raw_str.loc[remaining_mask].map(_replace_spanish_months)
        iso_mask = rem_str.str.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}")
        if iso_mask.any():
            iso_parsed = pd.to_datetime(rem_str.loc[iso_mask], errors="coerce", format="mixed")
            result.loc[rem_str.loc[iso_mask].index] = iso_parsed

        non_iso_mask = ~iso_mask
        if non_iso_mask.any():
            non_iso = rem_str.loc[non_iso_mask]
            use_dayfirst = _infer_dayfirst(non_iso.head(200)) if dayfirst is None else dayfirst
            parsed_non_iso = pd.to_datetime(
                non_iso,
                errors="coerce",
                format="mixed",
                dayfirst=use_dayfirst,
            )
            result.loc[non_iso.index] = parsed_non_iso

    return result.dt.tz_localize(None)


def _sanitize_extra_col_name(col_name: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_áéíóúñÁÉÍÓÚÑ]+", "_", str(col_name).strip()).strip("_")
    return cleaned or "extra_col"


def normalize_dataframe(
    df: pd.DataFrame,
    role_map: dict[str, str],
    cleaning_rules: CleaningRules,
    *,
    source_file: str,
    source_relpath: str,
    source_folder: str,
    source_sheet: str,
) -> tuple[pd.DataFrame, CleaningStats]:
    """
    Map a source DataFrame to canonical SalesPulseBI columns, parse types,
    preserve extra columns, and add provenance metadata.
    """
    stats = CleaningStats(rows_raw=len(df))
    if df.empty:
        return pd.DataFrame(), stats

    out = pd.DataFrame(index=df.index)
    mapped_raw_cols: set[str] = set()

    # 1. Extract mapped semantic columns into canonical `_role` columns
    for role in ALL_SEMANTIC_ROLES:
        src_col = role_map.get(role)
        if not src_col or src_col not in df.columns:
            continue
        mapped_raw_cols.add(src_col)
        series = df[src_col]

        if role == "date":
            parsed = parse_date_series(series, dayfirst=cleaning_rules.date_dayfirst)
            non_empty = series.notna() & (series.astype(str).str.strip() != "")
            stats.invalid_dates += int((non_empty & parsed.isna()).sum())
            out["_date"] = parsed
        elif role in {"revenue", "quantity", "unit_price", "cost", "discount", "tax"}:
            parsed_num = parse_currency_series(series)
            non_empty = series.notna() & (series.astype(str).str.strip() != "")
            stats.invalid_amounts += int((non_empty & parsed_num.isna()).sum())
            out[f"_{role}"] = parsed_num
        else:
            out[f"_{role}"] = (
                series.astype(str)
                .str.strip()
                .replace({"nan": None, "None": None, "NA": None, "N/A": None, "": None})
            )

    # 2. Preserve unmapped extra columns for Ad-Hoc Explorer & Data Explorer
    used_names = set(out.columns) | {
        "_profit",
        "_margin_pct",
        "_year",
        "_month",
        "_quarter",
        "_dow",
        "_hour",
        "_date_day",
        "_is_excluded_status",
        "_source_file",
        "_source_relpath",
        "_source_folder",
        "_source_sheet",
    }
    for col in df.columns:
        if col in mapped_raw_cols or str(col).startswith("_source_"):
            continue
        safe_name = _sanitize_extra_col_name(str(col))
        if safe_name.startswith("_"):
            safe_name = f"ext{safe_name}"
        candidate = safe_name
        idx = 2
        while candidate in used_names:
            candidate = f"{safe_name}_{idx}"
            idx += 1
        used_names.add(candidate)
        out[candidate] = df[col].map(lambda v: None if pd.isna(v) else str(v).strip() or None)

    # 3. Provenance columns
    out["_source_file"] = source_file
    out["_source_relpath"] = source_relpath
    out["_source_folder"] = source_folder
    out["_source_sheet"] = source_sheet

    # 4. Derived financial & calendar metrics
    if "_revenue" not in out.columns:
        if "_unit_price" in out.columns and "_quantity" in out.columns:
            disc = out["_discount"].fillna(0.0) if "_discount" in out.columns else 0.0
            out["_revenue"] = out["_unit_price"] * out["_quantity"] - disc
            stats.derived_revenue_used = True
        elif "_unit_price" in out.columns:
            out["_revenue"] = out["_unit_price"]
            stats.derived_revenue_used = True

    if "_revenue" in out.columns and "_cost" in out.columns:
        out["_profit"] = out["_revenue"] - out["_cost"]
        out["_margin_pct"] = np.where(
            out["_revenue"].notna() & (out["_revenue"] != 0),
            (out["_profit"] / out["_revenue"]) * 100.0,
            np.nan,
        )

    if "_date" in out.columns:
        dt = pd.to_datetime(out["_date"], errors="coerce")
        out["_date"] = dt
        out["_date_day"] = dt.dt.strftime("%Y-%m-%d")
        out["_year"] = dt.dt.year.astype("Int64")
        out["_month"] = dt.dt.strftime("%Y-%m")
        out["_quarter"] = dt.dt.to_period("Q").astype(str).replace("NaT", None)
        out["_dow"] = dt.dt.day_of_week.astype("Int64")
        out["_hour"] = dt.dt.hour.astype("Int64")

    # 5. Excluded status flag (preserved for cancellation KPIs & toggleable filtering)
    excluded_set = {s.strip().lower() for s in cleaning_rules.exclude_statuses if s.strip()}
    if "_status" in out.columns and excluded_set:
        out["_is_excluded_status"] = (
            out["_status"].fillna("").astype(str).str.strip().str.lower().isin(excluded_set)
        )
    else:
        out["_is_excluded_status"] = False

    if cleaning_rules.exclude_negative_revenue and "_revenue" in out.columns:
        out = out[out["_revenue"].isna() | (out["_revenue"] >= 0)].copy()

    stats.rows_clean = len(out)
    return out, stats


def deduplicate_combined(
    df: pd.DataFrame,
    cleaning_rules: CleaningRules,
) -> tuple[pd.DataFrame, int]:
    """Apply configured deduplication across the unified dataset."""
    if df.empty or not cleaning_rules.deduplicate:
        return df, 0

    before = len(df)
    if (
        cleaning_rules.dedup_mode == "order_product"
        and "_order_id" in df.columns
        and "_product" in df.columns
    ):
        subset = [c for c in ("_order_id", "_product", "_date_day", "_revenue") if c in df.columns]
        deduped = df.drop_duplicates(subset=subset, keep="first").copy()
    else:
        # Exact deduplication excluding provenance metadata so re-uploaded copies don't double count
        content_cols = [
            c
            for c in df.columns
            if c not in {"_source_file", "_source_relpath", "_source_folder", "_source_sheet"}
        ]
        deduped = df.drop_duplicates(subset=content_cols or None, keep="first").copy()

    removed = before - len(deduped)
    return deduped, removed
