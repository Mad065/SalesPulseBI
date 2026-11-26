"""Async market news ingestion (RSS/Atom) and financial sentiment analysis (local lexicon / FinBERT / LLM)."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import httpx

from salespulsebi.ai.router import MultiProviderLLMRouter
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.parquet_store import sanitize_json

POSITIVE_TERMS: frozenset[str] = frozenset(
    {
        "growth", "surge", "record", "profit", "beat", "rally", "expansion", "strong",
        "upgrade", "gain", "bullish", "boom", "rebound", "outperform", "efficiency",
        "crecimiento", "récord", "utilidades", "ganancia", "expansión", "fuerte",
        "repunte", "alza", "positivo", "demanda", "supera", "inversión", "rentabilidad",
    }
)

NEGATIVE_TERMS: frozenset[str] = frozenset(
    {
        "drop", "decline", "loss", "slump", "recession", "inflation", "tariff", "risk",
        "downgrade", "cut", "layoff", "bearish", "warning", "shortage", "default", "crash",
        "caída", "caida", "pérdida", "perdida", "recesión", "recesion", "inflación", "inflacion",
        "riesgo", "recorte", "desplome", "crisis", "alerta", "escasez", "contracción", "arancel",
    }
)

WORD_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+")


def score_financial_sentiment_local(
    text: str,
    *,
    entity_keywords: list[str] | None = None,
) -> dict[str, Any]:
    """
    Deterministic bilingual (ES/EN) financial sentiment classifier returning score in [-1, 1],
    label ('positive'|'neutral'|'negative'), and matched sector/entity impact tags.
    """
    tokens = [t.lower() for t in WORD_RE.findall(text or "")]
    if not tokens:
        return {"sentiment_label": "neutral", "sentiment_score": 0.0, "impact_tags": []}

    pos = sum(1 for tok in tokens if tok in POSITIVE_TERMS)
    neg = sum(1 for tok in tokens if tok in NEGATIVE_TERMS)
    denom = pos + neg
    if denom == 0:
        score = 0.0
    else:
        score = round((pos - neg) / max(denom, 2), 3)

    if score >= 0.2:
        label = "positive"
    elif score <= -0.2:
        label = "negative"
    else:
        label = "neutral"

    low_text = (text or "").lower()
    tags = [kw for kw in (entity_keywords or []) if kw and kw.lower() in low_text][:5]
    return {"sentiment_label": label, "sentiment_score": score, "impact_tags": tags}


def parse_rss_or_atom_xml(xml_bytes: bytes, source_label: str = "Market RSS") -> list[dict[str, Any]]:
    """Parse RSS 2.0 or Atom XML safely using stdlib ElementTree (zero external dependencies)."""
    items: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    # RSS 2.0 <channel><item>
    for el in root.findall(".//item")[:25]:
        title = (el.findtext("title") or "").strip()
        link = (el.findtext("link") or "").strip()
        pub = (el.findtext("pubDate") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", (el.findtext("description") or "")).strip()
        src_el = el.find("source")
        src = (src_el.text.strip() if src_el is not None and src_el.text else source_label)
        if title:
            items.append(
                {
                    "id": hashlib.sha256(f"{title}:{link}".encode()).hexdigest()[:16],
                    "source": src[:60],
                    "title": title[:260],
                    "link": link[:400],
                    "published_at": pub[:40] or datetime.now(timezone.utc).date().isoformat(),
                    "summary": desc[:320],
                }
            )

    if items:
        return items

    # Atom <entry>
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for el in root.findall(".//atom:entry", ns)[:25] or root.findall(".//entry")[:25]:
        title = (el.findtext("atom:title", namespaces=ns) or el.findtext("title") or "").strip()
        link_el = el.find("atom:link", ns) or el.find("link")
        link = (link_el.attrib.get("href", "") if link_el is not None else "").strip()
        pub = (el.findtext("atom:updated", namespaces=ns) or el.findtext("updated") or "").strip()
        desc = (el.findtext("atom:summary", namespaces=ns) or el.findtext("summary") or "").strip()
        if title:
            items.append(
                {
                    "id": hashlib.sha256(f"{title}:{link}".encode()).hexdigest()[:16],
                    "source": source_label[:60],
                    "title": title[:260],
                    "link": link[:400],
                    "published_at": pub[:40] or datetime.now(timezone.utc).date().isoformat(),
                    "summary": re.sub(r"<[^>]+>", " ", desc)[:320],
                }
            )
    return items


async def fetch_and_score_market_sentiment(
    *,
    query: str,
    entity_keywords: list[str],
    db: MetadataDB,
    router: MultiProviderLLMRouter,
    feed_urls: list[str] | None = None,
    custom_headlines: list[dict[str, str]] | None = None,
    lang: str = "es",
    use_cache: bool = True,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """
    Fetch financial/sector news asynchronously (or score supplied headlines), cache in SQLite,
    and compute aggregate positive/neutral/negative sentiment impact.
    """
    q_clean = (query or "retail economy sales").strip()
    cache_key = hashlib.sha256(f"{q_clean.lower()}:{lang}".encode()).hexdigest()[:20]

    raw_items: list[dict[str, Any]] = []
    source_mode = "live_rss"

    if custom_headlines:
        source_mode = "custom_headlines"
        for idx, h in enumerate(custom_headlines[:30]):
            raw_items.append(
                {
                    "id": f"custom_{idx}",
                    "source": h.get("source", "User Feed"),
                    "title": h.get("title", ""),
                    "link": h.get("link", ""),
                    "published_at": h.get("published_at") or datetime.now(timezone.utc).date().isoformat(),
                    "summary": h.get("summary", ""),
                }
            )
    else:
        cached = db.get_market_news(cache_key) if use_cache else []
        if cached:
            raw_items = cached
            source_mode = "sqlite_cache"
        else:
            urls = feed_urls or [
                f"https://news.google.com/rss/search?q={quote_plus(q_clean)}&hl={'es-419' if lang == 'es' else 'en-US'}&gl={'MX' if lang == 'es' else 'US'}&ceid={'MX:es-419' if lang == 'es' else 'US:en'}"
            ]
            timeout = httpx.Timeout(8.0, connect=4.0)
            async with httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=True) as client:
                for u in urls[:3]:
                    try:
                        resp = await client.get(u, headers={"User-Agent": "SalesPulseBI/1.0"})
                        if resp.status_code == 200:
                            raw_items.extend(parse_rss_or_atom_xml(resp.content, source_label="Market RSS"))
                    except Exception:
                        continue

    # Score every item deterministically (and optionally enrich summary via LLM fast tier)
    scored_items: list[dict[str, Any]] = []
    for it in raw_items[:20]:
        text_blob = f"{it.get('title', '')} {it.get('summary', '')}"
        sc = score_financial_sentiment_local(text_blob, entity_keywords=entity_keywords)
        scored_items.append({**it, **sc})

    if scored_items and source_mode == "live_rss":
        db.replace_market_news(cache_key, scored_items)

    pos_n = sum(1 for x in scored_items if x["sentiment_label"] == "positive")
    neg_n = sum(1 for x in scored_items if x["sentiment_label"] == "negative")
    neu_n = len(scored_items) - pos_n - neg_n
    avg_score = (
        round(sum(float(x["sentiment_score"]) for x in scored_items) / len(scored_items), 3)
        if scored_items
        else 0.0
    )
    overall = "positive" if avg_score >= 0.15 else ("negative" if avg_score <= -0.15 else "neutral")

    # Optional concise LLM executive pulse if enabled
    executive_summary = (
        f"Pulso de mercado ({overall}): {len(scored_items)} titulares analizados ({pos_n} positivos, {neu_n} neutrales, {neg_n} negativos)."
        if lang == "es"
        else f"Market pulse ({overall}): {len(scored_items)} headlines analyzed ({pos_n} positive, {neu_n} neutral, {neg_n} negative)."
    )
    cfg = router.get_config()
    if cfg.is_enabled and scored_items:
        titles_txt = "\n".join(f"- [{it['sentiment_label']}] {it['title']}" for it in scored_items[:10])
        res = await router.complete(
            system_prompt=f"Summarize market impact in 2 concise sentences in {'Spanish' if lang == 'es' else 'English'}.",
            user_prompt=f"Sector/Query: {q_clean}\nHeadlines:\n{titles_txt}",
            tier="fast",
            feature_name="market_sentiment_summary",
            temperature=0.1,
        )
        if res.ok and res.text.strip():
            executive_summary = res.text.strip()[:600]

    return sanitize_json(
        {
            "available": True,
            "query": q_clean,
            "source_mode": source_mode,
            "overall_sentiment": overall,
            "avg_sentiment_score": avg_score,
            "counts": {
                "total": len(scored_items),
                "positive": pos_n,
                "neutral": neu_n,
                "negative": neg_n,
            },
            "executive_summary": executive_summary,
            "items": scored_items,
        }
    )
