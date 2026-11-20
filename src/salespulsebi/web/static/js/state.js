export function defaultFilters(lang = "es") {
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
    include_excluded_statuses: false,
    compare_previous: true,
    granularity: "month",
    forecast_horizon: 30,
    lang,
  };
}

export const state = {
  theme: localStorage.getItem("sp_theme") || "dark",
  lang: localStorage.getItem("sp_lang") || "es",
  projects: [],
  activeProject: null,
  activeTab: "overview",
  filters: defaultFilters(localStorage.getItem("sp_lang") || "es"),
  filterOptions: null,
  onFilterChange: null,
};

export function setTheme(theme) {
  state.theme = theme;
  localStorage.setItem("sp_theme", theme);
  document.documentElement.setAttribute("data-theme", theme);
  const icon = document.getElementById("theme-icon");
  if (icon) icon.textContent = theme === "dark" ? "☀" : "☾";
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
