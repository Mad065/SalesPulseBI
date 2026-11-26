import { api } from "../api.js";
import { mountChart, palette } from "../charts.js";
import { fmtCurrency, fmtNumber, t } from "../i18n.js";
import { renderManagedDashboard } from "../layout_manager.js";
import { state } from "../state.js";
import { escapeHtml } from "./hub.js";

let forecastEngineMode = "holt";

export async function renderTimeseries(container, onGoMapping) {
  const proj = state.activeProject;
  const lang = state.lang;
  const data = await api.analytics(proj.id, "timeseries", state.filters);
  let automlOverlay = null;
  if (data.available && forecastEngineMode === "automl") {
    try {
      automlOverlay = await api.ml(proj.id, "forecast", {
        filters: state.filters,
        target_metric: "revenue",
        granularity: state.filters.granularity,
        horizon_days: state.filters.forecast_horizon,
      });
    } catch {
      automlOverlay = null;
    }
  }

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

  const p = palette();
  const series = data.series || [];
  const forecast =
    automlOverlay && automlOverlay.available ? automlOverlay.forecast || [] : data.forecast || [];
  const fcLabel =
    automlOverlay && automlOverlay.available
      ? `AutoML (${automlOverlay.winner_model})`
      : "Forecast";
  const allPeriods = [...series.map((r) => r.period), ...forecast.map((f) => f.period)];
  const actualData = [...series.map((r) => r.revenue), ...forecast.map(() => null)];
  const maData = [...series.map((r) => r.moving_avg), ...forecast.map(() => null)];
  const fcData = [
    ...series.map((_, idx) => (idx === series.length - 1 ? series[idx].revenue : null)),
    ...forecast.map((f) => f.forecast),
  ];
  const upperBand = [...series.map(() => null), ...forecast.map((f) => f.upper_95)];
  const lowerBand = [...series.map(() => null), ...forecast.map((f) => f.lower_95)];

  const panels = [
    {
      id: "ts_forecast",
      title: t(lang, "forecast_title"),
      defaultSpan: 2,
      renderHtml: () => `
        <div class="card">
          <div class="card-header">
            <h3 class="card-title">${t(lang, "forecast_title")}</h3>
            <div class="card-toolbar">
              <label class="toolbar-label" for="ts-granularity">${t(lang, "granularity")}</label>
              <select id="ts-granularity" class="input input-sm">
                ${["day", "week", "month", "quarter", "year"]
                  .map(
                    (g) =>
                      `<option value="${g}" ${state.filters.granularity === g ? "selected" : ""}>${t(lang, g)}</option>`
                  )
                  .join("")}
              </select>
              <label class="toolbar-label" for="ts-horizon">${t(lang, "forecast_horizon")}</label>
              <select id="ts-horizon" class="input input-sm">
                ${[14, 30, 60, 90]
                  .map(
                    (h) =>
                      `<option value="${h}" ${state.filters.forecast_horizon === h ? "selected" : ""}>${h}d</option>`
                  )
                  .join("")}
              </select>
              <select id="ts-fc-engine" class="input input-sm" aria-label="Forecast engine">
                <option value="holt" ${forecastEngineMode === "holt" ? "selected" : ""}>Holt-Winters</option>
                <option value="automl" ${forecastEngineMode === "automl" ? "selected" : ""}>AutoML</option>
              </select>
            </div>
          </div>
          <div id="ts-main-chart" class="chart-box-tall"></div>
        </div>`,
      mount: (panelEl) => {
        panelEl.querySelector("#ts-granularity")?.addEventListener("change", (e) => {
          state.filters.granularity = e.target.value;
          renderTimeseries(container, onGoMapping);
        });
        panelEl.querySelector("#ts-horizon")?.addEventListener("change", (e) => {
          state.filters.forecast_horizon = Number(e.target.value);
          renderTimeseries(container, onGoMapping);
        });
        panelEl.querySelector("#ts-fc-engine")?.addEventListener("change", (e) => {
          forecastEngineMode = e.target.value;
          renderTimeseries(container, onGoMapping);
        });
        mountChart(panelEl.querySelector("#ts-main-chart"), {
          legend: { textStyle: { color: p.text }, bottom: 0 },
          dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 26 }],
          grid: { left: 56, right: 22, top: 28, bottom: 68, containLabel: true },
          xAxis: { type: "category", data: allPeriods },
          yAxis: { type: "value", splitLine: { lineStyle: { color: p.grid } } },
          series: [
            { name: "Actual", type: "line", data: actualData, smooth: true, lineStyle: { width: 2.5 } },
            { name: "Moving Avg", type: "line", data: maData, smooth: true, symbol: "none", lineStyle: { width: 1.5, type: "dashed" } },
            { name: fcLabel, type: "line", data: fcData, smooth: true, lineStyle: { width: 2.5, type: "dotted", color: p.up }, itemStyle: { color: p.up } },
            { name: "Upper 95%", type: "line", data: upperBand, symbol: "none", lineStyle: { opacity: 0.3, type: "dashed", color: p.up } },
            { name: "Lower 95%", type: "line", data: lowerBand, symbol: "none", lineStyle: { opacity: 0.3, type: "dashed", color: p.up } },
          ],
        });
      },
    },
    {
      id: "ts_yoy",
      title: t(lang, "yoy_title"),
      defaultSpan: 1,
      renderHtml: () => `
        <div class="card">
          <div class="card-header"><h3 class="card-title">${t(lang, "yoy_title")}</h3></div>
          <div id="ts-yoy-chart" class="chart-box"></div>
        </div>`,
      mount: (panelEl) => {
        const yoy = data.yoy_cumulative || {};
        const years = Object.keys(yoy);
        if (years.length) {
          mountChart(panelEl.querySelector("#ts-yoy-chart"), {
            legend: { data: years, textStyle: { color: p.text }, bottom: 0 },
            xAxis: { type: "category", data: ["M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10", "M11", "M12"] },
            yAxis: { type: "value", splitLine: { lineStyle: { color: p.grid } } },
            series: years.map((yr) => ({ name: yr, type: "line", smooth: true, data: yoy[yr] })),
          });
        }
      },
    },
    {
      id: "ts_seasonality",
      title: t(lang, "seasonality_title"),
      defaultSpan: 1,
      renderHtml: () => `
        <div class="card">
          <div class="card-header"><h3 class="card-title">${t(lang, "seasonality_title")}</h3></div>
          <div id="ts-heat-chart" class="chart-box"></div>
        </div>`,
      mount: (panelEl) => {
        const seas = data.seasonality || [];
        const dows = t(lang, "dow_names");
        const months = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"];
        const maxS = Math.max(...seas.map((s) => s.revenue), 1);
        mountChart(panelEl.querySelector("#ts-heat-chart"), {
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
            inRange: { color: [p.elevated, p.accent, p.up] },
            textStyle: { color: p.text },
          },
          series: [
            {
              type: "heatmap",
              data: seas.map((s) => [s.month_num - 1, s.dow, Math.round(s.revenue)]),
            },
          ],
        });
      },
    },
    {
      id: "ts_anomalies",
      title: t(lang, "anomalies_title"),
      defaultSpan: 2,
      renderHtml: () => {
        const anoms = data.anomalies || [];
        const bodyHtml = anoms.length
          ? `
          <table class="data-table">
            <thead><tr><th>Date</th><th>Direction</th><th class="num">Actual</th><th class="num">Baseline</th><th class="num">Z-Score</th><th>Driver</th></tr></thead>
            <tbody>
              ${anoms
                .map(
                  (a) => `
                <tr>
                  <td class="tabular">${escapeHtml(a.date)}</td>
                  <td>
                    <span class="pill ${a.direction === "spike" ? "pill-emerald" : "pill-rose"}">
                      <span aria-hidden="true">${a.direction === "spike" ? "▲" : "▼"}</span>
                      ${escapeHtml(a.direction)}
                    </span>
                  </td>
                  <td class="num">${fmtCurrency(a.revenue, proj.currency, lang)}</td>
                  <td class="num">${fmtCurrency(a.expected, proj.currency, lang)}</td>
                  <td class="num">${fmtNumber(a.z_score, 2, lang)}</td>
                  <td>${escapeHtml(a.root_cause || "—")}</td>
                </tr>`
                )
                .join("")}
            </tbody>
          </table>`
          : `<div class="card-subtitle" style="padding:1.125rem;">No extreme statistical anomalies detected (|Z| ≥ 3.0).</div>`;

        return `
          <div class="card">
            <div class="card-header">
              <h3 class="card-title">${t(lang, "anomalies_title")}</h3>
              <span class="pill pill-amber">${fmtNumber(anoms.length, 0, lang)}</span>
            </div>
            <div class="table-wrap" tabindex="0" role="region" aria-label="${escapeHtml(t(lang, "anomalies_title"))}">${bodyHtml}</div>
          </div>`;
      },
      mount: () => {},
    },
  ];

  renderManagedDashboard({
    container,
    proj,
    tabKey: "timeseries",
    panels,
    onRerender: () => renderTimeseries(container, onGoMapping),
  });
}
