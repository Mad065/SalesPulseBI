import { api } from "./api.js";
import { fmtCurrency, fmtNumber, t } from "./i18n.js";
import { closeDialog, openDialog } from "./modal.js";
import { SUPPORTED_CURRENCIES, ensureProjectSettings, setStylePreset, state } from "./state.js";
import { escapeHtml, showToast } from "./views/hub.js";

const STYLE_PRESET_OPTIONS = [
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

export async function openProjectSettingsModal({
  projectId,
  initialTab = "general",
  onUpdated = null,
  onOpenCustomPanelBuilder = null,
}) {
  const lang = state.lang;
  const isEs = lang === "es";
  let detail = await api.getProject(projectId);
  let activeTab = initialTab;
  let sourceMode = "upload";
  let selectedFiles = [];
  let currentBrowsePath = "";

  const knownCodes = new Set(SUPPORTED_CURRENCIES.map((c) => c.code));
  const isCustomCurrency = !knownCodes.has((detail.currency || "USD").toUpperCase());
  const settings = ensureProjectSettings(detail);

  const dialog = openDialog(
    `
    <div class="modal-header">
      <div>
        <h1 id="modal-dialog-title" class="modal-title">${isEs ? "Configuración del Dashboard y Proyecto" : "Dashboard & Project Settings"}</h1>
        <p class="card-subtitle">${escapeHtml(detail.name)} · <span class="tabular" id="ps-meta-counts">${fmtNumber(detail.row_count, 0, lang)} ${t(lang, "rows").toLowerCase()} · ${fmtNumber(detail.file_count, 0, lang)} ${t(lang, "files").toLowerCase()}</span></p>
      </div>
      <button type="button" id="ps-close" class="btn btn-ghost btn-sm" aria-label="${isEs ? "Cerrar configuración" : "Close settings"}">✕</button>
    </div>

    <div class="tabs-row" role="tablist" aria-label="${isEs ? "Secciones de configuración" : "Settings sections"}">
      <button type="button" class="tab-btn ${activeTab === "general" ? "active" : ""}" data-pstab="general" role="tab" aria-selected="${activeTab === "general"}">
        ${isEs ? "Moneda y General" : "Currency & General"}
      </button>
      <button type="button" class="tab-btn ${activeTab === "sources" ? "active" : ""}" data-pstab="sources" role="tab" aria-selected="${activeTab === "sources"}">
        ${isEs ? "Fuentes y Archivos" : "Sources & Files"}
        <span class="pill pill-muted tabular" id="ps-files-pill">${fmtNumber(detail.file_count, 0, lang)}</span>
      </button>
      <button type="button" class="tab-btn ${activeTab === "layout" ? "active" : ""}" data-pstab="layout" role="tab" aria-selected="${activeTab === "layout"}">
        ${isEs ? "Paneles y Vista" : "Panels & Layout"}
      </button>
    </div>

    <!-- TAB 1: GENERAL & CURRENCY -->
    <div id="ps-pane-general" class="${activeTab === "general" ? "" : "hidden"}">
      <div class="form-grid-2-1">
        <div class="form-field">
          <label class="form-label" for="ps-name">${isEs ? "Nombre del Proyecto" : "Project Name"}</label>
          <input id="ps-name" class="input full-width" value="${escapeHtml(detail.name)}" autofocus />
        </div>
        <div class="form-field">
          <label class="form-label" for="ps-tags">${isEs ? "Etiquetas (separadas por coma)" : "Tags (comma-separated)"}</label>
          <input id="ps-tags" class="input full-width" value="${escapeHtml((detail.tags || []).join(", "))}" placeholder="retail, latam, 2026" />
        </div>
      </div>

      <div class="form-field">
        <label class="form-label" for="ps-desc">${isEs ? "Descripción" : "Description"}</label>
        <input id="ps-desc" class="input full-width" value="${escapeHtml(detail.description || "")}" placeholder="${isEs ? "Sucursales, canales, alcance..." : "Branches, channels, scope..."}" />
      </div>

      <div class="settings-section-box">
        <div class="card-header">
          <h2 class="card-title">${isEs ? "Moneda de Visualización y Tipo de Cambio" : "Display Currency & Exchange Rate"}</h2>
          <button type="button" id="ps-reset-fx" class="btn btn-ghost btn-sm">${isEs ? "Restablecer 1:1" : "Reset 1:1"}</button>
        </div>

        <div class="form-grid-3">
          <div class="form-field">
            <label class="form-label" for="ps-currency">${isEs ? "Moneda mostrada (ISO)" : "Display Currency (ISO)"}</label>
            <select id="ps-currency" class="input full-width">
              ${SUPPORTED_CURRENCIES.map(
                (c) =>
                  `<option value="${escapeHtml(c.code)}" ${!isCustomCurrency && detail.currency === c.code ? "selected" : ""}>${escapeHtml(c.label)}</option>`
              ).join("")}
              <option value="CUSTOM" ${isCustomCurrency ? "selected" : ""}>${isEs ? "Otra moneda ISO..." : "Other ISO code..."}</option>
            </select>
            <input id="ps-currency-custom" class="input input-sm full-width ${isCustomCurrency ? "" : "hidden"}" maxlength="6" placeholder="Ej. BOB, PYG, HNL" value="${isCustomCurrency ? escapeHtml(detail.currency) : ""}" style="margin-block-start:0.35rem;" />
          </div>

          <div class="form-field">
            <label class="form-label" for="ps-fx-rate">${isEs ? "Factor de tipo de cambio" : "Exchange Rate Factor"}</label>
            <input id="ps-fx-rate" type="number" step="any" min="0.000001" max="1000000" class="input full-width tabular" value="${escapeHtml(settings.exchange_rate)}" />
          </div>

          <div class="form-field">
            <label class="form-label" for="ps-base-currency">${isEs ? "Moneda origen (referencia)" : "Source Currency (label)"}</label>
            <input id="ps-base-currency" class="input full-width" maxlength="6" placeholder="${escapeHtml(detail.currency || "USD")}" value="${escapeHtml(settings.base_currency || "")}" />
          </div>
        </div>

        <div id="ps-fx-preview" class="fx-preview-banner tabular"></div>
      </div>

      <div class="pagination-bar">
        <button type="button" id="ps-cancel-gen" class="btn btn-ghost">${isEs ? "Cancelar" : "Cancel"}</button>
        <button type="button" id="ps-save-gen" class="btn btn-primary">${isEs ? "Guardar Configuración" : "Save Settings"}</button>
      </div>
    </div>

    <!-- TAB 2: SOURCES & ADD FILES -->
    <div id="ps-pane-sources" class="${activeTab === "sources" ? "" : "hidden"}">
      <div class="settings-section-box" style="margin-block-end:var(--space-3);">
        <div class="tabs-row" style="margin-block-end:var(--space-2);">
          <button type="button" class="tab-btn active" data-srcmode="upload">${isEs ? "Subir Archivos o Carpeta" : "Upload Files or Folder"}</button>
          <button type="button" class="tab-btn" data-srcmode="link">${isEs ? "Vincular Carpeta Local (Auto-Sync)" : "Link Local Folder (Auto-Sync)"}</button>
        </div>

        <div id="ps-src-upload">
          <div id="ps-dropzone" class="dropzone dropzone-compact">
            <div class="insight-title">${isEs ? "Arrastra nuevos archivos de ventas aquí para sumarlos al proyecto" : "Drag & drop additional sales files here to merge into this project"}</div>
            <div class="card-subtitle">CSV, TSV, Excel (.xlsx/.xls), JSON, JSONL, Parquet</div>
            <div class="card-toolbar" style="justify-content:center;margin-block-start:0.65rem;">
              <label class="btn btn-secondary btn-sm">
                ${isEs ? "+ Elegir Archivos" : "+ Select Files"}
                <input id="ps-file-input" type="file" multiple accept=".csv,.tsv,.txt,.xlsx,.xls,.json,.jsonl,.ndjson,.parquet" class="hidden" />
              </label>
              <label class="btn btn-secondary btn-sm">
                ${isEs ? "+ Subir Carpeta" : "+ Select Folder"}
                <input id="ps-dir-input" type="file" webkitdirectory directory multiple class="hidden" />
              </label>
            </div>
            <div id="ps-selected-row" class="hidden" style="margin-block-start:0.75rem;">
              <span id="ps-file-badge" class="pill pill-emerald tabular"></span>
              <button type="button" id="ps-upload-now" class="btn btn-primary btn-sm" style="margin-inline-start:0.5rem;">
                ${isEs ? "Ingerir y Actualizar Dashboard" : "Ingest & Update Dashboard"}
              </button>
            </div>
          </div>
        </div>

        <div id="ps-src-link" class="hidden">
          <div class="card-toolbar" style="margin-block-end:0.5rem;">
            <input id="ps-folder-path" class="input" style="flex:1;" placeholder="/ruta/a/carpeta_ventas" />
            <button type="button" id="ps-native-pick" class="btn btn-secondary btn-sm hidden">${isEs ? "Elegir..." : "Browse..."}</button>
            <button type="button" id="ps-browse-up" class="btn btn-secondary btn-sm">↑ Up</button>
            <button type="button" id="ps-link-now" class="btn btn-primary btn-sm">${isEs ? "Vincular Carpeta" : "Link Folder"}</button>
          </div>
          <div id="ps-fs-status" class="card-subtitle" style="margin-block-end:0.35rem;"></div>
          <div id="ps-fs-list" class="fs-list"></div>
        </div>
      </div>

      <div class="card-header">
        <h2 class="card-title">${isEs ? "Fuentes Conectadas Actualmente" : "Currently Connected Sources"}</h2>
        <button type="button" id="ps-sync-all" class="btn btn-secondary btn-sm">↻ ${t(lang, "sync_now")}</button>
      </div>
      <div id="ps-sources-table-wrap" class="table-wrap table-wrap-sm" tabindex="0" role="region" aria-label="${isEs ? "Fuentes de datos conectadas" : "Connected data sources"}"></div>
    </div>

    <!-- TAB 3: LAYOUT & CUSTOM PANELS -->
    <div id="ps-pane-layout" class="${activeTab === "layout" ? "" : "hidden"}">
      <div class="settings-section-box">
        <h2 class="card-title" style="margin-block-end:0.35rem;">${isEs ? "Estilo Visual del Dashboard (Preset)" : "Dashboard Visual Style Preset"}</h2>
        <p class="card-subtitle" style="margin-block-end:var(--space-3);">
          ${
            isEs
              ? "Selecciona la personalidad visual, tipografía y densidad cromática de la interfaz."
              : "Choose the visual personality, typography, and color density across the app."
          }
        </p>
        <div class="style-preset-grid" role="radiogroup" aria-label="${isEs ? "Estilo Visual" : "Visual Style"}">
          ${STYLE_PRESET_OPTIONS.map(
            (st) => `
            <label class="style-preset-card">
              <input type="radio" name="ps-style-preset" value="${escapeHtml(st.id)}" ${state.style === st.id ? "checked" : ""} />
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
        <h2 class="card-title" style="margin-block-end:0.4rem;">${isEs ? "Personalización de Paneles y Resumen" : "Dashboard Panels & Overview Customization"}</h2>
        <p class="insight-body" style="margin-block-end:var(--space-3);">
          ${
            isEs
              ? "Arrastra directamente cualquier panel desde su icono ⠿ en Inteligencia Comercial, IA & Predictivo o Ingeniería de Datos (Data Studio) para reordenarlo, cambiar su ancho (50% / 100%) u ocultarlo sin necesidad de activar ningún modo."
              : "Drag any panel directly via its ⠿ handle across Sales Intelligence, AI & Predictive, or Data Engineering (Data Studio) to reorder, resize (50% / 100%), or hide modules immediately."
          }
        </p>
        <div class="card-toolbar" style="margin-block-end:var(--space-3);">
          <button type="button" id="ps-add-custom-panel" class="btn btn-primary btn-sm ${onOpenCustomPanelBuilder ? "" : "hidden"}">
            ${t(lang, "new_custom_panel")}
          </button>
          <button type="button" id="ps-reset-all-layouts" class="btn btn-secondary btn-sm">
            ↺ ${isEs ? "Restablecer orden de todas las vistas" : "Reset panel order across all views"}
          </button>
        </div>
        <div id="ps-custom-panels-summary"></div>
      </div>
    </div>
    `,
    { wide: true }
  );

  if (!dialog) return;

  dialog.querySelector("#ps-close").onclick = closeDialog;
  dialog.querySelector("#ps-cancel-gen").onclick = closeDialog;

  function switchModalTab(nextTab) {
    activeTab = nextTab;
    dialog.querySelectorAll("[data-pstab]").forEach((btn) => {
      const isAct = btn.dataset.pstab === activeTab;
      btn.classList.toggle("active", isAct);
      btn.setAttribute("aria-selected", String(isAct));
    });
    dialog.querySelector("#ps-pane-general").classList.toggle("hidden", activeTab !== "general");
    dialog.querySelector("#ps-pane-sources").classList.toggle("hidden", activeTab !== "sources");
    dialog.querySelector("#ps-pane-layout").classList.toggle("hidden", activeTab !== "layout");
  }

  dialog.querySelectorAll("[data-pstab]").forEach((btn) =>
    btn.addEventListener("click", () => switchModalTab(btn.dataset.pstab))
  );

  // --- TAB 1: CURRENCY & FX LIVE PREVIEW ---
  const curSel = dialog.querySelector("#ps-currency");
  const curCustom = dialog.querySelector("#ps-currency-custom");
  const fxInput = dialog.querySelector("#ps-fx-rate");
  const baseCurInput = dialog.querySelector("#ps-base-currency");
  const previewEl = dialog.querySelector("#ps-fx-preview");

  function resolveChosenCurrency() {
    if (curSel.value === "CUSTOM") {
      return (curCustom.value || "USD").trim().toUpperCase();
    }
    return curSel.value;
  }

  function updateFxPreview() {
    curCustom.classList.toggle("hidden", curSel.value !== "CUSTOM");
    const targetCur = resolveChosenCurrency();
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
        ? `Formato activo: ${fmtCurrency(12500.5, targetCur, lang)} (${targetCur}) · Sin conversión numérica (1:1).`
        : `Active format: ${fmtCurrency(12500.5, targetCur, lang)} (${targetCur}) · No numeric conversion (1:1).`;
    } else {
      const sampleBase = 1000;
      const converted = sampleBase * rateNum;
      previewEl.textContent = isEs
        ? `Conversión activa: ${fmtNumber(sampleBase, 2, lang)} ${baseCur} × ${rateNum} = ${fmtCurrency(converted, targetCur, lang)} (${targetCur}) · Los archivos originales no se modifican.`
        : `Active conversion: ${fmtNumber(sampleBase, 2, lang)} ${baseCur} × ${rateNum} = ${fmtCurrency(converted, targetCur, lang)} (${targetCur}) · Original files remain untouched.`;
    }
  }

  curSel.addEventListener("change", updateFxPreview);
  curCustom.addEventListener("input", updateFxPreview);
  fxInput.addEventListener("input", updateFxPreview);
  baseCurInput.addEventListener("input", updateFxPreview);
  dialog.querySelector("#ps-reset-fx").addEventListener("click", () => {
    fxInput.value = "1";
    baseCurInput.value = "";
    updateFxPreview();
  });
  updateFxPreview();

  dialog.querySelector("#ps-save-gen").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const name = dialog.querySelector("#ps-name").value.trim();
    const description = dialog.querySelector("#ps-desc").value.trim();
    const tags = dialog
      .querySelector("#ps-tags")
      .value.split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const currency = resolveChosenCurrency();
    const fxVal = fxInput.value.trim() || "1";
    const baseCur = baseCurInput.value.trim().toUpperCase() || null;

    if (!name) {
      showToast(isEs ? "El nombre del proyecto es obligatorio" : "Project name is required", "error");
      return;
    }
    if (!/^[A-Z]{3,8}$/.test(currency)) {
      showToast(isEs ? "Código ISO de moneda inválido (ej. MXN, USD, EUR)" : "Invalid ISO currency code", "error");
      return;
    }
    if (!Number.isFinite(Number(fxVal)) || Number(fxVal) <= 0) {
      showToast(isEs ? "El tipo de cambio debe ser mayor a 0" : "Exchange rate must be > 0", "error");
      return;
    }

    btn.disabled = true;
    try {
      const nextSettings = {
        ...ensureProjectSettings(detail),
        base_currency: baseCur,
        exchange_rate: fxVal,
      };
      await api.updateProject(detail.id, {
        name,
        description,
        tags,
        currency,
        settings: nextSettings,
      });
      showToast(isEs ? "Configuración actualizada" : "Settings saved", "success");
      closeDialog();
      if (onUpdated) await onUpdated(detail.id);
    } catch (err) {
      showToast(err.message, "error");
      btn.disabled = false;
    }
  });

  // --- TAB 2: SOURCES & FILES ---
  function renderSourcesTable() {
    const sources = detail.sources || [];
    dialog.querySelector("#ps-files-pill").textContent = fmtNumber(detail.file_count, 0, lang);
    dialog.querySelector("#ps-meta-counts").textContent =
      `${fmtNumber(detail.row_count, 0, lang)} ${t(lang, "rows").toLowerCase()} · ${fmtNumber(detail.file_count, 0, lang)} ${t(lang, "files").toLowerCase()}`;

    const wrap = dialog.querySelector("#ps-sources-table-wrap");
    if (!sources.length) {
      wrap.innerHTML = `<div class="card-subtitle" style="padding:1rem;">${isEs ? "Aún no hay fuentes conectadas en este proyecto." : "No sources connected to this project yet."}</div>`;
      return;
    }
    wrap.innerHTML = `
      <table class="data-table">
        <thead>
          <tr>
            <th>${isEs ? "Tipo" : "Type"}</th>
            <th>${isEs ? "Archivo / Ruta" : "File / Path"}</th>
            <th class="num">${t(lang, "rows")}</th>
            <th>${isEs ? "Estado" : "Status"}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${sources
            .map(
              (s) => `
            <tr>
              <td><span class="pill pill-muted">${escapeHtml(s.source_type)}</span></td>
              <td class="cell-truncate" title="${escapeHtml(s.path)}">${escapeHtml(s.path.split("/").slice(-2).join("/"))}</td>
              <td class="num">${fmtNumber(s.row_count, 0, lang)}</td>
              <td><span class="pill ${s.status === "ready" ? "pill-emerald" : "pill-amber"}">${escapeHtml(s.status)}</span></td>
              <td class="num">
                <button type="button" class="btn btn-ghost btn-sm ps-del-source" data-id="${escapeHtml(s.id)}" aria-label="${isEs ? "Quitar fuente" : "Remove source"}">✕</button>
              </td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>
    `;
    wrap.querySelectorAll(".ps-del-source").forEach((btn) =>
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          await api.removeSource(detail.id, btn.dataset.id);
          detail = await api.getProject(detail.id);
          renderSourcesTable();
          showToast(isEs ? "Fuente eliminada y dataset actualizado" : "Source removed and dataset rebuilt", "success");
          if (onUpdated) await onUpdated(detail.id);
        } catch (err) {
          showToast(err.message, "error");
          btn.disabled = false;
        }
      })
    );
  }

  renderSourcesTable();

  async function loadFsBrowser(path = "") {
    const data = await api.browseFs(path);
    currentBrowsePath = data.current_path;
    dialog.querySelector("#ps-folder-path").value = data.current_path;
    dialog.querySelector("#ps-fs-status").textContent =
      `${data.data_files_count} ${isEs ? "archivos compatibles en esta carpeta" : "supported files in this folder"}`;
    const listEl = dialog.querySelector("#ps-fs-list");
    listEl.innerHTML =
      data.directories
        .map(
          (d) =>
            `<button type="button" class="fs-item" data-path="${escapeHtml(d.path)}"><span>📁 ${escapeHtml(d.name)}</span><span>→</span></button>`
        )
        .join("") || `<div class="card-subtitle" style="padding:0.75rem;">No subdirectories</div>`;
    listEl.querySelectorAll(".fs-item").forEach((btn) =>
      btn.addEventListener("click", () => loadFsBrowser(btn.dataset.path))
    );
    dialog.querySelector("#ps-browse-up").onclick = () => {
      if (data.parent_path) loadFsBrowser(data.parent_path);
    };
  }

  const nativePick = dialog.querySelector("#ps-native-pick");
  if (window.pywebview?.api?.pick_folder) {
    nativePick.classList.remove("hidden");
    nativePick.addEventListener("click", async () => {
      const picked = await window.pywebview.api.pick_folder();
      if (picked) {
        dialog.querySelector("#ps-folder-path").value = picked;
        loadFsBrowser(picked);
      }
    });
  }

  dialog.querySelectorAll("[data-srcmode]").forEach((btn) =>
    btn.addEventListener("click", () => {
      dialog.querySelectorAll("[data-srcmode]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      sourceMode = btn.dataset.srcmode;
      dialog.querySelector("#ps-src-upload").classList.toggle("hidden", sourceMode !== "upload");
      dialog.querySelector("#ps-src-link").classList.toggle("hidden", sourceMode !== "link");
      if (sourceMode === "link" && !currentBrowsePath) loadFsBrowser();
    })
  );

  const stageFiles = (files) => {
    selectedFiles = Array.from(files || []);
    const row = dialog.querySelector("#ps-selected-row");
    const badge = dialog.querySelector("#ps-file-badge");
    row.classList.toggle("hidden", !selectedFiles.length);
    badge.textContent = selectedFiles.length
      ? `${selectedFiles.length} ${isEs ? "archivo(s) seleccionados" : "file(s) selected"}`
      : "";
  };

  dialog.querySelector("#ps-file-input").addEventListener("change", (e) => stageFiles(e.target.files));
  dialog.querySelector("#ps-dir-input").addEventListener("change", (e) => stageFiles(e.target.files));

  const dz = dialog.querySelector("#ps-dropzone");
  dz.addEventListener("dragover", (e) => {
    e.preventDefault();
    dz.classList.add("dragover");
  });
  dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("dragover");
    if (e.dataTransfer?.files?.length) stageFiles(e.dataTransfer.files);
  });

  dialog.querySelector("#ps-upload-now").addEventListener("click", async (e) => {
    if (!selectedFiles.length) return;
    const btn = e.currentTarget;
    btn.disabled = true;
    const origTxt = btn.textContent;
    btn.textContent = isEs ? "Ingiriendo..." : "Ingesting...";
    try {
      const res = await api.uploadFiles(detail.id, selectedFiles);
      selectedFiles = [];
      stageFiles([]);
      detail = await api.getProject(detail.id);
      renderSourcesTable();
      showToast(
        isEs
          ? `Archivos integrados (${fmtNumber(res.total_rows, 0, lang)} registros totales)`
          : `Files ingested (${fmtNumber(res.total_rows, 0, lang)} total rows)`,
        "success"
      );
      if (onUpdated) await onUpdated(detail.id);
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = origTxt;
    }
  });

  dialog.querySelector("#ps-link-now").addEventListener("click", async (e) => {
    const path = dialog.querySelector("#ps-folder-path").value.trim();
    if (!path) return;
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const res = await api.linkFolder(detail.id, path, true);
      detail = await api.getProject(detail.id);
      renderSourcesTable();
      showToast(
        isEs
          ? `Carpeta vinculada (${fmtNumber(res.total_rows, 0, lang)} registros)`
          : `Folder linked (${fmtNumber(res.total_rows, 0, lang)} rows)`,
        "success"
      );
      if (onUpdated) await onUpdated(detail.id);
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      btn.disabled = false;
    }
  });

  dialog.querySelector("#ps-sync-all").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const res = await api.syncProject(detail.id);
      detail = await api.getProject(detail.id);
      renderSourcesTable();
      showToast(
        `${fmtNumber(res.total_rows, 0, lang)} ${t(lang, "rows").toLowerCase()}`,
        "success"
      );
      if (onUpdated) await onUpdated(detail.id);
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      btn.disabled = false;
    }
  });

  // --- TAB 3: LAYOUT & CUSTOM PANELS SUMMARY ---
  function renderLayoutSummary() {
    const s = ensureProjectSettings(detail);
    const cp = s.custom_panels || [];
    const summaryEl = dialog.querySelector("#ps-custom-panels-summary");
    if (!cp.length) {
      summaryEl.innerHTML = `<div class="card-subtitle">${isEs ? "Aún no has creado paneles personalizados en Resumen." : "No custom panels created in Overview yet."}</div>`;
      return;
    }
    summaryEl.innerHTML = `
      <div class="form-label" style="margin-block-end:0.4rem;">${isEs ? "Paneles personalizados en Resumen" : "Custom panels in Overview"} (${cp.length})</div>
      <div class="chips-row">
        ${cp
          .map(
            (p) =>
              `<span class="pill pill-accent">${escapeHtml(p.title)} <span class="pill-sub">(${escapeHtml(p.panel_type.toUpperCase())})</span></span>`
          )
          .join("")}
      </div>
    `;
  }
  renderLayoutSummary();

  dialog.querySelectorAll('input[name="ps-style-preset"]').forEach((radio) => {
    radio.addEventListener("change", (e) => {
      if (e.target.checked) {
        const nextStyle = e.target.value;
        setStylePreset(nextStyle);
        api.updateGlobalSettings({ style_preset: nextStyle }).catch(() => {});
        if (onUpdated) onUpdated(detail.id);
      }
    });
  });

  dialog.querySelector("#ps-add-custom-panel")?.addEventListener("click", () => {
    closeDialog();
    if (onOpenCustomPanelBuilder) onOpenCustomPanelBuilder();
  });

  dialog.querySelector("#ps-reset-all-layouts").addEventListener("click", async () => {
    const nextSettings = {
      ...ensureProjectSettings(detail),
      layouts: {},
    };
    await api.updateProject(detail.id, { settings: nextSettings });
    detail = await api.getProject(detail.id);
    showToast(isEs ? "Diseño de vistas restablecido" : "View layouts reset", "success");
    if (onUpdated) await onUpdated(detail.id);
  });
}
