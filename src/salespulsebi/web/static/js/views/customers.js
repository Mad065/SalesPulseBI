import { api } from "../api.js";
import { mountChart, palette } from "../charts.js";
import { fmtCurrency, fmtNumber, fmtPercent, t } from "../i18n.js";
import { renderManagedDashboard } from "../layout_manager.js";
import { state, toggleCrossFilter } from "../state.js";
import { escapeHtml } from "./hub.js";

export async function renderCustomers(container, onGoMapping) {
  const proj = state.activeProject;
  const lang = state.lang;
  const [data, churnData] = await Promise.all([
    api.analytics(proj.id, "customers", state.filters),
    api.ml(proj.id, "churn", { filters: state.filters, limit: 200 }).catch(() => ({ available: false })),
  ]);
  const churnByCustomer = new Map(
    ((churnData && churnData.customers) || []).map((c) => [c.customer, c])
  );

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
  const p = palette();

  const panels = [
    {
      id: "cu_kpis",
      title: t(lang, "kpi_strip_title"),
      defaultSpan: 2,
      renderHtml: () => `
        <div class="grid-kpis grid-no-margin">
          <div class="kpi-card">
            <div class="kpi-label">${t(lang, "unique_customers")}</div>
            <div class="kpi-value">${fmtNumber(s.total_customers, 0, lang)}</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">${lang === "es" ? "Tasa Recompra" : "Repeat Rate"}</div>
            <div class="kpi-value">${fmtPercent(s.repeat_rate_pct, 1, lang)}</div>
            <div class="kpi-footer"><span class="pill pill-emerald">${fmtNumber(s.repeat_customers, 0, lang)} repeat</span></div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">${lang === "es" ? "LTV Promedio" : "Avg Revenue / Customer"}</div>
            <div class="kpi-value">${fmtCurrency(s.avg_revenue_per_customer, proj.currency, lang)}</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">${lang === "es" ? "Concentración Top 10%" : "Top 10% Concentration"}</div>
            <div class="kpi-value">${fmtPercent(conc.top_10_pct_share, 1, lang)}</div>
            <div class="kpi-footer"><span class="card-subtitle">Top 1%: ${fmtPercent(conc.top_1_pct_share, 1, lang)} · Top 5%: ${fmtPercent(conc.top_5_pct_share, 1, lang)}</span></div>
          </div>
        </div>`,
      mount: () => {},
    },
    {
      id: "cu_rfm",
      title: t(lang, "rfm_title"),
      defaultSpan: 1,
      renderHtml: () => `
        <div class="card">
          <div class="card-header"><h3 class="card-title">${t(lang, "rfm_title")}</h3></div>
          <div id="cu-rfm-chart" class="chart-box"></div>
        </div>`,
      mount: (panelEl) => {
        const segs = data.rfm_segments || [];
        mountChart(panelEl.querySelector("#cu-rfm-chart"), {
          xAxis: { type: "category", data: segs.map((x) => x.segment), axisLabel: { rotate: 22 } },
          yAxis: [
            { type: "value", splitLine: { lineStyle: { color: p.grid } } },
            { type: "value", splitLine: { show: false } },
          ],
          series: [
            { name: "Revenue", type: "bar", data: segs.map((x) => x.revenue) },
            { name: "Customers", type: "line", yAxisIndex: 1, data: segs.map((x) => x.customers), itemStyle: { color: p.up } },
          ],
        });
      },
    },
    {
      id: "cu_nvr",
      title: t(lang, "nvr_title"),
      defaultSpan: 1,
      renderHtml: () => `
        <div class="card">
          <div class="card-header"><h3 class="card-title">${t(lang, "nvr_title")}</h3></div>
          <div id="cu-nvr-chart" class="chart-box"></div>
        </div>`,
      mount: (panelEl) => {
        const nvr = data.new_vs_returning || [];
        const el = panelEl.querySelector("#cu-nvr-chart");
        if (nvr.length) {
          mountChart(el, {
            legend: { data: ["New", "Returning"], textStyle: { color: p.text }, bottom: 0 },
            xAxis: { type: "category", data: nvr.map((r) => r.period) },
            yAxis: { type: "value", splitLine: { lineStyle: { color: p.grid } } },
            series: [
              { name: "New", type: "bar", stack: "rev", data: nvr.map((r) => r.new_revenue), itemStyle: { color: p.accent } },
              { name: "Returning", type: "bar", stack: "rev", data: nvr.map((r) => r.returning_revenue), itemStyle: { color: p.up } },
            ],
          });
        } else if (el) {
          el.innerHTML = `<div class="empty-state">Requires date column</div>`;
        }
      },
    },
    {
      id: "cu_cohorts",
      title: t(lang, "cohorts_title"),
      defaultSpan: 2,
      renderHtml: () => `
        <div class="card">
          <div class="card-header"><h3 class="card-title">${t(lang, "cohorts_title")}</h3></div>
          <div id="cu-cohorts-chart" class="chart-box-tall"></div>
        </div>`,
      mount: (panelEl) => {
        const cohortRows = data.cohorts?.rows || [];
        const el = panelEl.querySelector("#cu-cohorts-chart");
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
          mountChart(el, {
            tooltip: {
              trigger: "item",
              formatter: (pr) =>
                `${yCohorts[pr.value[1]]} · ${xCols[pr.value[0]]}: <b>${fmtPercent(pr.value[2], 1, lang)}</b>`,
            },
            grid: { left: 110, right: 20, top: 24, bottom: 48 },
            xAxis: { type: "category", data: xCols },
            yAxis: { type: "category", data: yCohorts },
            visualMap: {
              min: 0,
              max: 100,
              orient: "horizontal",
              left: "center",
              bottom: 0,
              inRange: { color: [p.elevated, p.accent, p.up] },
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
        } else if (el) {
          el.innerHTML = `<div class="empty-state">Requires date + repeat customers</div>`;
        }
      },
    },
    {
      id: "cu_table",
      title: t(lang, "customers_table_title"),
      defaultSpan: 2,
      renderHtml: () => `
        <div class="card">
          <div class="card-header">
            <h3 class="card-title">${t(lang, "customers_table_title")}</h3>
            <select id="cu-seg-filter" class="input input-sm" aria-label="Filter RFM Segment">
              <option value="">${t(lang, "all")}</option>
              ${(data.rfm_segments || [])
                .map(
                  (seg) =>
                    `<option value="${escapeHtml(seg.segment)}">${escapeHtml(seg.segment)} (${fmtNumber(seg.customers, 0, lang)})</option>`
                )
                .join("")}
            </select>
          </div>
          <div id="cu-table-wrap" class="table-wrap table-wrap-scroll" tabindex="0" role="region" aria-label="${escapeHtml(t(lang, "customers_table_title"))}"></div>
        </div>`,
      mount: (panelEl) => {
        const wrap = panelEl.querySelector("#cu-table-wrap");
        function drawCustomerTable(segFilter = "") {
          const rows = (data.top_customers || []).filter((c) => !segFilter || c.segment === segFilter);
          wrap.innerHTML = `
            <table class="data-table">
              <thead><tr><th>Customer</th><th>Segment</th><th class="num">Recency (d)</th><th class="num">Freq</th><th class="num">Monetary</th><th class="num">RFM</th><th class="num">Churn ML</th><th class="num">CLV 90d</th></tr></thead>
              <tbody>
                ${rows
                  .map((c) => {
                    const ch = churnByCustomer.get(c.customer);
                    return `
                  <tr class="row-clickable" data-cust="${escapeHtml(c.customer)}">
                    <td>${escapeHtml(c.customer)}</td>
                    <td><span class="pill ${c.segment === "Champions" ? "pill-emerald" : c.segment === "At Risk" ? "pill-rose" : "pill-accent"}">${escapeHtml(c.segment)}</span></td>
                    <td class="num">${fmtNumber(c.recency_days, 0, lang)}</td>
                    <td class="num">${fmtNumber(c.frequency, 0, lang)}</td>
                    <td class="num">${fmtCurrency(c.monetary, proj.currency, lang)}</td>
                    <td class="num tabular">${c.r_score}-${c.f_score}-${c.m_score}</td>
                    <td class="num">${ch ? `<span class="pill ${ch.risk_tier === "high" ? "pill-rose" : ch.risk_tier === "medium" ? "pill-amber" : "pill-emerald"} tabular">${fmtPercent(ch.churn_probability_pct, 1, lang)}</span>` : "—"}</td>
                    <td class="num">${ch ? fmtCurrency(ch.expected_clv_90d, proj.currency, lang) : "—"}</td>
                  </tr>`;
                  })
                  .join("")}
              </tbody>
            </table>`;
          wrap.querySelectorAll("tbody tr").forEach((tr) =>
            tr.addEventListener("click", () => toggleCrossFilter("customers", tr.dataset.cust))
          );
        }
        drawCustomerTable();
        panelEl
          .querySelector("#cu-seg-filter")
          ?.addEventListener("change", (e) => drawCustomerTable(e.target.value));
      },
    },
  ];

  renderManagedDashboard({
    container,
    proj,
    tabKey: "customers",
    panels,
    onRerender: () => renderCustomers(container, onGoMapping),
  });
}
