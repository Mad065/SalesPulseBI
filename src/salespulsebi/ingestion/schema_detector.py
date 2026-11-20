"""Bilingual (ES/EN) semantic column detection and statistical profiling."""

from __future__ import annotations

import re
import unicodedata

import pandas as pd

from salespulsebi.storage.models import ColumnProfile, SemanticRole

ROLE_ALIASES: dict[SemanticRole, tuple[str, ...]] = {
    "date": (
        "fecha",
        "fecha venta",
        "fecha pedido",
        "fecha orden",
        "fecha factura",
        "fecha compra",
        "fecha emision",
        "date",
        "order date",
        "sale date",
        "sales date",
        "invoice date",
        "transaction date",
        "created at",
        "timestamp",
        "fec",
        "dia",
    ),
    "revenue": (
        "total",
        "importe",
        "importe total",
        "monto",
        "monto total",
        "venta",
        "ventas",
        "venta total",
        "ingreso",
        "ingresos",
        "subtotal",
        "neto",
        "total neto",
        "revenue",
        "sales",
        "total sales",
        "amount",
        "total amount",
        "net amount",
        "gross sales",
        "grand total",
        "total usd",
        "total mxn",
        "total eur",
        "importe eur",
    ),
    "quantity": (
        "cantidad",
        "cant",
        "unidades",
        "uds",
        "piezas",
        "qty",
        "quantity",
        "units",
        "units sold",
        "items",
        "num items",
    ),
    "unit_price": (
        "precio",
        "precio unitario",
        "pvp",
        "valor unitario",
        "tarifa",
        "unit price",
        "price",
        "item price",
        "rate",
        "unit cost price",
    ),
    "cost": (
        "costo",
        "costos",
        "coste",
        "costo unitario",
        "costo total",
        "compra",
        "cost",
        "total cost",
        "unit cost",
        "cogs",
    ),
    "discount": (
        "descuento",
        "descuentos",
        "dcto",
        "rebaja",
        "promo",
        "discount",
        "discount amount",
        "discount pct",
        "disc",
    ),
    "tax": (
        "iva",
        "impuesto",
        "impuestos",
        "igv",
        "itbis",
        "tax",
        "vat",
        "tax amount",
    ),
    "product": (
        "producto",
        "nombre producto",
        "articulo",
        "item",
        "sku",
        "descripcion",
        "concepto",
        "product",
        "product name",
        "item name",
        "merchandise",
    ),
    "category": (
        "categoria",
        "subcategoria",
        "linea",
        "familia",
        "departamento",
        "depto",
        "rubro",
        "marca",
        "brand",
        "category",
        "product category",
        "department",
        "segment",
    ),
    "customer": (
        "cliente",
        "nombre cliente",
        "id cliente",
        "comprador",
        "razon social",
        "email",
        "customer",
        "customer id",
        "customer name",
        "client",
        "client id",
        "buyer",
        "account",
    ),
    "order_id": (
        "pedido",
        "id pedido",
        "numero pedido",
        "no pedido",
        "factura",
        "folio",
        "ticket",
        "orden",
        "id orden",
        "comprobante",
        "recibo",
        "transaccion",
        "order id",
        "order no",
        "order number",
        "invoice",
        "invoice id",
        "invoice number",
        "receipt",
        "transaction id",
        "tx id",
    ),
    "region": (
        "sucursal",
        "tienda",
        "almacen",
        "sede",
        "local",
        "region",
        "zona",
        "ciudad",
        "estado",
        "pais",
        "ubicacion",
        "store",
        "branch",
        "location",
        "city",
        "country",
        "territory",
        "warehouse",
    ),
    "channel": (
        "canal",
        "canal venta",
        "origen",
        "plataforma",
        "fuente",
        "medio",
        "channel",
        "sales channel",
        "source",
        "platform",
        "storefront",
    ),
    "salesperson": (
        "vendedor",
        "vendedora",
        "asesor",
        "ejecutivo",
        "agente",
        "comercial",
        "cajero",
        "empleado",
        "salesperson",
        "sales rep",
        "rep",
        "agent",
        "seller",
        "associate",
        "cashier",
    ),
    "payment_method": (
        "metodo pago",
        "forma pago",
        "medio pago",
        "tipo pago",
        "pago",
        "tarjeta",
        "payment method",
        "payment type",
        "payment",
        "pay method",
        "tender",
    ),
    "status": (
        "estatus",
        "estado pedido",
        "estado venta",
        "situacion",
        "status",
        "order status",
        "sale status",
        "state",
    ),
}

NUMERIC_ROLES: frozenset[str] = frozenset(
    {"revenue", "quantity", "unit_price", "cost", "discount", "tax"}
)


