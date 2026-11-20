"""Deterministic generator for 1-click SalesPulseBI demo projects."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from salespulsebi.config import Settings
from salespulsebi.ingestion.pipeline import ProjectIngestionPipeline
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import ProjectCreate, ProjectSummary

CATALOG = [
    ("Laptop Pro 14", "Electronics", 1180.0, 760.0),
    ("Wireless ANC Headphones", "Electronics", 210.0, 95.0),
    ("USB-C Hub 8-in-1", "Accessories", 58.0, 21.0),
    ("Mechanical Keyboard", "Accessories", 135.0, 58.0),
    ("4K Monitor 27\"", "Electronics", 420.0, 260.0),
    ("Ergonomic Mouse", "Accessories", 72.0, 28.0),
    ("Standing Desk Mat", "Office", 48.0, 17.0),
    ("Smart LED Desk Lamp", "Office", 64.0, 24.0),
    ("Webcam FullHD", "Electronics", 89.0, 38.0),
    ("Laptop Sleeve 14\"", "Accessories", 34.0, 11.0),
    ("Mesh Ergonomic Chair", "Furniture", 310.0, 175.0),
    ("Portable SSD 1TB", "Storage", 115.0, 62.0),
    ("Condenser Mic USB", "Audio", 128.0, 54.0),
    ("Acoustic Desk Panel", "Office", 82.0, 32.0),
]

BUNDLE_PAIRS = [
    ("Laptop Pro 14", "USB-C Hub 8-in-1"),
    ("Laptop Pro 14", "Laptop Sleeve 14\""),
    ("Mechanical Keyboard", "Ergonomic Mouse"),
    ("4K Monitor 27\"", "USB-C Hub 8-in-1"),
    ("Webcam FullHD", "Condenser Mic USB"),
]


def _generate_branch_orders(
    rng: np.random.Generator,
    *,
    branch_name: str,
    start_date: date,
    days: int,
    n_orders: int,
    n_customers: int,
    channels: list[str],
    reps: list[str],
    payments: list[str],
    prefix: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    # Skewed product weights (Pareto 80/20)
    raw_weights = np.array([1.0 / ((i + 1) ** 1.15) for i in range(len(CATALOG))])
    prod_probs = raw_weights / raw_weights.sum()

    # Pre-generate dates with weekend + Nov/Dec Q4 boost
    all_dates = [start_date + timedelta(days=int(d)) for d in range(days)]
    date_weights = []
    for d in all_dates:
        w = 1.0
        if d.weekday() >= 4:  # Fri-Sun boost
            w *= 1.35
        if d.month == 11:
            w *= 1.45
        elif d.month == 12:
            w *= 1.65
        date_weights.append(w)
    dw = np.array(date_weights) / sum(date_weights)
    chosen_dates = rng.choice(len(all_dates), size=n_orders, p=dw)

    # Spike days (anomalies)
    spike_day = start_date + timedelta(days=int(days * 0.72))

    # Skewed customer weights so some are loyal Champions
    cust_ids = [f"CUST-{prefix}-{i:04d}" for i in range(1, n_customers + 1)]
    c_weights = np.array([1.0 / ((i + 1) ** 0.75) for i in range(n_customers)])
    c_probs = c_weights / c_weights.sum()

    for ord_idx in range(1, n_orders + 1):
        d = all_dates[int(chosen_dates[ord_idx - 1])]
        order_id = f"{prefix}-{d.strftime('%y%m')}-{ord_idx:05d}"
        customer = str(rng.choice(cust_ids, p=c_probs))
        channel = str(rng.choice(channels, p=[0.42, 0.35, 0.23]))
        rep = str(rng.choice(reps))
        pay = str(rng.choice(payments))
        is_cancelled = rng.random() < 0.042
        status = (
            str(rng.choice(["Cancelled", "Returned"]))
            if is_cancelled
            else "Completed"
        )

        n_items = int(rng.choice([1, 2, 3], p=[0.62, 0.28, 0.10]))
        picked_idx = rng.choice(len(CATALOG), size=n_items, replace=False, p=prod_probs)
        items = [CATALOG[int(i)] for i in picked_idx]

        # Enforce frequent cross-sell bundles 35% of the time when primary item picked
        for a_name, b_name in BUNDLE_PAIRS:
            if items[0][0] == a_name and rng.random() < 0.38 and all(x[0] != b_name for x in items):
                partner = next(c for c in CATALOG if c[0] == b_name)
                items.append(partner)
                break

        for prod_name, cat, unit_price, unit_cost in items:
            qty = int(rng.choice([1, 2, 3], p=[0.74, 0.19, 0.07]))
            disc_pct = float(rng.choice([0.0, 0.05, 0.10, 0.20], p=[0.64, 0.16, 0.14, 0.06]))
            gross = unit_price * qty
            discount = round(gross * disc_pct, 2)
            total = round(gross - discount, 2)
            cost = round(unit_cost * qty, 2)
            rows.append(
                {
                    "order_id": order_id,
                    "date": d.isoformat(),
                    "product": prod_name,
                    "category": cat,
                    "quantity": qty,
                    "unit_price": unit_price,
                    "discount": discount,
                    "cost": cost,
                    "total": total,
                    "customer": customer,
                    "region": branch_name,
                    "channel": channel,
                    "salesperson": rep,
                    "payment_method": pay,
                    "status": status,
                }
            )

    # Inject intentional corporate bulk spike anomaly on spike_day
    for k in range(18):
        prod_name, cat, unit_price, unit_cost = CATALOG[0]
        qty = 4
        rows.append(
            {
                "order_id": f"{prefix}-VIP-{k:03d}",
                "date": spike_day.isoformat(),
                "product": prod_name,
                "category": cat,
                "quantity": qty,
                "unit_price": unit_price,
                "discount": round(unit_price * qty * 0.05, 2),
                "cost": round(unit_cost * qty, 2),
                "total": round(unit_price * qty * 0.95, 2),
                "customer": f"CUST-{prefix}-0001",
                "region": branch_name,
                "channel": "B2B Direct",
                "salesperson": reps[0],
                "payment_method": "Bank Transfer",
                "status": "Completed",
            }
        )

    return rows


def seed_demo_projects(
    db: MetadataDB,
    settings: Settings,
    pipeline: ProjectIngestionPipeline,
) -> list[ProjectSummary]:
    """Create and ingest the 2 built-in demo projects idempotently."""
    rng = np.random.default_rng(42)
    demo_root = settings.demo_sources_dir
    demo_root.mkdir(parents=True, exist_ok=True)
    created_projects: list[ProjectSummary] = []

    # ------------------------------------------------------------------
    # Demo 1: OmniRetail LatAm — Multi-Branch Heterogeneous Folder
    # ------------------------------------------------------------------
    p1_name = "OmniRetail LatAm — Multi-Branch"
    existing1 = db.find_project_by_name(p1_name)
    if not existing1:
        folder1 = demo_root / "omniretail_multibranch"
        (folder1 / "CDMX").mkdir(parents=True, exist_ok=True)
        (folder1 / "Bogota").mkdir(parents=True, exist_ok=True)
        (folder1 / "Madrid").mkdir(parents=True, exist_ok=True)

        start = date.today() - timedelta(days=480)
        # 1) CDMX CSV in Spanish with $ currency format
        cdmx_rows = _generate_branch_orders(
            rng,
            branch_name="CDMX Norte",
            start_date=start,
            days=480,
            n_orders=1900,
            n_customers=380,
            channels=["Web Store", "Marketplace", "B2B Direct"],
            reps=["Ana Torres", "Carlos Méndez", "Sofía Ruiz"],
            payments=["Credit Card", "SPEI Transfer", "Debit Card"],
            prefix="MX",
        )
        cdmx_df = pd.DataFrame(cdmx_rows).rename(
            columns={
                "order_id": "Folio_Pedido",
                "date": "Fecha_Venta",
                "product": "Producto",
                "category": "Categoria",
                "quantity": "Cantidad",
                "unit_price": "Precio_Unitario",
                "discount": "Descuento",
                "cost": "Costo",
                "total": "Importe_Total",
                "customer": "Cliente",
                "region": "Sucursal",
                "channel": "Canal",
                "salesperson": "Vendedor",
                "payment_method": "Metodo_Pago",
                "status": "Estatus",
            }
        )
        cdmx_df["Importe_Total"] = cdmx_df["Importe_Total"].map(lambda v: f"${v:,.2f}")
        cdmx_df["Precio_Unitario"] = cdmx_df["Precio_Unitario"].map(lambda v: f"${v:,.2f}")
        cdmx_df.to_csv(folder1 / "CDMX" / "ventas_cdmx.csv", index=False, encoding="utf-8-sig")

        # 2) Bogota Multi-Sheet Excel (.xlsx) with English headers
        bog_rows = _generate_branch_orders(
            rng,
            branch_name="Bogotá Centro",
            start_date=start,
            days=480,
            n_orders=1550,
            n_customers=310,
            channels=["Web Store", "Retail POS", "B2B Direct"],
            reps=["Mateo Gómez", "Valentina Rojas", "Daniela Castro"],
            payments=["Credit Card", "PSE Transfer", "Cash"],
            prefix="CO",
        )
        bog_df = pd.DataFrame(bog_rows).rename(
            columns={
                "order_id": "Order_ID",
                "date": "Order_Date",
                "product": "Product_Name",
                "category": "Category",
                "quantity": "Qty",
                "unit_price": "Unit_Price",
                "discount": "Discount",
                "cost": "Cost",
                "total": "Sales_Amount",
                "customer": "Customer_ID",
                "region": "Branch",
                "channel": "Channel",
                "salesperson": "Sales_Rep",
                "payment_method": "Payment_Method",
                "status": "Status",
            }
        )
        mid_idx = len(bog_df) // 2
        with pd.ExcelWriter(folder1 / "Bogota" / "bogota_sales.xlsx", engine="openpyxl") as writer:
            bog_df.iloc[:mid_idx].to_excel(writer, sheet_name="H1_Orders", index=False)
            bog_df.iloc[mid_idx:].to_excel(writer, sheet_name="H2_Orders", index=False)

        # 3) Madrid CSV with semicolon delimiter + European € formatting
        mad_rows = _generate_branch_orders(
            rng,
            branch_name="Madrid Serrano",
            start_date=start,
            days=480,
            n_orders=1250,
            n_customers=260,
            channels=["Web Store", "Retail POS", "B2B Direct"],
            reps=["Lucía Navarro", "Javier Ortega", "Pablo Martín"],
            payments=["Credit Card", "Bizum", "Bank Transfer"],
            prefix="ES",
        )
        mad_df = pd.DataFrame(mad_rows).rename(
            columns={
                "order_id": "Factura",
                "date": "Fecha",
                "product": "Articulo",
                "category": "Linea",
                "quantity": "Unidades",
                "unit_price": "PVP",
                "discount": "Rebaja",
                "cost": "Coste",
                "total": "Total_EUR",
                "customer": "ID_Cliente",
                "region": "Tienda",
                "channel": "Canal_Venta",
                "salesperson": "Comercial",
                "payment_method": "Forma_Pago",
                "status": "Estado",
            }
        )
        mad_df["Fecha"] = pd.to_datetime(mad_df["Fecha"]).dt.strftime("%d/%m/%Y")
        mad_df["Total_EUR"] = mad_df["Total_EUR"].map(
            lambda v: f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
        )
        mad_df.to_csv(folder1 / "Madrid" / "madrid_ventas.csv", sep=";", index=False, encoding="utf-8")

        proj1 = db.create_project(
            ProjectCreate(
                name=p1_name,
                description="Multi-branch folder (CSV + multi-sheet XLSX + European ; CSV) with heterogeneous headers, RFM, cohorts, market basket & anomalies.",
                tags=["demo", "multi-folder", "b2b+b2c"],
                currency="USD",
                locale="es",
                is_favorite=True,
            )
        )
        pipeline.link_local_path(proj1.id, str(folder1), recursive=True)
        existing1 = db.get_project(proj1.id)
    if existing1:
        created_projects.append(existing1)

    # ------------------------------------------------------------------
    # Demo 2: Café Aurora — Minimal 3-Column CSV
    # ------------------------------------------------------------------
    p2_name = "Café Aurora — Minimal CSV"
    existing2 = db.find_project_by_name(p2_name)
    if not existing2:
        folder2 = demo_root / "cafe_aurora"
        folder2.mkdir(parents=True, exist_ok=True)
        cafe_items = [
            ("Latte Vainilla", 4.50),
            ("Flat White", 4.20),
            ("Espresso Doble", 3.10),
            ("Croissant Mantequilla", 3.80),
            ("Cold Brew Tónica", 5.20),
            ("Matcha Latte", 5.50),
            ("Panini Pesto", 8.90),
            ("Cheesecake Frutos Rojos", 6.40),
        ]
        cafe_rows = []
        base_d = date.today() - timedelta(days=180)
        for _ in range(620):
            d = base_d + timedelta(days=int(rng.integers(0, 180)))
            name, price = cafe_items[int(rng.choice(len(cafe_items), p=[0.22, 0.18, 0.15, 0.14, 0.10, 0.09, 0.07, 0.05]))]
            qty = int(rng.choice([1, 2, 3], p=[0.78, 0.18, 0.04]))
            cafe_rows.append({"fecha": d.isoformat(), "producto": name, "total": round(price * qty, 2)})

        cafe_csv = folder2 / "caja_cafe_aurora.csv"
        pd.DataFrame(cafe_rows).to_csv(cafe_csv, index=False, encoding="utf-8")

        proj2 = db.create_project(
            ProjectCreate(
                name=p2_name,
                description="Minimal 3-column CSV (fecha, producto, total) showcasing adaptive analytics without extra columns.",
                tags=["demo", "minimal", "csv"],
                currency="USD",
                locale="es",
                is_favorite=False,
            )
        )
        pipeline.ingest_uploaded_files(proj2.id, [("caja_cafe_aurora.csv", cafe_csv.read_bytes())])
        existing2 = db.get_project(proj2.id)
    if existing2:
        created_projects.append(existing2)

    return created_projects
