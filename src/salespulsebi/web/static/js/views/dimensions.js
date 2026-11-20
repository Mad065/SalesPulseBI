import { api } from "../api.js";
import { mountChart, palette } from "../charts.js";
import { fmtCurrency, fmtNumber, t } from "../i18n.js";
import { state, toggleCrossFilter } from "../state.js";
import { escapeHtml } from "./hub.js";

export async function renderDimensions(container) {
  const proj = state.activeProject;
  const lang = state.lang;
  const data = await api.analytics(proj.id, "dimensions", state.filters);
  const dims = data.dimensions || {};
  const cols = data.available_columns || [];

  const dimBlocks = [
    { key: "regions", filterKey: "regions", title: t(lang, "region") },
    { key: "channels", filterKey: "channels", title: t(lang, "channel") },
    { key: "salespersons", filterKey: "salespersons", title: lang === "es" ? "Vendedores" : "Sales Team" },
    { key: "payment_methods", filterKey: "payment_methods", title: lang === "es" ? "Métodos de Pago" : "Payment Methods" },
    { key: "source_folders", filterKey: "source_folders", title: lang === "es" ? "Carpetas de Origen" : "Source Folders" },
    { key: "source_files", filterKey: "source_files", title: lang === "es" ? "Archivos Fuente" : "Source Files" },
  ].filter((b) => dims[b.key]?.length);

  container.innerHTML = `
    <div class="grid-2">
      ${dimBlocks
        .map(
          (b) => `
        <div class="card">
          <div class="card-header"><h3 class="card-title">${escapeHtml(b.title)}</h3></div>
          <div id="dim-chart-${b.key}" class="chart-box-sm"></div>
        </div>`
        )
        .join("")}
      ${
        data.discount_impact?.length
          ? `<div class="card">
               <div class="card-header"><h3 class="card-title">${t(lang, "discount_impact_title")}</h3></div>
               <div id="dim-discount-chart" class="chart-box-sm"></div>
             </div>`
          : ""
      }
    </div>

    <div class="card">
      <div class="card-header" style="flex-wrap:wrap;">
        <h3 class="card-title">✦ ${t(lang, "adhoc_title")}</h3>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
          <select id="adhoc-x" class="input input-sm">
            ${cols.map((c) => `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)}</option>`).join("")}
          </select>
          <select id="adhoc-agg" class="input input-sm">
            <option value="sum">SUM</option>
            <option value="avg">AVG</option>
            <option value="count">COUNT</option>
            <option value="max">MAX</option>
            <option value="min">MIN</option>
          </select>
          <select id="adhoc-y" class="input input-sm">
            ${cols
              .filter((c) => c.is_numeric)
              .map((c) => `<option value="${escapeHtml(c.name)}" ${c.name === "_revenue" ? "selected" : ""}>${escapeHtml(c.name)}</option>`)
              .join("")}
          </select>
          <button id="adhoc-run" class="btn btn-primary btn-sm">${t(lang, "run_query")}</button>
        </div>
      </div>
      <div id="adhoc-chart" class="chart-box"></div>
    </div>
  `;

  const p = palette();
  for (const b of dimBlocks) {
    const rows = [...dims[b.key]].slice(0, 10).reverse();
    mountChart(
      container.querySelector(`#dim-chart-${b.key}`),
      {
        grid: { left: 105, right: 20, top: 18, bottom: 24 },
        xAxis: { type: "value", splitLine: { lineStyle: { color: p.grid } } },
        yAxis: { type: "category", data: rows.map((r) => r.name), axisLabel: { width: 95, overflow: "truncate" } },
        series: [{ type: "bar", data: rows.map((r) => r.revenue), itemStyle: { borderRadius: [0, 5, 5, 0] } }],
      },
      (params) => toggleCrossFilter(b.filterKey, params.name)
    );
  }

  if (data.discount_impact?.length) {
    const di = data.discount_impact;
    mountChart(container.querySelector("#dim-discount-chart"), {
      legend: { textStyle: { color: p.text }, bottom: 0 },
      xAxis: { type: "category", data: di.map((r) => r.tier) },
      yAxis: [
        { type: "value", splitLine: { lineStyle: { color: p.grid } } },
        { type: "value", splitLine: { show: false } },
      ],
      series: [
        { name: "Revenue", type: "bar", data: di.map((r) => r.revenue) },
        { name: "Avg Ticket", type: "line", yAxisIndex: 1, data: di.map((r) => r.avg_ticket), itemStyle: { color: "#f59e0b" } },
      ],
    });
  }

  async function runAdhoc() {
    const x_column = container.querySelector("#adhoc-x").value;
    const aggregation = container.querySelector("#adhoc-agg").value;
    const y_column = container.querySelector("#adhoc-y")?.value || null;
    if (!x_column) return;
    const res = await api.analytics(proj.id, "adhoc", {
      filters: state.filters,
      x_column,
      y_column,
      aggregation,
      limit: 20,
    });
    const rows = res.rows || [];
    mountChart(container.querySelector("#adhoc-chart"), {
      xAxis: { type: "category", data: rows.map((r) => r.label), axisLabel: { rotate: 24, width: 90, overflow: "truncate" } },
      yAxis: { type: "value", splitLine: { lineStyle: { color: p.grid } } },
      series: [{ type: "bar", data: rows.map((r) => r.value), itemStyle: { color: "#06b6d4", borderRadius: [4, 4, 0, 0] } }],
    });
  }

  // Pick a reasonable default X column
  const preferredX = cols.find((c) => ["_category", "_product", "_region", "_source_file"].includes(c.name));
  if (preferredX) container.querySelector("#adhoc-x").value = preferredX.name;
  container.querySelector("#adhoc-run").addEventListener("click", runAdhoc);
  await runAdhoc();
}
