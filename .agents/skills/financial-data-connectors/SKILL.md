---
name: financial-data-connectors
description: >-
  Guides integration with financial APIs (Polygon, Alpha Vantage, yfinance, Plaid)
  using rate limiting, exponential backoff, Redis/SQLite caching, and provider
  fallbacks. Use when writing market data fetchers, bank/broker connectors, or
  external API clients.
---

# Financial Data Connectors

External financial APIs enforce strict quotas, experience transient outages, and return inconsistent schemas. Connectors must be resilient, polite, and normalized.

## Core Rules

1. **Rate Limiting**:
   - Enforce client-side rate limits per provider (token bucket or `asyncio.Semaphore` + sliding window) matching the provider's tier (e.g., Alpha Vantage free tier, Polygon minute/second quotas).
   - Never fire unbounded `asyncio.gather()` loops against external market APIs.
2. **Retries, Backoff & Timeouts**:
   - Set explicit connect and read timeouts on every HTTP client (`httpx.Timeout(connect=5.0, read=15.0)`).
   - Retry only transient failures (`429 Too Many Requests`, `500`, `502`, `503`, `504`, network timeouts) using **exponential backoff with jitter**; honor `Retry-After` headers when present.
   - Fail fast on `400`, `401`, `403`, `404`—do not retry invalid requests or bad credentials.
3. **Tiered Caching (Redis / SQLite)**:
   - Cache **immutable historical data** (e.g., closed daily bars older than today) with long or infinite TTL in SQLite/DuckDB/Redis.
   - Cache **intraday / quote data** with short TTLs (e.g., 15s–60s) and **fundamental/reference data** with medium TTLs (e.g., 24h).
   - Key caches deterministically: `{provider}:{endpoint}:{symbol}:{interval}:{start}:{end}`.
4. **Circuit Breaker & Fallbacks**:
   - Wrap provider calls with a circuit breaker (trip after `N` consecutive failures, half-open probe after cooldown).
   - Define explicit fallback chains (e.g., primary Polygon -> secondary Alpha Vantage/yfinance -> stale cached snapshot flagged `is_stale=True`) when business continuity allows stale reads.
5. **Canonical Normalization**:
   - Map every provider's raw payload immediately into internal Pydantic/Decimal/UTC canonical models at the connector boundary. Never leak provider-specific field names into domain logic.

## Verification Checklist

- Confirm every `httpx`/`requests` call has an explicit `timeout`.
- Confirm `429` handling respects `Retry-After` and uses jittered backoff.
- Verify API keys are read from environment/settings, never logged in URLs or headers.
