import { api } from "./api.js";
import { applyStaticI18n, fmtCurrency, fmtNumber, t } from "./i18n.js";
import { closeDialog, openDialog } from "./modal.js";
import {
  SUPPORTED_CURRENCIES,
  applyGlobalSettingsToState,
  defaultFilters,
  setStylePreset,
  setTheme,
  state,
} from "./state.js";
import { escapeHtml, showToast } from "./views/hub.js";

const STYLE_PRESET_CARDS = [
  {
    id: "precision",
    title: "Precision FinTech",
    descEs: "Geometría suiza, bordes nítidos de 1px y máxima claridad analítica.",
    descEn: "Swiss editorial geometry, crisp 1px borders, and high analytical clarity.",
    badge: "Default",
  },
  {
    id: "terminal",
    title: "Quantum Terminal",
    descEs: "Alta densidad tipo Bloomberg/TradingView con acentos neón y tipografía tabular.",
    descEn: "High-density quantitative terminal look with neon accents and tabular precision.",
    badge: "Quant",
  },
  {
    id: "editorial",
    title: "Nordic Editorial",
    descEs: "Tonos cálidos tipo papel, encabezados serif ejecutivos y lectura relajada.",
    descEn: "Warm paper surfaces, executive serif headings, and comfortable reading.",
    badge: "Executive",
  },
  {
    id: "bento",
    title: "Atmospheric Bento",
    descEs: "Tarjetas modulares redondeadas con superficies traslúcidas y sombras suaves.",
    descEn: "Rounded modular Bento cards with translucent surfaces and soft elevation.",
    badge: "Modern",
  },
];

