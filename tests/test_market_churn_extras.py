"""Phase 4 tests: Market sentiment RSS/lexicon, AutoML Churn/CLV, Entity Resolution, Margin Leakage, Smart Alerts."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from salespulsebi.ai.router import MultiProviderLLMRouter
from salespulsebi.ai.sentiment import (
    fetch_and_score_market_sentiment,
    parse_rss_or_atom_xml,
    score_financial_sentiment_local,
)
from salespulsebi.config import Settings
from salespulsebi.ingestion.pipeline import ProjectIngestionPipeline
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import ProjectCreate

SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Finance Feed</title>
    <item>
      <title>Retail sector posts record profit growth and strong demand</title>
      <link>https://example.com/news/1</link>
      <pubDate>2026-11-20</pubDate>
      <description>Electronics expansion drives margin beat.</description>
    </item>
    <item>
      <title>Inflation risk and tariff warning trigger slump in margins</title>
      <link>https://example.com/news/2</link>
      <pubDate>2026-11-21</pubDate>
      <description>Supply shortage causes decline across regional branches.</description>
    </item>
  </channel>
</rss>
"""


def test_rss_parser_and_bilingual_sentiment_scorer() -> None:
    items = parse_rss_or_atom_xml(SAMPLE_RSS)
    assert len(items) == 2

    pos = score_financial_sentiment_local(
        "Crecimiento récord en utilidades y fuerte expansión en Electronics",
        entity_keywords=["Electronics"],
    )
    assert pos["sentiment_label"] == "positive"
    assert pos["sentiment_score"] > 0
    assert "Electronics" in pos["impact_tags"]

    neg = score_financial_sentiment_local("Caída por inflación, crisis y desplome de demanda")
    assert neg["sentiment_label"] == "negative"
    assert neg["sentiment_score"] < 0


@pytest.mark.anyio
async def test_market_sentiment_async_fetch_and_sqlite_cache(
    isolated_settings: Settings, db: MetadataDB
) -> None:
    hits = {"count": 0}

    def _mock_rss(req: httpx.Request) -> httpx.Response:
        hits["count"] += 1
        return httpx.Response(200, content=SAMPLE_RSS, headers={"content-type": "application/xml"})

    router = MultiProviderLLMRouter(isolated_settings, db)
    res1 = await fetch_and_score_market_sentiment(
        query="retail electronics",
        entity_keywords=["Electronics", "Retail"],
        db=db,
        router=router,
        feed_urls=["https://mock.local/rss"],
        lang="en",
        use_cache=True,
        transport=httpx.MockTransport(_mock_rss),
    )
    assert res1["counts"]["total"] == 2
    assert res1["counts"]["positive"] == 1
    assert res1["counts"]["negative"] == 1
    assert hits["count"] == 1

    # Second call hits SQLite cache with zero network requests
    res2 = await fetch_and_score_market_sentiment(
        query="retail electronics",
        entity_keywords=["Electronics"],
        db=db,
        router=router,
        feed_urls=["https://mock.local/rss"],
        lang="en",
        use_cache=True,
        transport=httpx.MockTransport(_mock_rss),
    )
    assert res2["source_mode"] == "sqlite_cache"
    assert hits["count"] == 1


def test_churn_clv_diagnostics_entity_resolution_and_alerts(
    client: TestClient, tmp_path: Path, db: MetadataDB, pipeline: ProjectIngestionPipeline
) -> None:
    proj = client.post("/api/v1/projects", json=ProjectCreate(name="Phase4 Retail", currency="USD").model_dump()).json()
    pid = proj["id"]

    rows = []
    start = date(2025, 1, 1)
    for i in range(80):
        d = start + timedelta(days=i * 2)
        # Active customers buy throughout; churned customers stop in first 30 days
        cust = f"VIP-{i % 8}" if i < 65 else f"Loyal-{i % 4}"
        if i < 16:
            cust = f"Churned-{i % 6}"
        # Near-duplicate product spellings for Entity Resolution
        prod = "iPhone 15 Pro" if i % 5 != 0 else "Iphone 15 pro!"
        # Heavy discount leakage on "Outlet" channel
        is_outlet = i % 4 == 0
        disc = 45.0 if is_outlet else 2.0
        rev = 155.0 if is_outlet else 298.0
        cost = 140.0 if is_outlet else 140.0
        rows.append(
            {
                "Order_ID": f"P4-{i}",
                "Order_Date": d.isoformat(),
                "Product": prod,
                "Category": "Electronics",
                "Quantity": 1,
                "Discount": disc,
                "Cost": cost,
                "Revenue": rev,
                "Customer": cust,
                "Channel": "Outlet" if is_outlet else "Direct",
                "Status": "Completed",
            }
        )
    csv_bytes = pd.DataFrame(rows).to_csv(index=False).encode("utf-8")
    r_up = client.post(
        f"/api/v1/projects/{pid}/upload",
        files=[("files", ("phase4.csv", csv_bytes, "text/csv"))],
    )
    assert r_up.status_code == 200

    # 1. AutoML Churn & CLV
    r_ch = client.post(f"/api/v1/projects/{pid}/ml/churn", json={"horizon_days": 45})
    assert r_ch.status_code == 200
    ch = r_ch.json()
    assert ch["available"] is True
    assert len(ch["customers"]) >= 10
    assert Decimal(ch["summary"]["expected_clv_90d_total_decimal"]) >= Decimal("0")
    for c in ch["customers"]:
        assert 0.0 <= c["churn_probability_pct"] <= 100.0

    # 2. Diagnostics: Entity Resolution + Margin Leakage
    r_diag = client.post(f"/api/v1/projects/{pid}/market/diagnostics", json={})
    assert r_diag.status_code == 200
    diag = r_diag.json()
    assert diag["entity_resolution"]["total_candidates"] >= 1
    assert diag["margin_leakage"]["available"] is True
    assert any(p["segment"] == "Outlet" for p in diag["margin_leakage"]["pockets"])

    # 3. Smart Alerts + Acknowledge lifecycle
    r_al = client.get(f"/api/v1/projects/{pid}/market/alerts?refresh=true")
    assert r_al.status_code == 200
    alerts = r_al.json()["alerts"]
    assert len(alerts) >= 1
    aid = alerts[0]["id"]
    r_ack = client.post(f"/api/v1/projects/{pid}/market/alerts/{aid}/ack")
    assert r_ack.status_code == 200
    assert r_ack.json()["acknowledged"] is True

    # 4. Market sentiment endpoint with custom headlines (zero network)
    r_mkt = client.post(
        f"/api/v1/projects/{pid}/market/sentiment",
        json={
            "query": "Electronics",
            "custom_headlines": [
                {"title": "Electronics demand posts strong record growth", "source": "TestWire"},
                {"title": "Supply risk causes minor margin warning", "source": "TestWire"},
            ],
            "lang": "en",
        },
    )
    assert r_mkt.status_code == 200
    assert r_mkt.json()["counts"]["total"] == 2
