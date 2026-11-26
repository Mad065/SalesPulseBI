"""Exact Decimal smart reconciliation between extracted OCR invoices/bank statements and Parquet sales."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any

from salespulsebi.ai.extractor import _parse_dec_str
from salespulsebi.analytics.query_builder import AnalyticsContext
from salespulsebi.storage.parquet_store import ParquetStore, sanitize_json

Q2 = Decimal("0.01")


def _parse_iso_date(val: Any) -> date | None:
    if not val:
        return None
    try:
        return date.fromisoformat(str(val)[:10])
    except ValueError:
        return None


def reconcile_documents_with_sales(
    ctx: AnalyticsContext,
    documents: list[dict[str, Any]],
    *,
    date_tolerance_days: int = 3,
    amount_tolerance: Decimal = Decimal("0.01"),
) -> dict[str, Any]:
    """
    Cross-check extracted PDF invoices & bank statement credits against `dataset.parquet` orders
    using exact Decimal arithmetic (ROUND_HALF_EVEN).
    """
    if "_revenue" not in ctx.columns:
        return sanitize_json({"available": False, "reason": "requires_revenue"})

    order_col = "_order_id" if "_order_id" in ctx.columns else "CAST(ROW_NUMBER() OVER () AS VARCHAR)"
    date_col = "_date_day" if "_date_day" in ctx.columns else "NULL"
    cust_col = "_customer" if "_customer" in ctx.columns else "'N/A'"

    sales_rows = ParquetStore.query_dicts(
        ctx.con,
        f"""
        SELECT
            COALESCE(CAST({order_col} AS VARCHAR), 'N/A') AS order_id,
            MIN({date_col}) AS order_date,
            COALESCE(CAST(MIN({cust_col}) AS VARCHAR), 'N/A') AS customer,
            SUM(_revenue) AS amount
        FROM sales
        WHERE COALESCE(_is_excluded_status, FALSE) = FALSE
        GROUP BY 1
        LIMIT 2000
        """,
    )

    pool = []
    for idx, r in enumerate(sales_rows):
        pool.append(
            {
                "idx": idx,
                "order_id": str(r["order_id"]),
                "date": _parse_iso_date(r.get("order_date")),
                "date_str": r.get("order_date"),
                "customer": str(r.get("customer") or ""),
                "amount": Decimal(_parse_dec_str(r.get("amount"))),
                "used": False,
            }
        )

    doc_entries: list[dict[str, Any]] = []
    for doc in documents:
        ext = doc.get("extracted") or {}
        dtype = doc.get("doc_type")
        if dtype == "invoice":
            amt = Decimal(_parse_dec_str(ext.get("grand_total")))
            if amt > Decimal("0"):
                doc_entries.append(
                    {
                        "doc_id": doc["id"],
                        "filename": doc["filename"],
                        "ref": ext.get("invoice_number") or doc["filename"],
                        "date": _parse_iso_date(ext.get("issue_date")),
                        "date_str": ext.get("issue_date"),
                        "counterparty": ext.get("customer") or ext.get("vendor") or "",
                        "amount": amt,
                    }
                )
        elif dtype == "bank_statement":
            for t_idx, tx in enumerate(ext.get("transactions") or [], start=1):
                credit = Decimal(_parse_dec_str(tx.get("credit")))
                if credit > Decimal("0"):
                    doc_entries.append(
                        {
                            "doc_id": doc["id"],
                            "filename": doc["filename"],
                            "ref": f"{doc['filename']}#{t_idx}",
                            "date": _parse_iso_date(tx.get("date")),
                            "date_str": tx.get("date"),
                            "counterparty": tx.get("description") or "",
                            "amount": credit,
                        }
                    )

    matches: list[dict[str, Any]] = []
    matched_total = Decimal("0")
    unmatched_total = Decimal("0")
    variance_total = Decimal("0")

    with localcontext() as dctx:
        dctx.prec = 28
        for entry in doc_entries:
            best_exact = None
            best_near = None
            for cand in pool:
                if cand["used"]:
                    continue
                diff = abs(entry["amount"] - cand["amount"]).quantize(Q2, rounding=ROUND_HALF_EVEN)
                date_ok = True
                if entry["date"] and cand["date"]:
                    date_ok = abs((entry["date"] - cand["date"]).days) <= date_tolerance_days

                # Exact reference or exact amount within date window
                if diff <= amount_tolerance and date_ok:
                    best_exact = (cand, diff)
                    break
                if entry["ref"] and entry["ref"] == cand["order_id"]:
                    best_near = (cand, entry["amount"] - cand["amount"])
                elif date_ok and entry["amount"] > Decimal("0") and (diff / entry["amount"]) <= Decimal("0.05"):
                    if best_near is None or abs(diff) < abs(best_near[1]):
                        best_near = (cand, entry["amount"] - cand["amount"])

            if best_exact is not None:
                cand, _ = best_exact
                cand["used"] = True
                matched_total += entry["amount"]
                matches.append(
                    {
                        "status": "matched",
                        "doc_id": entry["doc_id"],
                        "filename": entry["filename"],
                        "doc_ref": entry["ref"],
                        "doc_date": entry["date_str"],
                        "doc_amount": format(entry["amount"], "f"),
                        "matched_order_id": cand["order_id"],
                        "order_date": cand["date_str"],
                        "order_amount": format(cand["amount"], "f"),
                        "variance": "0.00",
                    }
                )
            elif best_near is not None:
                cand, var = best_near
                cand["used"] = True
                var_q = var.quantize(Q2, rounding=ROUND_HALF_EVEN)
                variance_total += abs(var_q)
                matches.append(
                    {
                        "status": "amount_mismatch",
                        "doc_id": entry["doc_id"],
                        "filename": entry["filename"],
                        "doc_ref": entry["ref"],
                        "doc_date": entry["date_str"],
                        "doc_amount": format(entry["amount"], "f"),
                        "matched_order_id": cand["order_id"],
                        "order_date": cand["date_str"],
                        "order_amount": format(cand["amount"], "f"),
                        "variance": format(var_q, "f"),
                    }
                )
            else:
                unmatched_total += entry["amount"]
                matches.append(
                    {
                        "status": "unmatched",
                        "doc_id": entry["doc_id"],
                        "filename": entry["filename"],
                        "doc_ref": entry["ref"],
                        "doc_date": entry["date_str"],
                        "doc_amount": format(entry["amount"], "f"),
                        "matched_order_id": None,
                        "order_date": None,
                        "order_amount": None,
                        "variance": format(entry["amount"], "f"),
                    }
                )

    n_matched = sum(1 for m in matches if m["status"] == "matched")
    n_mismatch = sum(1 for m in matches if m["status"] == "amount_mismatch")
    n_unmatched = sum(1 for m in matches if m["status"] == "unmatched")

    return sanitize_json(
        {
            "available": True,
            "summary": {
                "total_entries": len(matches),
                "matched": n_matched,
                "amount_mismatch": n_mismatch,
                "unmatched": n_unmatched,
                "matched_amount_decimal": format(matched_total.quantize(Q2, rounding=ROUND_HALF_EVEN), "f"),
                "unmatched_amount_decimal": format(unmatched_total.quantize(Q2, rounding=ROUND_HALF_EVEN), "f"),
                "variance_amount_decimal": format(variance_total.quantize(Q2, rounding=ROUND_HALF_EVEN), "f"),
            },
            "items": matches,
        }
    )