export async function openGlobalSettingsModal({ onSaved = null, initialTab = "currency" } = {}) {
  let cfg;
  try {
    cfg = await api.getGlobalSettings();
    applyGlobalSettingsToState(cfg);
  } catch {
    cfg = { ...state.globalSettings };
  }

  const lang = state.lang;
  const isEs = lang === "es";
  let activeTab = initialTab;

  const knownCodes = new Set(SUPPORTED_CURRENCIES.map((c) => c.code));
  const curCode = (cfg.default_currency || "USD").toUpperCase();
  const isCustomCurrency = !knownCodes.has(curCode);

  const dateOrderValue =
    cfg.default_date_dayfirst === true
      ? "dayfirst"
      : cfg.default_date_dayfirst === false
        ? "monthfirst"
        : "auto";

  const dialog = openDialog(
    `
    <div class="modal-header">
      <div>
        <h1 id="modal-dialog-title" class="modal-title">${isEs ? "Configuración General de SalesPulseBI" : "SalesPulseBI General Settings"}</h1>
        <p class="card-subtitle">${
          isEs
            ? "Preferencias predeterminadas de moneda, estilo visual, analítica e ingesta de datos."
            : "Default preferences for currency, visual style, analytics, and data ingestion."
        }</p>
      </div>
      <button type="button" id="gs-close" class="btn btn-ghost btn-sm" aria-label="${isEs ? "Cerrar configuración general" : "Close general settings"}">✕</button>
    </div>

    <div class="tabs-row" role="tablist" aria-label="${isEs ? "Secciones de configuración general" : "General settings sections"}">
      <button type="button" class="tab-btn ${activeTab === "currency" ? "active" : ""}" data-gstab="currency" role="tab" aria-selected="${activeTab === "currency"}">
        ${isEs ? "Moneda e Idioma" : "Currency & Language"}
      </button>
      <button type="button" class="tab-btn ${activeTab === "appearance" ? "active" : ""}" data-gstab="appearance" role="tab" aria-selected="${activeTab === "appearance"}">
        ${isEs ? "Estilo Visual y Tema" : "Visual Style & Theme"}
      </button>
      <button type="button" class="tab-btn ${activeTab === "analytics" ? "active" : ""}" data-gstab="analytics" role="tab" aria-selected="${activeTab === "analytics"}">
        ${isEs ? "Analítica e Ingesta" : "Analytics & Ingestion"}
      </button>
    </div>

    <!-- TAB 1: CURRENCY & LANGUAGE -->
    <div id="gs-pane-currency" class="${activeTab === "currency" ? "" : "hidden"}">
      <div class="settings-section-box">
        <div class="card-header">
          <h2 class="card-title">${isEs ? "Moneda por Defecto para Proyectos" : "Default Currency for Projects"}</h2>
          <button type="button" id="gs-reset-fx" class="btn btn-ghost btn-sm">${isEs ? "Restablecer 1:1" : "Reset 1:1"}</button>
        </div>

        <div class="form-grid-3">
          <div class="form-field">
            <label class="form-label" for="gs-currency">${isEs ? "Moneda por defecto (ISO)" : "Default Currency (ISO)"}</label>
            <select id="gs-currency" class="input full-width" autofocus>
              ${SUPPORTED_CURRENCIES.map(
                (c) =>
                  `<option value="${escapeHtml(c.code)}" ${!isCustomCurrency && curCode === c.code ? "selected" : ""}>${escapeHtml(c.label)}</option>`
              ).join("")}
              <option value="CUSTOM" ${isCustomCurrency ? "selected" : ""}>${isEs ? "Otra moneda ISO..." : "Other ISO code..."}</option>
            </select>
            <input id="gs-currency-custom" class="input input-sm full-width ${isCustomCurrency ? "" : "hidden"}" maxlength="8" placeholder="Ej. BOB, PYG, HNL" value="${isCustomCurrency ? escapeHtml(curCode) : ""}" style="margin-block-start:0.35rem;" />
          </div>

          <div class="form-field">
            <label class="form-label" for="gs-fx-rate">${isEs ? "Tipo de cambio por defecto" : "Default Exchange Rate"}</label>
            <input id="gs-fx-rate" type="number" step="any" min="0.000001" max="1000000" class="input full-width tabular" value="${escapeHtml(cfg.default_exchange_rate || "1")}" />
          </div>

          <div class="form-field">
            <label class="form-label" for="gs-base-currency">${isEs ? "Moneda base (referencia)" : "Base Currency (reference)"}</label>
            <input id="gs-base-currency" class="input full-width" maxlength="8" placeholder="${escapeHtml(curCode)}" value="${escapeHtml(cfg.default_base_currency || "")}" />
          </div>
        </div>

        <div id="gs-fx-preview" class="fx-preview-banner tabular" style="margin-block-end:var(--space-2);"></div>

        <label class="checkbox-pill">
          <input id="gs-apply-existing" type="checkbox" />
          <span>${
            isEs
              ? "Aplicar esta moneda y tipo de cambio también a todos los proyectos existentes"
              : "Also apply this currency and exchange rate to all existing projects"
          }</span>
        </label>
      </div>

      <div class="settings-section-box">
        <h2 class="card-title" style="margin-block-end:var(--space-3);">${isEs ? "Idioma y Formato Numérico" : "Language & Number Formatting"}</h2>
        <div class="form-grid-2-1">
          <div class="form-field">
            <label class="form-label" for="gs-locale">${isEs ? "Idioma de la interfaz" : "Interface Language"}</label>
            <select id="gs-locale" class="input full-width">
              <option value="es" ${cfg.locale === "es" ? "selected" : ""}>Español (ES)</option>
              <option value="en" ${cfg.locale === "en" ? "selected" : ""}>English (EN)</option>
            </select>
          </div>
          <div class="form-field">
            <label class="form-label" for="gs-compact">${isEs ? "Tarjetas de resumen (Hub)" : "Summary Cards (Hub)"}</label>
            <select id="gs-compact" class="input full-width">
              <option value="true" ${cfg.compact_numbers !== false ? "selected" : ""}>${isEs ? "Compacto ($1.2M)" : "Compact ($1.2M)"}</option>
              <option value="false" ${cfg.compact_numbers === false ? "selected" : ""}>${isEs ? "Completo ($1,234,567)" : "Full ($1,234,567)"}</option>
            </select>
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 2: VISUAL STYLE & THEME -->
    <div id="gs-pane-appearance" class="${activeTab === "appearance" ? "" : "hidden"}">
      <div class="settings-section-box">
        <h2 class="card-title" style="margin-block-end:0.35rem;">${isEs ? "Estilo Visual del Dashboard (Preset)" : "Dashboard Visual Style Preset"}</h2>
        <p class="card-subtitle" style="margin-block-end:var(--space-3);">
          ${
            isEs
              ? "Cambia la arquitectura visual, tipografía, densidad y paleta cromática en toda la aplicación."
              : "Switch visual architecture, typography, density, and color tokens across the entire app."
          }
        </p>

        <div class="style-preset-grid" role="radiogroup" aria-label="${isEs ? "Estilo Visual" : "Visual Style"}">
          ${STYLE_PRESET_CARDS.map(
            (st) => `
            <label class="style-preset-card">
              <input type="radio" name="gs-style-preset" value="${escapeHtml(st.id)}" ${cfg.style_preset === st.id ? "checked" : ""} />
              <div class="style-preset-card-head">
                <span class="style-preset-card-title">${escapeHtml(st.title)}</span>
                <span class="pill pill-accent">${escapeHtml(st.badge)}</span>
              </div>
              <p class="style-preset-card-desc">${escapeHtml(isEs ? st.descEs : st.descEn)}</p>
            </label>`
          ).join("")}
        </div>
      </div>

      <div class="settings-section-box">
        <h2 class="card-title" style="margin-block-end:var(--space-2);">${isEs ? "Tema de Color" : "Color Theme"}</h2>
        <div class="panel-type-grid" role="radiogroup" aria-label="${isEs ? "Tema de Color" : "Color Theme"}">
          <label class="checkbox-pill">
            <input type="radio" name="gs-theme-mode" value="dark" ${cfg.theme === "dark" ? "checked" : ""} />
            <span>☾ ${isEs ? "Oscuro (Dark)" : "Dark"}</span>
          </label>
          <label class="checkbox-pill">
            <input type="radio" name="gs-theme-mode" value="light" ${cfg.theme === "light" ? "checked" : ""} />
            <span>☀ ${isEs ? "Claro (Light)" : "Light"}</span>
          </label>
          <label class="checkbox-pill">
            <input type="radio" name="gs-theme-mode" value="system" ${cfg.theme === "system" ? "checked" : ""} />
            <span>◐ ${isEs ? "Automático (Sistema)" : "System Auto"}</span>
          </label>
        </div>
      </div>
    </div>

    <!-- TAB 3: ANALYTICS & INGESTION DEFAULTS -->
    <div id="gs-pane-analytics" class="${activeTab === "analytics" ? "" : "hidden"}">
      <div class="settings-section-box">
        <h2 class="card-title" style="margin-block-end:var(--space-3);">${isEs ? "Valores Iniciales de Analítica y Pronóstico" : "Analytics & Forecast Defaults"}</h2>
        <div class="form-grid-2-1">
          <div class="form-field">
            <label class="form-label" for="gs-granularity">${isEs ? "Granularidad temporal inicial" : "Default Time Granularity"}</label>
            <select id="gs-granularity" class="input full-width">
              ${["day", "week", "month", "quarter", "year"]
                .map(
                  (g) =>
                    `<option value="${g}" ${cfg.default_granularity === g ? "selected" : ""}>${escapeHtml(t(lang, g))}</option>`
                )
                .join("")}
            </select>
          </div>
          <div class="form-field">
            <label class="form-label" for="gs-horizon">${isEs ? "Horizonte de pronóstico (días)" : "Forecast Horizon (days)"}</label>
            <select id="gs-horizon" class="input full-width">
              ${[14, 30, 60, 90, 120, 180]
                .map(
                  (h) =>
                    `<option value="${h}" ${Number(cfg.default_forecast_horizon) === h ? "selected" : ""}>${h} ${isEs ? "días" : "days"}</option>`
                )
                .join("")}
            </select>
          </div>
        </div>

        <div class="card-toolbar" style="margin-block-start:var(--space-2);">
          <label class="checkbox-pill">
            <input id="gs-compare-prev" type="checkbox" ${cfg.default_compare_previous !== false ? "checked" : ""} />
            <span>${isEs ? "Comparar contra periodo anterior por defecto" : "Compare with previous period by default"}</span>
          </label>
          <label class="checkbox-pill">
            <input id="gs-include-cancelled" type="checkbox" ${cfg.default_include_excluded_statuses ? "checked" : ""} />
            <span>${isEs ? "Incluir pedidos cancelados/devueltos por defecto" : "Include cancelled/returned orders by default"}</span>
          </label>
        </div>
      </div>

      <div class="settings-section-box">
        <h2 class="card-title" style="margin-block-end:var(--space-3);">${isEs ? "Reglas de Limpieza e Ingesta para Nuevos Proyectos" : "Data Cleaning & Ingestion Defaults for New Projects"}</h2>
        <div class="form-grid-2-1">
          <div class="form-field">
            <label class="form-label" for="gs-dedup-mode">${isEs ? "Criterio de deduplicación" : "Deduplication Mode"}</label>
            <select id="gs-dedup-mode" class="input full-width">
              <option value="exact" ${cfg.default_dedup_mode === "exact" ? "selected" : ""}>${isEs ? "Fila exacta completa (Exact)" : "Exact full row"}</option>
              <option value="order_product" ${cfg.default_dedup_mode === "order_product" ? "selected" : ""}>${isEs ? "Por Pedido + Producto (Order + Product)" : "By Order ID + Product"}</option>
            </select>
          </div>
          <div class="form-field">
            <label class="form-label" for="gs-date-order">${isEs ? "Formato de fechas ambiguas" : "Ambiguous Date Order"}</label>
            <select id="gs-date-order" class="input full-width">
              <option value="auto" ${dateOrderValue === "auto" ? "selected" : ""}>${isEs ? "Detección automática" : "Auto-detect"}</option>
              <option value="dayfirst" ${dateOrderValue === "dayfirst" ? "selected" : ""}>DD/MM/YYYY (${isEs ? "Día primero" : "Day first"})</option>
              <option value="monthfirst" ${dateOrderValue === "monthfirst" ? "selected" : ""}>MM/DD/YYYY (${isEs ? "Mes primero" : "Month first"})</option>
            </select>
          </div>
        </div>

        <div class="card-toolbar" style="margin-block-start:var(--space-2);">
          <label class="checkbox-pill">
            <input id="gs-deduplicate" type="checkbox" ${cfg.default_deduplicate !== false ? "checked" : ""} />
            <span>${isEs ? "Eliminar filas duplicadas automáticamente" : "Automatically remove duplicate rows"}</span>
          </label>
          <label class="checkbox-pill">
            <input id="gs-exclude-neg" type="checkbox" ${cfg.default_exclude_negative_revenue ? "checked" : ""} />
            <span>${isEs ? "Excluir ingresos negativos por defecto" : "Exclude negative revenue by default"}</span>
          </label>
        </div>
      </div>
    </div>

    <div class="pagination-bar" style="justify-content:space-between;margin-block-start:var(--space-3);">
      <button type="button" id="gs-factory-reset" class="btn btn-ghost btn-sm">${isEs ? "Restablecer valores de fábrica" : "Reset to factory defaults"}</button>
      <div class="card-toolbar">
        <button type="button" id="gs-cancel" class="btn btn-ghost">${isEs ? "Cancelar" : "Cancel"}</button>
        <button type="button" id="gs-save" class="btn btn-primary">${isEs ? "Guardar Configuración General" : "Save General Settings"}</button>
      </div>
    </div>
    `,
    { wide: true }
  );

  if (!dialog) return;

  const origStyle = state.style;
  const origThemeMode = state.themeMode;
  let savedCommitted = false;

  dialog.addEventListener(
    "close",
    () => {
      if (!savedCommitted) {
        setStylePreset(origStyle);
        setTheme(origThemeMode);
      }
    },
    { once: true }
  );

  dialog.querySelector("#gs-close").onclick = closeDialog;
  dialog.querySelector("#gs-cancel").onclick = closeDialog;

  function switchTab(nextTab) {
    activeTab = nextTab;
    dialog.querySelectorAll("[data-gstab]").forEach((btn) => {
      const isAct = btn.dataset.gstab === activeTab;
      btn.classList.toggle("active", isAct);
      btn.setAttribute("aria-selected", String(isAct));
    });
    dialog.querySelector("#gs-pane-currency").classList.toggle("hidden", activeTab !== "currency");
    dialog.querySelector("#gs-pane-appearance").classList.toggle("hidden", activeTab !== "appearance");
    dialog.querySelector("#gs-pane-analytics").classList.toggle("hidden", activeTab !== "analytics");
  }

  dialog.querySelectorAll("[data-gstab]").forEach((btn) =>
    btn.addEventListener("click", () => switchTab(btn.dataset.gstab))
  );

  // Live currency & FX preview
  const curSel = dialog.querySelector("#gs-currency");
  const curCustom = dialog.querySelector("#gs-currency-custom");
  const fxInput = dialog.querySelector("#gs-fx-rate");
  const baseCurInput = dialog.querySelector("#gs-base-currency");
  const previewEl = dialog.querySelector("#gs-fx-preview");

  function resolveCurrency() {
    if (curSel.value === "CUSTOM") {
      return (curCustom.value || "USD").trim().toUpperCase();
    }
    return curSel.value;
  }

  function updateFxPreview() {
    curCustom.classList.toggle("hidden", curSel.value !== "CUSTOM");
    const targetCur = resolveCurrency();
    const rateNum = Number(fxInput.value);
    const baseCur = (baseCurInput.value || "").trim().toUpperCase() || targetCur;

    if (!Number.isFinite(rateNum) || rateNum <= 0) {
      previewEl.textContent = isEs
        ? "Ingresa un factor de tipo de cambio mayor a 0."
        : "Enter an exchange rate factor greater than 0.";
      return;
    }

    if (Math.abs(rateNum - 1) < 1e-9) {
      previewEl.textContent = isEs
        ? `Moneda predeterminada: ${fmtCurrency(12500.5, targetCur, lang)} (${targetCur}) · Sin conversión numérica (1:1).`
        : `Default currency format: ${fmtCurrency(12500.5, targetCur, lang)} (${targetCur}) · No numeric conversion (1:1).`;
    } else {
      const sampleBase = 1000;
      const converted = sampleBase * rateNum;
      previewEl.textContent = isEs
        ? `Conversión por defecto: ${fmtNumber(sampleBase, 2, lang)} ${baseCur} × ${rateNum} = ${fmtCurrency(converted, targetCur, lang)} (${targetCur}).`
        : `Default conversion: ${fmtNumber(sampleBase, 2, lang)} ${baseCur} × ${rateNum} = ${fmtCurrency(converted, targetCur, lang)} (${targetCur}).`;
    }
  }

  curSel.addEventListener("change", updateFxPreview);
  curCustom.addEventListener("input", updateFxPreview);
  fxInput.addEventListener("input", updateFxPreview);
  baseCurInput.addEventListener("input", updateFxPreview);
  dialog.querySelector("#gs-reset-fx").addEventListener("click", () => {
    fxInput.value = "1";
    baseCurInput.value = "";
    updateFxPreview();
  });
  updateFxPreview();

  // Live visual style & theme preview while modal is open
  dialog.querySelectorAll('input[name="gs-style-preset"]').forEach((radio) => {
    radio.addEventListener("change", (e) => {
      if (e.target.checked) setStylePreset(e.target.value);
    });
  });

  dialog.querySelectorAll('input[name="gs-theme-mode"]').forEach((radio) => {
    radio.addEventListener("change", (e) => {
      if (e.target.checked) setTheme(e.target.value);
    });
  });

  // Reset to factory defaults
  dialog.querySelector("#gs-factory-reset").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const fresh = await api.resetGlobalSettings();
      savedCommitted = true;
      applyGlobalSettingsToState(fresh);
      applyStaticI18n(state.lang);
      state.filters = defaultFilters(state.lang, fresh);
      showToast(
        state.lang === "es"
          ? "Configuración general restablecida"
          : "General settings reset to defaults",
        "success"
      );
      closeDialog();
      if (onSaved) await onSaved(fresh);
    } catch (err) {
      showToast(err.message, "error");
      btn.disabled = false;
    }
  });

  // Save General Settings
  dialog.querySelector("#gs-save").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const defaultCurrency = resolveCurrency();
    const fxVal = fxInput.value.trim() || "1";
    const baseCur = baseCurInput.value.trim().toUpperCase() || null;

    if (!/^[A-Z]{3,8}$/.test(defaultCurrency)) {
      showToast(
        isEs ? "Código ISO de moneda inválido (ej. MXN, USD, EUR)" : "Invalid ISO currency code",
        "error"
      );
      return;
    }
    if (!Number.isFinite(Number(fxVal)) || Number(fxVal) <= 0) {
      showToast(
        isEs ? "El tipo de cambio debe ser mayor a 0" : "Exchange rate must be > 0",
        "error"
      );
      return;
    }

    const chosenStyle =
      dialog.querySelector('input[name="gs-style-preset"]:checked')?.value || "precision";
    const chosenTheme =
      dialog.querySelector('input[name="gs-theme-mode"]:checked')?.value || "dark";
    const chosenLocale = dialog.querySelector("#gs-locale").value === "en" ? "en" : "es";
    const compactNumbers = dialog.querySelector("#gs-compact").value === "true";
    const defaultGranularity = dialog.querySelector("#gs-granularity").value;
    const defaultHorizon = Number(dialog.querySelector("#gs-horizon").value) || 30;
    const comparePrev = dialog.querySelector("#gs-compare-prev").checked;
    const includeCancelled = dialog.querySelector("#gs-include-cancelled").checked;
    const dedupMode = dialog.querySelector("#gs-dedup-mode").value;
    const dateOrderRaw = dialog.querySelector("#gs-date-order").value;
    const dateDayfirst =
      dateOrderRaw === "dayfirst" ? true : dateOrderRaw === "monthfirst" ? false : null;
    const deduplicate = dialog.querySelector("#gs-deduplicate").checked;
    const excludeNeg = dialog.querySelector("#gs-exclude-neg").checked;
    const applyToExisting = dialog.querySelector("#gs-apply-existing").checked;

    btn.disabled = true;
    try {
      const updated = await api.updateGlobalSettings({
        default_currency: defaultCurrency,
        default_base_currency: baseCur,
        default_exchange_rate: fxVal,
        style_preset: chosenStyle,
        theme: chosenTheme,
        locale: chosenLocale,
        compact_numbers: compactNumbers,
        default_granularity: defaultGranularity,
        default_forecast_horizon: defaultHorizon,
        default_compare_previous: comparePrev,
        default_include_excluded_statuses: includeCancelled,
        default_deduplicate: deduplicate,
        default_dedup_mode: dedupMode,
        default_exclude_negative_revenue: excludeNeg,
        default_date_dayfirst: dateDayfirst,
        apply_currency_to_existing: applyToExisting,
      });
      savedCommitted = true;
      applyGlobalSettingsToState(updated);
      applyStaticI18n(state.lang);
      state.filters.lang = state.lang;
      state.filters.granularity = updated.default_granularity;
      state.filters.forecast_horizon = updated.default_forecast_horizon;
      state.filters.compare_previous = updated.default_compare_previous;
      state.filters.include_excluded_statuses = updated.default_include_excluded_statuses;
      showToast(
        state.lang === "es"
          ? "Configuración general guardada"
          : "General settings saved",
        "success"
      );
      closeDialog();
      if (onSaved) await onSaved(updated);
    } catch (err) {
      showToast(err.message, "error");
      btn.disabled = false;
    }
  });
}
