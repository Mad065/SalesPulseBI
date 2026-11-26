"""Structured financial document extraction (PDF/Text + LLM Structured Outputs + Decimal accounting audit)."""

from __future__ import annotations

import hashlib
import re
import zlib
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator

from salespulsebi.ai.router import MultiProviderLLMRouter
from salespulsebi.ingestion.pipeline import ProjectIngestionPipeline
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.parquet_store import sanitize_json

DocType = Literal["invoice", "bank_statement", "financial_report", "policy", "other"]
Q2 = Decimal("0.01")


def _parse_dec_str(v: Any) -> str:
    if v is None or v == "":
        return "0.00"
    cleaned = str(v).strip().replace("$", "").replace("€", "").replace(" ", "")
    # Handle European 1.234,56 vs US 1,234.56
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned and len(cleaned.split(",")[-1]) == 2:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        dec = Decimal(cleaned)
        if not dec.is_finite():
            return "0.00"
        with localcontext() as ctx:
            ctx.prec = 28
            return format(dec.quantize(Q2, rounding=ROUND_HALF_EVEN), "f")
    except InvalidOperation:
        return "0.00"


# ── Strict Pydantic v2 Financial Document Schemas ────────────────────────────


class InvoiceLineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=240)
    quantity: str = "1.00"
    unit_price: str = "0.00"
    line_total: str = "0.00"

    @field_validator("quantity", "unit_price", "line_total", mode="before")
    @classmethod
    def _val_dec(cls, v: Any) -> str:
        return _parse_dec_str(v)


class InvoiceExtract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rationale: str = ""
    status: Literal["ok", "partial", "insufficient_data"] = "ok"
    confidence: Literal["high", "medium", "low"] = "high"
    invoice_number: str = ""
    issue_date: str | None = None
    vendor: str = ""
    customer: str = ""
    currency: str = Field(default="USD", min_length=3, max_length=8)
    line_items: list[InvoiceLineItem] = Field(default_factory=list)
    subtotal: str = "0.00"
    tax_total: str = "0.00"
    discount_total: str = "0.00"
    grand_total: str = "0.00"

    @field_validator("subtotal", "tax_total", "discount_total", "grand_total", mode="before")
    @classmethod
    def _val_money(cls, v: Any) -> str:
        return _parse_dec_str(v)


class BankTransaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    description: str = Field(min_length=1, max_length=240)
    debit: str = "0.00"
    credit: str = "0.00"
    balance: str | None = None
    category: str = "General"

    @field_validator("debit", "credit", mode="before")
    @classmethod
    def _val_amt(cls, v: Any) -> str:
        return _parse_dec_str(v)


class BankStatementExtract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rationale: str = ""
    status: Literal["ok", "partial", "insufficient_data"] = "ok"
    confidence: Literal["high", "medium", "low"] = "high"
    bank_name: str = ""
    account_masked: str = "****0000"
    period_start: str | None = None
    period_end: str | None = None
    currency: str = Field(default="USD", min_length=3, max_length=8)
    opening_balance: str = "0.00"
    closing_balance: str = "0.00"
    transactions: list[BankTransaction] = Field(default_factory=list)

    @field_validator("opening_balance", "closing_balance", mode="before")
    @classmethod
    def _val_bal(cls, v: Any) -> str:
        return _parse_dec_str(v)


class FinancialReportExtract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rationale: str = ""
    status: Literal["ok", "partial", "insufficient_data"] = "ok"
    confidence: Literal["high", "medium", "low"] = "high"
    entity_name: str = ""
    fiscal_period: str = ""
    currency: str = Field(default="USD", min_length=3, max_length=8)
    revenue: str = "0.00"
    cost_of_revenue: str = "0.00"
    gross_profit: str = "0.00"
    operating_cash_flow: str = "0.00"
    capex: str = "0.00"
    free_cash_flow: str = "0.00"

    @field_validator(
        "revenue",
        "cost_of_revenue",
        "gross_profit",
        "operating_cash_flow",
        "capex",
        "free_cash_flow",
        mode="before",
    )
    @classmethod
    def _val_fin(cls, v: Any) -> str:
        return _parse_dec_str(v)


