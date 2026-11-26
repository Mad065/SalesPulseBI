import { api } from "../api.js";
import { mountChart, palette } from "../charts.js";
import { fmtCurrency, fmtNumber, fmtPercent, t } from "../i18n.js";
import { renderManagedDashboard } from "../layout_manager.js";
import { state } from "../state.js";
import { escapeHtml, showToast } from "./hub.js";

const STUDIO_STATE = {
  subtab: "automl",
  targetMetric: "revenue",
  whatIf: {
    price_delta_pct: 5,
    discount_delta_pct: 0,
    cost_delta_pct: 0,
    volume_delta_pct: 0,
    fx_multiplier: 1,
    use_estimated_elasticity: false,
  },
};

function fmtSignedDelta(val, currency, lang) {
  if (val === null || val === undefined || Number.isNaN(Number(val))) return "—";
  const num = Number(val);
  const arrow = num >= 0 ? "▲" : "▼";
  const cls = num >= 0 ? "delta-pos" : "delta-neg";
  return `<span class="${cls} tabular"><span aria-hidden="true">${arrow}</span> ${num >= 0 ? "+" : ""}${fmtCurrency(num, currency, lang)}</span>`;
}

function fmtSignedPct(val, lang, suffix = "%") {
  if (val === null || val === undefined || Number.isNaN(Number(val))) return "—";
  const num = Number(val);
  const arrow = num >= 0 ? "▲" : "▼";
  const cls = num >= 0 ? "delta-pos" : "delta-neg";
  return `<span class="${cls} tabular"><span aria-hidden="true">${arrow}</span> ${num >= 0 ? "+" : ""}${fmtNumber(num, 2, lang)}${suffix}</span>`;
}

export async function renderAiMl(container, onGoMapping) {
  const proj = state.activeProject;
  const lang = state.lang;
  const isEs = lang === "es";

  const subtabs = [
    { id: "automl", label: isEs ? "AutoML & Escenarios" : "AutoML & Scenarios" },
    { id: "documents", label: isEs ? "Documentos (OCR & RAG)" : "Documents (OCR & RAG)" },
    { id: "market", label: isEs ? "Noticias, Churn & Alertas" : "News, Churn & Alerts" },
  ];

  const headerHtml = `
    <div class="ai-studio-header">
      <div class="tabs-row" role="tablist" aria-label="AI & ML Studio sections">
        ${subtabs
          .map(
            (st) => `
          <button type="button" class="tab-btn ${STUDIO_STATE.subtab === st.id ? "active" : ""}" data-studio-tab="${st.id}" role="tab" aria-selected="${STUDIO_STATE.subtab === st.id}">
            ${escapeHtml(st.label)}
          </button>`
          )
          .join("")}
      </div>
      <div class="card-toolbar">
        <button type="button" id="ai-studio-open-copilot" class="btn btn-secondary btn-sm">
          <span aria-hidden="true">✦</span> ${isEs ? "Abrir Copiloto IA" : "Open AI Copilot"}
        </button>
      </div>
    </div>
    <div id="ai-studio-body"></div>
  `;

  container.innerHTML = headerHtml;
  container.querySelectorAll("[data-studio-tab]").forEach((btn) =>
    btn.addEventListener("click", () => {
      STUDIO_STATE.subtab = btn.dataset.studioTab;
      renderAiMl(container, onGoMapping);
    })
  );
  container.querySelector("#ai-studio-open-copilot")?.addEventListener("click", () => {
    document.getElementById("btn-copilot-toggle")?.click();
  });

  const bodyEl = container.querySelector("#ai-studio-body");
  if (STUDIO_STATE.subtab === "automl") {
    await renderAutoMlTab(bodyEl, proj, lang, onGoMapping);
  } else if (STUDIO_STATE.subtab === "documents") {
    await renderDocumentsTab(bodyEl, proj, lang);
  } else {
    await renderMarketAlertsTab(bodyEl, proj, lang);
  }
}

