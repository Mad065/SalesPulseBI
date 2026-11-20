"""Shared pytest fixtures for SalesPulseBI unit and integration tests."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from salespulsebi.api.app import create_app
from salespulsebi.config import Settings
from salespulsebi.ingestion.pipeline import ProjectIngestionPipeline
from salespulsebi.storage.database import MetadataDB


@pytest.fixture()
def isolated_settings(tmp_path: Path) -> Settings:
    cfg = Settings(data_dir=tmp_path / "sp_data", host="127.0.0.1", port=8499)
    cfg.ensure_dirs()
    return cfg


@pytest.fixture()
def db(isolated_settings: Settings) -> MetadataDB:
    return MetadataDB(isolated_settings.db_path)


@pytest.fixture()
def pipeline(db: MetadataDB, isolated_settings: Settings) -> ProjectIngestionPipeline:
    return ProjectIngestionPipeline(db=db, settings=isolated_settings)


@pytest.fixture()
def client(isolated_settings: Settings) -> TestClient:
    app = create_app(isolated_settings)
    return TestClient(app)


@pytest.fixture()
def heterogeneous_folder(tmp_path: Path) -> Path:
    """Create a folder tree with Spanish CSV, European ; Latin-1 CSV, multi-sheet XLSX, and JSONL."""
    root = tmp_path / "sales_folder"
    (root / "north").mkdir(parents=True)
    (root / "eu").mkdir(parents=True)

    # 1. Spanish CSV with USD formatting and extra column
    df1 = pd.DataFrame(
        [
            {
                "Folio": "ORD-1",
                "Fecha_Venta": "2026-01-05",
                "Producto": "Laptop Pro",
                "Categoria": "Electronics",
                "Cantidad": "1",
                "Costo": "700",
                "Importe_Total": "$1,200.50",
                "Cliente": "Alice",
                "Canal": "Web",
                "Estado": "Completed",
                "Campaña": "Winter",
            },
            {
                "Folio": "ORD-1",
                "Fecha_Venta": "2026-01-05",
                "Producto": "USB Hub",
                "Categoria": "Accessories",
                "Cantidad": "2",
                "Costo": "20",
                "Importe_Total": "$50.00",
                "Cliente": "Alice",
                "Canal": "Web",
                "Estado": "Completed",
                "Campaña": "Winter",
            },
        ]
    )
    df1.to_csv(root / "north" / "north_jan.csv", index=False, encoding="utf-8-sig")

    # 2. European semicolon Latin-1 CSV with Euro comma decimals and DD/MM/YYYY dates
    eu_csv = (
        "Factura;Fecha;Articulo;Linea;Unidades;Coste;Total_EUR;ID_Cliente;Canal_Venta;Estatus\n"
        "ORD-2;14/01/2026;Laptop Pro;Electronics;1;700;1.200,00 €;Bob;Retail;Completed\n"
        "ORD-3;15/01/2026;Mouse Pro;Accessories;1;15;(45,00);Bob;Retail;Cancelled\n"
    )
    (root / "eu" / "eu_jan.csv").write_bytes(eu_csv.encode("cp1252"))

    # 3. Multi-sheet Excel
    df_x1 = pd.DataFrame(
        [
            {
                "Order_ID": "ORD-4",
                "Order_Date": "2026-02-01",
                "Product_Name": "Laptop Pro",
                "Category": "Electronics",
                "Qty": 2,
                "Cost": 1400,
                "Sales_Amount": 2400.0,
                "Customer_ID": "Alice",
                "Channel": "B2B",
                "Status": "Completed",
            }
        ]
    )
    df_x2 = pd.DataFrame(
        [
            {
                "Order_ID": "ORD-5",
                "Order_Date": "2026-02-10",
                "Product_Name": "USB Hub",
                "Category": "Accessories",
                "Qty": 1,
                "Cost": 10,
                "Sales_Amount": 25.0,
                "Customer_ID": "Charlie",
                "Channel": "Web",
                "Status": "Completed",
            }
        ]
    )
    with pd.ExcelWriter(root / "feb_orders.xlsx", engine="openpyxl") as writer:
        df_x1.to_excel(writer, sheet_name="Week1", index=False)
        df_x2.to_excel(writer, sheet_name="Week2", index=False)

    # 4. JSONL file
    (root / "extra.jsonl").write_text(
        '{"order_id":"ORD-6","date":"2026-02-15","product":"Monitor 4K","category":"Electronics","quantity":1,"cost":250,"total":450,"customer":"Diana","channel":"Web","status":"Completed"}\n',
        encoding="utf-8",
    )

    return root
