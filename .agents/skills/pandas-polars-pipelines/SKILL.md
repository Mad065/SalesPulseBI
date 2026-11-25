---
name: pandas-polars-pipelines
description: >-
  Standardizes financial time-series cleaning in Pandas and Polars, strict UTC
  normalization, market-closed/holiday handling, and prevention of look-ahead bias.
  Use when building ETL pipelines, feature engineering, OHLCV resampling, or
  time-series joins on financial or sales data.
---

# Pandas & Polars Financial Pipelines

Financial and analytical time-series pipelines fail silently when timezones drift, holidays are misfilled, or future data leaks into past rows.

## Core Rules

1. **Strict UTC Normalization**:
   - Parse all timestamps as timezone-aware and normalize immediately to **UTC** (`pd.to_datetime(..., utc=True)` / `.dt.tz_convert("UTC")` in Pandas; `.dt.replace_time_zone(...)` -> `.dt.convert_time_zone("UTC")` in Polars).
   - Never store or join naive datetimes with aware datetimes.
   - Preserve the original exchange/source timezone as metadata if needed for market-hours filtering, then convert to UTC before indexing or joining.
2. **Sorting & Uniqueness**:
   - Sort explicitly by `(symbol, timestamp_utc)` ascending before any `.shift()`, `.diff()`, `.rolling()`, or `asof` join.
   - Deduplicate timestamps deterministically (e.g., keep `"last"` with explicit audit logging) before resampling.
3. **Closed Markets, Weekends & Holidays**:
   - Distinguish **no-trade/closed market** from **missing data**:
     - Prices/valuations: forward-fill (`ffill`) ONLY up to a bounded limit (e.g., `limit=3` for brief gaps) or align to an explicit trading calendar; never `bfill` prices.
     - Returns: NEVER `ffill` returns across closed days (use `0` only if explicitly modeling zero-return cash, or keep `NaN` and exclude from active-day denominators).
     - Volume/cashflows: fill non-trading intervals with `0`, never `ffill`.
4. **Look-Ahead Bias Prevention**:
   - Every feature or signal computed at bar $t$ using bar $t$'s close MUST be shifted (`.shift(1)`) before being used to decide or evaluate an action at $t$.
   - Use **backward** `asof` joins (`pd.merge_asof(..., direction="backward")`, Polars `join_asof(..., strategy="backward")`) with `allow_exact_matches` chosen deliberately so future fundamentals/earnings never leak backward.
   - NEVER compute global `mean`, `std`, `min`, `max`, or quantiles over the entire dataset before a train/test or walk-forward split; compute statistics strictly on rolling/expanding past windows.
5. **Polars & Pandas Best Practices**:
   - Prefer vectorized expressions (`pl.Expr`, Pandas vectorized ops) over `.apply()` / Python row loops.
   - Use explicit schemas (`dtypes`) on CSV/Parquet ingestion so numeric IDs or string codes are not coerced to floats.

## Verification Checklist

- Assert `df.index.tz == datetime.timezone.utc` (or UTC dtype in Polars) at pipeline boundaries.
- Verify no `.bfill()` is used on price/feature columns.
- Verify `asof` joins specify `direction="backward"` / `strategy="backward"`.
