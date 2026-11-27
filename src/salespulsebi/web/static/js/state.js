export const VALID_STYLES = ["precision", "terminal", "editorial", "bento"];
export const VALID_THEMES = ["dark", "light", "system"];
export const VALID_GRANULARITIES = ["day", "week", "month", "quarter", "year"];

export const SUPPORTED_CURRENCIES = [
  { code: "USD", label: "USD ($)" },
  { code: "MXN", label: "MXN ($)" },
  { code: "EUR", label: "EUR (€)" },
  { code: "COP", label: "COP ($)" },
  { code: "ARS", label: "ARS ($)" },
  { code: "CLP", label: "CLP ($)" },
  { code: "PEN", label: "PEN (S/)" },
  { code: "BRL", label: "BRL (R$)" },
  { code: "GBP", label: "GBP (£)" },
  { code: "CAD", label: "CAD ($)" },
  { code: "JPY", label: "JPY (¥)" },
  { code: "CHF", label: "CHF (Fr)" },
  { code: "UYU", label: "UYU ($)" },
  { code: "GTQ", label: "GTQ (Q)" },
  { code: "CRC", label: "CRC (₡)" },
  { code: "DOP", label: "DOP (RD$)" },
];

export function defaultGlobalSettings() {
  const storedTheme = localStorage.getItem("sp_theme_mode") || localStorage.getItem("sp_theme") || "dark";
  const storedStyle = localStorage.getItem("sp_style");
  const storedLang = localStorage.getItem("sp_lang") || "es";
  const storedCurrency = localStorage.getItem("sp_default_currency") || "USD";
  return {
    default_currency: storedCurrency,
    default_base_currency: null,
    default_exchange_rate: "1",
    style_preset: VALID_STYLES.includes(storedStyle) ? storedStyle : "precision",
    theme: VALID_THEMES.includes(storedTheme) ? storedTheme : "dark",
    locale: storedLang === "en" ? "en" : "es",
    compact_numbers: true,
    default_granularity: "month",
    default_forecast_horizon: 30,
    default_compare_previous: true,
    default_include_excluded_statuses: false,
    default_deduplicate: true,
    default_dedup_mode: "exact",
    default_exclude_negative_revenue: false,
    default_date_dayfirst: null,
  };
}

export function defaultFilters(lang = "es", globalCfg = null) {
  const g = globalCfg || state?.globalSettings || defaultGlobalSettings();
  return {
    date_from: null,
    date_to: null,
    search: "",
    categories: [],
    regions: [],
    channels: [],
    salespersons: [],
    payment_methods: [],
    statuses: [],
    source_files: [],
    source_folders: [],
    products: [],
    customers: [],
    include_excluded_statuses: !!g.default_include_excluded_statuses,
    compare_previous: g.default_compare_previous ?? true,
    granularity: VALID_GRANULARITIES.includes(g.default_granularity) ? g.default_granularity : "month",
    forecast_horizon: Number(g.default_forecast_horizon) || 30,
    lang,
  };
}

export function ensureProjectSettings(proj) {
  const raw = proj?.settings || {};
  return {
    base_currency: raw.base_currency || null,
    exchange_rate: raw.exchange_rate ? String(raw.exchange_rate) : "1",
    layouts: raw.layouts && typeof raw.layouts === "object" ? structuredClone(raw.layouts) : {},
    custom_panels: Array.isArray(raw.custom_panels) ? structuredClone(raw.custom_panels) : [],
  };
}

const initialGlobal = defaultGlobalSettings();

export const state = {
  globalSettings: initialGlobal,
  themeMode: initialGlobal.theme,
  theme: initialGlobal.theme === "light" ? "light" : "dark",
  style: initialGlobal.style_preset,
  lang: initialGlobal.locale,
  sidebarCollapsed: localStorage.getItem("sp_sidebar_collapsed") === "true",
  projects: [],
  activeProject: null,
  activeTab: "overview",
  filters: null,
  filterOptions: null,
  onFilterChange: null,
  onProjectUpdated: null,
};

state.filters = defaultFilters(state.lang, initialGlobal);

function resolveEffectiveTheme(mode) {
  if (mode === "system") {
    if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
      return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    }
    return "dark";
  }
  return mode === "light" ? "light" : "dark";
}

export function setTheme(modeOrTheme) {
  const chosenMode = VALID_THEMES.includes(modeOrTheme) ? modeOrTheme : "dark";
  const effective = resolveEffectiveTheme(chosenMode);
  state.themeMode = chosenMode;
  state.theme = effective;
  state.globalSettings.theme = chosenMode;
  localStorage.setItem("sp_theme_mode", chosenMode);
  localStorage.setItem("sp_theme", effective);
  document.documentElement.setAttribute("data-theme", effective);
  const icon = document.getElementById("theme-icon");
  if (icon) icon.textContent = effective === "dark" ? "☀" : "☾";
}

export function setStylePreset(style) {
  const chosen = VALID_STYLES.includes(style) ? style : "precision";
  state.style = chosen;
  state.globalSettings.style_preset = chosen;
  localStorage.setItem("sp_style", chosen);
  document.documentElement.setAttribute("data-style", chosen);
}

export function setSidebarCollapsed(collapsed) {
  const isCollapsed = !!collapsed;
  state.sidebarCollapsed = isCollapsed;
  localStorage.setItem("sp_sidebar_collapsed", String(isCollapsed));
  const sidebarEl = document.getElementById("sidebar");
  if (sidebarEl) {
    sidebarEl.classList.toggle("is-collapsed", isCollapsed);
  }
  const toggleBtn = document.getElementById("btn-sidebar-toggle");
  if (toggleBtn) {
    toggleBtn.setAttribute("aria-expanded", String(!isCollapsed));
  }
}

export function applyGlobalSettingsToState(cfg) {
  if (!cfg || typeof cfg !== "object") return;
  state.globalSettings = {
    ...defaultGlobalSettings(),
    ...cfg,
    default_exchange_rate: cfg.default_exchange_rate ? String(cfg.default_exchange_rate) : "1",
  };
  localStorage.setItem("sp_default_currency", state.globalSettings.default_currency || "USD");
  setTheme(state.globalSettings.theme);
  setStylePreset(state.globalSettings.style_preset);
  if (state.globalSettings.locale === "es" || state.globalSettings.locale === "en") {
    state.lang = state.globalSettings.locale;
    localStorage.setItem("sp_lang", state.lang);
    const lbl = document.getElementById("lang-label");
    if (lbl) lbl.textContent = state.lang.toUpperCase();
  }
}

if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
  window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
    if (state.themeMode === "system") {
      setTheme("system");
    }
  });
}

export function toggleCrossFilter(key, value) {
  const arr = state.filters[key];
  if (!Array.isArray(arr)) return;
  const idx = arr.indexOf(value);
  if (idx >= 0) {
    arr.splice(idx, 1);
  } else {
    state.filters[key] = [value];
  }
  if (typeof state.onFilterChange === "function") {
    state.onFilterChange();
  }
}
