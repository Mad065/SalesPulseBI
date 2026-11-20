# Security Policy

## Local-First Privacy Architecture

**SalesPulseBI** is designed around strict data privacy:
- All ingested datasets, SQLite metadata, and Parquet files remain strictly on your local machine (`~/.salespulsebi/` by default).
- The server binds to `127.0.0.1` by default and makes **zero external telemetry or cloud API calls**.
- Frontend visualization libraries (Apache ECharts) are bundled locally so the web app works completely offline.

## Reporting a Vulnerability

If you discover a security vulnerability (such as path traversal in file/folder ingestion, SQL injection in DuckDB query generation, or CSV formula injection), please do **not** open a public issue with exploit details.

Instead, report it privately via GitHub Security Advisories or contact the maintainers directly so we can investigate and release a patch promptly.