async function renderAutoMlTab(container, proj, lang, onGoMapping) {
  const isEs = lang === "es";
  const p = palette();

  const [fcData, anomData, pvmData, whatIfData] = await Promise.all([
    api.ml(proj.id, "forecast", {
      filters: state.filters,
      target_metric: STUDIO_STATE.targetMetric,
      granularity: state.filters.granularity,
      horizon_days: state.filters.forecast_horizon,
    }),
    api.ml(proj.id, "anomalies", { filters: state.filters, contamination: 0.08, limit: 15 }),
    api.ml(proj.id, "pvm-bridge", state.filters),
    api.ml(proj.id, "what-if", {
      filters: state.filters,
      ...STUDIO_STATE.whatIf,
    }),
  ]);

  if (!fcData.available && !whatIfData.available) {
    container.innerHTML = `
      <div class="empty-state">
        <h3>${t(lang, "locked_title")}</h3>
        <p>${t(lang, "locked_desc")}</p>
        <button id="ml-go-map" class="btn btn-primary">${t(lang, "go_mapping")}</button>
      </div>`;
    container.querySelector("#ml-go-map")?.addEventListener("click", onGoMapping);
    return;
  }

  const formatMetricVal = (val) =>
    STUDIO_STATE.targetMetric === "revenue" || STUDIO_STATE.targetMetric === "profit"
      ? fmtCurrency(val, proj.currency, lang)
      : fmtNumber(val, 0, lang);

  const panels = [
    {
      id: "ml_automl_forecast",
      title: t(lang, "automl_title"),
      defaultSpan: 2,
      renderHtml: () => {
        const winner = fcData.available ? fcData.winner_model : "—";
        const wape = fcData.available ? fmtPercent(fcData.winner_metrics?.wape, 2, lang) : "—";
        const mase = fcData.available ? fmtNumber(fcData.winner_metrics?.mase, 3, lang) : "—";
        const fcTotal = fcData.available ? formatMetricVal(fcData.forecast_total) : "—";
        return `
          <div class="card">
            <div class="card-header">
              <div>
                <h3 class="card-title">${t(lang, "automl_title")}</h3>
                <p class="card-subtitle tabular">
                  ${
                    fcData.available
                      ? `${isEs ? "Ganador" : "Winner"}: <strong>${escapeHtml(winner)}</strong> · WAPE ${wape} · MASE ${mase} · CV ${fcData.cv_folds} ${isEs ? "pliegues" : "folds"} · ${isEs ? "Proyección total" : "Forecast total"}: <strong>${fcTotal}</strong>`
                      : isEs
                        ? "Se requieren al menos 3 periodos históricos."
                        : "At least 3 historical periods are required."
                  }
                </p>
              </div>
              <div class="card-toolbar">
                <select id="ml-target-metric" class="input input-sm" aria-label="Target metric">
                  <option value="revenue" ${STUDIO_STATE.targetMetric === "revenue" ? "selected" : ""}>${isEs ? "Ingresos" : "Revenue"}</option>
                  <option value="profit" ${STUDIO_STATE.targetMetric === "profit" ? "selected" : ""}>${isEs ? "Utilidad" : "Profit"}</option>
                  <option value="units" ${STUDIO_STATE.targetMetric === "units" ? "selected" : ""}>${isEs ? "Unidades" : "Units"}</option>
                  <option value="orders" ${STUDIO_STATE.targetMetric === "orders" ? "selected" : ""}>${isEs ? "Pedidos" : "Orders"}</option>
                </select>
                <button type="button" id="ml-retrain-btn" class="btn btn-primary btn-sm">
                  ↻ ${t(lang, "automl_retrain")}
                </button>
              </div>
            </div>
            <div id="ml-forecast-chart" class="chart-box-tall"></div>
          </div>`;
      },
      mount: (panelEl) => {
        panelEl.querySelector("#ml-target-metric")?.addEventListener("change", (e) => {
          STUDIO_STATE.targetMetric = e.target.value;
          renderAutoMlTab(container, proj, lang, onGoMapping);
        });
        panelEl.querySelector("#ml-retrain-btn")?.addEventListener("click", async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true;
          try {
            await api.ml(proj.id, "forecast/train", {
              filters: state.filters,
              target_metric: STUDIO_STATE.targetMetric,
              granularity: state.filters.granularity,
              horizon_days: state.filters.forecast_horizon,
              force_retrain: true,
            });
            showToast(isEs ? "AutoML entrenado y ganador guardado" : "AutoML trained and saved", "success");
            await renderAutoMlTab(container, proj, lang, onGoMapping);
          } catch (err) {
            showToast(err.message, "error");
            btn.disabled = false;
          }
        });
        if (!fcData.available) return;
        const series = fcData.series || [];
        const fc = fcData.forecast || [];
        const labels = [...series.map((r) => r.period), ...fc.map((f) => f.period)];
        const actual = [...series.map((r) => r.value), ...fc.map(() => null)];
        const predLine = [
          ...series.map((_, idx) => (idx === series.length - 1 ? series[idx].value : null)),
          ...fc.map((f) => f.forecast),
        ];
        const up95 = [...series.map(() => null), ...fc.map((f) => f.upper_95)];
        const lo95 = [...series.map(() => null), ...fc.map((f) => f.lower_95)];
        const up80 = [...series.map(() => null), ...fc.map((f) => f.upper_80)];
        const lo80 = [...series.map(() => null), ...fc.map((f) => f.lower_80)];

        mountChart(panelEl.querySelector("#ml-forecast-chart"), {
          legend: { textStyle: { color: p.text }, bottom: 0 },
          dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 26 }],
          grid: { left: 56, right: 24, top: 24, bottom: 68, containLabel: true },
          xAxis: { type: "category", data: labels },
          yAxis: { type: "value", splitLine: { lineStyle: { color: p.grid } } },
          series: [
            { name: isEs ? "Histórico" : "Actual", type: "line", data: actual, smooth: true, lineStyle: { width: 2.5, color: p.accent }, itemStyle: { color: p.accent } },
            { name: `AutoML (${fcData.winner_model})`, type: "line", data: predLine, smooth: true, lineStyle: { width: 2.5, type: "dashed", color: p.up }, itemStyle: { color: p.up } },
            { name: "80% Hi", type: "line", data: up80, symbol: "none", lineStyle: { opacity: 0.45, type: "dotted", color: p.up } },
            { name: "80% Lo", type: "line", data: lo80, symbol: "none", lineStyle: { opacity: 0.45, type: "dotted", color: p.up } },
            { name: "95% Hi", type: "line", data: up95, symbol: "none", lineStyle: { opacity: 0.25, type: "dashed", color: p.warn } },
            { name: "95% Lo", type: "line", data: lo95, symbol: "none", lineStyle: { opacity: 0.25, type: "dashed", color: p.warn } },
          ],
        });
      },
    },
    {
      id: "ml_leaderboard",
      title: t(lang, "automl_leaderboard"),
      defaultSpan: 1,
      renderHtml: () => {
        const rows = fcData.leaderboard || [];
        return `
          <div class="card">
            <div class="card-header">
              <h3 class="card-title">${t(lang, "automl_leaderboard")}</h3>
              <span class="pill pill-accent tabular">${fmtNumber(rows.length, 0, lang)} ${isEs ? "modelos" : "models"}</span>
            </div>
            <div class="table-wrap" tabindex="0" role="region" aria-label="${escapeHtml(t(lang, "automl_leaderboard"))}">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>${isEs ? "Modelo" : "Model"}</th>
                    <th class="num">WAPE</th>
                    <th class="num">sMAPE</th>
                    <th class="num">MASE</th>
                    <th class="num">RMSE</th>
                  </tr>
                </thead>
                <tbody>
                  ${rows
                    .map(
                      (r, i) => `
                    <tr>
                      <td class="tabular">${i === 0 ? '<span class="pill pill-emerald">★ 1</span>' : i + 1}</td>
                      <td><strong>${escapeHtml(r.model)}</strong></td>
                      <td class="num">${fmtPercent(r.wape, 2, lang)}</td>
                      <td class="num">${fmtPercent(r.smape, 2, lang)}</td>
                      <td class="num">${fmtNumber(r.mase, 3, lang)}</td>
                      <td class="num">${fmtNumber(r.rmse, 2, lang)}</td>
                    </tr>`
                    )
                    .join("")}
                </tbody>
              </table>
            </div>
          </div>`;
      },
      mount: () => {},
    },
    {
      id: "ml_importance",
      title: t(lang, "automl_importance"),
      defaultSpan: 1,
      renderHtml: () => `
        <div class="card">
          <div class="card-header"><h3 class="card-title">${t(lang, "automl_importance")}</h3></div>
          <div id="ml-imp-chart" class="chart-box"></div>
        </div>`,
      mount: (panelEl) => {
        const imps = (fcData.feature_importance || []).slice(0, 7).reverse();
        const el = panelEl.querySelector("#ml-imp-chart");
        if (!imps.length) {
          if (el) {
            el.innerHTML = `<div class="empty-state">${
              isEs ? "Se requieren ≥10 periodos para explicar rezagos ML." : "Requires ≥10 periods for ML lag attribution."
            }</div>`;
          }
          return;
        }
        mountChart(el, {
          grid: { left: 130, right: 24, top: 16, bottom: 24, containLabel: true },
          xAxis: { type: "value", max: 100, axisLabel: { formatter: "{value}%" }, splitLine: { lineStyle: { color: p.grid } } },
          yAxis: { type: "category", data: imps.map((x) => x.label) },
          series: [{ type: "bar", data: imps.map((x) => x.importance_pct), itemStyle: { color: p.accent, borderRadius: [0, 4, 4, 0] } }],
        });
      },
    },
    {
      id: "ml_pvm",
      title: t(lang, "pvm_title"),
      defaultSpan: 1,
      renderHtml: () => `
        <div class="card">
          <div class="card-header">
            <div>
              <h3 class="card-title">${t(lang, "pvm_title")}</h3>
              <p class="card-subtitle tabular">
                ${
                  pvmData.available
                    ? `Δ ${fmtSignedDelta(pvmData.total_delta, proj.currency, lang)} · ${pvmData.conservation_verified ? "✓ Cuadre Decimal exacto" : ""}`
                    : isEs
                      ? "Requiere dos periodos comparables."
                      : "Requires two comparable periods."
                }
              </p>
            </div>
          </div>
          <div id="ml-pvm-chart" class="chart-box"></div>
        </div>`,
      mount: (panelEl) => {
        const el = panelEl.querySelector("#ml-pvm-chart");
        if (!pvmData.available) {
          if (el) el.innerHTML = `<div class="empty-state">${isEs ? "Sin periodo previo comparable." : "No comparable prior period."}</div>`;
          return;
        }
        const steps = pvmData.steps || [];
        const labels = steps.map((s) => (isEs ? s.label_es : s.label_en));
        const baseStack = [];
        const barVals = [];
        let running = 0;
        steps.forEach((s) => {
          if (s.kind === "total") {
            baseStack.push(0);
            barVals.push({ value: s.value, itemStyle: { color: p.accent } });
            running = s.value;
          } else {
            const v = s.value;
            baseStack.push(v >= 0 ? running : running + v);
            barVals.push({
              value: Math.abs(v),
              rawDelta: v,
              itemStyle: { color: v >= 0 ? p.up : p.down },
            });
            running += v;
          }
        });
        mountChart(el, {
          tooltip: {
            trigger: "axis",
            axisPointer: { type: "shadow" },
            formatter: (params) => {
              const item = params[1];
              if (!item) return "";
              const raw = item.data.rawDelta !== undefined ? item.data.rawDelta : item.value;
              return `${escapeHtml(item.name)}: <strong>${fmtCurrency(raw, proj.currency, lang)}</strong>`;
            },
          },
          grid: { left: 52, right: 16, top: 20, bottom: 48, containLabel: true },
          xAxis: { type: "category", data: labels, axisLabel: { rotate: 20 } },
          yAxis: { type: "value", splitLine: { lineStyle: { color: p.grid } } },
          series: [
            { name: "base", type: "bar", stack: "pvm", itemStyle: { color: "transparent" }, emphasis: { itemStyle: { color: "transparent" } }, data: baseStack },
            { name: "PVM", type: "bar", stack: "pvm", data: barVals },
          ],
        });
      },
    },
    {
      id: "ml_whatif",
      title: t(lang, "whatif_title"),
      defaultSpan: 1,
      renderHtml: () => {
        const w = STUDIO_STATE.whatIf;
        const base = whatIfData.baseline || {};
        const sim = whatIfData.simulated || {};
        const dlt = whatIfData.deltas || {};
        return `
          <div class="card">
            <div class="card-header">
              <h3 class="card-title">${t(lang, "whatif_title")}</h3>
              <button type="button" id="wi-reset" class="btn btn-ghost btn-xs">${t(lang, "reset")}</button>
            </div>
            <div class="whatif-controls">
              <label class="whatif-slider-row">
                <span>${isEs ? "Cambio Precio" : "Price Delta"}</span>
                <input id="wi-price" type="range" min="-40" max="60" step="1" value="${w.price_delta_pct}" />
                <strong id="wi-price-val" class="tabular">${w.price_delta_pct >= 0 ? "+" : ""}${w.price_delta_pct}%</strong>
              </label>
              <label class="whatif-slider-row">
                <span>${isEs ? "Cambio Volumen" : "Volume Delta"}</span>
                <input id="wi-vol" type="range" min="-50" max="80" step="1" value="${w.volume_delta_pct}" />
                <strong id="wi-vol-val" class="tabular">${w.volume_delta_pct >= 0 ? "+" : ""}${w.volume_delta_pct}%</strong>
              </label>
              <label class="whatif-slider-row">
                <span>${isEs ? "Cambio Descuento" : "Discount Delta"}</span>
                <input id="wi-disc" type="range" min="-50" max="50" step="1" value="${w.discount_delta_pct}" />
                <strong id="wi-disc-val" class="tabular">${w.discount_delta_pct >= 0 ? "+" : ""}${w.discount_delta_pct}%</strong>
              </label>
              <label class="whatif-slider-row">
                <span>${isEs ? "Cambio Costo" : "Cost Delta"}</span>
                <input id="wi-cost" type="range" min="-40" max="60" step="1" value="${w.cost_delta_pct}" />
                <strong id="wi-cost-val" class="tabular">${w.cost_delta_pct >= 0 ? "+" : ""}${w.cost_delta_pct}%</strong>
              </label>
              <label class="checkbox-pill" style="margin-block-start:0.25rem;">
                <input id="wi-elasticity" type="checkbox" ${w.use_estimated_elasticity ? "checked" : ""} ${whatIfData.estimated_elasticity === null ? "disabled" : ""} />
                <span>${
                  whatIfData.estimated_elasticity !== null
                    ? `${isEs ? "Aplicar elasticidad estimada" : "Apply estimated elasticity"} (${whatIfData.estimated_elasticity})`
                    : isEs
                      ? "Elasticidad no estimable (requiere precio unitario)"
                      : "Elasticity unavailable (needs unit price)"
                }</span>
              </label>
            </div>
            <div id="wi-results" class="whatif-kpi-grid">
              <div class="whatif-kpi">
                <span class="kpi-label">${isEs ? "Ingreso Simulado" : "Simulated Revenue"}</span>
                <strong class="tabular">${fmtCurrency(sim.revenue, proj.currency, lang)}</strong>
                <span>${fmtSignedDelta(dlt.revenue, proj.currency, lang)} (${fmtSignedPct(dlt.revenue_pct, lang)})</span>
              </div>
              <div class="whatif-kpi">
                <span class="kpi-label">${isEs ? "Utilidad Simulada" : "Simulated Profit"}</span>
                <strong class="tabular">${sim.gross_profit !== null ? fmtCurrency(sim.gross_profit, proj.currency, lang) : "—"}</strong>
                <span>${dlt.gross_profit !== null ? fmtSignedDelta(dlt.gross_profit, proj.currency, lang) : isEs ? "Requiere costo" : "Requires cost"}</span>
              </div>
              <div class="whatif-kpi">
                <span class="kpi-label">${isEs ? "Margen Bruto" : "Gross Margin"}</span>
                <strong class="tabular">${sim.gross_margin_pct !== null ? fmtPercent(sim.gross_margin_pct, 2, lang) : "—"}</strong>
                <span>${dlt.margin_pp !== null ? fmtSignedPct(dlt.margin_pp, lang, " pp") : `${isEs ? "Base" : "Base"}: ${fmtCurrency(base.revenue, proj.currency, lang)}`}</span>
              </div>
            </div>
          </div>`;
      },
      mount: (panelEl) => {
        let timer = null;
        const triggerSim = () => {
          clearTimeout(timer);
          timer = setTimeout(async () => {
            try {
              const res = await api.ml(proj.id, "what-if", {
                filters: state.filters,
                ...STUDIO_STATE.whatIf,
              });
              const box = panelEl.querySelector("#wi-results");
              if (!box || !res.available) return;
              const sim = res.simulated;
              const dlt = res.deltas;
              box.innerHTML = `
                <div class="whatif-kpi">
                  <span class="kpi-label">${isEs ? "Ingreso Simulado" : "Simulated Revenue"}</span>
                  <strong class="tabular">${fmtCurrency(sim.revenue, proj.currency, lang)}</strong>
                  <span>${fmtSignedDelta(dlt.revenue, proj.currency, lang)} (${fmtSignedPct(dlt.revenue_pct, lang)})</span>
                </div>
                <div class="whatif-kpi">
                  <span class="kpi-label">${isEs ? "Utilidad Simulada" : "Simulated Profit"}</span>
                  <strong class="tabular">${sim.gross_profit !== null ? fmtCurrency(sim.gross_profit, proj.currency, lang) : "—"}</strong>
                  <span>${dlt.gross_profit !== null ? fmtSignedDelta(dlt.gross_profit, proj.currency, lang) : "—"}</span>
                </div>
                <div class="whatif-kpi">
                  <span class="kpi-label">${isEs ? "Margen Bruto" : "Gross Margin"}</span>
                  <strong class="tabular">${sim.gross_margin_pct !== null ? fmtPercent(sim.gross_margin_pct, 2, lang) : "—"}</strong>
                  <span>${dlt.margin_pp !== null ? fmtSignedPct(dlt.margin_pp, lang, " pp") : "—"}</span>
                </div>`;
            } catch {}
          }, 160);
        };

        const bindSlider = (id, valId, field) => {
          const el = panelEl.querySelector(`#${id}`);
          const lbl = panelEl.querySelector(`#${valId}`);
          el?.addEventListener("input", () => {
            const v = Number(el.value);
            STUDIO_STATE.whatIf[field] = v;
            if (lbl) lbl.textContent = `${v >= 0 ? "+" : ""}${v}%`;
            triggerSim();
          });
        };
        bindSlider("wi-price", "wi-price-val", "price_delta_pct");
        bindSlider("wi-vol", "wi-vol-val", "volume_delta_pct");
        bindSlider("wi-disc", "wi-disc-val", "discount_delta_pct");
        bindSlider("wi-cost", "wi-cost-val", "cost_delta_pct");

        panelEl.querySelector("#wi-elasticity")?.addEventListener("change", (e) => {
          STUDIO_STATE.whatIf.use_estimated_elasticity = e.target.checked;
          triggerSim();
        });
        panelEl.querySelector("#wi-reset")?.addEventListener("click", () => {
          STUDIO_STATE.whatIf = {
            price_delta_pct: 0,
            discount_delta_pct: 0,
            cost_delta_pct: 0,
            volume_delta_pct: 0,
            fx_multiplier: 1,
            use_estimated_elasticity: false,
          };
          renderAutoMlTab(container, proj, lang, onGoMapping);
        });
      },
    },
    {
      id: "ml_mv_anomalies",
      title: t(lang, "mv_anomalies_title"),
      defaultSpan: 2,
      renderHtml: () => {
        const anoms = anomData.anomalies || [];
        return `
          <div class="card">
            <div class="card-header">
              <div>
                <h3 class="card-title">${t(lang, "mv_anomalies_title")}</h3>
                <p class="card-subtitle">${isEs ? "Variables evaluadas" : "Evaluated features"}: ${escapeHtml((anomData.active_features || []).join(", ") || "—")}</p>
              </div>
              <span class="pill pill-amber tabular">${fmtNumber(anoms.length, 0, lang)}</span>
            </div>
            <div class="table-wrap" tabindex="0" role="region" aria-label="${escapeHtml(t(lang, "mv_anomalies_title"))}">
              ${
                anoms.length
                  ? `<table class="data-table">
                      <thead>
                        <tr>
                          <th>${isEs ? "Fecha" : "Date"}</th>
                          <th>${isEs ? "Tipo" : "Type"}</th>
                          <th>${isEs ? "Severidad" : "Severity"}</th>
                          <th class="num">IF Score</th>
                          <th class="num">${isEs ? "Ingresos" : "Revenue"}</th>
                          <th class="num">${isEs ? "Margen %" : "Margin %"}</th>
                          <th>${isEs ? "Causa Raíz" : "Root Cause"}</th>
                        </tr>
                      </thead>
                      <tbody>
                        ${anoms
                          .map(
                            (a) => `
                          <tr>
                            <td class="tabular">${escapeHtml(a.date)}</td>
                            <td><span class="pill ${a.anomaly_type === "revenue_spike" ? "pill-emerald" : "pill-rose"}">${escapeHtml(a.anomaly_type)}</span></td>
                            <td><span class="pill ${a.severity === "high" ? "pill-rose" : "pill-amber"}">${escapeHtml(a.severity)}</span></td>
                            <td class="num">${fmtNumber(a.if_score, 3, lang)}</td>
                            <td class="num">${fmtCurrency(a.revenue, proj.currency, lang)}</td>
                            <td class="num">${a.margin_pct !== null ? fmtPercent(a.margin_pct, 1, lang) : "—"}</td>
                            <td>${escapeHtml(a.root_cause || "—")}</td>
                          </tr>`
                          )
                          .join("")}
                      </tbody>
                    </table>`
                  : `<div class="card-subtitle" style="padding:1.125rem;">${isEs ? "No se detectaron anomalías multivariadas relevantes." : "No significant multivariate anomalies detected."}</div>`
              }
            </div>
          </div>`;
      },
      mount: () => {},
    },
  ];

  renderManagedDashboard({
    container,
    proj,
    tabKey: "ai_ml",
    panels,
    onRerender: () => renderAutoMlTab(container, proj, lang, onGoMapping),
  });
}

