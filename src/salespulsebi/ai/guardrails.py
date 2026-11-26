"""AI security guardrails: prompt-injection defense, PII redaction, read-only SQL AST validation, and numeric grounding."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

try:
    import sqlglot  # type: ignore[import-not-found]
    from sqlglot import exp  # type: ignore[import-not-found]

    HAS_SQLGLOT = True
except Exception:
    HAS_SQLGLOT = False


# ── Prompt Injection & Untrusted Data Isolation ──────────────────────────────

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", re.IGNORECASE),
    re.compile(r"ignora\s+(todas?\s+las?\s+)?instrucciones\s+(anteriores|previas)", re.IGNORECASE),
    re.compile(r"(reveal|show|print|dump|leak)\s+(your\s+)?(system\s+prompt|hidden\s+instructions|api\s*key|secrets?)", re.IGNORECASE),
    re.compile(r"(muestra|revela|imprime)\s+(tu\s+)?(prompt\s+del?\s+sistema|instrucciones\s+ocultas|clave\s+api|secretos?)", re.IGNORECASE),
    re.compile(r"disregard\s+safety|bypass\s+(guardrails|security|auth)", re.IGNORECASE),
    re.compile(r"\b(DROP\s+TABLE|ATTACH\s+DATABASE|COPY\s+.+\s+TO|PRAGMA\s+)", re.IGNORECASE),
)


def detect_prompt_injection(text: str) -> tuple[bool, str | None]:
    """Screen untrusted user text for common prompt-injection or exfiltration attempts."""
    if not text:
        return False, None
    for pat in _INJECTION_PATTERNS:
        if pat.search(text):
            return True, "prompt_injection_detected"
    return False, None


def wrap_untrusted_input(user_text: str) -> str:
    """Wrap user input in explicit structural delimiters so models treat it strictly as data."""
    safe = user_text.replace("</untrusted_user_query>", "")
    return f"<untrusted_user_query>\n{safe}\n</untrusted_user_query>"


def wrap_retrieved_record(record_id: str, content: str) -> str:
    safe = content.replace("</retrieved_record>", "")
    return f'<retrieved_record id="{record_id}">\n{safe}\n</retrieved_record>'


# ── PII & Secret Redaction ───────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?|\d{2,4}[\s.-])\d{3,4}[\s.-]\d{4}(?!\d)")
_CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_SECRET_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|ghp_[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9._-]{16,})"
)


def redact_pii(text: str) -> str:
    """Mask emails, phone numbers, card/account numbers, and API keys before logging or remote LLM calls."""
    if not text:
        return text
    out = _SECRET_RE.sub("[REDACTED_SECRET]", text)
    out = _EMAIL_RE.sub("[REDACTED_EMAIL]", out)
    out = _CARD_RE.sub(lambda m: f"****{re.sub(r'[^0-9]', '', m.group(0))[-4:]}", out)
    out = _PHONE_RE.sub("[REDACTED_PHONE]", out)
    return out


# ── Strict Read-Only DuckDB SQL Validation ───────────────────────────────────

_FORBIDDEN_SQL_TOKENS: frozenset[str] = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "CREATE",
        "TRUNCATE",
        "REPLACE",
        "MERGE",
        "ATTACH",
        "DETACH",
        "COPY",
        "EXPORT",
        "IMPORT",
        "INSTALL",
        "LOAD",
        "PRAGMA",
        "CALL",
        "EXECUTE",
        "PREPARE",
        "SET",
        "RESET",
        "VACUUM",
        "CHECKPOINT",
        "GRANT",
        "REVOKE",
        "TRANSACTION",
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "USE",
        "DESCRIBE",
        "SHOW",
    }
)

_FORBIDDEN_FUNCTIONS_RE = re.compile(
    r"\b(read_csv|read_csv_auto|read_parquet|read_json|read_json_auto|read_ndjson|"
    r"read_text|read_blob|parquet_scan|csv_scan|glob|sqlite_attach|sqlite_scan|"
    r"postgres_attach|postgres_scan|duckdb_secrets|duckdb_settings|current_setting|"
    r"getenv|http_get|http_post)\s*\(",
    re.IGNORECASE,
)

_SQL_COMMENT_RE = re.compile(r"(--[^\n]*|/\*[\s\S]*?\*/)")
_STRING_LITERAL_RE = re.compile(r"'(?:''|[^'])*'")


def validate_readonly_sql(
    raw_sql: str,
    *,
    allowed_tables: set[str] | None = None,
    max_limit: int = 500,
) -> tuple[bool, str, str | None]:
    """
    Validate that an AI-generated DuckDB SQL query is strictly read-only SELECT on allowed tables.

    Returns `(is_valid, sanitized_sql, error_reason)`.
    """
    allowed = {t.lower() for t in (allowed_tables or {"sales"})}
    sql = raw_sql.strip()
    if not sql:
        return False, "", "empty_sql"

    # Strip trailing semicolon only; reject stacked statements
    if sql.endswith(";"):
        sql = sql[:-1].strip()
    if _SQL_COMMENT_RE.search(sql):
        return False, "", "sql_comments_not_allowed"

    # Inspect non-literal portion for stacked statements and forbidden tokens
    stripped_literals = _STRING_LITERAL_RE.sub("''", sql)
    if ";" in stripped_literals:
        return False, "", "stacked_statements_not_allowed"

    if _FORBIDDEN_FUNCTIONS_RE.search(stripped_literals):
        return False, "", "forbidden_filesystem_or_system_function"

    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", stripped_literals)
    if not tokens:
        return False, "", "invalid_sql"

    first_kw = tokens[0].upper()
    if first_kw not in ("SELECT", "WITH"):
        return False, "", "only_select_queries_allowed"

    upper_tokens = {tok.upper() for tok in tokens}
    bad = upper_tokens & _FORBIDDEN_SQL_TOKENS
    if bad:
        return False, "", f"forbidden_sql_keyword:{sorted(bad)[0].lower()}"

    # AST validation with sqlglot when available
    if HAS_SQLGLOT:
        try:
            parsed_list = sqlglot.parse(sql, read="duckdb")
            if len(parsed_list) != 1 or parsed_list[0] is None:
                return False, "", "single_statement_required"
            tree = parsed_list[0]
            if not isinstance(tree, (exp.Select, exp.Union)):
                return False, "", "only_select_ast_allowed"
            cte_names = {
                cte.alias_or_name.lower()
                for cte in tree.find_all(exp.CTE)
                if cte.alias_or_name
            }
            for tbl in tree.find_all(exp.Table):
                tname = (tbl.name or "").lower()
                if tname and tname not in allowed and tname not in cte_names:
                    return False, "", f"table_not_allowed:{tname}"
        except Exception:
            return False, "", "sql_parse_error"
    else:
        # Strict fallback table reference validation: extract CTE names and FROM/JOIN targets
        cte_names = {
            m.group(1).lower()
            for m in re.finditer(r"(?:\bWITH\b|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(", stripped_literals, re.IGNORECASE)
        }
        for m in re.finditer(r"\b(?:FROM|JOIN)\s+([A-Za-z_\"][A-Za-z0-9_\".]*)", stripped_literals, re.IGNORECASE):
            raw_t = m.group(1).strip('"').lower()
            if raw_t in ("(", "select"):
                continue
            if raw_t not in allowed and raw_t not in cte_names:
                return False, "", f"table_not_allowed:{raw_t}"

    # Enforce hard LIMIT <= max_limit
    limit_match = re.search(r"\bLIMIT\s+(\d+)\b", stripped_literals, re.IGNORECASE)
    if limit_match:
        existing_limit = int(limit_match.group(1))
        if existing_limit > max_limit:
            sql = re.sub(
                r"\bLIMIT\s+\d+\b",
                f"LIMIT {max_limit}",
                sql,
                count=1,
                flags=re.IGNORECASE,
            )
    else:
        sql = f"{sql} LIMIT {max_limit}"

    return True, sql, None


# ── Post-Generation Numeric Grounding Verifier ───────────────────────────────

_NUM_RE = re.compile(r"(?<![A-Za-z0-9_])-?\d{1,3}(?:,\d{3})*(?:\.\d+)?(?![A-Za-z0-9_])")


def _extract_decimals_from_obj(obj: Any, bucket: set[Decimal]) -> None:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float, Decimal)):
        try:
            d = Decimal(str(obj))
            if d.is_finite():
                bucket.add(abs(d))
        except InvalidOperation:
            pass
    elif isinstance(obj, str):
        for m in _NUM_RE.findall(obj):
            try:
                d = Decimal(m.replace(",", ""))
                if d.is_finite():
                    bucket.add(abs(d))
            except InvalidOperation:
                pass
    elif isinstance(obj, dict):
        for v in obj.values():
            _extract_decimals_from_obj(v, bucket)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _extract_decimals_from_obj(v, bucket)


def verify_numeric_claims(
    answer_text: str,
    grounded_context: Any,
    *,
    rel_tolerance: Decimal = Decimal("0.015"),
) -> dict[str, Any]:
    """
    Verify numeric claims in an AI answer against retrieved tool/RAG records.
    Ignores small structural ordinals (1..12), calendar years (2000..2100), and markdown numbering.
    """
    allowed: set[Decimal] = set()
    _extract_decimals_from_obj(grounded_context, allowed)

    claimed_raw = _NUM_RE.findall(answer_text or "")
    checked = 0
    unverified: list[str] = []

    for tok in claimed_raw:
        try:
            val = abs(Decimal(tok.replace(",", "")))
        except InvalidOperation:
            continue
        # Ignore dates/years and small list indices (0..12)
        if val <= Decimal("12") and "." not in tok:
            continue
        if Decimal("1990") <= val <= Decimal("2100") and "." not in tok:
            continue
        checked += 1
        matched = False
        for ref in allowed:
            if ref == val:
                matched = True
                break
            if ref > Decimal("0") and abs(val - ref) / ref <= rel_tolerance:
                matched = True
                break
            # Allow compact K/M conversions (e.g., 1.2K -> 1200)
            for scale in (Decimal("1000"), Decimal("1000000")):
                scaled = val * scale
                if ref > Decimal("0") and abs(scaled - ref) / ref <= Decimal("0.06"):
                    matched = True
                    break
            if matched:
                break
        if not matched:
            unverified.append(tok)

    return {
        "checked_count": checked,
        "unverified_numbers": unverified[:10],
        "grounded": len(unverified) == 0,
    }
