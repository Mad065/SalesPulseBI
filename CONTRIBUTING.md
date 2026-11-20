# Contributing to SalesPulseBI

Thank you for your interest in contributing to **SalesPulseBI**! Every bug report, feature idea, translation improvement, or pull request helps make local-first sales analytics better for everyone.

## Local Development Setup

```bash
git clone https://github.com/salespulsebi/salespulsebi.git
cd salespulsebi
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,xls]"
```

Run the development server with auto-reload:

```bash
salespulsebi serve --reload
```

Or seed the built-in demo projects immediately:

```bash
salespulsebi demo
```

## Code Style & Quality Checks

Before opening a pull request, please ensure linting and all tests pass:

```bash
ruff check src tests
pytest -v --tb=short
```

## Architecture Overview

- `src/salespulsebi/storage/`: SQLite metadata store & DuckDB-backed Parquet store.
- `src/salespulsebi/ingestion/`: Multi-format readers, recursive folder scanner, bilingual schema detector, currency/date cleaner, and consolidation pipeline.
- `src/salespulsebi/analytics/`: Adaptive DuckDB OLAP analytics (KPIs, Holt-Winters forecast, MAD anomalies, Pareto ABC, Market Basket, RFM, Cohorts, Data Quality, Insights, and Exports).
- `src/salespulsebi/api/`: FastAPI REST layer serving `/api/v1/*` and the self-contained SPA.
- `src/salespulsebi/web/`: Vanilla ES2022 SPA + bundled Apache ECharts (zero Node build step required).
