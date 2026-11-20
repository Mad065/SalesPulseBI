import { api } from "../api.js";
import { mountChart, palette } from "../charts.js";
import { fmtCurrency, fmtNumber, t } from "../i18n.js";
import { state, toggleCrossFilter } from "../state.js";
import { escapeHtml } from "./hub.js";

export async function renderCustomers(container, onGoMapping) {
  const proj = state.activeProject;
  const lang = state.lang;
  const data = await api.analytics(proj.id, "customers", state.filters);

  if (!data.available) {
    container.innerHTML = `
      <div class="empty-state">
        <h3>${t(lang, "locked_title")}</h3>
        <p>${t(lang, "locked_desc")}</p>
        <button id="cu-go-map" class="btn btn-primary">${t(lang, "go_mapping")}</button>
      </div>`;
    container.querySelector("#cu-go-map").onclick = onGoMapping;
    return;
  }

  const s = data.summary || {};
  const conc = s.concentration || {};

  container.innerHTML = `
    <div class="grid-kpis">
      <div class="kpi-card">
        <div class="kpi-label">${t(lang, "unique_customers")}</div>
        <div class="kpi-value">${fmtNumber(s.total_customers, 0, lang)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">${lang === "es" ? "Tasa Recompra" : "Repeat Rate"}</div>
        <div class="kpi-value">${s.repeat_rate_pct}%</div>
        <div class="kpi-footer"><span class="pill pill-emerald">${fmtNumber(s.repeat_customers, 0, lang)} repeat</span></div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">${lang === "es" ? "LTV Promedio" : "Avg Revenue / Customer"}</div>
        <div class="kpi-value">${fmtCurrency(s.avg_revenue_per_customer, proj.currency, lang)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">${lang === "es" ? "Concentración Top 10%" : "Top 10% Concentration"}</div>
        <div class="kpi-value">${conc.top_10_pct_share}%</div>
        <div class="kpi-footer"><span class="card-subtitle">Top 1%: ${conc.top_1_pct_share}% · Top 5%: ${conc.top_5_pct_share}%</span></div>
      </div>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-header"><h3 class="card-title">${t(lang, "rfm_title")}</h3></div>
        <div id="cu-rfm-chart" class="chart-box"></div>
      </div>
      <div class="card">
        <div class="card-header"><h3 class="card-title">${t(lang, "nvr_title")}</h3></div>
        <div id="cu-nvr-chart" class="chart-box"></div>
      </div>
    </div>

    <div class="card" style="margin-bottom:16px;">
      <div class="card-header"><h3 class="card-title">${t(lang, "cohorts_title")}</h3></div>
      <div id="cu-cohorts-chart" class="chart-box-tall"></div>
    </div>

    <div class="card">
      <div class="card-header">
        <h3 class="card-title">${t(lang, "customers_table_title")}</h3>
        <select id="cu-seg-filter" class="input input-sm">
          <option value="">${t(lang, "all")}</option>
          ${(data.rfm_segments || []).map((seg) => `<option value="${escapeHtml(seg.segment)}">${escapeHtml(seg.segment)} (${seg.customers})</option>`).join("")}
        </select>
      </div>
      <div id="cu-table-wrap" class="table-wrap" style="max-height:360px;"></div>
    </div>
  `;

  const p = palette();

  // RFM Segments Bar
  const segs = data.rfm_segments || [];
  mountChart(container.querySelector("#cu-rfm-chart"), {
    xAxis: { type: "category", data: segs.map((x) => x.segment), axisLabel: { rotate: 22 } },
    yAxis: [
      { type: "value", splitLine: { lineStyle: { color: p.grid } } },
      { type: "value", splitLine: { show: false } },
    ],
    series: [
      { name: "Revenue", type: "bar", data: segs.map((x) => x.revenue) },
      { name: "Customers", type: "line", yAxisIndex: 1, data: segs.map((x) => x.customers), itemStyle: { color: "#10b981" } },
    ],
  });

  // New vs Returning
  const nvr = data.new_vs_returning || [];
  if (nvr.length) {
    mountChart(container.querySelector("#cu-nvr-chart"), {
      legend: { data: ["New", "Returning"], textStyle: { color: p.text }, bottom: 0 },
      xAxis: { type: "category", data: nvr.map((r) => r.period) },
      yAxis: { type: "value", splitLine: { lineStyle: { color: p.grid } } },
      series: [
        { name: "New", type: "bar", stack: "rev", data: nvr.map((r) => r.new_revenue), itemStyle: { color: "#6366f1" } },
        { name: "Returning", type: "bar", stack: "rev", data: nvr.map((r) => r.returning_revenue), itemStyle: { color: "#10b981" } },
      ],
    });
  } else {
    container.querySelector("#cu-nvr-chart").innerHTML = `<div class="empty-state">Requires date column</div>`;
  }

  // Cohort Heatmap
  const cohortRows = data.cohorts?.rows || [];
  if (cohortRows.length) {
    const maxOff = data.cohorts.max_offset || 0;
    const xCols = Array.from({ length: maxOff + 1 }, (_, i) => `M+${i}`);
    const yCohorts = cohortRows.map((r) => `${r.cohort} (${r.size})`);
    const heatData = [];
    cohortRows.forEach((row, rIdx) => {
      (row.retention || []).forEach((val, cIdx) => {
        if (val !== null && val !== undefined) heatData.push([cIdx, rIdx, val]);
      });
    });
    mountChart(container.querySelector("#cu-cohorts-chart"), {
      tooltip: { trigger: "item", formatter: (pr) => `${yCohorts[pr.value[1]]} · ${xCols[pr.value[0]]}: <b>${pr.value[2]}%</b>` },
      grid: { left: 110, right: 20, top: 24, bottom: 48 },
      xAxis: { type: "category", data: xCols },
      yAxis: { type: "category", data: yCohorts },
      visualMap: {
        min: 0,
        max: 100,
        orient: "horizontal",
        left: "center",
        bottom: 0,
        inRange: { color: ["#1e293b", "#4f46e5", "#10b981"] },
        textStyle: { color: p.text },
      },
      series: [
        {
          type: "heatmap",
          data: heatData,
          label: { show: cohortRows.length <= 12 && maxOff <= 11, formatter: "{@[2]}%", fontSize: 10 },
        },
      ],
    });
  } else {
    container.querySelector("#cu-cohorts-chart").innerHTML = `<div class="empty-state">Requires date + repeat customers</div>`;
  }

  function drawCustomerTable(segFilter = "") {
    const rows = (data.top_customers || []).filter((c) => !segFilter || c.segment === segFilter);
    container.querySelector("#cu-table-wrap").innerHTML = `
      <table class="data-table">
        <thead><tr><th>Customer</th><th>Segment</th><th class="num">Recency (d)</th><th class="num">Freq</th><th class="num">Monetary</th><th class="num">RFM</th></tr></thead>
        <tbody>
          ${rows
            .map(
              (c) => `
            <tr style="cursor:pointer;" data-cust="${escapeHtml(c.customer)}">
              <td>${escapeHtml(c.customer)}</td>
              <td><span class="pill ${c.segment === "Champions" ? "pill-emerald" : c.segment === "At Risk" ? "pill-rose" : "pill-accent"}">${escapeHtml(c.segment)}</span></td>
              <td class="num">${c.recency_days}</td>
              <td class="num">${c.frequency}</td>
              <td class="num">${fmtCurrency(c.monetary, proj.currency, lang)}</td>
              <td class="num">${c.r_score}-${c.f_score}-${c.m_score}</td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
    container.querySelectorAll("#cu-table-wrap tbody tr").forEach((tr) =>
      tr.addEventListener("click", () => toggleCrossFilter("customers", tr.dataset.cust))
    );
  }

  drawCustomerTable();
  container.querySelector("#cu-seg-filter").addEventListener("change", (e) => drawCustomerTable(e.target.value));
}
