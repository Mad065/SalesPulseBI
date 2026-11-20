import { api } from "../api.js";
import { mountChart, mountSparkline, palette } from "../charts.js";
import { fmtCurrency, fmtNumber, t } from "../i18n.js";
import { state, toggleCrossFilter } from "../state.js";
import { escapeHtml } from "./hub.js";

function formatKpiVal(key, val, currency, lang) {
  if (val === null || val === undefined) return "—";
  if (["revenue", "aov", "gross_profit"].includes(key)) return fmtCurrency(val, currency, lang);
  if (key.includes("pct")) return `${fmtNumber(val, 1, lang)}%`;
  return fmtNumber(val, 0, lang);
}

export async function renderOverview(container) {
  const proj = state.activeProject;
  const lang = state.lang;
  const [ov, insRes] = await Promise.all([
    api.analytics(proj.id, "overview", state.filters),
    api.analytics(proj.id, "insights", state.filters),
  ]);

  const kpis = ov.kpis || {};
  const insights = insRes.insights || [];

  const kpiOrder = [
    "revenue",
    "orders",
    "aov",
    "units",
    "gross_profit",
    "gross_margin_pct",
    "unique_customers",
    "discount_rate_pct",
    "cancellation_rate_pct",
    "rows_count",
  ];

  const kpiHtml = kpiOrder
    .filter((k) => k in kpis)
    .map((k) => {
      const item = kpis[k];
      const delta = item.delta_pct;
      let deltaBadge = `<span class="delta-badge delta-neu">—</span>`;
      if (delta !== null && delta !== undefined) {
        const isGood = k === "cancellation_rate_pct" ? delta <= 0 : delta >= 0;
        const sign = delta >= 0 ? "+" : "";
        deltaBadge = `<span class="delta-badge ${isGood ? "delta-pos" : "delta-neg"}">${sign}${delta.toFixed(1)}%</span>`;
      }
      return `
      <div class="kpi-card">
        <div class="kpi-label">${t(lang, k)}</div>
        <div class="kpi-value">${formatKpiVal(k, item.value, proj.currency, lang)}</div>
        <div class="kpi-footer">
          ${deltaBadge}
          <div class="sparkline-box" id="spark-${k}"></div>
        </div>
      </div>`;
    })
    .join("");

  const insHtml = insights.length
    ? `
      <div class="card" style="margin-bottom:16px;">
        <div class="card-header">
          <h3 class="card-title">✦ ${t(lang, "insights_title")}</h3>
          <span class="pill pill-accent">${insights.length}</span>
        </div>
        <div class="insights-grid" style="margin-bottom:0;">
          ${insights
            .map(
              (ins) => `
            <div class="insight-card sev-${escapeHtml(ins.severity)}">
              <div class="insight-title">${escapeHtml(ins.title)}</div>
              <div class="insight-body">${escapeHtml(ins.body)}</div>
            </div>`
            )
            .join("")}
        </div>
      </div>`
    : "";

  container.innerHTML = `
    <div class="grid-kpis">${kpiHtml}</div>
    ${insHtml}
    <div class="grid-2">
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">${t(lang, "rev_orders_trend")}</h3>
        </div>
        <div id="ov-chart-trend" class="chart-box"></div>
      </div>
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">${t(lang, "top_categories")}</h3>
        </div>
        <div id="ov-chart-cats" class="chart-box"></div>
      </div>
    </div>
    <div class="grid-2">
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">${t(lang, "top_products")}</h3>
        </div>
        <div id="ov-chart-prods" class="chart-box"></div>
      </div>
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">${t(lang, "ticket_dist")}</h3>
          <span class="card-subtitle" id="ov-pct-summary"></span>
        </div>
        <div id="ov-chart-ticket" class="chart-box"></div>
      </div>
    </div>
  `;

  // Sparklines
  for (const k of kpiOrder) {
    if (kpis[k]?.sparkline?.length >= 2) {
      const d = kpis[k].delta_pct;
      mountSparkline(container.querySelector(`#spark-${k}`), kpis[k].sparkline, d === null || d >= 0);
    }
  }

  const p = palette();

  // Trend chart
  const trend = ov.trend || [];
  if (trend.length) {
    mountChart(container.querySelector("#ov-chart-trend"), {
      legend: { data: [t(lang, "revenue"), t(lang, "orders")], textStyle: { color: p.text }, bottom: 0 },
      xAxis: { type: "category", data: trend.map((r) => r.period), axisLine: { lineStyle: { color: p.grid } } },
      yAxis: [
        { type: "value", splitLine: { lineStyle: { color: p.grid } } },
        { type: "value", splitLine: { show: false } },
      ],
      series: [
        {
          name: t(lang, "revenue"),
          type: "line",
          smooth: true,
          data: trend.map((r) => r.revenue),
          areaStyle: { opacity: 0.14 },
        },
        {
          name: t(lang, "orders"),
          type: "bar",
          yAxisIndex: 1,
          itemStyle: { opacity: 0.55 },
          data: trend.map((r) => r.orders),
        },
      ],
    });
  } else {
    container.querySelector("#ov-chart-trend").innerHTML = `<div class="empty-state">No date column mapped</div>`;
  }

  // Categories Donut
  const cats = ov.top_categories || [];
  if (cats.length) {
    mountChart(
      container.querySelector("#ov-chart-cats"),
      {
        tooltip: { trigger: "item" },
        series: [
          {
            type: "pie",
            radius: ["45%", "74%"],
            itemStyle: { borderRadius: 6, borderColor: p.bg, borderWidth: 2 },
            label: { color: p.text, formatter: "{b}: {d}%" },
            data: cats.map((c) => ({ name: c.name, value: c.revenue })),
          },
        ],
      },
      (params) => toggleCrossFilter("categories", params.name)
    );
  } else {
    container.querySelector("#ov-chart-cats").innerHTML = `<div class="empty-state">No category column mapped</div>`;
  }

  // Top products horizontal bar
  const prods = [...(ov.top_products || [])].reverse();
  if (prods.length) {
    mountChart(
      container.querySelector("#ov-chart-prods"),
      {
        grid: { left: 120, right: 24, top: 24, bottom: 24 },
        xAxis: { type: "value", splitLine: { lineStyle: { color: p.grid } } },
        yAxis: { type: "category", data: prods.map((x) => x.name), axisLabel: { width: 105, overflow: "truncate" } },
        series: [{ type: "bar", data: prods.map((x) => x.revenue), itemStyle: { borderRadius: [0, 5, 5, 0] } }],
      },
      (params) => toggleCrossFilter("products", params.name)
    );
  } else {
    container.querySelector("#ov-chart-prods").innerHTML = `<div class="empty-state">No product column mapped</div>`;
  }

  // Ticket histogram
  const td = ov.ticket_distribution || {};
  const pct = td.percentiles || {};
  if (pct.p50 != null) {
    container.querySelector("#ov-pct-summary").textContent =
      `P50: ${fmtCurrency(pct.p50, proj.currency, lang)} · P90: ${fmtCurrency(pct.p90, proj.currency, lang)}`;
  }
  if (td.histogram?.length) {
    mountChart(container.querySelector("#ov-chart-ticket"), {
      xAxis: { type: "category", data: td.histogram.map((b) => b.label), axisLabel: { rotate: 28, fontSize: 10.5 } },
      yAxis: { type: "value", splitLine: { lineStyle: { color: p.grid } } },
      series: [{ type: "bar", data: td.histogram.map((b) => b.count), itemStyle: { color: "#10b981", borderRadius: [4, 4, 0, 0] } }],
    });
  } else {
    container.querySelector("#ov-chart-ticket").innerHTML = `<div class="empty-state">No ticket data</div>`;
  }

  return ov;
}
