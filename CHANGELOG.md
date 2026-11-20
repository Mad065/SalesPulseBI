# Changelog

All notable changes to **SalesPulseBI** will be documented in this file.

## [0.1.0] - 2026-11-19

### Added
- **Multi-Format & Folder Ingestion**: Read CSV, TSV, TXT, multi-sheet Excel (`.xlsx`/`.xls`), JSON, JSONL, and Parquet; link local folders with recursive scanning, SHA-256 incremental change detection, and 1-click sync.
- **Heterogeneous Schema Alignment**: Automatic bilingual (ES/EN) semantic column detection across 16 roles (`date`, `revenue`, `quantity`, `unit_price`, `cost`, `discount`, `tax`, `product`, `category`, `customer`, `order_id`, `region`, `channel`, `salesperson`, `payment_method`, `status`) plus messy currency and mixed date normalization.
- **Persistent Project Workspaces**: SQLite metadata store + DuckDB-backed ZSTD Parquet storage, saved filter views, project duplication, and portable `.zip` project bundles.
- **Adaptive Analytics Engine**:
  - Executive Overview KPIs with previous-period deltas, sparklines, and ticket percentiles.
  - Time Series & Forecasting (Holt-Winters / damped trend with 80%/95% bands, YoY cumulative, seasonality heatmap, MAD anomaly detection with root-cause attribution).
  - Product Portfolio Intelligence (Pareto 80/20 ABC, BCG Matrix, Market Basket Cross-Sell affinity).
  - Customer Intelligence (RFM segmentation, New vs Returning, Cohort retention heatmap, concentration risk).
  - Operational Dimensions, Discount Impact & Interactive Ad-Hoc Explorer.
  - Data Quality & Health Auditor (0–100 score), Bilingual Narrative Insights, and HTML/PDF, XLSX, CSV, Parquet exports.
- **Self-Contained Bilingual Web App**: Offline-first SPA with bundled Apache ECharts, instant ES/EN switching, and Dark/Light themes.
- **CLI & Demo Mode**: `salespulsebi serve`, `salespulsebi open <path>`, `salespulsebi demo`, `salespulsebi info`.
