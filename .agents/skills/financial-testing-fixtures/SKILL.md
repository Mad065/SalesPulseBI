---
name: financial-testing-fixtures
description: >-
  Mandates unit and property tests with edge-case scenarios (division by zero,
  negative rates, DST transitions, missing data) and validation against audited
  golden results. Use when writing tests for financial logic, analytics, or pipelines.
---

# Financial Testing & Golden Fixtures

Financial bugs often hide in boundary conditions—zero denominators, negative interest rates, daylight saving shifts, or rounding drift. Every financial module requires deterministic edge-case and golden-dataset tests.

## Core Rules

1. **Mandatory Edge-Case Scenarios**:
   - **Zero & Empty**: Zero principal, zero denominator (`std == 0`, `volume == 0`), empty DataFrame/series, single-row series.
   - **Negative & Extreme Values**: Negative returns, negative interest rates, 100% drawdown (total loss), extreme outliers.
   - **Calendar & Timezone Boundaries**: Daylight Saving Time (DST) spring-forward/fall-back transitions, leap years (`Feb 29`), month-end/quarter-end alignment, weekends and market holidays, UTC midnight rollovers.
   - **Missing & Dirty Data**: `NaN`/`None` gaps, duplicate timestamps, out-of-order rows, mixed currencies.
   - **Precision & Rounding**: Sub-cent remainders (`$10.00 / 3`), banker's rounding ties (`0.005`, `0.015`), large notional values (`10^12`).
2. **Deterministic Fixtures & Golden Datasets**:
   - Store small, immutable **golden fixtures** (e.g., audited CSV/JSON OHLCV series, known ledger entries) with pre-verified expected outputs (CAGR, Sharpe, MDD, trial balance) computed independently.
   - Assert `Decimal` equality (`==`) for accounting/ledger tests and tight explicit tolerances (`pytest.approx(..., rel=1e-9)`) only for continuous floating-point quant stats.
3. **Accounting & Invariant Assertions**:
   - Assert conservation laws in every ledger test: `sum(debits) == sum(credits)`, `sum(allocated_parts) == total_amount`.
   - Test idempotency (replaying the same event/request produces no duplicate postings).
4. **No Network in Unit Tests**:
   - Mock external financial APIs (`httpx.MockTransport` / `respx`) with realistic recorded payloads including `429` rate-limit and malformed responses.

## Verification Checklist

- Every new financial function has tests covering: happy path (golden value), zero/empty input, negative input, and missing/NaN values.
- Run `pytest` and verify 100% pass with zero skipped assertions.
