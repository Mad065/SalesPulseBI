---
name: financial-security-compliance
description: >-
  Requires PII sanitization in logs, environment variables for API keys, role-based
  access control (RBAC), and strict validation of generated SQL queries. Use when
  handling sensitive data, auth/permissions, logging, secrets, or dynamic SQL/DuckDB.
---

# Financial Security & Compliance

Financial and BI applications handle sensitive revenue, account, and PII data. Security controls must be enforced by default at every layer.

## Core Rules

1. **PII & Secret Sanitization in Logs**:
   - NEVER log raw API keys, bearer tokens, passwords, bank account numbers, tax IDs, emails, or full financial payloads.
   - Mask/redact sensitive fields at logger/filter boundaries (e.g., `****1234`, `[REDACTED]`) and scrub query parameters from logged URLs.
   - Never return internal stack traces, file paths, or raw SQL errors in HTTP responses.
2. **Secrets & Configuration Management**:
   - Load all API keys, DB credentials, and signing secrets from environment variables or a secret manager via `pydantic-settings` (`SecretStr`).
   - NEVER hardcode secrets in source code, tests, Dockerfiles, or frontend bundles (`VITE_*` / `NEXT_PUBLIC_*` are public—keep third-party financial API keys strictly on the backend).
3. **Authorization & RBAC**:
   - Enforce authentication and Role-Based Access Control (RBAC) / resource ownership checks on every protected endpoint via FastAPI dependencies (`Depends(require_role(...))`).
   - Scope all queries by tenant/user/project ID server-side; never trust a client-supplied `user_id` or `role` without verifying the session/token.
4. **Strict SQL & DuckDB Query Validation**:
   - **NEVER** interpolate user input into SQL strings (`f"SELECT * FROM {table}"` or `WHERE name = '{val}'`).
   - Always use **parameterized queries** (`?` / `$1` / named params) for values.
   - For dynamic identifiers (table names, column names, sort fields, aggregations), validate against an explicit **allowlist** (`ALLOWED_SORT_COLUMNS`) before building SQL.
   - For AI-generated or user-built analytical SQL (e.g., DuckDB): parse/validate AST to allow **read-only `SELECT` only**, block `ATTACH`, `COPY`, `EXPORT`, `INSTALL`, `LOAD`, file-system functions (`read_csv`, `read_parquet` outside sandboxed paths), and enforce row `LIMIT` + execution timeout.

## Verification Checklist

- Grep for f-strings or `%` formatting inside SQL execution calls and replace with parameterized queries / allowlisted identifiers.
- Verify `SecretStr` is used for sensitive settings and no secrets appear in logs or client JS.
- Confirm every data-access endpoint enforces authorization/ownership checks.
