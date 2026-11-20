import { api } from "../api.js";
import { mountChart, palette } from "../charts.js";
import { fmtCurrency, fmtNumber, t } from "../i18n.js";
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

  const abcCards = (data.abc_summary || [])
    .map(
      (s) => `
    <div class="kpi-card">
      <div class="kpi-label">Class ${s.tier} (${s.product_count} SKUs · ${s.product_pct}%)</div>
      <div class="kpi-value">${fmtCurrency(s.revenue, proj.currency, lang)}</div>
      <div class="kpi-footer"><span class="pill ${s.tier === "A" ? "pill-emerald" : s.tier === "B" ? "pill-accent" : "pill-muted"}">${s.revenue_pct}% Revenue</span></div>
    </div>`
    )
    .join("");

  container.innerHTML = `
    <div class="grid-3">${abcCards}</div>

    <div class="grid-2">
      <div class="card">
        <div class="card-header"><h3 class="card-title">${t(lang, "pareto_title")}</h3></div>
        <div id="pr-pareto-chart" class="chart-box"></div>
      </div>
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">${t(lang, "matrix_title")}</h3>
          <span class="pill pill-muted">${escapeHtml(data.matrix?.mode || "")}</span>
        </div>
        <div id="pr-matrix-chart" class="chart-box"></div>
      </div>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-header"><h3 class="card-title">${t(lang, "market_basket_title")}</h3></div>
        <div id="pr-basket-wrap" class="table-wrap"></div>
      </div>
      <div class="card">
        <div class="card-header"><h3 class="card-title">${t(lang, "products_table_title")}</h3></div>
        <div class="table-wrap" style="max-height:360px;">
          <table class="data-table">
            <thead><tr><th>Product</th><th>ABC</th><th class="num">Revenue</th><th class="num">Share</th><th class="num">Margin</th></tr></thead>
            <tbody>
              ${(data.products_table || [])
                .slice(0, 40)
                .map(
                  (r) => `
                <tr>
                  <td>${escapeHtml(r.product)}</td>
                  <td><span class="pill ${r.abc_tier === "A" ? "pill-emerald" : r.abc_tier === "B" ? "pill-accent" : "pill-muted"}">${r.abc_tier}</span></td>
                  <td class="num">${fmtCurrency(r.revenue, proj.currency, lang)}</td>
                  <td class="num">${r.share_pct}%</td>
                  <td class="num">${r.margin_pct != null ? `${r.margin_pct}%` : "—"}</td>
                </tr>`
                )
                .join("")}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  `;

  const p = palette();
  const curve = (data.pareto_curve || []).slice(0, 25);
  mountChart(
    container.querySelector("#pr-pareto-chart"),
    {
      xAxis: { type: "category", data: curve.map((r) => r.product), axisLabel: { rotate: 28, width: 70, overflow: "truncate", fontSize: 10.5 } },
      yAxis: [
        { type: "value", splitLine: { lineStyle: { color: p.grid } } },
        { type: "value", max: 100, splitLine: { show: false } },
      ],
      series: [
        { name: "Revenue", type: "bar", data: curve.map((r) => r.revenue) },
        { name: "Cum %", type: "line", yAxisIndex: 1, smooth: true, data: curve.map((r) => r.cum_share_pct), itemStyle: { color: "#10b981" } },
      ],
    },
    (params) => toggleCrossFilter("products", params.name)
  );

  // BCG Scatter Matrix
  const pts = data.matrix?.points || [];
  const qColors = { star: "#10b981", cash_cow: "#6366f1", question_mark: "#f59e0b", dog: "#64748b" };
  mountChart(container.querySelector("#pr-matrix-chart"), {
    tooltip: {
      trigger: "item",
      formatter: (pr) => `${pr.data.name}<br/>Rev: ${fmtCurrency(pr.data.value[0], proj.currency, lang)}<br/>Y: ${pr.data.value[1]}`,
    },
    xAxis: { type: "value", name: "Revenue", splitLine: { lineStyle: { color: p.grid } } },
    yAxis: { type: "value", name: data.matrix?.mode === "margin" ? "Margin %" : "Growth %", splitLine: { lineStyle: { color: p.grid } } },
    series: [
      {
        type: "scatter",
        symbolSize: 13,
        data: pts.map((pt) => ({
          name: pt.product,
          value: [pt.revenue, pt.y_value],
          itemStyle: { color: qColors[pt.quadrant] || "#6366f1" },
        })),
      },
    ],
  });

  // Market Basket Table
  const mb = data.market_basket || [];
  container.querySelector("#pr-basket-wrap").innerHTML = mb.length
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
            <td class="num">${b.confidence_a_to_b_pct}%</td>
            <td class="num"><span class="pill pill-emerald">${b.lift}x</span></td>
          </tr>`
          )
          .join("")}
      </tbody>
    </table>`
    : `<div style="padding:20px;color:var(--text-secondary);">Requires Order ID + multi-item baskets to compute product affinities.</div>`;
}
