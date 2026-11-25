import { api } from "../api.js";
import { mountChart, palette } from "../charts.js";
import { fmtCurrency, fmtNumber, fmtPercent, t } from "../i18n.js";
import { renderManagedDashboard } from "../layout_manager.js";
import { state, toggleCrossFilter } from "../state.js";
import { escapeHtml } from "./hub.js";

export async function renderProducts(container, onGoMapping) {
  const proj = state.activeProject;
  const lang = state.lang;
  const data = await api.analytics(proj.id, "products", state.filters);

  if (!data.available) {
    container.innerHTML = `
      <div class="empty-state">
        <h3>${t(lang, "locked_title")}</h3>
        <p>${t(lang, "locked_desc")}</p>
        <button id="pr-go-map" class="btn btn-primary">${t(lang, "go_mapping")}</button>
      </div>`;
    container.querySelector("#pr-go-map").onclick = onGoMapping;
    return;
  }

  const p = palette();
  const abcCardsHtml = (data.abc_summary || [])
    .map(
      (s) => `
      <div class="kpi-card">
        <div class="kpi-label">Class ${escapeHtml(s.tier)} (${fmtNumber(s.product_count, 0, lang)} SKUs · ${fmtPercent(s.product_pct, 1, lang)})</div>
        <div class="kpi-value">${fmtCurrency(s.revenue, proj.currency, lang)}</div>
        <div class="kpi-footer"><span class="pill ${s.tier === "A" ? "pill-emerald" : s.tier === "B" ? "pill-accent" : "pill-muted"}">${fmtPercent(s.revenue_pct, 1, lang)} ${t(lang, "revenue")}</span></div>
      </div>`
    )
    .join("");

  const panels = [
    {
      id: "pr_abc_summary",
      title: "ABC Summary",
      defaultSpan: 2,
      renderHtml: () => `<div class="grid-3 grid-no-margin">${abcCardsHtml}</div>`,
      mount: () => {},
    },
    {
      id: "pr_pareto",
      title: t(lang, "pareto_title"),
      defaultSpan: 1,
      renderHtml: () => `
        <div class="card">
          <div class="card-header"><h3 class="card-title">${t(lang, "pareto_title")}</h3></div>
          <div id="pr-pareto-chart" class="chart-box"></div>
        </div>`,
      mount: (panelEl) => {
        const curve = (data.pareto_curve || []).slice(0, 25);
        mountChart(
          panelEl.querySelector("#pr-pareto-chart"),
          {
            xAxis: {
              type: "category",
              data: curve.map((r) => r.product),
              axisLabel: { rotate: 28, width: 70, overflow: "truncate", fontSize: 10.5 },
            },
            yAxis: [
              { type: "value", splitLine: { lineStyle: { color: p.grid } } },
              { type: "value", max: 100, splitLine: { show: false } },
            ],
            series: [
              { name: "Revenue", type: "bar", data: curve.map((r) => r.revenue) },
              { name: "Cum %", type: "line", yAxisIndex: 1, smooth: true, data: curve.map((r) => r.cum_share_pct), itemStyle: { color: p.up } },
            ],
          },
          (params) => toggleCrossFilter("products", params.name)
        );
      },
    },
    {
      id: "pr_matrix",
      title: t(lang, "matrix_title"),
      defaultSpan: 1,
      renderHtml: () => `
        <div class="card">
          <div class="card-header">
            <h3 class="card-title">${t(lang, "matrix_title")}</h3>
            <span class="pill pill-muted">${escapeHtml(data.matrix?.mode || "")}</span>
          </div>
          <div id="pr-matrix-chart" class="chart-box"></div>
        </div>`,
      mount: (panelEl) => {
        const pts = data.matrix?.points || [];
        const qColors = { star: p.up, cash_cow: p.accent, question_mark: p.warn, dog: p.muted };
        mountChart(panelEl.querySelector("#pr-matrix-chart"), {
          tooltip: {
            trigger: "item",
            formatter: (pr) =>
              `${escapeHtml(pr.data.name)}<br/>Rev: ${fmtCurrency(pr.data.value[0], proj.currency, lang)}<br/>Y: ${pr.data.value[1]}`,
          },
          xAxis: { type: "value", name: "Revenue", splitLine: { lineStyle: { color: p.grid } } },
          yAxis: {
            type: "value",
            name: data.matrix?.mode === "margin" ? "Margin %" : "Growth %",
            splitLine: { lineStyle: { color: p.grid } },
          },
          series: [
            {
              type: "scatter",
              symbolSize: 13,
              data: pts.map((pt) => ({
                name: pt.product,
                value: [pt.revenue, pt.y_value],
                itemStyle: { color: qColors[pt.quadrant] || p.accent },
              })),
            },
          ],
        });
      },
    },
    {
      id: "pr_market_basket",
      title: t(lang, "market_basket_title"),
      defaultSpan: 1,
      renderHtml: () => {
        const mb = data.market_basket || [];
        const html = mb.length
          ? `
          <table class="data-table">
            <thead><tr><th>Product A</th><th>Product B</th><th class="num">Co-Orders</th><th class="num">Conf %</th><th class="num">Lift</th></tr></thead>
            <tbody>
              ${mb
                .map(
                  (b) => `
                <tr>
                  <td>${escapeHtml(b.product_a)}</td>
                  <td>${escapeHtml(b.product_b)}</td>
                  <td class="num">${fmtNumber(b.co_count, 0, lang)}</td>
                  <td class="num">${fmtPercent(b.confidence_a_to_b_pct, 1, lang)}</td>
                  <td class="num"><span class="pill pill-emerald">${fmtNumber(b.lift, 2, lang)}x</span></td>
                </tr>`
                )
                .join("")}
            </tbody>
          </table>`
          : `<div class="card-subtitle" style="padding:1.25rem;">Requires Order ID + multi-item baskets to compute product affinities.</div>`;
        return `
          <div class="card">
            <div class="card-header"><h3 class="card-title">${t(lang, "market_basket_title")}</h3></div>
            <div class="table-wrap table-wrap-scroll" tabindex="0" role="region" aria-label="${escapeHtml(t(lang, "market_basket_title"))}">${html}</div>
          </div>`;
      },
      mount: () => {},
    },
    {
      id: "pr_catalog_table",
      title: t(lang, "products_table_title"),
      defaultSpan: 1,
      renderHtml: () => `
        <div class="card">
          <div class="card-header"><h3 class="card-title">${t(lang, "products_table_title")}</h3></div>
          <div class="table-wrap table-wrap-scroll" tabindex="0" role="region" aria-label="${escapeHtml(t(lang, "products_table_title"))}">
            <table class="data-table">
              <thead><tr><th>Product</th><th>ABC</th><th class="num">Revenue</th><th class="num">Share</th><th class="num">Margin</th></tr></thead>
              <tbody>
                ${(data.products_table || [])
                  .slice(0, 40)
                  .map(
                    (r) => `
                  <tr class="row-clickable" data-prod="${escapeHtml(r.product)}">
                    <td>${escapeHtml(r.product)}</td>
                    <td><span class="pill ${r.abc_tier === "A" ? "pill-emerald" : r.abc_tier === "B" ? "pill-accent" : "pill-muted"}">${escapeHtml(r.abc_tier)}</span></td>
                    <td class="num">${fmtCurrency(r.revenue, proj.currency, lang)}</td>
                    <td class="num">${fmtPercent(r.share_pct, 1, lang)}</td>
                    <td class="num">${r.margin_pct != null ? fmtPercent(r.margin_pct, 1, lang) : "—"}</td>
                  </tr>`
                  )
                  .join("")}
              </tbody>
            </table>
          </div>
        </div>`,
      mount: (panelEl) => {
        panelEl.querySelectorAll("tbody tr[data-prod]").forEach((tr) =>
          tr.addEventListener("click", () => toggleCrossFilter("products", tr.dataset.prod))
        );
      },
    },
  ];

  renderManagedDashboard({
    container,
    proj,
    tabKey: "products",
    panels,
    onRerender: () => renderProducts(container, onGoMapping),
  });
}
