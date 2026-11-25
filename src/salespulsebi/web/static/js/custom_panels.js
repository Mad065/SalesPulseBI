import { api } from "./api.js";
import { mountChart, palette } from "./charts.js";
import { colLabel, fmtCurrency, fmtNumber, fmtPercent, t } from "./i18n.js";
import { closeDialog, openDialog } from "./modal.js";
import { ensureProjectSettings, state, toggleCrossFilter } from "./state.js";
import { escapeHtml, showToast } from "./views/hub.js";

const PANEL_TYPES = [
  { id: "bar", es: "Barras", en: "Bar Chart" },
  { id: "hbar", es: "Barras Horiz.", en: "Horiz. Bar" },
  { id: "line", es: "Línea", en: "Line Chart" },
  { id: "area", es: "Área", en: "Area Chart" },
  { id: "donut", es: "Dona (%)", en: "Donut (%)" },
  { id: "kpi", es: "Tarjeta KPI", en: "KPI Card" },
  { id: "table", es: "Tabla Top-N", en: "Top-N Table" },
];

const AGGREGATIONS = [
  { id: "sum", es: "Suma (SUM)", en: "Sum (SUM)" },
  { id: "avg", es: "Promedio (AVG)", en: "Average (AVG)" },
  { id: "count", es: "Conteo de Filas (COUNT)", en: "Row Count (COUNT)" },
  { id: "count_distinct", es: "Valores Únicos (DISTINCT)", en: "Unique Values (DISTINCT)" },
  { id: "max", es: "Máximo (MAX)", en: "Maximum (MAX)" },
  { id: "min", es: "Mínimo (MIN)", en: "Minimum (MIN)" },
];

export function formatCustomMetric(val, valueFormat, currency, lang, compact = false) {
  if (val === null || val === undefined || Number.isNaN(Number(val))) return "—";
  if (valueFormat === "currency") return fmtCurrency(val, currency, lang, compact);
  if (valueFormat === "percent") return fmtPercent(val, 1, lang, false);
  const num = Number(val);
  const decimals = Number.isInteger(num) ? 0 : 2;
  return fmtNumber(num, decimals, lang);
}

export function renderCustomPanelShell(spec) {
  const subtitle =
    spec.panel_type === "kpi"
      ? `${spec.aggregation.toUpperCase()}${spec.y_column ? `(${spec.y_column})` : ""}`
      : `${spec.aggregation.toUpperCase()}${spec.y_column && spec.aggregation !== "count" ? `(${spec.y_column})` : ""} · ${spec.x_column || ""}`;

  if (spec.panel_type === "kpi") {
    return `
      <div class="card custom-kpi-panel" id="cp-body-${escapeHtml(spec.id)}">
        <div class="kpi-label">${escapeHtml(spec.title)}</div>
        <div class="kpi-value tabular" data-cp-kpi-val>—</div>
        <div class="kpi-footer">
          <span class="delta-badge delta-neu" data-cp-kpi-delta>—</span>
          <span class="card-subtitle">${escapeHtml(subtitle)}</span>
        </div>
      </div>`;
  }

  return `
    <div class="card">
      <div class="card-header">
        <h3 class="card-title">${escapeHtml(spec.title)}</h3>
        <span class="pill pill-muted">${escapeHtml(subtitle)}</span>
      </div>
      <div id="cp-viz-${escapeHtml(spec.id)}" class="${spec.panel_type === "table" ? "" : "chart-box"}"></div>
    </div>`;
}

