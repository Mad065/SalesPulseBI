"""Tests for multi-format readers, bilingual schema detection, cleaning, and folder sync."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from salespulsebi.ingestion.cleaner import parse_currency_series, parse_date_series
from salespulsebi.ingestion.pipeline import ProjectIngestionPipeline
from salespulsebi.ingestion.readers import read_tabular_file
from salespulsebi.ingestion.schema_detector import detect_schema_and_profile
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import ProjectCreate
from salespulsebi.storage.parquet_store import ParquetStore


def test_currency_and_date_parsing() -> None:
    cur = pd.Series(["$1,234.56", "1.234,56 €", "MXN 450.25", "(125.50)", "", None])
    parsed = parse_currency_series(cur)
    assert round(float(parsed.iloc[0]), 2) == 1234.56
    assert round(float(parsed.iloc[1]), 2) == 1234.56
    assert round(float(parsed.iloc[2]), 2) == 450.25
    assert round(float(parsed.iloc[3]), 2) == -125.50
    assert pd.isna(parsed.iloc[4])

    dates = pd.Series(["2026-01-15", "14/02/2026", "45320", "15-ene-2026"])
    dparsed = parse_date_series(dates)
    assert dparsed.notna().all()
    assert dparsed.iloc[0].strftime("%Y-%m-%d") == "2026-01-15"
    assert dparsed.iloc[1].strftime("%Y-%m-%d") == "2026-02-14"


def test_bilingual_schema_detection() -> None:
    df = pd.DataFrame(
        {
            "Fecha_Venta": ["2026-01-01", "2026-01-02"],
            "Importe_Total": ["$100.00", "$200.00"],
            "Producto": ["Widget A", "Widget B"],
            "Cliente": ["C1", "C2"],
            "Cantidad": ["2", "4"],
        }
    )
    role_map, profiles = detect_schema_and_profile(df)
    assert role_map["date"] == "Fecha_Venta"
    assert role_map["revenue"] == "Importe_Total"
    assert role_map["product"] == "Producto"
    assert role_map["customer"] == "Cliente"
    assert role_map["quantity"] == "Cantidad"
    assert len(profiles) == 5


def test_heterogeneous_folder_ingestion_and_incremental_sync(
    heterogeneous_folder: Path,
    db: MetadataDB,
    pipeline: ProjectIngestionPipeline,
) -> None:
    proj = db.create_project(ProjectCreate(name="Folder Test", currency="USD"))
    res1 = pipeline.link_local_path(proj.id, str(heterogeneous_folder), recursive=True)

    assert res1.total_files == 4
    assert res1.total_rows == 7

    ppath = pipeline.settings.project_parquet_path(proj.id)
    con = ParquetStore.connect_parquet(ppath)
    try:
        tot = ParquetStore.query_one(
            con, "SELECT COUNT(*) AS c, SUM(_revenue) AS rev FROM sales WHERE NOT _is_excluded_status"
        )
        assert tot["c"] == 6  # 1 cancelled row flagged excluded
        assert round(float(tot["rev"]), 2) == round(1200.50 + 50.0 + 1200.0 + 2400.0 + 25.0 + 450.0, 2)
    finally:
        con.close()

    # Add a new Parquet file into folder and sync again
    new_df = pd.DataFrame(
        [{"order_id": "ORD-7", "date": "2026-02-20", "product": "Mouse Pro", "total": 99.0}]
    )
    ParquetStore.write_parquet(new_df, heterogeneous_folder / "march_extra.parquet")
    assert len(read_tabular_file(heterogeneous_folder / "march_extra.parquet")) == 1

    res2 = pipeline.sync_project(proj.id)
    assert res2.total_files == 5
    assert res2.total_rows == 8