def normalize_col_name(name: str) -> str:
    """Normalize column header by stripping accents, lowercasing, and normalizing separators."""
    nfkd = unicodedata.normalize("NFKD", str(name))
    ascii_str = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    cleaned = re.sub(r"[_\-./]+", " ", ascii_str.lower()).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def _looks_numeric(series: pd.Series) -> float:
    non_null = series.dropna().astype(str).str.strip()
    non_null = non_null[non_null != ""]
    if non_null.empty:
        return 0.0
    sample = non_null.head(200)
    cleaned = (
        sample.str.replace(r"[$€£¥₹]", "", regex=True)
        .str.replace(r"\b(USD|MXN|EUR|COP|ARS|CLP|PEN|GBP|CAD)\b", "", regex=True, case=False)
        .str.replace(r"[,\s]", "", regex=True)
        .str.replace(r"^\((.+)\)$", r"-\1", regex=True)
    )
    parsed = pd.to_numeric(cleaned, errors="coerce")
    return float(parsed.notna().mean())


def _looks_like_date(series: pd.Series) -> float:
    non_null = series.dropna().astype(str).str.strip()
    non_null = non_null[non_null != ""]
    if non_null.empty:
        return 0.0
    sample = non_null.head(100)
    # Pure plain numbers that aren't 8-digit YYYYMMDD or Excel serials shouldn't score high as dates
    pure_small_num = sample.str.match(r"^\d+(\.\d+)?$").mean()
    if pure_small_num > 0.8:
        nums = pd.to_numeric(sample, errors="coerce")
        # Allow Excel date serials (roughly 1990..2050 -> 32874..55000)
        in_excel_range = ((nums >= 32000) & (nums <= 60000)).mean()
        if in_excel_range < 0.7:
            return 0.0
    parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
    return float(parsed.notna().mean())


def _score_column_for_role(col_name: str, series: pd.Series, role: SemanticRole) -> float:
    norm = normalize_col_name(col_name)
    aliases = ROLE_ALIASES[role]

    name_score = 0.0
    if norm in aliases:
        name_score = 1.0
    else:
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias)}\b", norm):
                name_score = max(name_score, 0.84)
            elif len(alias) >= 4 and alias in norm:
                name_score = max(name_score, 0.68)

    if name_score == 0.0:
        return 0.0

    # Content verification boosts or disqualifies candidates
    if role == "date":
        date_ratio = _looks_like_date(series)
        if date_ratio < 0.45:
            return 0.0
        return min(1.0, name_score * 0.7 + date_ratio * 0.3)

    if role in NUMERIC_ROLES:
        num_ratio = _looks_numeric(series)
        if num_ratio < 0.45:
            return 0.0
        return min(1.0, name_score * 0.75 + num_ratio * 0.25)

    # Disambiguate "estado" (status vs region/state) using cardinality
    if norm == "estado":
        non_null = series.dropna().astype(str).str.lower()
        status_words = {"completed", "completado", "paid", "pagado", "cancelled", "cancelado", "pending", "pendiente", "returned", "devuelto", "shipped", "enviado"}
        overlap = non_null.head(200).isin(status_words).mean() if not non_null.empty else 0.0
        if role == "status":
            return 0.92 if overlap > 0.25 else 0.45
        if role == "region":
            return 0.35 if overlap > 0.25 else 0.80

    return name_score


def detect_schema_and_profile(
    df: pd.DataFrame,
    manual_overrides: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[ColumnProfile]]:
    """
    Auto-detect semantic role mapping (`role -> column_name`) and build column profiles.

    `manual_overrides` takes precedence whenever the referenced column exists in `df`.
    """
    overrides = manual_overrides or {}
    role_map: dict[str, str] = {}
    used_cols: set[str] = set()

    # 1. Apply manual overrides first
    for role, col in overrides.items():
        if col and col in df.columns:
            role_map[role] = col
            used_cols.add(col)

    # 2. Score remaining (role, col) pairs greedily by highest confidence
    candidates: list[tuple[float, SemanticRole, str]] = []
    for role in ROLE_ALIASES:
        if role in role_map:
            continue
        for col in df.columns:
            if col in used_cols or str(col).startswith("_source_"):
                continue
            score = _score_column_for_role(str(col), df[col], role)
            if score >= 0.55:
                candidates.append((score, role, str(col)))

    candidates.sort(key=lambda item: item[0], reverse=True)
    col_confidences: dict[str, tuple[str, float]] = {}
    for role, col in role_map.items():
        col_confidences[col] = (role, 1.0)

    for score, role, col in candidates:
        if role in role_map or col in used_cols:
            continue
        role_map[role] = col
        used_cols.add(col)
        col_confidences[col] = (role, round(score, 2))

    # 3. Build rich column profiles for Mapping Studio & Data Quality
    total_rows = max(len(df), 1)
    profiles: list[ColumnProfile] = []
    for col in df.columns:
        s = df[col]
        null_cnt = int(s.isna().sum())
        non_null = s.dropna()
        samples = [str(v)[:60] for v in non_null.drop_duplicates().head(5).tolist()]
        detected_role, conf = col_confidences.get(str(col), (None, 0.0))
        profiles.append(
            ColumnProfile(
                name=str(col),
                dtype=str(s.dtype),
                null_count=null_cnt,
                null_pct=round(null_cnt / total_rows * 100.0, 2),
                distinct_count=int(non_null.nunique()),
                sample_values=samples,
                detected_role=detected_role,
                confidence=conf,
            )
        )

    return role_map, profiles
