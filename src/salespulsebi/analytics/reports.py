"""Export engines: CSV (formula-safe), styled XLSX workbook, Parquet, and self-contained HTML/PDF report."""

from __future__ import annotations

import html
import io
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Font, PatternFill

from salespulsebi.analytics.query_builder import AnalyticsContext, build_where_clause
from salespulsebi.storage.models import FilterState, ProjectSummary

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _sanitize_csv_cell(val: Any) -> Any:
    if isinstance(val, str) and val.startswith(FORMULA_PREFIXES):
        return f"'{val}"
    return val


def export_csv_bytes(ctx: AnalyticsContext, filters: FilterState) -> bytes:
    where_sql, params = build_where_clause(filters, ctx.columns)
    df = ctx.con.execute(f"SELECT * FROM sales {where_sql}", params).df()
    for col in df.select_dtypes(include=["object", "string"]).columns:
        df[col] = df[col].map(_sanitize_csv_cell)
    return df.to_csv(index=False).encode("utf-8-sig")


def export_parquet_bytes(ctx: AnalyticsContext, filters: FilterState, tmp_dir: Path) -> bytes:
    where_sql, params = build_where_clause(filters, ctx.columns)
    tmp_file = tmp_dir / "export_tmp.parquet"
    escaped = str(tmp_file).replace("'", "''")
    ctx.con.execute(
        f"COPY (SELECT * FROM sales {where_sql}) TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)",
        params,
    )
    data = tmp_file.read_bytes()
    tmp_file.unlink(missing_ok=True)
    return data


