"""Deterministic bilingual (ES/EN) narrative insights generator."""

from __future__ import annotations

from typing import Any

from salespulsebi.storage.parquet_store import sanitize_json

DOW_NAMES = {
    "es": ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}


def generate_insights(
    *,
    overview: dict[str, Any],
    timeseries: dict[str, Any] | None = None,
    products: dict[str, Any] | None = None,
    customers: dict[str, Any] | None = None,
    quality: dict[str, Any] | None = None,
    currency: str = "USD",
    lang: str = "es",
) -> list[dict[str, Any]]:
    """Generate prioritized, metric-backed executive insight cards in ES or EN."""
    is_es = lang == "es"
    insights: list[dict[str, Any]] = []
    kpis = overview.get("kpis", {})

    # 1. Revenue momentum vs previous period
    rev_kpi = kpis.get("revenue")
    if rev_kpi and rev_kpi.get("delta_pct") is not None:
        delta = float(rev_kpi["delta_pct"])
        sev = "positive" if delta >= 5 else ("warning" if delta <= -5 else "info")
        if is_es:
            title = f"Ingresos {'crecieron' if delta >= 0 else 'cayeron'} {abs(delta):.1f}% vs. periodo anterior"
            body = f"El ingreso neto del periodo alcanzó {currency} {rev_kpi['value']:,.2f} frente a {currency} {rev_kpi['previous_value']:,.2f}."
        else:
            title = f"Revenue {'grew' if delta >= 0 else 'declined'} {abs(delta):.1f}% vs. previous period"
            body = f"Net period revenue reached {currency} {rev_kpi['value']:,.2f} compared to {currency} {rev_kpi['previous_value']:,.2f}."
        insights.append({"id": "rev_delta", "category": "trend", "severity": sev, "title": title, "body": body})

    # 2. Pareto ABC concentration
    if products and products.get("available"):
        abc = {row["tier"]: row for row in products.get("abc_summary", [])}
        tier_a = abc.get("A")
        if tier_a and products.get("total_products", 0) >= 3:
            if is_es:
                title = f"Regla 80/20: {tier_a['product_count']} productos ({tier_a['product_pct']}%) generan el {tier_a['revenue_pct']}% del ingreso"
                body = "Concentrar stock, visibilidad y promociones en los SKUs Clase A protege la mayor parte de la facturación."
            else:
                title = f"80/20 Pareto: {tier_a['product_count']} products ({tier_a['product_pct']}%) drive {tier_a['revenue_pct']}% of revenue"
                body = "Prioritizing inventory availability and merchandising on Class A SKUs safeguards your core revenue."
            insights.append({"id": "pareto", "category": "pareto", "severity": "info", "title": title, "body": body})

        basket = products.get("market_basket", [])
        if basket:
            top_pair = basket[0]
            if is_es:
                title = f"Oportunidad Cross-Sell: '{top_pair['product_a']}' + '{top_pair['product_b']}'"
                body = f"Aparecen juntos en {top_pair['co_count']} pedidos (Lift {top_pair['lift']}x, confianza {top_pair['confidence_a_to_b_pct']}%)."
            else:
                title = f"Cross-Sell Opportunity: '{top_pair['product_a']}' + '{top_pair['product_b']}'"
                body = f"Co-purchased in {top_pair['co_count']} orders (Lift {top_pair['lift']}x, confidence {top_pair['confidence_a_to_b_pct']}%)."
            insights.append({"id": "basket", "category": "pareto", "severity": "positive", "title": title, "body": body})

    # 3. Best & weakest day of week + Anomalies
    if timeseries and timeseries.get("available"):
        seas = timeseries.get("seasonality", [])
        if seas:
            dow_totals: dict[int, float] = {}
            for row in seas:
                d = int(row["dow"])
                dow_totals[d] = dow_totals.get(d, 0.0) + float(row["revenue"])
            if len(dow_totals) >= 3:
                best_d = max(dow_totals, key=dow_totals.get)  # type: ignore[arg-type]
                worst_d = min(dow_totals, key=dow_totals.get)  # type: ignore[arg-type]
                names = DOW_NAMES["es" if is_es else "en"]
                ratio = dow_totals[best_d] / max(dow_totals[worst_d], 1.0)
                if is_es:
                    title = f"Día pico: {names[best_d]} ({ratio:.1f}x más ventas que {names[worst_d]})"
                    body = f"La estacionalidad semanal muestra mayor tracción comercial los {names[best_d]}."
                else:
                    title = f"Peak Day: {names[best_d]} ({ratio:.1f}x higher revenue than {names[worst_d]})"
                    body = f"Weekly seasonality peaks on {names[best_d]}, making it ideal for key campaigns."
                insights.append({"id": "seasonality", "category": "seasonality", "severity": "info", "title": title, "body": body})

        anomalies = timeseries.get("anomalies", [])
        if anomalies:
            top_a = anomalies[0]
            driver_txt = (
                f" ({'impulsado por' if is_es else 'driven by'} {top_a['root_cause']})"
                if top_a.get("root_cause")
                else ""
            )
            if is_es:
                kind = "Pico inusual" if top_a["direction"] == "spike" else "Caída inusual"
                title = f"{len(anomalies)} anomalías detectadas — {kind} el {top_a['date']}"
                body = f"Se registró {currency} {top_a['revenue']:,.2f} frente a una mediana esperada de {currency} {top_a['expected']:,.2f}{driver_txt}."
            else:
                kind = "Unusual spike" if top_a["direction"] == "spike" else "Unusual drop"
                title = f"{len(anomalies)} anomalies detected — {kind} on {top_a['date']}"
                body = f"Observed {currency} {top_a['revenue']:,.2f} vs. expected baseline of {currency} {top_a['expected']:,.2f}{driver_txt}."
            insights.append({"id": "anomaly", "category": "anomaly", "severity": "warning", "title": title, "body": body})

    # 4. Customer concentration & At-Risk RFM
    if customers and customers.get("available"):
        conc = customers["summary"]["concentration"]
        top10 = conc.get("top_10_pct_share", 0)
        if top10 >= 40:
            if is_es:
                title = f"Alta concentración de cartera: el 10% superior aporta el {top10}% del ingreso"
                body = "Implementar programas de fidelización VIP reduce el riesgo ante la pérdida de cuentas clave."
            else:
                title = f"Revenue concentration: top 10% customers contribute {top10}% of revenue"
                body = "Dedicated VIP retention workflows can mitigate revenue exposure to top accounts."
            insights.append({"id": "cust_conc", "category": "customers", "severity": "warning", "title": title, "body": body})

        at_risk = next((s for s in customers.get("rfm_segments", []) if s["segment"] == "At Risk"), None)
        if at_risk and at_risk["customers"] > 0:
            if is_es:
                title = f"{at_risk['customers']} clientes valiosos están 'En Riesgo' ({currency} {at_risk['revenue']:,.0f} históricos)"
                body = "Compraron frecuentemente en el pasado pero llevan tiempo inactivos; candidatos ideales para campaña de reactivación."
            else:
                title = f"{at_risk['customers']} high-value customers are 'At Risk' ({currency} {at_risk['revenue']:,.0f} historical)"
                body = "Previously frequent buyers with high recency gap; prime targets for win-back campaigns."
            insights.append({"id": "rfm_risk", "category": "customers", "severity": "alert", "title": title, "body": body})

    # 5. Margin & Cancellations
    margin_kpi = kpis.get("gross_margin_pct")
    if margin_kpi:
        m = float(margin_kpi["value"])
        if is_es:
            title = f"Margen bruto promedio de {m:.1f}%"
            body = f"Utilidad bruta acumulada de {currency} {kpis['gross_profit']['value']:,.2f}."
        else:
            title = f"Average gross margin is {m:.1f}%"
            body = f"Total gross profit stands at {currency} {kpis['gross_profit']['value']:,.2f}."
        insights.append(
            {
                "id": "margin",
                "category": "margin",
                "severity": "positive" if m >= 30 else "warning",
                "title": title,
                "body": body,
            }
        )

    canc_kpi = kpis.get("cancellation_rate_pct")
    if canc_kpi and float(canc_kpi["value"]) >= 3.0:
        c_val = float(canc_kpi["value"])
        if is_es:
            title = f"Tasa de cancelaciones/devoluciones del {c_val:.1f}%"
            body = "Estos registros se excluyen por defecto del ingreso neto (puedes incluirlos desde los filtros)."
        else:
            title = f"Cancellation/return rate at {c_val:.1f}%"
            body = "Excluded from net revenue by default (toggleable in the global filter bar)."
        insights.append({"id": "canc", "category": "cancellations", "severity": "warning", "title": title, "body": body})

    # 6. Data health
    if quality:
        hs = float(quality.get("health_score") or 100.0)
        if hs < 85:
            if is_es:
                title = f"Salud de datos en {hs:.0f}/100 — revisa Data Studio"
                body = "Se detectaron valores nulos, negativos o diferencias entre archivos que conviene auditar."
            else:
                title = f"Data Health Score is {hs:.0f}/100 — review Data Studio"
                body = "Nulls, negative amounts, or cross-file schema differences were flagged."
            insights.append({"id": "quality", "category": "quality", "severity": "warning", "title": title, "body": body})

    return sanitize_json(insights[:8])
