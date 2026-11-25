import { api } from "./api.js";
import { disposeChartsIn } from "./charts.js";
import { openCustomPanelModal } from "./custom_panels.js";
import { openGlobalSettingsModal } from "./global_settings_modal.js";
import { applyStaticI18n, fmtNumber, t } from "./i18n.js";
import { openProjectSettingsModal } from "./settings_modal.js";
import {
  applyGlobalSettingsToState,
  defaultFilters,
  ensureProjectSettings,
  setStylePreset,
  setTheme,
  state,
} from "./state.js";
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
const customizeBtn = document.getElementById("btn-customize-view");

function updateChromeVisibility(inProject) {
  sidebar.classList.toggle("hidden", !inProject);
  crumb.classList.toggle("hidden", !inProject);
  actionsBar.classList.toggle("hidden", !inProject);
  filterBar.classList.toggle("hidden", !inProject || state.activeTab === "data_studio");
  if (customizeBtn) {
    customizeBtn.classList.toggle("hidden", !inProject || state.activeTab === "data_studio");
    customizeBtn.setAttribute("aria-pressed", String(!!state.customizingView));
    customizeBtn.classList.toggle("btn-customize-active", !!state.customizingView);
  }
}

function updateProjectHeaderCrumb(detail) {
  if (!detail) return;
  document.getElementById("crumb-project-name").textContent = detail.name;
  document.getElementById("crumb-project-meta").textContent =
    `${fmtNumber(detail.row_count, 0, state.lang)} ${t(state.lang, "rows").toLowerCase()} · ${fmtNumber(detail.file_count, 0, state.lang)} ${t(state.lang, "files").toLowerCase()}`;
  const curBtn = document.getElementById("crumb-project-currency");
  if (curBtn) {
    const s = ensureProjectSettings(detail);
    const fx = Number(s.exchange_rate || "1");
    curBtn.textContent =
      Number.isFinite(fx) && Math.abs(fx - 1) > 1e-9
        ? `${detail.currency} · ×${s.exchange_rate}`
        : detail.currency;
  }
}

function renderLoadingSkeleton() {
  viewContainer.innerHTML = `
    <div class="grid-kpis" aria-hidden="true">
      <div class="skeleton-block skeleton-kpi"></div>
      <div class="skeleton-block skeleton-kpi"></div>
      <div class="skeleton-block skeleton-kpi"></div>
      <div class="skeleton-block skeleton-kpi"></div>
    </div>
    <div class="grid-2" aria-hidden="true">
      <div class="skeleton-block skeleton-chart"></div>
      <div class="skeleton-block skeleton-chart"></div>
    </div>
  `;
}

