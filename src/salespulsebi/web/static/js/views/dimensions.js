import { api } from "../api.js";
import { mountChart, palette } from "../charts.js";
import { colLabel, t } from "../i18n.js";
import { renderManagedDashboard } from "../layout_manager.js";
import { state, toggleCrossFilter } from "../state.js";
import { escapeHtml } from "./hub.js";

export async function renderDimensions(container) {
  const proj = state.activeProject;
  const lang = state.lang;
  const data = await api.analytics(proj.id, "dimensions", state.filters);
  const dims = data.dimensions || {};
  const cols = data.available_columns || [];
  const p = palette();

  const dimBlocks = [
    { key: "regions", filterKey: "regions", title: t(lang, "region") },
    { key: "channels", filterKey: "channels", title: t(lang, "channel") },
    { key: "salespersons", filterKey: "salespersons", title: lang === "es" ? "Vendedores" : "Sales Team" },
    { key: "payment_methods", filterKey: "payment_methods", title: lang === "es" ? "Métodos de Pago" : "Payment Methods" },
    { key: "source_folders", filterKey: "source_folders", title: lang === "es" ? "Carpetas de Origen" : "Source Folders" },
    { key: "source_files", filterKey: "source_files", title: lang === "es" ? "Archivos Fuente" : "Source Files" },
  ].filter((b) => dims[b.key]?.length);

  const panels = dimBlocks.map((b) => ({
    id: `dim_${b.key}`,
    title: b.title,
    defaultSpan: 1,
    renderHtml: () => `
      <div class="card">
        <div class="card-header"><h3 class="card-title">${escapeHtml(b.title)}</h3></div>
        <div id="dim-chart-${b.key}" class="chart-box-sm"></div>
      </div>`,
    mount: (panelEl) => {
      const rows = [...dims[b.key]].slice(0, 10).reverse();
      mountChart(
        panelEl.querySelector(`#dim-chart-${b.key}`),
        {
          grid: { left: 105, right: 20, top: 18, bottom: 24 },
          xAxis: { type: "value", splitLine: { lineStyle: { color: p.grid } } },
          yAxis: {
            type: "category",
            data: rows.map((r) => r.name),
            axisLabel: { width: 95, overflow: "truncate" },
          },
          series: [
            {
              type: "bar",
              data: rows.map((r) => r.revenue),
              itemStyle: { borderRadius: [0, 5, 5, 0] },
            },
          ],
        },
        (params) => toggleCrossFilter(b.filterKey, params.name)
      );
    },
  }));

  if (data.discount_impact?.length) {
    panels.push({
      id: "dim_discount_impact",
      title: t(lang, "discount_impact_title"),
      defaultSpan: 1,
      renderHtml: () => `
        <div class="card">
          <div class="card-header"><h3 class="card-title">${t(lang, "discount_impact_title")}</h3></div>
          <div id="dim-discount-chart" class="chart-box-sm"></div>
        </div>`,
      mount: (panelEl) => {
        const di = data.discount_impact;
        mountChart(panelEl.querySelector("#dim-discount-chart"), {
          legend: { textStyle: { color: p.text }, bottom: 0 },
          xAxis: { type: "category", data: di.map((r) => r.tier) },
          yAxis: [
            { type: "value", splitLine: { lineStyle: { color: p.grid } } },
            { type: "value", splitLine: { show: false } },
          ],
          series: [
            { name: "Revenue", type: "bar", data: di.map((r) => r.revenue) },
            { name: "Avg Ticket", type: "line", yAxisIndex: 1, data: di.map((r) => r.avg_ticket), itemStyle: { color: p.warn } },
          ],
        });
      },
    });
  }

  panels.push({
    id: "dim_adhoc",
    title: t(lang, "adhoc_title"),
    defaultSpan: 2,
    renderHtml: () => `
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">✦ ${t(lang, "adhoc_title")}</h3>
          <div class="card-toolbar">
            <select id="adhoc-x" class="input input-sm" aria-label="X Dimension">
              ${cols
                .map((c) => `<option value="${escapeHtml(c.name)}">${escapeHtml(colLabel(c.name, lang))}</option>`)
                .join("")}
            </select>
            <select id="adhoc-agg" class="input input-sm" aria-label="Aggregation">
              <option value="sum">SUM</option>
              <option value="avg">AVG</option>
              <option value="count">COUNT</option>
              <option value="count_distinct">DISTINCT</option>
              <option value="max">MAX</option>
              <option value="min">MIN</option>
            </select>
            <select id="adhoc-y" class="input input-sm" aria-label="Y Metric">
              ${cols
                .filter((c) => c.is_numeric)
                .map(
                  (c) =>
                    `<option value="${escapeHtml(c.name)}" ${c.name === "_revenue" ? "selected" : ""}>${escapeHtml(colLabel(c.name, lang))}</option>`
                )
                .join("")}
            </select>
            <button type="button" id="adhoc-run" class="btn btn-primary btn-sm">${t(lang, "run_query")}</button>
          </div>
        </div>
        <div id="adhoc-chart" class="chart-box"></div>
      </div>`,
    mount: (panelEl) => {
      async function runAdhoc() {
        const x_column = panelEl.querySelector("#adhoc-x")?.value;
        const aggregation = panelEl.querySelector("#adhoc-agg")?.value;
        const y_column = panelEl.querySelector("#adhoc-y")?.value || null;
        if (!x_column) return;
        const res = await api.analytics(proj.id, "adhoc", {
          filters: state.filters,
          x_column,
          y_column,
          aggregation,
          limit: 20,
        });
        const rows = res.rows || [];
        mountChart(panelEl.querySelector("#adhoc-chart"), {
          xAxis: {
            type: "category",
            data: rows.map((r) => r.label),
            axisLabel: { rotate: 24, width: 90, overflow: "truncate" },
          },
          yAxis: { type: "value", splitLine: { lineStyle: { color: p.grid } } },
          series: [
            {
              type: "bar",
              data: rows.map((r) => r.value),
              itemStyle: { color: p.info, borderRadius: [4, 4, 0, 0] },
            },
          ],
        });
      }

      const preferredX = cols.find((c) =>
        ["_category", "_product", "_region", "_source_file"].includes(c.name)
      );
      if (preferredX && panelEl.querySelector("#adhoc-x")) {
        panelEl.querySelector("#adhoc-x").value = preferredX.name;
      }
      panelEl.querySelector("#adhoc-run")?.addEventListener("click", runAdhoc);
      runAdhoc();
    },
  });

  renderManagedDashboard({
    container,
    proj,
    tabKey: "dimensions",
    panels,
    onRerender: () => renderDimensions(container),
  });
}
