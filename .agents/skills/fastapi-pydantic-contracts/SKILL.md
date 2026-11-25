---
name: fastapi-pydantic-contracts
description: >-
  Defines typed API schemas using Pydantic v2, strict numeric range validation,
  UTC datetime enforcement, and consistent JSON serialization of dates and Decimals.
  Use when creating or modifying FastAPI endpoints, request/response models, or
  API data contracts.
---

# FastAPI & Pydantic v2 Contracts

API boundaries between Python backends and web frontends must preserve numeric precision, timezone clarity, and strict input validation.

## Core Rules

1. **Pydantic v2 Model Configuration**:
   - Use `model_config = ConfigDict(strict=True, extra="forbid")` on request models (or explicit `extra="ignore"` only when documented for external webhooks).
   - Use `Annotated` types with `Field(...)` constraints rather than bare primitives.
2. **Numeric & Decimal Validation**:
   - Use `Decimal` for monetary fields with explicit bounds: `Field(gt=0, max_digits=18, decimal_places=4)` or `ge=0` where zero is valid.
   - Reject `NaN` and `Infinity` (`allow_inf_nan=False`).
   - Serialize `Decimal` to JSON as **strings** (or explicit `PlainSerializer(lambda v: str(v), return_type=str, when_used="json")`) so JavaScript clients do not lose precision when parsing `JSON.parse`. Document the string format in OpenAPI.
3. **Date & Timezone Contracts**:
   - Use `AwareDatetime` (from `pydantic`) on all timestamp inputs and outputs; reject naive datetimes.
   - Normalize timestamps to UTC and serialize in ISO-8601 format with `Z` suffix (`2026-01-15T14:30:00Z`).
   - Use `date` (ISO `YYYY-MM-DD`) strictly for calendar dates without time-of-day semantics (e.g., fiscal date, settlement date).
4. **Pagination, Filtering & Error Responses**:
   - Bound all list endpoints with `limit: Annotated[int, Field(ge=1, le=500)] = 50` and cursor/offset validation.
   - Validate date ranges (`start_date <= end_date`) with `@model_validator(mode="after")`.
   - Return structured, consistent error payloads (e.g., `{ "error": { "code": "...", "message": "...", "details": [...] } }`) without leaking stack traces or internal SQL.
5. **FastAPI Dependency & Response Hygiene**:
   - Always declare `response_model=...` (or return typed Pydantic models) on route handlers so FastAPI validates and filters outbound fields.
   - Use `status.HTTP_*` constants and explicit OpenAPI `responses={...}` for domain errors (`400`, `404`, `409`, `422`).

## Verification Checklist

- Check OpenAPI schema (`/openapi.json`) to confirm `Decimal` fields are documented cleanly and no `float` is used for money.
- Test invalid inputs (negative amounts, naive datetimes, `end_date < start_date`, extra fields) return HTTP `422`.
