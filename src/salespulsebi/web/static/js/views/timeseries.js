import { api } from "../api.js";
import { mountChart, palette } from "../charts.js";
import { fmtCurrency, t } from "../i18n.js";
import { state } from "../state.js";
import { escapeHtml } from "./hub.js";

export async function renderTimeseries(container, onGoMapping) {
  const proj = state.activeProject;
  const lang = state.lang;
  const data = await api.analytics(proj.id, "timeseries", state.filters);

  if (!data.available) {
    container.innerHTML = `
      <div class="empty-state">
        <h3>${t(lang, "locked_title")}</h3>
        <p>${t(lang, "locked_desc")}</p>
        <button id="ts-go-map" class="btn btn-primary">${t(lang, "go_mapping")}</button>
      </div>`;
    container.querySelector("#ts-go-map").onclick = onGoMapping;
    return;
  }

  container.innerHTML = `
    <div class="card" style="margin-bottom:16px;">
      <div class="card-header" style="flex-wrap:wrap;">
        <h3 class="card-title">${t(lang, "forecast_title")}</h3>
        <div style="display:flex;gap:8px;align-items:center;">
          <label style="font-size:12px;color:var(--text-secondary);">${t(lang, "granularity")}</label>
          <select id="ts-granularity" class="input input-sm">
            ${["day", "week", "month", "quarter", "year"]
              .map((g) => `<option value="${g}" ${state.filters.granularity === g ? "selected" : ""}>${t(lang, g)}</option>`)
              .join("")}
          </select>
          <label style="font-size:12px;color:var(--text-secondary);">${t(lang, "forecast_horizon")}</label>
          <select id="ts-horizon" class="input input-sm">
            ${[14, 30, 60, 90]
              .map((h) => `<option value="${h}" ${state.filters.forecast_horizon === h ? "selected" : ""}>${h}d</option>`)
              .join("")}
          </select>
        </div>
      </div>
      <div id="ts-main-chart" class="chart-box-tall"></div>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-header"><h3 class="card-title">${t(lang, "yoy_title")}</h3></div>
        <div id="ts-yoy-chart" class="chart-box"></div>
      </div>
      <div class="card">
        <div class="card-header"><h3 class="card-title">${t(lang, "seasonality_title")}</h3></div>
        <div id="ts-heat-chart" class="chart-box"></div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <h3 class="card-title">${t(lang, "anomalies_title")}</h3>
        <span class="pill pill-amber">${(data.anomalies || []).length}</span>
      </div>
      <div id="ts-anomalies-wrap" class="table-wrap"></div>
    </div>
  `;

  container.querySelector("#ts-granularity").addEventListener("change", (e) => {
    state.filters.granularity = e.target.value;
    renderTimeseries(container, onGoMapping);
  });
  container.querySelector("#ts-horizon").addEventListener("change", (e) => {
    state.filters.forecast_horizon = Number(e.target.value);
    renderTimeseries(container, onGoMapping);
  });

  const p = palette();
  const series = data.series || [];
  const forecast = data.forecast || [];
  const allPeriods = [...series.map((r) => r.period), ...forecast.map((f) => f.period)];

  const actualData = [...series.map((r) => r.revenue), ...forecast.map(() => null)];
  const maData = [...series.map((r) => r.moving_avg), ...forecast.map(() => null)];
  const fcData = [...series.map((_, idx) => (idx === series.length - 1 ? series[idx].revenue : null)), ...forecast.map((f) => f.forecast)];
  const upperBand = [...series.map(() => null), ...forecast.map((f) => f.upper_95)];
  const lowerBand = [...series.map(() => null), ...forecast.map((f) => f.lower_95)];

  mountChart(container.querySelector("#ts-main-chart"), {
    legend: { textStyle: { color: p.text }, bottom: 0 },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 26 }],
    grid: { left: 56, right: 22, top: 28, bottom: 68, containLabel: true },
    xAxis: { type: "category", data: allPeriods },
    yAxis: { type: "value", splitLine: { lineStyle: { color: p.grid } } },
    series: [
      { name: "Actual", type: "line", data: actualData, smooth: true, lineStyle: { width: 2.5 } },
      { name: "Moving Avg", type: "line", data: maData, smooth: true, symbol: "none", lineStyle: { width: 1.5, type: "dashed" } },
      { name: "Forecast", type: "line", data: fcData, smooth: true, lineStyle: { width: 2.5, type: "dotted", color: "#10b981" }, itemStyle: { color: "#10b981" } },
      { name: "Upper 95%", type: "line", data: upperBand, symbol: "none", lineStyle: { opacity: 0.25, type: "dashed", color: "#10b981" } },
      { name: "Lower 95%", type: "line", data: lowerBand, symbol: "none", lineStyle: { opacity: 0.25, type: "dashed", color: "#10b981" } },
    ],
  });

  // YoY Cumulative
  const yoy = data.yoy_cumulative || {};
  const years = Object.keys(yoy);
  if (years.length) {
    mountChart(container.querySelector("#ts-yoy-chart"), {
      legend: { data: years, textStyle: { color: p.text }, bottom: 0 },
      xAxis: { type: "category", data: ["M1","M2","M3","M4","M5","M6","M7","M8","M9","M10","M11","M12"] },
      yAxis: { type: "value", splitLine: { lineStyle: { color: p.grid } } },
      series: years.map((yr) => ({ name: yr, type: "line", smooth: true, data: yoy[yr] })),
    });
  }

  // Seasonality Heatmap
  const seas = data.seasonality || [];
  const dows = t(lang, "dow_names");
  const months = ["1","2","3","4","5","6","7","8","9","10","11","12"];
  const maxS = Math.max(...seas.map((s) => s.revenue), 1);
  mountChart(container.querySelector("#ts-heat-chart"), {
    tooltip: { trigger: "item" },
    grid: { left: 48, right: 20, top: 20, bottom: 48 },
    xAxis: { type: "category", data: months },
    yAxis: { type: "category", data: dows },
    visualMap: {
      min: 0,
      max: maxS,
      calculable: false,
      orient: "horizontal",
      left: "center",
      bottom: 0,
      inRange: { color: ["#1e293b", "#4f46e5", "#10b981"] },
      textStyle: { color: p.text },
    },
    series: [
      {
        type: "heatmap",
        data: seas.map((s) => [s.month_num - 1, s.dow, Math.round(s.revenue)]),
      },
    ],
  });

  // Anomalies table
  const anoms = data.anomalies || [];
  container.querySelector("#ts-anomalies-wrap").innerHTML = anoms.length
    ? `
    <table class="data-table">
      <thead><tr><th>Date</th><th>Direction</th><th class="num">Actual</th><th class="num">Baseline</th><th class="num">Z-Score</th><th>Driver</th></tr></thead>
      <tbody>
        ${anoms
          .map(
            (a) => `
          <tr>
            <td class="tabular">${escapeHtml(a.date)}</td>
            <td><span class="pill ${a.direction === "spike" ? "pill-emerald" : "pill-rose"}">${escapeHtml(a.direction)}</span></td>
            <td class="num">${fmtCurrency(a.revenue, proj.currency, lang)}</td>
            <td class="num">${fmtCurrency(a.expected, proj.currency, lang)}</td>
            <td class="num">${a.z_score}</td>
            <td>${escapeHtml(a.root_cause || "—")}</td>
          </tr>`
          )
          .join("")}
      </tbody>
    </table>`
    : `<div style="padding:18px;color:var(--text-secondary);">No extreme statistical anomalies detected (|Z| ≥ 3.0).</div>`;
}
