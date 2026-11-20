import { api } from "./api.js";
import { disposeChartsIn } from "./charts.js";
import { applyStaticI18n, t } from "./i18n.js";
import { defaultFilters, setTheme, state } from "./state.js";
import { renderCustomers } from "./views/customers.js";
import { renderDataStudio } from "./views/data_studio.js";
import { renderDimensions } from "./views/dimensions.js";
import { escapeHtml, renderHub, showToast } from "./views/hub.js";
import { renderOverview } from "./views/overview.js";
import { renderProducts } from "./views/products.js";
import { renderTimeseries } from "./views/timeseries.js";

const viewContainer = document.getElementById("view-container");
const sidebar = document.getElementById("sidebar");
const filterBar = document.getElementById("filter-bar");
const crumb = document.getElementById("active-project-crumb");
const actionsBar = document.getElementById("project-actions-bar");

function updateChromeVisibility(inProject) {
  sidebar.classList.toggle("hidden", !inProject);
  crumb.classList.toggle("hidden", !inProject);
  actionsBar.classList.toggle("hidden", !inProject);
  filterBar.classList.toggle("hidden", !inProject || state.activeTab === "data_studio");
}

function populateSelect(el, values, selectedArr) {
  if (!el) return;
  const current = selectedArr?.[0] || "";
  el.innerHTML = `<option value="">${escapeHtml(t(state.lang, "all"))}</option>` +
    (values || []).map((v) => `<option value="${escapeHtml(v)}" ${v === current ? "selected" : ""}>${escapeHtml(v)}</option>`).join("");
}

function syncFilterUI() {
  const f = state.filters;
  document.getElementById("f-date-from").value = f.date_from || "";
  document.getElementById("f-date-to").value = f.date_to || "";
  document.getElementById("f-search").value = f.search || "";
  document.getElementById("f-include-excluded").checked = !!f.include_excluded_statuses;

  const opts = state.filterOptions || {};
  populateSelect(document.getElementById("f-category"), opts.categories, f.categories);
  populateSelect(document.getElementById("f-region"), opts.regions, f.regions);
  populateSelect(document.getElementById("f-channel"), opts.channels, f.channels);
  populateSelect(document.getElementById("f-folder"), opts.source_folders, f.source_folders);

  document.getElementById("wrap-f-category").classList.toggle("hidden", !(opts.categories?.length));
  document.getElementById("wrap-f-region").classList.toggle("hidden", !(opts.regions?.length));
  document.getElementById("wrap-f-channel").classList.toggle("hidden", !(opts.channels?.length));
  document.getElementById("wrap-f-folder").classList.toggle("hidden", !(opts.source_folders?.length));

  // Active cross-filter chips
  const chipsEl = document.getElementById("active-filter-chips");
  const chips = [];
  for (const key of ["categories", "regions", "channels", "source_folders", "products", "customers"]) {
    for (const val of f[key] || []) {
      chips.push({ key, val });
    }
  }
  chipsEl.innerHTML = chips
    .map((c) => `<span class="chip">${escapeHtml(c.val)} <button data-k="${escapeHtml(c.key)}" data-v="${escapeHtml(c.val)}">×</button></span>`)
    .join("");
  chipsEl.querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => {
      state.filters[b.dataset.k] = (state.filters[b.dataset.k] || []).filter((x) => x !== b.dataset.v);
      syncFilterUI();
      renderActiveTab();
    })
  );
}

function updateCapabilityBadges(caps) {
  document.querySelectorAll("[data-lock]").forEach((el) => {
    const key = el.getAttribute("data-lock");
    el.classList.toggle("hidden", !!caps?.[key]);
  });
}

async function openProject(projectId, tab = "overview") {
  const detail = await api.getProject(projectId);
  state.activeProject = detail;
  state.activeTab = tab;
  state.filters = defaultFilters(state.lang);

  document.getElementById("crumb-project-name").textContent = detail.name;
  document.getElementById("crumb-project-meta").textContent =
    `${detail.row_count.toLocaleString()} rows · ${detail.file_count} files`;

  // Saved views dropdown
  const svSel = document.getElementById("saved-views-select");
  svSel.innerHTML =
    `<option value="">${escapeHtml(t(state.lang, "saved_views"))}</option>` +
    (detail.saved_views || []).map((v) => `<option value="${escapeHtml(v.id)}">${escapeHtml(v.name)}</option>`).join("");

  updateCapabilityBadges(detail.capabilities);
  updateChromeVisibility(true);
  await renderActiveTab();
}

async function goHub() {
  disposeChartsIn(viewContainer);
  state.activeProject = null;
  updateChromeVisibility(false);
  await renderHub(viewContainer, openProject);
}