async function renderDocumentsTab(container, proj, lang) {
  const isEs = lang === "es";
  let docs = [];
  try {
    const res = await api.listDocuments(proj.id);
    docs = res.documents || [];
  } catch {
    docs = [];
  }

  container.innerHTML = `
    <div class="grid-2">
      <div class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">${isEs ? "Extracción Estructurada (OCR + Pydantic)" : "Structured Extraction (OCR + Pydantic)"}</h3>
            <p class="card-subtitle">${
              isEs
                ? "Sube facturas, estados de cuenta o reportes (.pdf/.txt) con auditoría matemática Decimal e ingesta 1-clic."
                : "Upload invoices, bank statements, or reports (.pdf/.txt) with exact Decimal audit and 1-click dataset ingest."
            }</p>
          </div>
        </div>
        <div class="settings-section-box" style="margin:var(--space-3);">
          <div class="form-grid-3">
            <div class="form-field">
              <label class="form-label" for="doc-ocr-type">${isEs ? "Tipo de documento" : "Document Type"}</label>
              <select id="doc-ocr-type" class="input full-width">
                <option value="auto">${isEs ? "Auto-detectar" : "Auto-detect"}</option>
                <option value="invoice">${isEs ? "Factura (Invoice)" : "Invoice"}</option>
                <option value="bank_statement">${isEs ? "Estado de Cuenta" : "Bank Statement"}</option>
                <option value="financial_report">${isEs ? "Reporte Trimestral / 10-K" : "Financial Report / 10-K"}</option>
              </select>
            </div>
            <div class="form-field">
              <label class="form-label" for="doc-ocr-file">${isEs ? "Archivo (.pdf, .txt, .md)" : "File (.pdf, .txt, .md)"}</label>
              <input id="doc-ocr-file" type="file" accept=".pdf,.txt,.md" class="input input-sm full-width" />
            </div>
            <div class="form-field" style="align-self:end;">
              <button type="button" id="doc-ocr-run" class="btn btn-primary btn-sm full-width">
                ${isEs ? "Extraer y Auditar" : "Extract & Audit"}
              </button>
            </div>
          </div>
          <div id="doc-ocr-preview" class="fx-preview-banner hidden" style="margin-block-start:var(--space-2);"></div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">${isEs ? "RAG Financiero Local (DuckDB Híbrido)" : "Local Financial RAG (DuckDB Hybrid)"}</h3>
            <p class="card-subtitle">${
              isEs
                ? "Indexa balances, 10-K o políticas de gasto y pregunta con citas exactas por página."
                : "Index balance sheets, 10-Ks, or expense policies and ask questions with exact page citations."
            }</p>
          </div>
        </div>
        <div class="settings-section-box" style="margin:var(--space-3);">
          <div class="form-grid-3">
            <div class="form-field">
              <label class="form-label" for="rag-file">${isEs ? "Documento para RAG" : "Document for RAG"}</label>
              <input id="rag-file" type="file" accept=".pdf,.txt,.md" class="input input-sm full-width" />
            </div>
            <div class="form-field">
              <label class="form-label" for="rag-asof">${isEs ? "Fecha corte (As-Of)" : "As-Of Date"}</label>
              <input id="rag-asof" type="date" class="input input-sm full-width" />
            </div>
            <div class="form-field" style="align-self:end;">
              <button type="button" id="rag-index-btn" class="btn btn-secondary btn-sm full-width">
                ${isEs ? "Indexar en RAG" : "Index in RAG"}
              </button>
            </div>
          </div>
          <div class="copilot-composer-row" style="margin-block-start:var(--space-2);">
            <input id="rag-question" type="text" class="input input-sm full-width" placeholder="${
              isEs ? "Ej. ¿Cuál fue el flujo de caja libre o política de viáticos?" : "e.g. What was free cash flow or travel expense policy?"
            }" />
            <button type="button" id="rag-ask-btn" class="btn btn-primary btn-sm">${isEs ? "Preguntar" : "Ask"}</button>
          </div>
          <div id="rag-answer-box" class="fx-preview-banner hidden" style="margin-block-start:var(--space-2);"></div>
        </div>
      </div>
    </div>

    <div class="card" style="margin-block-start:var(--space-4);">
      <div class="card-header">
        <div>
          <h3 class="card-title">${isEs ? "Biblioteca Documental y Conciliación Contable" : "Document Library & Smart Reconciliation"}</h3>
          <p class="card-subtitle">${isEs ? "Cruza facturas/estados de cuenta extraídos contra tus ventas en Parquet." : "Cross-check extracted invoices/statements against your Parquet sales."}</p>
        </div>
        <button type="button" id="doc-reconcile-btn" class="btn btn-secondary btn-sm">
          ⇄ ${isEs ? "Conciliar vs. Ventas" : "Reconcile vs. Sales"}
        </button>
      </div>
      <div id="doc-reconcile-result" class="hidden" style="padding-inline:var(--space-4);padding-block-end:var(--space-3);"></div>
      <div class="table-wrap" tabindex="0" role="region" aria-label="Project documents">
        ${
          docs.length
            ? `<table class="data-table">
                <thead>
                  <tr>
                    <th>${isEs ? "Archivo" : "Filename"}</th>
                    <th>${isEs ? "Tipo" : "Type"}</th>
                    <th>${isEs ? "Estado" : "Status"}</th>
                    <th class="num">${isEs ? "Páginas" : "Pages"}</th>
                    <th>RAG</th>
                    <th>${isEs ? "Acciones" : "Actions"}</th>
                  </tr>
                </thead>
                <tbody>
                  ${docs
                    .map(
                      (d) => `
                    <tr>
                      <td><strong>${escapeHtml(d.filename)}</strong></td>
                      <td><span class="pill pill-muted">${escapeHtml(d.doc_type)}</span></td>
                      <td><span class="pill ${d.validation_errors?.length ? "pill-amber" : "pill-emerald"}">${escapeHtml(d.status)}</span></td>
                      <td class="num">${fmtNumber(d.page_count, 0, lang)}</td>
                      <td>${d.indexed_in_rag ? '<span class="pill pill-accent">✓ Indexado</span>' : "—"}</td>
                      <td>
                        <div class="card-toolbar">
                          ${
                            (d.doc_type === "invoice" || d.doc_type === "bank_statement") && !d.ingested_to_dataset
                              ? `<button type="button" class="btn btn-primary btn-xs" data-doc-commit="${escapeHtml(d.id)}">${isEs ? "Ingestar" : "Commit"}</button>`
                              : ""
                          }
                          <button type="button" class="btn btn-ghost btn-xs" data-doc-del="${escapeHtml(d.id)}">✕</button>
                        </div>
                      </td>
                    </tr>`
                    )
                    .join("")}
                </tbody>
              </table>`
            : `<div class="card-subtitle" style="padding:1.125rem;">${isEs ? "Aún no hay documentos en este proyecto." : "No documents uploaded to this project yet."}</div>`
        }
      </div>
    </div>
  `;

  container.querySelector("#doc-ocr-run")?.addEventListener("click", async () => {
    const fileInput = container.querySelector("#doc-ocr-file");
    const file = fileInput?.files?.[0];
    if (!file) return showToast(isEs ? "Selecciona un archivo PDF o TXT" : "Select a PDF or TXT file", "error");
    try {
      const res = await api.extractDocument(proj.id, file, container.querySelector("#doc-ocr-type").value);
      const box = container.querySelector("#doc-ocr-preview");
      box.classList.remove("hidden");
      box.innerHTML = `
        <strong>${res.accounting_valid ? "✓ Cuadre Decimal exacto" : "⚠ Revisar discrepancias contables"}</strong>
        (${escapeHtml(res.extraction_mode)} · ${escapeHtml(res.reader_engine)})
        <pre style="margin-block-start:0.4rem;max-block-size:12rem;overflow:auto;">${escapeHtml(JSON.stringify(res.document.extracted, null, 2))}</pre>
      `;
      showToast(isEs ? "Documento extraído y auditado" : "Document extracted and audited", "success");
      await renderDocumentsTab(container, proj, lang);
    } catch (err) {
      showToast(err.message, "error");
    }
  });

  container.querySelector("#rag-index-btn")?.addEventListener("click", async () => {
    const file = container.querySelector("#rag-file")?.files?.[0];
    if (!file) return showToast(isEs ? "Selecciona un archivo para indexar" : "Select a file to index", "error");
    try {
      const res = await api.indexDocument(proj.id, file, {
        as_of_date: container.querySelector("#rag-asof")?.value || undefined,
        currency: proj.currency,
      });
      showToast(
        isEs ? `Indexados ${res.chunks_indexed} fragmentos en DuckDB RAG` : `Indexed ${res.chunks_indexed} chunks into DuckDB RAG`,
        "success"
      );
      await renderDocumentsTab(container, proj, lang);
    } catch (err) {
      showToast(err.message, "error");
    }
  });

  container.querySelector("#rag-ask-btn")?.addEventListener("click", async () => {
    const q = container.querySelector("#rag-question")?.value.trim();
    if (!q) return;
    const box = container.querySelector("#rag-answer-box");
    box.classList.remove("hidden");
    box.textContent = isEs ? "Buscando citas exactas..." : "Searching exact citations...";
    try {
      const res = await api.askDocuments(proj.id, {
        question: q,
        filters: state.filters,
        lang,
      });
      const cites = (res.citations || [])
        .map((c) => `<span class="pill pill-accent">${escapeHtml(c.label)}</span>`)
        .join(" ");
      box.innerHTML = `
        <div style="white-space:pre-wrap;">${escapeHtml(res.answer_markdown)}</div>
        <div style="margin-block-start:0.5rem;">${cites}</div>
      `;
    } catch (err) {
      box.textContent = err.message;
    }
  });

  container.querySelector("#doc-reconcile-btn")?.addEventListener("click", async () => {
    const box = container.querySelector("#doc-reconcile-result");
    try {
      const res = await api.reconcileDocuments(proj.id, { date_tolerance_days: 3 });
      box.classList.remove("hidden");
      const s = res.summary || {};
      box.innerHTML = `
        <div class="fx-preview-banner tabular">
          ${isEs ? "Conciliados" : "Matched"}: <strong>${s.matched || 0}</strong> (${fmtCurrency(s.matched_amount_decimal, proj.currency, lang)}) ·
          ${isEs ? "Discrepancias" : "Mismatches"}: <strong>${s.amount_mismatch || 0}</strong> ·
          ${isEs ? "Sin cruce" : "Unmatched"}: <strong>${s.unmatched || 0}</strong>
        </div>`;
    } catch (err) {
      showToast(err.message, "error");
    }
  });

  container.querySelectorAll("[data-doc-commit]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      try {
        const res = await api.commitDocument(proj.id, btn.dataset.docCommit, {});
        showToast(
          isEs ? `${res.rows_committed} registros ingestados al proyecto` : `${res.rows_committed} rows committed to project`,
          "success"
        );
        if (typeof state.onProjectUpdated === "function") await state.onProjectUpdated();
        await renderDocumentsTab(container, proj, lang);
      } catch (err) {
        showToast(err.message, "error");
      }
    })
  );

  container.querySelectorAll("[data-doc-del]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      await api.deleteDocument(proj.id, btn.dataset.docDel);
      showToast(isEs ? "Documento eliminado" : "Document deleted", "success");
      await renderDocumentsTab(container, proj, lang);
    })
  );
}