export function mountCustomPanelVisual(rootEl, spec, panelData, proj, lang, allowCrossFilter = true) {
  const isEs = lang === "es";
  if (!rootEl) return;

  if (!panelData || !panelData.available) {
    const target = rootEl.querySelector(`#cp-viz-${CSS.escape(spec.id)}`) || rootEl;
    target.innerHTML = `<div class="empty-state"><p>${
      isEs
        ? "La columna configurada en este panel ya no está presente en los datos."
        : "The column configured for this panel is not present in the dataset."
    }</p></div>`;
    return;
  }

  const fmt = panelData.value_format || "number";

  if (spec.panel_type === "kpi") {
    const valEl = rootEl.querySelector("[data-cp-kpi-val]");
    const deltaEl = rootEl.querySelector("[data-cp-kpi-delta]");
    if (valEl) {
      valEl.textContent = formatCustomMetric(panelData.value, fmt, proj.currency, lang, false);
      if (fmt === "currency" && panelData.value != null) {
        valEl.title = fmtCurrency(panelData.value, proj.currency, lang, false);
      }
    }
    if (deltaEl) {
      const d = panelData.delta_pct;
      if (d !== null && d !== undefined) {
        const isPos = d >= 0;
        const arrow = isPos ? "▲" : "▼";
        deltaEl.className = `delta-badge ${isPos ? "delta-pos" : "delta-neg"}`;
        deltaEl.innerHTML = `<span aria-hidden="true">${arrow}</span> ${fmtPercent(d, 1, lang, true)}`;
      } else {
        deltaEl.className = "delta-badge delta-neu";
        deltaEl.textContent = "—";
      }
    }
    return;
  }

  const vizEl = rootEl.querySelector(`#cp-viz-${CSS.escape(spec.id)}`) || rootEl;
  const rows = panelData.rows || [];
  if (!rows.length) {
    vizEl.innerHTML = `<div class="empty-state">${isEs ? "Sin datos para los filtros seleccionados" : "No data for active filters"}</div>`;
    return;
  }

  const cfKey = allowCrossFilter ? panelData.cross_filter_key : null;
  const onClickItem = cfKey ? (params) => toggleCrossFilter(cfKey, params.name) : null;

  if (spec.panel_type === "table") {
    vizEl.innerHTML = `
      <div class="table-wrap table-wrap-sm" tabindex="0" role="region" aria-label="${escapeHtml(spec.title)}">
        <table class="data-table">
          <thead>
            <tr>
              <th>${escapeHtml(colLabel(spec.x_column, lang))}</th>
              <th class="num">${escapeHtml(spec.aggregation.toUpperCase())}</th>
              <th class="num">%</th>
              <th class="num">${t(lang, "rows")}</th>
            </tr>
          </thead>
          <tbody>
            ${rows
              .map(
                (r) => `
              <tr class="${cfKey ? "row-clickable" : ""}" data-lbl="${escapeHtml(r.label)}">
                <td>${escapeHtml(r.label)}</td>
                <td class="num">${formatCustomMetric(r.value, fmt, proj.currency, lang)}</td>
                <td class="num">${fmtPercent(r.share_pct, 1, lang)}</td>
                <td class="num">${fmtNumber(r.row_count, 0, lang)}</td>
              </tr>`
              )
              .join("")}
          </tbody>
        </table>
      </div>`;
    if (cfKey) {
      vizEl.querySelectorAll("tbody tr").forEach((tr) =>
        tr.addEventListener("click", () => toggleCrossFilter(cfKey, tr.dataset.lbl))
      );
    }
    return;
  }

  const p = palette();
  const tooltipFmt = (item) => {
    const pt = Array.isArray(item) ? item[0] : item;
    const rawVal = typeof pt.value === "object" && pt.value !== null ? pt.value : pt.value;
    return `<strong>${escapeHtml(pt.name)}</strong><br/>${formatCustomMetric(rawVal, fmt, proj.currency, lang)}`;
  };

  if (spec.panel_type === "donut") {
    mountChart(
      vizEl,
      {
        tooltip: { trigger: "item", formatter: tooltipFmt },
        series: [
          {
            type: "pie",
            radius: ["44%", "74%"],
            itemStyle: { borderRadius: 6, borderColor: p.bg, borderWidth: 2 },
            label: { color: p.text, formatter: "{b}: {d}%" },
            data: rows.map((r) => ({ name: r.label, value: r.value })),
          },
        ],
      },
      onClickItem
    );
    return;
  }

  if (spec.panel_type === "hbar") {
    const rev = [...rows].reverse();
    mountChart(
      vizEl,
      {
        tooltip: { trigger: "axis", formatter: tooltipFmt },
        grid: { left: 115, right: 24, top: 20, bottom: 24 },
        xAxis: { type: "value", splitLine: { lineStyle: { color: p.grid } } },
        yAxis: {
          type: "category",
          data: rev.map((r) => r.label),
          axisLabel: { width: 100, overflow: "truncate" },
        },
        series: [
          {
            type: "bar",
            data: rev.map((r) => r.value),
            itemStyle: { color: p.accent, borderRadius: [0, 5, 5, 0] },
          },
        ],
      },
      onClickItem
    );
    return;
  }

  const isArea = spec.panel_type === "area";
  const isLine = spec.panel_type === "line" || isArea;
  mountChart(
    vizEl,
    {
      tooltip: { trigger: "axis", formatter: tooltipFmt },
      xAxis: {
        type: "category",
        data: rows.map((r) => r.label),
        axisLabel: { rotate: rows.length > 7 ? 24 : 0, width: 88, overflow: "truncate" },
      },
      yAxis: { type: "value", splitLine: { lineStyle: { color: p.grid } } },
      series: [
        isLine
          ? {
              type: "line",
              smooth: true,
              data: rows.map((r) => r.value),
              lineStyle: { width: 2.5, color: p.accent },
              itemStyle: { color: p.accent },
              ...(isArea ? { areaStyle: { color: p.accent, opacity: 0.18 } } : {}),
            }
          : {
              type: "bar",
              data: rows.map((r) => r.value),
              itemStyle: { color: p.accent, borderRadius: [4, 4, 0, 0] },
            },
      ],
    },
    onClickItem
  );
}