async function renderActiveTab() {
  if (!state.activeProject) return goHub();

  document.querySelectorAll(".nav-item").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === state.activeTab)
  );
  updateChromeVisibility(true);
  disposeChartsIn(viewContainer);

  try {
    if (state.activeTab === "overview") {
      const ov = await renderOverview(viewContainer);
      if (ov?.filter_options) state.filterOptions = ov.filter_options;
      syncFilterUI();
    } else if (state.activeTab === "timeseries") {
      await renderTimeseries(viewContainer, () => switchTab("data_studio"));
    } else if (state.activeTab === "products") {
      await renderProducts(viewContainer, () => switchTab("data_studio"));
    } else if (state.activeTab === "customers") {
      await renderCustomers(viewContainer, () => switchTab("data_studio"));
    } else if (state.activeTab === "dimensions") {
      await renderDimensions(viewContainer);
    } else if (state.activeTab === "data_studio") {
      await renderDataStudio(viewContainer, () => openProject(state.activeProject.id, "data_studio"));
    }
  } catch (err) {
    viewContainer.innerHTML = `<div class="empty-state"><h3>Error</h3><p>${escapeHtml(err.message)}</p></div>`;
  }
}

function switchTab(tab) {
  state.activeTab = tab;
  renderActiveTab();
}

// Wire navigation & controls
document.getElementById("btn-home").onclick = goHub;
document.getElementById("btn-back-hub").onclick = goHub;
document.querySelectorAll(".nav-item").forEach((b) =>
  b.addEventListener("click", () => switchTab(b.dataset.tab))
);

document.getElementById("btn-theme-toggle").onclick = () => {
  setTheme(state.theme === "dark" ? "light" : "dark");
  if (state.activeProject) renderActiveTab();
};

document.getElementById("btn-lang-toggle").onclick = () => {
  state.lang = state.lang === "es" ? "en" : "es";
  state.filters.lang = state.lang;
  localStorage.setItem("sp_lang", state.lang);
  document.getElementById("lang-label").textContent = state.lang.toUpperCase();
  applyStaticI18n(state.lang);
  if (state.activeProject) renderActiveTab();
  else goHub();
};

// Filter handlers
["f-date-from", "f-date-to", "f-category", "f-region", "f-channel", "f-folder", "f-include-excluded"].forEach((id) => {
  document.getElementById(id).addEventListener("change", () => {
    state.filters.date_from = document.getElementById("f-date-from").value || null;
    state.filters.date_to = document.getElementById("f-date-to").value || null;
    const cat = document.getElementById("f-category").value;
    const reg = document.getElementById("f-region").value;
    const ch = document.getElementById("f-channel").value;
    const fld = document.getElementById("f-folder").value;
    state.filters.categories = cat ? [cat] : [];
    state.filters.regions = reg ? [reg] : [];
    state.filters.channels = ch ? [ch] : [];
    state.filters.source_folders = fld ? [fld] : [];
    state.filters.include_excluded_statuses = document.getElementById("f-include-excluded").checked;
    syncFilterUI();
    renderActiveTab();
  });
});

let searchTimer = null;
document.getElementById("f-search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.filters.search = e.target.value.trim();
    renderActiveTab();
  }, 300);
});

document.getElementById("btn-reset-filters").onclick = () => {
  state.filters = defaultFilters(state.lang);
  syncFilterUI();
  renderActiveTab();
};

document.getElementById("btn-save-view").onclick = async () => {
  if (!state.activeProject) return;
  const name = prompt(state.lang === "es" ? "Nombre de la vista:" : "View name:");
  if (!name?.trim()) return;
  await api.createSavedView(state.activeProject.id, {
    name: name.trim(),
    filters: state.filters,
    active_tab: state.activeTab,
  });
  const detail = await api.getProject(state.activeProject.id);
  state.activeProject = detail;
  const svSel = document.getElementById("saved-views-select");
  svSel.innerHTML =
    `<option value="">${escapeHtml(t(state.lang, "saved_views"))}</option>` +
    (detail.saved_views || []).map((v) => `<option value="${escapeHtml(v.id)}">${escapeHtml(v.name)}</option>`).join("");
  showToast("View saved", "success");
};

document.getElementById("saved-views-select").addEventListener("change", (e) => {
  const id = e.target.value;
  const found = (state.activeProject?.saved_views || []).find((v) => v.id === id);
  if (!found) return;
  state.filters = { ...defaultFilters(state.lang), ...(found.filters || {}), lang: state.lang };
  state.activeTab = found.active_tab || "overview";
  syncFilterUI();
  renderActiveTab();
});

document.getElementById("btn-sync-now").onclick = async () => {
  if (!state.activeProject) return;
  const res = await api.syncProject(state.activeProject.id);
  showToast(`Synced ${res.total_rows.toLocaleString()} rows`, "success");
  await openProject(state.activeProject.id, state.activeTab);
};

// Export menu
const exportMenu = document.getElementById("export-dropdown");
document.getElementById("btn-export-menu").onclick = (e) => {
  e.stopPropagation();
  exportMenu.classList.toggle("hidden");
};
document.addEventListener("click", () => exportMenu.classList.add("hidden"));
exportMenu.querySelectorAll("[data-export]").forEach((btn) =>
  btn.addEventListener("click", async () => {
    if (!state.activeProject) return;
    try {
      await api.downloadExport(state.activeProject.id, btn.dataset.export, state.filters);
      showToast("Export ready", "success");
    } catch (err) {
      showToast(err.message, "error");
    }
  })
);

state.onFilterChange = () => {
  syncFilterUI();
  renderActiveTab();
};

// Boot
setTheme(state.theme);
document.getElementById("lang-label").textContent = state.lang.toUpperCase();
applyStaticI18n(state.lang);
goHub();