async function renderMarketAlertsTab(container, proj, lang) {
  const isEs = lang === "es";

  const [alertsRes, diagRes, churnRes] = await Promise.all([
    api.projectAlerts(proj.id, true).catch(() => ({ alerts: [] })),
    api.projectDiagnostics(proj.id, state.filters).catch(() => ({ entity_resolution: {}, margin_leakage: {} })),
    api.ml(proj.id, "churn", { filters: state.filters, limit: 25 }).catch(() => ({ available: false })),
  ]);

  const alerts = alertsRes.alerts || [];
  const leak = diagRes.margin_leakage || {};
  const ent = diagRes.entity_resolution || {};
  const churnCusts = churnRes.available ? churnRes.customers || [] : [];
  const churnSum = churnRes.summary || {};

  container.innerHTML = `
    <div class="grid-2">
      <!-- Market News & Sentiment -->
      <div class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">${t(lang, "market_sentiment_title")}</h3>
            <p class="card-subtitle">${
              isEs
                ? "Analiza titulares sectoriales (RSS asíncrono o titulares personalizados) y mide impacto positivo/negativo."
                : "Analyze sector headlines (async RSS or custom headlines) and classify positive/negative impact."
            }</p>
          </div>
        </div>
        <div class="settings-section-box" style="margin:var(--space-3);">
          <div class="copilot-composer-row">
            <input id="mkt-query" type="text" class="input input-sm full-width" placeholder="${
              isEs ? "Sector, activo o categoría (ej. retail latinoamérica)" : "Sector, ticker, or category (e.g. retail electronics)"
            }" value="${escapeHtml(proj.name)}" />
            <button type="button" id="mkt-fetch-btn" class="btn btn-primary btn-sm">${isEs ? "Analizar" : "Analyze"}</button>
          </div>
          <div id="mkt-sentiment-output" class="fx-preview-banner hidden" style="margin-block-start:var(--space-2);"></div>
        </div>
        <div id="mkt-news-cards" class="news-card-list"></div>
      </div>

      <!-- Smart Alerts & Executive Brief -->
      <div class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">${t(lang, "alerts_extras_title")}</h3>
            <p class="card-subtitle">${isEs ? "Detectadas automáticamente sobre anomalías ML, margen y riesgo de clientes." : "Automatically detected across ML anomalies, margin leakage, and churn risk."}</p>
          </div>
          <span class="pill pill-amber tabular">${fmtNumber(alerts.length, 0, lang)}</span>
        </div>
        <div class="table-wrap" tabindex="0" role="region" aria-label="Smart project alerts">
          ${
            alerts.length
              ? `<table class="data-table">
                  <thead><tr><th>${isEs ? "Severidad" : "Severity"}</th><th>${isEs ? "Alerta" : "Alert"}</th><th>${isEs ? "Acción" : "Action"}</th></tr></thead>
                  <tbody>
                    ${alerts
                      .map(
                        (al) => `
                      <tr>
                        <td><span class="pill ${al.severity === "high" ? "pill-rose" : "pill-amber"}">${escapeHtml(al.severity)}</span></td>
                        <td>
                          <strong>${escapeHtml(al.title)}</strong>
                          <div class="card-subtitle">${escapeHtml(al.body)}</div>
                        </td>
                        <td>
                          ${
                            al.acknowledged
                              ? `<span class="pill pill-muted">✓</span>`
                              : `<button type="button" class="btn btn-ghost btn-xs" data-ack-alert="${escapeHtml(al.id)}">${isEs ? "Revisada" : "Ack"}</button>`
                          }
                        </td>
                      </tr>`
                      )
                      .join("")}
                  </tbody>
                </table>`
              : `<div class="card-subtitle" style="padding:1.125rem;">${isEs ? "Sin alertas críticas activas en este proyecto." : "No active critical alerts for this project."}</div>`
          }
        </div>
      </div>
    </div>

    <!-- AutoML Churn & CLV -->
    <div class="card" style="margin-block-start:var(--space-4);">
      <div class="card-header">
        <div>
          <h3 class="card-title">${isEs ? "AutoML — Predicción de Abandono (Churn) y CLV 90d" : "AutoML — Customer Churn Prediction & 90d CLV"}</h3>
          <p class="card-subtitle tabular">
            ${
              churnRes.available
                ? `${isEs ? "Modelo" : "Model"}: ${escapeHtml(churnRes.model_used)} · ${isEs ? "Alto riesgo" : "High risk"}: <strong>${churnSum.high_risk_count || 0}</strong> (${fmtCurrency(churnSum.revenue_at_high_risk_decimal, proj.currency, lang)}) · CLV 90d: <strong>${fmtCurrency(churnSum.expected_clv_90d_total_decimal, proj.currency, lang)}</strong>`
                : isEs
                  ? "Requiere columnas de cliente, fecha e ingreso."
                  : "Requires customer, date, and revenue columns."
            }
          </p>
        </div>
      </div>
      <div class="table-wrap table-wrap-scroll" tabindex="0" role="region" aria-label="Customer churn prediction">
        ${
          churnCusts.length
            ? `<table class="data-table">
                <thead>
                  <tr>
                    <th>${isEs ? "Cliente" : "Customer"}</th>
                    <th>${isEs ? "Riesgo" : "Risk"}</th>
                    <th class="num">${isEs ? "Prob. Churn" : "Churn Prob."}</th>
                    <th class="num">CLV 90d</th>
                    <th class="num">${isEs ? "Recencia (d)" : "Recency (d)"}</th>
                    <th class="num">${isEs ? "Ingresos Hist." : "Hist. Revenue"}</th>
                  </tr>
                </thead>
                <tbody>
                  ${churnCusts
                    .slice(0, 15)
                    .map(
                      (c) => `
                    <tr>
                      <td><strong>${escapeHtml(c.customer)}</strong></td>
                      <td><span class="pill ${c.risk_tier === "high" ? "pill-rose" : c.risk_tier === "medium" ? "pill-amber" : "pill-emerald"}">${escapeHtml(c.risk_tier)}</span></td>
                      <td class="num">${fmtPercent(c.churn_probability_pct, 1, lang)}</td>
                      <td class="num">${fmtCurrency(c.expected_clv_90d, proj.currency, lang)}</td>
                      <td class="num">${fmtNumber(c.recency_days, 0, lang)}</td>
                      <td class="num">${fmtCurrency(c.historical_revenue, proj.currency, lang)}</td>
                    </tr>`
                    )
                    .join("")}
                </tbody>
              </table>`
            : `<div class="card-subtitle" style="padding:1.125rem;">${isEs ? "Sin datos de clientes suficientes para scoring de Churn." : "Insufficient customer data for churn scoring."}</div>`
        }
      </div>
    </div>

    <!-- Margin Leakage & Entity Resolution -->
    <div class="grid-2" style="margin-block-start:var(--space-4);">
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">${isEs ? "Auditoría de Fuga de Margen por Descuentos" : "Discount Margin Leakage Audit"}</h3>
          <span class="pill pill-rose tabular">${fmtNumber((leak.pockets || []).length, 0, lang)}</span>
        </div>
        <div class="table-wrap" tabindex="0" role="region" aria-label="Margin leakage pockets">
          ${
            (leak.pockets || []).length
              ? `<table class="data-table">
                  <thead><tr><th>${isEs ? "Dimensión" : "Dimension"}</th><th>${isEs ? "Segmento" : "Segment"}</th><th class="num">${isEs ? "Desc. %" : "Disc. %"}</th><th class="num">${isEs ? "Margen %" : "Margin %"}</th><th class="num">${isEs ? "Fuga Est." : "Est. Leakage"}</th></tr></thead>
                  <tbody>
                    ${leak.pockets
                      .slice(0, 8)
                      .map(
                        (pk) => `
                      <tr>
                        <td><span class="pill pill-muted">${escapeHtml(pk.dimension)}</span></td>
                        <td>${escapeHtml(pk.segment)}</td>
                        <td class="num">${fmtPercent(pk.discount_pct, 1, lang)}</td>
                        <td class="num">${fmtPercent(pk.margin_pct, 1, lang)}</td>
                        <td class="num delta-neg">${fmtCurrency(pk.estimated_leakage_decimal, proj.currency, lang)}</td>
                      </tr>`
                      )
                      .join("")}
                  </tbody>
                </table>`
              : `<div class="card-subtitle" style="padding:1.125rem;">${isEs ? "No se detectaron focos severos de fuga de margen." : "No severe discount margin leakage pockets detected."}</div>`
          }
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">${isEs ? "Entity Resolution (Duplicados Sugeridos)" : "Entity Resolution (Duplicate Candidates)"}</h3>
          <span class="pill pill-accent tabular">${fmtNumber(ent.total_candidates || 0, 0, lang)}</span>
        </div>
        <div class="table-wrap" tabindex="0" role="region" aria-label="Duplicate entity candidates">
          ${
            (ent.products?.length || ent.customers?.length)
              ? `<table class="data-table">
                  <thead><tr><th>${isEs ? "Canónico" : "Canonical"}</th><th>${isEs ? "Variante" : "Variant"}</th><th class="num">${isEs ? "Similitud" : "Similarity"}</th></tr></thead>
                  <tbody>
                    ${[...(ent.products || []), ...(ent.customers || [])]
                      .slice(0, 10)
                      .map(
                        (d) => `
                      <tr>
                        <td><strong>${escapeHtml(d.canonical_suggestion)}</strong></td>
                        <td>${escapeHtml(d.variant)}</td>
                        <td class="num">${fmtPercent(d.similarity_pct, 1, lang)}</td>
                      </tr>`
                      )
                      .join("")}
                  </tbody>
                </table>`
              : `<div class="card-subtitle" style="padding:1.125rem;">${isEs ? "Catálogos de productos y clientes consistentes (sin duplicados tipográficos)." : "Product and customer names look clean (no near-duplicates found)."}</div>`
          }
        </div>
      </div>
    </div>
  `;

  container.querySelectorAll("[data-ack-alert]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      await api.acknowledgeAlert(proj.id, btn.dataset.ackAlert);
      showToast(isEs ? "Alerta marcada como revisada" : "Alert acknowledged", "success");
      await renderMarketAlertsTab(container, proj, lang);
    })
  );

  container.querySelector("#mkt-fetch-btn")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const outBox = container.querySelector("#mkt-sentiment-output");
    const cardsBox = container.querySelector("#mkt-news-cards");
    btn.disabled = true;
    outBox.classList.remove("hidden");
    outBox.textContent = isEs ? "Consultando feeds RSS y calculando sentimiento..." : "Fetching RSS feeds and scoring sentiment...";
    try {
      const res = await api.marketSentiment(proj.id, {
        query: container.querySelector("#mkt-query")?.value.trim() || proj.name,
        lang,
        use_cache: false,
      });
      outBox.innerHTML = `
        <strong>${escapeHtml(res.executive_summary)}</strong>
        <span class="pill ${res.overall_sentiment === "positive" ? "pill-emerald" : res.overall_sentiment === "negative" ? "pill-rose" : "pill-muted"} tabular" style="margin-inline-start:0.4rem;">
          ${escapeHtml(res.overall_sentiment)} (${fmtNumber(res.avg_sentiment_score, 3, lang)})
        </span>`;
      const items = res.items || [];
      cardsBox.innerHTML = items.length
        ? items
            .slice(0, 6)
            .map(
              (it) => `
            <article class="news-item-card">
              <div class="card-header" style="padding:0;border:none;">
                <span class="pill ${it.sentiment_label === "positive" ? "pill-emerald" : it.sentiment_label === "negative" ? "pill-rose" : "pill-muted"}">
                  ${it.sentiment_label === "positive" ? "▲" : it.sentiment_label === "negative" ? "▼" : "●"} ${escapeHtml(it.sentiment_label)} (${fmtNumber(it.sentiment_score, 2, lang)})
                </span>
                <span class="card-subtitle tabular">${escapeHtml(it.source || "RSS")}</span>
              </div>
              <strong style="font-size:var(--text-sm);">${escapeHtml(it.title)}</strong>
              ${it.link ? `<a href="${escapeHtml(it.link)}" target="_blank" rel="noopener noreferrer" class="card-subtitle">${isEs ? "Abrir fuente ↗" : "Open source ↗"}</a>` : ""}
            </article>`
            )
            .join("")
        : `<div class="card-subtitle">${isEs ? "No se obtuvieron titulares en modo offline o para esa búsqueda." : "No headlines returned (offline or empty query)."}</div>`;
    } catch (err) {
      outBox.textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  });
}
