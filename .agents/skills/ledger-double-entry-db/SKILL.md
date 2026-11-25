---
name: ledger-double-entry-db
description: >-
  Enforces relational database design with double-entry accounting, ACID transactions,
  temporal indexes, and immutable append-only audit records. Use when designing
  database schemas, migrations, balance calculations, or transaction recording logic.
---

# Double-Entry Ledger & Immutable Database Design

Financial state must be provably consistent at every point in time. Balances are derived from immutable journal entries, never overwritten in place.

## Core Rules

1. **Double-Entry Invariant**:
   - Every `journal_entry` (transaction) MUST have at least two `ledger_lines` (postings).
   - For every `journal_entry` and per `currency`, `SUM(debit) == SUM(credit)` (or signed `SUM(amount) == 0` where debits are positive and credits negative).
   - Enforce the zero-sum invariant in application logic inside the transaction AND via database constraints/triggers where supported.
2. **ACID Transactions & Concurrency**:
   - Wrap multi-row postings in a single atomic database transaction (`BEGIN ... COMMIT`, rollback on any failure).
   - Use idempotency keys (`idempotency_key UNIQUE`) on external/command-driven postings to prevent duplicate entries on retries.
   - Lock account rows in deterministic order (`ORDER BY account_id` with `SELECT ... FOR UPDATE`) when enforcing non-negative balance checks under concurrency.
3. **Immutable Append-Only Audit Trail**:
   - **NEVER `UPDATE` or `DELETE`** posted `journal_entries` or `ledger_lines`.
   - To correct a mistake, post a **reversal entry** referencing `reverses_entry_id` plus a new correcting entry.
   - Record `created_at` (server UTC timestamp), `posted_at` (effective accounting UTC timestamp), `actor_id`, and `source_event_id` on every entry.
4. **Temporal & Query Indexing**:
   - Index postings by `(account_id, posted_at DESC, id DESC)` for fast account statements and point-in-time balance queries.
   - Index `(transaction_id)` and `(idempotency_key)` for fast lookups and deduplication.
   - Maintain snapshot/checkpoint tables only as a read optimization derived from `ledger_lines`, never as the source of truth.

## Verification Checklist

- Confirm no `UPDATE` or `DELETE` statements target posted ledger tables.
- Verify every posting path enforces `SUM(debits) == SUM(credits)` per currency inside a single transaction.
- Verify idempotency keys are checked/enforced on retryable endpoints.