function populateSelect(el, values, selectedArr) {
  if (!el) return;
  const current = selectedArr?.[0] || "";
  el.innerHTML =
    `<option value="">${escapeHtml(t(state.lang, "all"))}</option>` +
    (values || [])
      .map((v) => `<option value="${escapeHtml(v)}" ${v === current ? "selected" : ""}>${escapeHtml(v)}</option>`)
      .join("");
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

  document.getElementById("wrap-f-category").classList.toggle("hidden", !opts.categories?.length);
  document.getElementById("wrap-f-region").classList.toggle("hidden", !opts.regions?.length);
  document.getElementById("wrap-f-channel").classList.toggle("hidden", !opts.channels?.length);
  document.getElementById("wrap-f-folder").classList.toggle("hidden", !opts.source_folders?.length);

  // Active cross-filter chips
  const chipsEl = document.getElementById("active-filter-chips");
  const chips = [];
  for (const key of [
    "categories",
    "regions",
    "channels",
    "salespersons",
    "payment_methods",
    "statuses",
    "source_files",
    "source_folders",
    "products",
    "customers",
  ]) {
    for (const val of f[key] || []) {
      chips.push({ key, val });
    }
  }
  chipsEl.innerHTML = chips
    .map(
      (c) =>
        `<span class="chip">${escapeHtml(c.val)} <button type="button" aria-label="Remove ${escapeHtml(c.val)}" data-k="${escapeHtml(c.key)}" data-v="${escapeHtml(c.val)}">×</button></span>`
    )
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

async function refreshActiveProject() {
  if (!state.activeProject) return;
  const detail = await api.getProject(state.activeProject.id);
  state.activeProject = detail;
  updateProjectHeaderCrumb(detail);
  updateCapabilityBadges(detail.capabilities);
  await renderActiveTab();
}

async function openProject(projectId, tab = "overview") {
  const detail = await api.getProject(projectId);
  state.activeProject = detail;
  state.activeTab = tab;
  state.customizingView = false;
  state.filters = defaultFilters(state.lang);

  updateProjectHeaderCrumb(detail);

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
  state.customizingView = false;
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
  renderLoadingSkeleton();

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
    viewContainer.innerHTML = `
      <div class="empty-state">
        <h3>Error</h3>
        <p>${escapeHtml(err.message)}</p>
        <button id="err-retry-btn" class="btn btn-secondary btn-sm">Retry</button>
      </div>`;
    viewContainer.querySelector("#err-retry-btn")?.addEventListener("click", () => renderActiveTab());
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
  const nextTheme = state.theme === "dark" ? "light" : "dark";
  setTheme(nextTheme);
  api.updateGlobalSettings({ theme: nextTheme }).catch(() => {});
  if (state.activeProject) renderActiveTab();
};

const styleSelect = document.getElementById("style-preset-select");
if (styleSelect) {
  styleSelect.addEventListener("change", (e) => {
    const nextStyle = e.target.value;
    setStylePreset(nextStyle);
    api.updateGlobalSettings({ style_preset: nextStyle }).catch(() => {});
    if (state.activeProject) renderActiveTab();
  });
}

document.getElementById("btn-lang-toggle").onclick = () => {
  state.lang = state.lang === "es" ? "en" : "es";
  state.filters.lang = state.lang;
  state.globalSettings.locale = state.lang;
  localStorage.setItem("sp_lang", state.lang);
  document.getElementById("lang-label").textContent = state.lang.toUpperCase();
  applyStaticI18n(state.lang);
  api.updateGlobalSettings({ locale: state.lang }).catch(() => {});
  if (state.activeProject) renderActiveTab();
  else goHub();
};

document.getElementById("btn-global-settings")?.addEventListener("click", () => {
  openGlobalSettingsModal({
    onSaved: async () => {
      syncFilterUI();
      if (state.activeProject) await refreshActiveProject();
      else await goHub();
    },
  });
});

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
  showToast(`Synced ${fmtNumber(res.total_rows, 0, state.lang)} rows`, "success");
  await refreshActiveProject();
};

function openSettingsForActive(initialTab = "general") {
  if (!state.activeProject) return;
  openProjectSettingsModal({
    projectId: state.activeProject.id,
    initialTab,
    onUpdated: refreshActiveProject,
    onOpenCustomPanelBuilder: () => {
      state.activeTab = "overview";
      openCustomPanelModal({
        proj: state.activeProject,
        onSaved: refreshActiveProject,
      });
    },
  });
}

document.getElementById("btn-project-settings")?.addEventListener("click", () =>
  openSettingsForActive("general")
);
document.getElementById("crumb-project-currency")?.addEventListener("click", () =>
  openSettingsForActive("general")
);
document.getElementById("btn-quick-add-files")?.addEventListener("click", () =>
  openSettingsForActive("sources")
);
document.getElementById("btn-customize-view")?.addEventListener("click", () => {
  if (!state.activeProject || state.activeTab === "data_studio") return;
  state.customizingView = !state.customizingView;
  updateChromeVisibility(true);
  renderActiveTab();
});

state.onProjectUpdated = refreshActiveProject;

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

// Keep sticky sidebar & filter-bar offset synchronized with dynamic topbar height
const topbarEl = document.querySelector(".topbar");
if (topbarEl && typeof ResizeObserver !== "undefined") {
  const syncTopbarHeight = () => {
    document.documentElement.style.setProperty("--topbar-h", `${topbarEl.offsetHeight}px`);
  };
  syncTopbarHeight();
  new ResizeObserver(syncTopbarHeight).observe(topbarEl);
}

// Boot
async function bootApp() {
  setTheme(state.themeMode);
  setStylePreset(state.style);
  document.getElementById("lang-label").textContent = state.lang.toUpperCase();
  applyStaticI18n(state.lang);

  try {
    const serverCfg = await api.getGlobalSettings();
    applyGlobalSettingsToState(serverCfg);
    state.filters = defaultFilters(state.lang, serverCfg);
    applyStaticI18n(state.lang);
  } catch {}

  await goHub();
}

bootApp();