# ── Multi-Engine PDF & Document Page Reader (Python 3.15 Safe) ───────────────


def _extract_pdf_text_native_fallback(raw: bytes) -> list[str]:
    """
    Zero-dependency PDF text stream extractor supporting uncompressed & FlateDecode streams.
    Handles standard PDF `Tj` and `TJ` text operators so minimal/generated PDFs parse cleanly
    even when pymupdf/pypdf are not installed.
    """
    streams: list[bytes] = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.DOTALL):
        blob = m.group(1)
        try:
            streams.append(zlib.decompress(blob))
        except Exception:
            streams.append(blob)

    pages: list[str] = []
    for st in streams:
        decoded = st.decode("latin-1", errors="ignore")
        # Extract literal strings inside (...) followed by Tj or inside [...] TJ
        fragments: list[str] = []
        for match in re.finditer(r"\(([^()]*)\)", decoded):
            txt = (
                match.group(1)
                .replace(r"\(", "(")
                .replace(r"\)", ")")
                .replace(r"\\", "\\")
                .strip()
            )
            if txt:
                fragments.append(txt)
        if fragments:
            pages.append("\n".join(fragments))

    if pages:
        return pages

    # Fallback: extract printable ASCII/Latin-1 lines if raw text
    plain = raw.decode("utf-8", errors="ignore")
    cleaned_lines = [ln.strip() for ln in plain.splitlines() if ln.strip() and not ln.startswith("%PDF")]
    return ["\n".join(cleaned_lines)] if cleaned_lines else [""]


def extract_document_pages(path: Path) -> tuple[list[str], str]:
    """Read a PDF or text-based financial document into per-page text strings."""
    suffix = path.suffix.lower()
    raw = path.read_bytes()

    if suffix in (".txt", ".md", ".csv", ".json"):
        text = raw.decode("utf-8-sig", errors="replace")
        pages = [p.strip() for p in text.split("\f") if p.strip()] or [text]
        return pages, "plain_text"

    if suffix == ".pdf":
        # 1. Try PyMuPDF (fitz) if installed
        try:
            import fitz  # type: ignore[import-not-found]

            doc = fitz.open(stream=raw, filetype="pdf")
            pages = [page.get_text("text") or "" for page in doc]
            doc.close()
            if any(p.strip() for p in pages):
                return pages, "pymupdf"
        except Exception:
            pass

        # 2. Try pypdf if installed
        try:
            import io

            from pypdf import PdfReader  # type: ignore[import-not-found]

            reader = PdfReader(io.BytesIO(raw))
            pages = [page.extract_text() or "" for page in reader.pages]
            if any(p.strip() for p in pages):
                return pages, "pypdf"
        except Exception:
            pass

        # 3. Built-in zero-dependency native PDF text parser
        return _extract_pdf_text_native_fallback(raw), "native_pdf_stream"

    return [raw.decode("utf-8", errors="replace")], "raw_text"


def detect_document_type(text: str, requested: str = "auto") -> DocType:
    if requested in ("invoice", "bank_statement", "financial_report", "policy", "other"):
        return requested  # type: ignore[return-value]
    low = text.lower()
    if any(k in low for k in ("invoice", "factura", "bill to", "subtotal", "grand total", "iva")):
        return "invoice"
    if any(k in low for k in ("bank statement", "estado de cuenta", "opening balance", "saldo inicial", "debit", "credit")):
        return "bank_statement"
    if any(k in low for k in ("10-k", "operating cash flow", "capex", "ebitda", "income statement", "estado de resultados", "balance general")):
        return "financial_report"
    if any(k in low for k in ("policy", "política", "gasto", "reimbursement", "compliance")):
        return "policy"
    return "other"


# ── Deterministic Accounting Verification (Exact Decimal) ────────────────────


