---
name: financial-math-precision
description: >-
  Enforces exact decimal arithmetic using decimal.Decimal in Python and NUMERIC/DECIMAL
  in SQL instead of binary floats, defining strict rounding rules (ROUND_HALF_EVEN)
  and multi-currency handling. Use when writing financial calculations, pricing,
  ledger math, tax/fee logic, currency conversions, or SQL monetary schemas.
---

# Financial Math & Precision

Binary floating-point (`float`, `DOUBLE`, `REAL`) introduces representation errors (e.g., `0.1 + 0.2 != 0.3`). All monetary, quantity, rate, and accounting calculations MUST use exact decimal arithmetic.

## Core Rules

1. **Python Types**:
   - Use `decimal.Decimal` for all monetary amounts, prices, fees, tax rates, and asset quantities.
   - NEVER initialize `Decimal` from a `float` (`Decimal(0.1)` captures float imprecision). Always construct from `str`, `int`, or another `Decimal`: `Decimal("0.10")`.
   - Avoid `float()` casts in intermediate calculations. Convert only at final non-financial chart/UI boundaries if a charting library strictly requires JS numbers.
2. **SQL / DuckDB Types**:
   - Use `NUMERIC(p, s)` or `DECIMAL(p, s)` in SQL schemas and DuckDB queries (e.g., `NUMERIC(18, 4)` for fiat amounts, `NUMERIC(24, 8)` for FX rates/crypto, `NUMERIC(12, 6)` for interest/percentage rates).
   - NEVER use `FLOAT`, `REAL`, or `DOUBLE PRECISION` for monetary columns.
3. **Rounding Policy**:
   - Default to **Banker's Rounding** (`decimal.ROUND_HALF_EVEN`) for intermediate and final financial aggregations to avoid systematic upward bias.
   - Use explicit `.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)` (or currency minor-unit precision) at defined boundaries—never rely on implicit truncation.
   - Use `decimal.localcontext()` when temporarily changing precision (`prec = 28`) so global context is never mutated.
4. **Currency & Money Handling**:
   - Store and pass amounts paired with an ISO 4217 currency code (e.g., `Money(amount: Decimal, currency: str)`).
   - NEVER add, subtract, or compare amounts of different currencies without an explicit FX conversion step carrying the rate, source, and timestamp.
   - Respect currency minor units (e.g., 2 decimals for USD/EUR/MXN, 0 for JPY, 3 for BHD) when formatting or settling.
5. **Division & Allocation**:
   - Guard every division against zero divisors explicitly (`if denominator == 0:`).
   - When splitting an amount across `N` buckets (e.g., invoice splits, prorating), distribute remainder cents deterministically (largest-remainder or first-N method) so `sum(parts) == total` holds exactly.

## Verification Checklist

- Search modified files for `float(` or `REAL`/`DOUBLE` in monetary contexts and replace with `Decimal` / `NUMERIC`.
- Verify all `Decimal(...)` calls pass string or integer literals, never float literals.
- Assert `sum(allocated_parts) == original_total` in unit tests for splits and fees.