export async function openCustomPanelModal({
  proj,
  existingSpec = null,
  availableColumns = null,
  onSaved = null,
}) {
  const lang = state.lang;
  const isEs = lang === "es";

  let cols = availableColumns;
  if (!cols || !cols.length) {
    const res = await api.analytics(proj.id, "custom-panels", { filters: state.filters });
    cols = res.available_columns || [];
  }

  const numericCols = cols.filter((c) => c.is_numeric);
  const defaultX =
    existingSpec?.x_column ||
    cols.find((c) => ["_channel", "_region", "_salesperson", "_payment_method", "_category", "_product"].includes(c.name))?.name ||
    cols[0]?.name ||
    "";
  const defaultY =
    existingSpec?.y_column ||
    numericCols.find((c) => c.name === "_revenue")?.name ||
    numericCols[0]?.name ||
    "";

  let chosenType = existingSpec?.panel_type || "bar";

  const dialog = openDialog(
    `
    <div class="modal-header">
      <h1 id="modal-dialog-title" class="modal-title">
        ${existingSpec ? (isEs ? "Editar Panel Personalizado" : "Edit Custom Panel") : isEs ? "Nuevo Panel Personalizado en Resumen" : "New Custom Panel in Overview"}
      </h1>
      <button type="button" id="cp-close" class="btn btn-ghost btn-sm" aria-label="Close">✕</button>
    </div>

    <div class="form-field">
      <label class="form-label">${isEs ? "Tipo de visualización" : "Visualization Type"}</label>
      <div class="panel-type-grid" role="radiogroup" aria-label="${isEs ? "Tipo de panel" : "Panel type"}">
        ${PANEL_TYPES.map(
          (pt) => `
          <button type="button" class="panel-type-pill ${chosenType === pt.id ? "active" : ""}" data-ptype="${pt.id}" aria-pressed="${chosenType === pt.id}">
            ${escapeHtml(isEs ? pt.es : pt.en)}
          </button>`
        ).join("")}
      </div>
    </div>

    <div class="form-grid-2-1">
      <div class="form-field">
        <label class="form-label" for="cp-title">${isEs ? "Título del Panel" : "Panel Title"}</label>
        <input id="cp-title" class="input full-width" maxlength="80" value="${escapeHtml(existingSpec?.title || "")}" placeholder="${isEs ? "Ej. Ventas por Vendedor" : "e.g. Revenue by Salesperson"}" autofocus />
      </div>
      <div class="form-field">
        <label class="form-label" for="cp-span">${isEs ? "Tamaño en el Dashboard" : "Panel Width"}</label>
        <select id="cp-span" class="input full-width">
          <option value="1" ${(existingSpec?.span || 1) === 1 ? "selected" : ""}>${isEs ? "Mitad (1 columna / 50%)" : "Half width (1 col / 50%)"}</option>
          <option value="2" ${existingSpec?.span === 2 ? "selected" : ""}>${isEs ? "Ancho completo (2 columnas / 100%)" : "Full width (2 cols / 100%)"}</option>
        </select>
      </div>
    </div>

    <div class="form-grid-3">
      <div class="form-field" id="cp-x-wrap">
        <label class="form-label" for="cp-x">${isEs ? "Agrupar por (Dimensión X)" : "Group By (X Dimension)"}</label>
        <select id="cp-x" class="input full-width">
          ${cols
            .map(
              (c) =>
                `<option value="${escapeHtml(c.name)}" ${c.name === defaultX ? "selected" : ""}>${escapeHtml(colLabel(c.name, lang))}</option>`
            )
            .join("")}
        </select>
      </div>

      <div class="form-field">
        <label class="form-label" for="cp-agg">${isEs ? "Operación / Agregación" : "Aggregation"}</label>
        <select id="cp-agg" class="input full-width">
          ${AGGREGATIONS.map(
            (a) =>
              `<option value="${a.id}" ${(existingSpec?.aggregation || "sum") === a.id ? "selected" : ""}>${escapeHtml(isEs ? a.es : a.en)}</option>`
          ).join("")}
        </select>
      </div>

      <div class="form-field" id="cp-y-wrap">
        <label class="form-label" for="cp-y">${isEs ? "Métrica / Columna (Y)" : "Metric / Column (Y)"}</label>
        <select id="cp-y" class="input full-width"></select>
      </div>
    </div>

    <div class="form-grid-2-1">
      <div class="form-field">
        <label class="form-label" for="cp-format">${isEs ? "Formato numérico" : "Value Format"}</label>
        <select id="cp-format" class="input full-width">
          <option value="auto" ${(existingSpec?.format_kind || "auto") === "auto" ? "selected" : ""}>${isEs ? "Automático (detectar moneda / número)" : "Automatic (detect currency / number)"}</option>
          <option value="currency" ${existingSpec?.format_kind === "currency" ? "selected" : ""}>${isEs ? `Moneda (${proj.currency})` : `Currency (${proj.currency})`}</option>
          <option value="number" ${existingSpec?.format_kind === "number" ? "selected" : ""}>${isEs ? "Número estándar" : "Standard Number"}</option>
          <option value="percent" ${existingSpec?.format_kind === "percent" ? "selected" : ""}>${isEs ? "Porcentaje (%)" : "Percentage (%)"}</option>
        </select>
      </div>

      <div class="form-field" id="cp-limit-wrap">
        <label class="form-label" for="cp-limit">${isEs ? "Elementos Top-N" : "Top-N Limit"}</label>
        <select id="cp-limit" class="input full-width">
          ${[5, 8, 10, 12, 15, 20, 30, 50]
            .map(
              (n) =>
                `<option value="${n}" ${(existingSpec?.limit || 12) === n ? "selected" : ""}>Top ${n}</option>`
            )
            .join("")}
        </select>
      </div>
    </div>

    <div class="settings-section-box">
      <div class="form-label" style="margin-block-end:0.4rem;">${isEs ? "Vista Previa en Vivo" : "Live Preview"}</div>
      <div id="cp-preview-host"></div>
    </div>

    <div class="pagination-bar">
      <button type="button" id="cp-cancel" class="btn btn-ghost">${isEs ? "Cancelar" : "Cancel"}</button>
      <button type="button" id="cp-save" class="btn btn-primary">${existingSpec ? (isEs ? "Guardar Cambios" : "Save Changes") : isEs ? "Agregar a Resumen" : "Add to Overview"}</button>
    </div>
    `,
    { wide: true }
  );

  if (!dialog) return;

  dialog.querySelector("#cp-close").onclick = closeDialog;
  dialog.querySelector("#cp-cancel").onclick = closeDialog;

  const xWrap = dialog.querySelector("#cp-x-wrap");
  const yWrap = dialog.querySelector("#cp-y-wrap");
  const limitWrap = dialog.querySelector("#cp-limit-wrap");
  const xSel = dialog.querySelector("#cp-x");
  const aggSel = dialog.querySelector("#cp-agg");
  const ySel = dialog.querySelector("#cp-y");
  const fmtSel = dialog.querySelector("#cp-format");
  const limSel = dialog.querySelector("#cp-limit");
  const spanSel = dialog.querySelector("#cp-span");
  const titleInput = dialog.querySelector("#cp-title");
  const previewHost = dialog.querySelector("#cp-preview-host");

  function syncYOptions(preferredY = null) {
    const agg = aggSel.value;
    const targetList = agg === "count_distinct" ? cols : numericCols;
    const currentVal = preferredY ?? ySel.value ?? defaultY;
    ySel.innerHTML = targetList
      .map(
        (c) =>
          `<option value="${escapeHtml(c.name)}" ${c.name === currentVal ? "selected" : ""}>${escapeHtml(colLabel(c.name, lang))}</option>`
      )
      .join("");
    yWrap.classList.toggle("hidden", agg === "count");
    xWrap.classList.toggle("hidden", chosenType === "kpi");
    limitWrap.classList.toggle("hidden", chosenType === "kpi");
  }

  syncYOptions(defaultY);

  function buildDraftSpec() {
    const agg = aggSel.value;
    const xCol = chosenType === "kpi" ? null : xSel.value || null;
    const yCol = agg === "count" ? null : ySel.value || null;
    const cleanXLabel = xCol ? colLabel(xCol, lang).replace(/\s*\([^)]+\)$/, "") : "";
    const cleanYLabel = yCol ? colLabel(yCol, lang).replace(/\s*\([^)]+\)$/, "") : t(lang, "rows");
    const autoTitle =
      chosenType === "kpi"
        ? `${agg.toUpperCase()} · ${cleanYLabel}`
        : `${cleanYLabel} ${isEs ? "por" : "by"} ${cleanXLabel}`;

    return {
      id: existingSpec?.id || `custom_${Date.now().toString(36)}`,
      title: titleInput.value.trim() || autoTitle.slice(0, 80),
      panel_type: chosenType,
      x_column: xCol,
      y_column: yCol,
      aggregation: agg,
      format_kind: fmtSel.value,
      limit: Number(limSel.value) || 12,
      span: Number(spanSel.value) === 2 ? 2 : 1,
    };
  }

  let previewReqId = 0;
  async function refreshPreview() {
    const reqId = ++previewReqId;
    const draft = buildDraftSpec();
    const previewSpec = { ...draft, id: "preview_slot" };
    previewHost.innerHTML = renderCustomPanelShell(previewSpec);
    const chartBox = previewHost.querySelector(".chart-box");
    if (chartBox) chartBox.classList.replace("chart-box", "chart-box-sm");

    try {
      const res = await api.analytics(proj.id, "custom-panels", {
        filters: state.filters,
        preview_panel: previewSpec,
      });
      if (reqId !== previewReqId || !dialog.open) return;
      mountCustomPanelVisual(
        previewHost,
        previewSpec,
        res.panels?.preview_slot,
        proj,
        lang,
        false
      );
    } catch (err) {
      if (reqId !== previewReqId) return;
      previewHost.innerHTML = `<div class="card-subtitle" style="padding:0.75rem;">${escapeHtml(err.message)}</div>`;
    }
  }

  dialog.querySelectorAll(".panel-type-pill").forEach((btn) =>
    btn.addEventListener("click", () => {
      chosenType = btn.dataset.ptype;
      dialog.querySelectorAll(".panel-type-pill").forEach((b) => {
        const isAct = b === btn;
        b.classList.toggle("active", isAct);
        b.setAttribute("aria-pressed", String(isAct));
      });
      syncYOptions();
      refreshPreview();
    })
  );

  aggSel.addEventListener("change", () => {
    syncYOptions();
    refreshPreview();
  });
  [xSel, ySel, fmtSel, limSel].forEach((el) => el.addEventListener("change", refreshPreview));
  titleInput.addEventListener("input", () => {
    const titleEl = previewHost.querySelector(".card-title, .kpi-label");
    if (titleEl && titleInput.value.trim()) titleEl.textContent = titleInput.value.trim();
  });

  refreshPreview();

  dialog.querySelector("#cp-save").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const spec = buildDraftSpec();
      const settings = ensureProjectSettings(proj);
      const existingIdx = settings.custom_panels.findIndex((p) => p.id === spec.id);
      if (existingIdx >= 0) {
        settings.custom_panels[existingIdx] = spec;
      } else {
        settings.custom_panels.push(spec);
      }

      // Sync panel span into overview layout config as well
      const ovLayout = settings.layouts.overview || {
        panel_order: [],
        hidden_panels: [],
        panel_spans: {},
        kpi_order: [],
        hidden_kpis: [],
      };
      ovLayout.panel_spans = ovLayout.panel_spans || {};
      ovLayout.panel_spans[spec.id] = spec.span;
      settings.layouts.overview = ovLayout;

      const updated = await api.updateProject(proj.id, { settings });
      proj.settings = updated.settings || settings;
      showToast(
        existingSpec
          ? isEs
            ? "Panel personalizado actualizado"
            : "Custom panel updated"
          : isEs
            ? "Panel agregado a Resumen"
            : "Custom panel added to Overview",
        "success"
      );
      closeDialog();
      if (onSaved) await onSaved(spec);
    } catch (err) {
      showToast(err.message, "error");
      btn.disabled = false;
    }
  });
}