def audit_extracted_document(doc_type: DocType, payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """
    Verify financial invariants in exact Decimal arithmetic (ROUND_HALF_EVEN).
    Also deterministically computes derived accounting metrics like Free Cash Flow.
    """
    errors: list[str] = []
    out = dict(payload)

    with localcontext() as ctx:
        ctx.prec = 28
        if doc_type == "invoice":
            items = out.get("line_items") or []
            item_sum = sum((Decimal(_parse_dec_str(it.get("line_total"))) for it in items), Decimal("0"))
            sub = Decimal(_parse_dec_str(out.get("subtotal")))
            tax = Decimal(_parse_dec_str(out.get("tax_total")))
            disc = Decimal(_parse_dec_str(out.get("discount_total")))
            grand = Decimal(_parse_dec_str(out.get("grand_total")))

            if items and sub > Decimal("0") and abs(item_sum - sub) > Decimal("0.01"):
                errors.append(
                    f"Line items sum ({item_sum:.2f}) does not match subtotal ({sub:.2f})."
                )
            base_sub = sub if sub > Decimal("0") else item_sum
            expected_grand = (base_sub + tax - disc).quantize(Q2, rounding=ROUND_HALF_EVEN)
            if grand > Decimal("0") and abs(expected_grand - grand) > Decimal("0.01"):
                errors.append(
                    f"Accounting mismatch: subtotal({base_sub:.2f}) + tax({tax:.2f}) - discount({disc:.2f}) = {expected_grand:.2f} != grand_total({grand:.2f})."
                )

        elif doc_type == "bank_statement":
            # Mask account number strictly
            acct = re.sub(r"[^0-9]", "", str(out.get("account_masked") or ""))
            out["account_masked"] = f"****{acct[-4:]}" if len(acct) >= 4 else "****0000"
            opening = Decimal(_parse_dec_str(out.get("opening_balance")))
            closing = Decimal(_parse_dec_str(out.get("closing_balance")))
            txs = out.get("transactions") or []
            credits = sum((Decimal(_parse_dec_str(t.get("credit"))) for t in txs), Decimal("0"))
            debits = sum((Decimal(_parse_dec_str(t.get("debit"))) for t in txs), Decimal("0"))
            expected_close = (opening + credits - debits).quantize(Q2, rounding=ROUND_HALF_EVEN)
            if txs and abs(expected_close - closing) > Decimal("0.01"):
                errors.append(
                    f"Statement balance mismatch: opening({opening:.2f}) + credits({credits:.2f}) - debits({debits:.2f}) = {expected_close:.2f} != closing({closing:.2f})."
                )

        elif doc_type == "financial_report":
            rev = Decimal(_parse_dec_str(out.get("revenue")))
            cogs = Decimal(_parse_dec_str(out.get("cost_of_revenue")))
            ocf = Decimal(_parse_dec_str(out.get("operating_cash_flow")))
            capex = abs(Decimal(_parse_dec_str(out.get("capex"))))
            # Deterministic Free Cash Flow = Operating Cash Flow - CapEx (per quant-metrics-backtesting)
            fcf = (ocf - capex).quantize(Q2, rounding=ROUND_HALF_EVEN)
            out["free_cash_flow"] = format(fcf, "f")
            if rev > Decimal("0") and cogs > Decimal("0"):
                gp = (rev - cogs).quantize(Q2, rounding=ROUND_HALF_EVEN)
                out["gross_profit"] = format(gp, "f")

    return out, errors


# ── Deterministic Heuristic Extractor (100% Offline Fallback) ────────────────


def _extract_heuristic(doc_type: DocType, full_text: str, default_currency: str) -> dict[str, Any]:
    cur_m = re.search(r"\b(USD|MXN|EUR|GBP|COP|CLP|BRL|PEN|CAD)\b", full_text)
    currency = cur_m.group(1) if cur_m else default_currency

    if doc_type == "invoice":
        inv_m = re.search(r"(?:invoice|factura)\s*(?:#|no\.?|num\.?)?\s*[:\-]?\s*([A-Za-z0-9_-]+)", full_text, re.I)
        date_m = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", full_text)
        vendor_m = re.search(r"(?:vendor|proveedor|from|emisor)\s*[:\-]\s*(.+)", full_text, re.I)
        cust_m = re.search(r"(?:customer|cliente|bill\s+to)\s*[:\-]\s*(.+)", full_text, re.I)

        items: list[dict[str, str]] = []
        # Match table lines like: "Item Name | 2 | 150.00 | 300.00" or "Item Name, qty=2, unit=150.00, total=300.00"
        for line in full_text.splitlines():
            parts = [p.strip() for p in re.split(r"\s*\|\s*", line) if p.strip()]
            if len(parts) == 4 and re.search(r"\d", parts[1]) and re.search(r"\d", parts[3]):
                if parts[0].lower() in ("item", "description", "producto", "concepto"):
                    continue
                items.append(
                    {
                        "description": parts[0][:240],
                        "quantity": _parse_dec_str(parts[1]),
                        "unit_price": _parse_dec_str(parts[2]),
                        "line_total": _parse_dec_str(parts[3]),
                    }
                )

        def _grab_amount(pattern: str) -> str:
            m = re.search(pattern, full_text, re.I)
            return _parse_dec_str(m.group(1)) if m else "0.00"

        sub = _grab_amount(r"\bsubtotal\s*[:\-]?\s*\$?([\d,.\-]+)")
        tax = _grab_amount(r"\b(?:tax|iva|impuesto)\s*[:\-]?\s*\$?([\d,.\-]+)")
        disc = _grab_amount(r"\b(?:discount|descuento)\s*[:\-]?\s*\$?([\d,.\-]+)")
        grand = _grab_amount(r"\b(?:grand\s+total|total\s+general|total)\s*[:\-]?\s*\$?([\d,.\-]+)")
        if sub == "0.00" and items:
            sub = format(sum((Decimal(it["line_total"]) for it in items), Decimal("0")), "f")

        return InvoiceExtract(
            rationale="Deterministic layout extraction",
            status="ok" if (items or grand != "0.00") else "partial",
            confidence="high" if items else "medium",
            invoice_number=inv_m.group(1) if inv_m else "",
            issue_date=date_m.group(1) if date_m else None,
            vendor=(vendor_m.group(1).strip()[:120] if vendor_m else ""),
            customer=(cust_m.group(1).strip()[:120] if cust_m else ""),
            currency=currency,
            line_items=[InvoiceLineItem(**it) for it in items],
            subtotal=sub,
            tax_total=tax,
            discount_total=disc,
            grand_total=grand,
        ).model_dump()

    if doc_type == "bank_statement":
        bank_m = re.search(r"(?:bank|banco)\s*[:\-]\s*(.+)", full_text, re.I)
        acct_m = re.search(r"(?:account|cuenta)\s*[:\-]?\s*([0-9*\- ]{4,})", full_text, re.I)
        open_m = re.search(r"(?:opening\s+balance|saldo\s+inicial)\s*[:\-]?\s*\$?([\d,.\-]+)", full_text, re.I)
        close_m = re.search(r"(?:closing\s+balance|saldo\s+final)\s*[:\-]?\s*\$?([\d,.\-]+)", full_text, re.I)
        txs: list[BankTransaction] = []
        for line in full_text.splitlines():
            parts = [p.strip() for p in re.split(r"\s*\|\s*", line) if p.strip()]
            if len(parts) >= 4 and re.match(r"20\d{2}-\d{2}-\d{2}", parts[0]):
                txs.append(
                    BankTransaction(
                        date=parts[0][:10],
                        description=parts[1][:240],
                        debit=_parse_dec_str(parts[2]),
                        credit=_parse_dec_str(parts[3]),
                    )
                )
        return BankStatementExtract(
            rationale="Deterministic bank statement extraction",
            status="ok" if txs else "partial",
            confidence="high" if txs else "medium",
            bank_name=bank_m.group(1).strip()[:120] if bank_m else "",
            account_masked=acct_m.group(1).strip() if acct_m else "****0000",
            currency=currency,
            opening_balance=_parse_dec_str(open_m.group(1)) if open_m else "0.00",
            closing_balance=_parse_dec_str(close_m.group(1)) if close_m else "0.00",
            transactions=txs,
        ).model_dump()

    if doc_type == "financial_report":
        def _find_metric(pat: str) -> str:
            m = re.search(pat, full_text, re.I)
            return _parse_dec_str(m.group(1)) if m else "0.00"

        period_m = re.search(r"(?:period|quarter|periodo|fiscal)\s*[:\-]\s*(.+)", full_text, re.I)
        return FinancialReportExtract(
            rationale="Deterministic financial statement extraction",
            status="ok",
            confidence="medium",
            fiscal_period=period_m.group(1).strip()[:60] if period_m else "",
            currency=currency,
            revenue=_find_metric(r"(?:total\s+revenue|revenue|ingresos\s+totales)\s*[:\-]?\s*\$?([\d,.\-]+)"),
            cost_of_revenue=_find_metric(r"(?:cost\s+of\s+revenue|cogs|costo\s+de\s+ventas)\s*[:\-]?\s*\$?([\d,.\-]+)"),
            operating_cash_flow=_find_metric(r"(?:operating\s+cash\s+flow|flujo\s+operativo)\s*[:\-]?\s*\$?([\d,.\-]+)"),
            capex=_find_metric(r"(?:capex|capital\s+expenditures?|inversiones\s+de\s+capital)\s*[:\-]?\s*\$?([\d,.\-]+)"),
        ).model_dump()

    return {"doc_type": doc_type, "currency": currency, "preview": full_text[:1000]}


async def extract_structured_document(
    *,
    project_id: str,
    file_path: Path,
    filename: str,
    requested_type: str,
    default_currency: str,
    db: MetadataDB,
    router: MultiProviderLLMRouter,
) -> dict[str, Any]:
    """Extract structured JSON from a PDF/text financial document with Decimal accounting verification."""
    pages, reader_engine = extract_document_pages(file_path)
    full_text = "\n\n".join(pages)
    doc_type = detect_document_type(full_text, requested_type)
    file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()[:16]

    schema_map: dict[str, type[BaseModel]] = {
        "invoice": InvoiceExtract,
        "bank_statement": BankStatementExtract,
        "financial_report": FinancialReportExtract,
    }

    extracted_dict: dict[str, Any] | None = None
    extraction_mode = "deterministic_parser"

    cfg = router.get_config()
    if cfg.is_enabled and doc_type in schema_map:
        schema_cls = schema_map[doc_type]
        parsed, _ = await router.complete_structured(
            schema_cls=schema_cls,
            system_prompt=(
                f"Extract structured financial data ({doc_type}) from the document text. "
                "Represent all monetary amounts as exact decimal strings with 2 decimals. "
                "Mask bank account numbers to last 4 digits (****1234). Never invent missing values."
            ),
            user_prompt=full_text[:14000],
            tier="fast",
            project_id=project_id,
            feature_name=f"ocr_extract_{doc_type}",
        )
        if parsed is not None:
            extracted_dict = parsed.model_dump()
            extraction_mode = f"llm:{cfg.provider}"

    if extracted_dict is None:
        extracted_dict = _extract_heuristic(doc_type, full_text, default_currency)

    audited, validation_errors = audit_extracted_document(doc_type, extracted_dict)
    currency = str(audited.get("currency") or default_currency).upper()
    as_of = audited.get("issue_date") or audited.get("period_end")

    record = db.upsert_document(
        project_id=project_id,
        filename=filename,
        doc_type=doc_type,
        file_path=str(file_path),
        file_hash=file_hash,
        page_count=len(pages),
        as_of_date=as_of,
        period_start=audited.get("period_start"),
        period_end=audited.get("period_end"),
        currency=currency,
        status="needs_review" if validation_errors else "extracted",
        extracted=audited,
        validation_errors=validation_errors,
    )

    return sanitize_json(
        {
            "document": record,
            "reader_engine": reader_engine,
            "extraction_mode": extraction_mode,
            "accounting_valid": len(validation_errors) == 0,
            "validation_errors": validation_errors,
        }
    )


def commit_document_to_dataset(
    *,
    project_id: str,
    doc_record: dict[str, Any],
    override_extracted: dict[str, Any] | None,
    db: MetadataDB,
    pipeline: ProjectIngestionPipeline,
) -> dict[str, Any]:
    """Convert a reviewed extracted invoice/bank statement into normalized CSV rows and sync Parquet."""
    doc_type: DocType = doc_record.get("doc_type", "other")  # type: ignore[assignment]
    payload = override_extracted if override_extracted is not None else (doc_record.get("extracted") or {})
    audited, errors = audit_extracted_document(doc_type, payload)

    rows: list[dict[str, Any]] = []
    if doc_type == "invoice":
        inv_id = audited.get("invoice_number") or doc_record["id"][:8]
        inv_date = audited.get("issue_date") or "2026-01-01"
        cust = audited.get("customer") or audited.get("vendor") or "PDF Import"
        items = audited.get("line_items") or []
        if items:
            for it in items:
                rows.append(
                    {
                        "Order_ID": inv_id,
                        "Order_Date": inv_date,
                        "Product": it.get("description") or "Invoice Line",
                        "Category": "PDF Invoice",
                        "Quantity": it.get("quantity", "1"),
                        "Unit_Price": it.get("unit_price", "0"),
                        "Revenue": it.get("line_total", "0"),
                        "Customer": cust,
                        "Channel": "Document OCR",
                        "Status": "Completed",
                    }
                )
        elif Decimal(_parse_dec_str(audited.get("grand_total"))) > Decimal("0"):
            rows.append(
                {
                    "Order_ID": inv_id,
                    "Order_Date": inv_date,
                    "Product": f"Invoice {inv_id}",
                    "Category": "PDF Invoice",
                    "Quantity": "1",
                    "Revenue": audited["grand_total"],
                    "Customer": cust,
                    "Channel": "Document OCR",
                    "Status": "Completed",
                }
            )
    elif doc_type == "bank_statement":
        for idx, tx in enumerate(audited.get("transactions") or [], start=1):
            credit = Decimal(_parse_dec_str(tx.get("credit")))
            debit = Decimal(_parse_dec_str(tx.get("debit")))
            net_amt = credit if credit > 0 else -debit
            rows.append(
                {
                    "Order_ID": f"STMT-{doc_record['id'][:6]}-{idx}",
                    "Order_Date": tx.get("date") or "2026-01-01",
                    "Product": tx.get("description") or "Bank Movement",
                    "Category": tx.get("category") or "Bank Statement",
                    "Quantity": "1",
                    "Revenue": format(net_amt, "f"),
                    "Customer": audited.get("bank_name") or "Bank",
                    "Channel": "Bank Statement OCR",
                    "Status": "Completed",
                }
            )
    else:
        raise ValueError(f"Document type '{doc_type}' is for RAG reference, not tabular sales commit")

    if not rows:
        raise ValueError("No transactional rows found to commit from extracted document")

    csv_bytes = pd.DataFrame(rows).to_csv(index=False).encode("utf-8")
    rel_name = f"ocr_extracted/{doc_record['id'][:8]}_{Path(doc_record['filename']).stem}.csv"
    sync_res = pipeline.ingest_uploaded_files(project_id, [(rel_name, csv_bytes)])

    updated_doc = db.upsert_document(
        doc_id=str(doc_record["id"]),
        project_id=project_id,
        filename=str(doc_record["filename"]),
        doc_type=doc_type,
        file_path=str(doc_record["file_path"]),
        file_hash=str(doc_record["file_hash"]),
        page_count=int(doc_record.get("page_count") or 0),
        as_of_date=doc_record.get("as_of_date"),
        period_start=doc_record.get("period_start"),
        period_end=doc_record.get("period_end"),
        currency=doc_record.get("currency"),
        status="ingested",
        extracted=audited,
        validation_errors=errors,
        ingested_to_dataset=True,
        indexed_in_rag=bool(doc_record.get("indexed_in_rag")),
    )
    return sanitize_json({"document": updated_doc, "rows_committed": len(rows), "sync": sync_res.model_dump()})