def export_xlsx_bytes(
    ctx: AnalyticsContext,
    filters: FilterState,
    project: ProjectSummary,
    overview: dict[str, Any],
) -> bytes:
    where_sql, params = build_where_clause(filters, ctx.columns)
    df = ctx.con.execute(f"SELECT * FROM sales {where_sql} LIMIT 100000", params).df()

    wb = openpyxl.Workbook()
    ws_sum = wb.active
    ws_sum.title = "Executive Summary"
    header_fill = PatternFill(start_color="4F46E5", end_color="4F46E5", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")

    ws_sum.append(["SalesPulseBI Executive Report"])
    ws_sum["A1"].font = Font(bold=True, size=15)
    ws_sum.append(["Project", project.name])
    ws_sum.append(["Currency", project.currency])
    ws_sum.append(["Generated At", datetime.now().strftime("%Y-%m-%d %H:%M")])
    ws_sum.append([])
    ws_sum.append(["Metric", "Value", "Previous", "Delta %"])
    for cell in ws_sum[6]:
        cell.fill = header_fill
        cell.font = header_font

    for k, kpi in overview.get("kpis", {}).items():
        ws_sum.append([k, kpi.get("value"), kpi.get("previous_value"), kpi.get("delta_pct")])

    ws_sum.column_dimensions["A"].width = 28
    ws_sum.column_dimensions["B"].width = 20
    ws_sum.column_dimensions["C"].width = 20
    ws_sum.column_dimensions["D"].width = 14

    ws_data = wb.create_sheet("Cleaned Data")
    headers = list(df.columns)
    ws_data.append(headers)
    for cell in ws_data[1]:
        cell.fill = header_fill
        cell.font = header_font

    for row in df.itertuples(index=False, name=None):
        clean_row = [
            None if (v is None or str(v) == "NaT" or (isinstance(v, float) and v != v)) else _sanitize_csv_cell(v)
            for v in row
        ]
        ws_data.append(clean_row)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_html_report(
    *,
    project: ProjectSummary,
    overview: dict[str, Any],
    insights: list[dict[str, Any]],
    products: dict[str, Any],
    quality: dict[str, Any],
    lang: str = "es",
) -> str:
    """Build a self-contained printable HTML5 executive report (PDF-ready via browser print)."""
    is_es = lang == "es"
    kpis = overview.get("kpis", {})
    cur = html.escape(project.currency)

    def fmt_kpi(key: str, val: Any) -> str:
        if val is None:
            return "—"
        if key in {"revenue", "aov", "gross_profit"}:
            return f"{cur} {float(val):,.2f}"
        if "pct" in key:
            return f"{float(val):.1f}%"
        return f"{float(val):,.0f}"

    labels = {
        "revenue": "Ingresos Netos" if is_es else "Net Revenue",
        "orders": "Pedidos" if is_es else "Orders",
        "aov": "Ticket Promedio" if is_es else "Avg Order Value",
        "units": "Unidades" if is_es else "Units Sold",
        "gross_profit": "Utilidad Bruta" if is_es else "Gross Profit",
        "gross_margin_pct": "Margen Bruto" if is_es else "Gross Margin",
        "unique_customers": "Clientes Únicos" if is_es else "Unique Customers",
        "cancellation_rate_pct": "Cancelaciones" if is_es else "Cancellation Rate",
    }

    kpi_cards = ""
    for k, lbl in labels.items():
        if k not in kpis:
            continue
        item = kpis[k]
        delta = item.get("delta_pct")
        delta_html = ""
        if delta is not None:
            cls = "pos" if delta >= 0 else "neg"
            sign = "+" if delta >= 0 else ""
            delta_html = f'<span class="delta {cls}">{sign}{delta:.1f}%</span>'
        kpi_cards += f"""
        <div class="kpi">
          <div class="kpi-label">{html.escape(lbl)}</div>
          <div class="kpi-val">{fmt_kpi(k, item.get('value'))}</div>
          {delta_html}
        </div>"""

    insight_cards = "".join(
        f"""
        <div class="insight sev-{html.escape(i['severity'])}">
          <strong>{html.escape(i['title'])}</strong>
          <p>{html.escape(i['body'])}</p>
        </div>"""
        for i in insights
    )

    top_prod_rows = ""
    for p in (products.get("products_table") or [])[:15]:
        top_prod_rows += f"""
        <tr>
          <td>{html.escape(str(p.get('product', '')))}</td>
          <td>{html.escape(str(p.get('category', '—')))}</td>
          <td><span class="badge tier-{html.escape(str(p.get('abc_tier', 'C')))}">{html.escape(str(p.get('abc_tier', 'C')))}</span></td>
          <td class="num">{cur} {float(p.get('revenue', 0)):,.2f}</td>
          <td class="num">{float(p.get('share_pct', 0)):.1f}%</td>
        </tr>"""

    title_txt = "Reporte Ejecutivo de Ventas" if is_es else "Executive Sales Intelligence Report"
    return f"""<!DOCTYPE html>
<html lang="{html.escape(lang)}">
<head>
<meta charset="UTF-8">
<title>SalesPulseBI — {html.escape(project.name)}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; color: #0f172a; background: #f8fafc; margin: 0; padding: 32px; line-height: 1.5; }}
  .sheet {{ max-width: 1020px; margin: 0 auto; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 36px; }}
  header {{ display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 2px solid #e2e8f0; padding-bottom: 20px; margin-bottom: 24px; }}
  h1 {{ margin: 0 0 4px 0; font-size: 24px; color: #0f172a; }}
  .brand {{ font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #4f46e5; }}
  .meta {{ text-align: right; font-size: 13px; color: #475569; }}
  h2 {{ font-size: 16px; margin: 28px 0 12px; color: #1e293b; border-left: 4px solid #4f46e5; padding-left: 10px; }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }}
  .kpi {{ border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px; background: #f8fafc; }}
  .kpi-label {{ font-size: 12px; color: #64748b; font-weight: 600; }}
  .kpi-val {{ font-size: 20px; font-weight: 700; margin: 4px 0; font-variant-numeric: tabular-nums; }}
  .delta {{ font-size: 12px; font-weight: 600; padding: 2px 6px; border-radius: 999px; }}
  .delta.pos {{ background: #dcfce7; color: #166534; }}
  .delta.neg {{ background: #fee2e2; color: #991b1b; }}
  .insights {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
  .insight {{ border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px 14px; background: #ffffff; }}
  .insight p {{ margin: 4px 0 0; font-size: 13px; color: #475569; }}
  .sev-positive {{ border-left: 4px solid #10b981; }}
  .sev-warning, .sev-alert {{ border-left: 4px solid #f59e0b; }}
  .sev-info {{ border-left: 4px solid #6366f1; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; }}
  th, td {{ padding: 9px 10px; border-bottom: 1px solid #e2e8f0; text-align: left; }}
  th {{ background: #f1f5f9; font-weight: 600; color: #334155; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .badge {{ padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; }}
  .tier-A {{ background: #dcfce7; color: #166534; }}
  .tier-B {{ background: #e0e7ff; color: #3730a3; }}
  .tier-C {{ background: #f1f5f9; color: #475569; }}
  .print-btn {{ background: #4f46e5; color: #fff; border: 0; padding: 8px 16px; border-radius: 8px; cursor: pointer; font-weight: 600; }}
  @media print {{ body {{ background: #fff; padding: 0; }} .sheet {{ border: 0; padding: 12px; }} .print-btn {{ display: none; }} }}
</style>
</head>
<body>
<div class="sheet">
  <header>
    <div>
      <div class="brand">SalesPulseBI · {html.escape(title_txt)}</div>
      <h1>{html.escape(project.name)}</h1>
      <div style="color:#64748b;font-size:13px;">{html.escape(project.description or '')}</div>
    </div>
    <div class="meta">
      <div><strong>{'Registros' if is_es else 'Rows'}:</strong> {project.row_count:,}</div>
      <div><strong>{'Archivos' if is_es else 'Files'}:</strong> {project.file_count}</div>
      <div><strong>{'Salud de datos' if is_es else 'Health Score'}:</strong> {quality.get('health_score', '—')}/100</div>
      <div style="margin-top:8px;"><button class="print-btn" onclick="window.print()">{'Imprimir / Guardar PDF' if is_es else 'Print / Save PDF'}</button></div>
    </div>
  </header>

  <h2>{'Indicadores Clave (KPIs)' if is_es else 'Key Performance Indicators'}</h2>
  <div class="kpi-grid">{kpi_cards}</div>

  <h2>{'Insights Automáticos' if is_es else 'Automated Insights'}</h2>
  <div class="insights">{insight_cards or ('<p>Sin insights disponibles.</p>' if is_es else '<p>No insights available.</p>')}</div>

  {f'''<h2>{"Top Productos y Clasificación ABC" if is_es else "Top Products & ABC Classification"}</h2>
  <table>
    <thead><tr><th>{"Producto" if is_es else "Product"}</th><th>{"Categoría" if is_es else "Category"}</th><th>ABC</th><th class="num">{"Ingresos" if is_es else "Revenue"}</th><th class="num">%</th></tr></thead>
    <tbody>{top_prod_rows}</tbody>
  </table>''' if top_prod_rows else ''}
</div>
</body>
</html>"""
