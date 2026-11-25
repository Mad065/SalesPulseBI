import { api } from "../api.js";
import { fmtCurrency, fmtNumber, t } from "../i18n.js";
import { state } from "../state.js";

export function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function showToast(msg, kind = "info") {
  const c = document.getElementById("toast-container");
  if (!c) return;
  const el = document.createElement("div");
  el.className = `toast toast-${kind}`;
  el.textContent = msg;
  c.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

export async function renderHub(container, onOpenProject) {
  const lang = state.lang;
  let searchQ = "";
  let favOnly = false;

  const projects = await api.listProjects();
  state.projects = projects;

  container.innerHTML = `
    <div class="hub-hero">
      <div>
        <h1 style="margin:0 0 6px 0;font-size:22px;">${t(lang, "hub_title")}</h1>
        <p style="margin:0;color:var(--text-secondary);font-size:13.5px;">${t(lang, "hub_subtitle")}</p>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <button id="hub-seed-demo" class="btn btn-secondary">${t(lang, "load_demo")}</button>
        <label class="btn btn-secondary" style="cursor:pointer;">
          ${t(lang, "import_bundle")}
          <input id="hub-import-zip" type="file" accept=".zip" class="hidden" />
        </label>
        <button id="hub-new-project" class="btn btn-primary">${t(lang, "new_project")}</button>
      </div>
    </div>

    <div style="display:flex;gap:10px;align-items:center;margin-bottom:16px;flex-wrap:wrap;">
      <input id="hub-search" type="text" class="input" style="min-width:240px;" placeholder="${t(lang, "search_projects")}" />
      <label class="checkbox-pill">
        <input id="hub-fav-only" type="checkbox" />
        <span>★ ${t(lang, "favorites_only")}</span>
      </label>
    </div>

    <div id="hub-grid" class="grid-projects"></div>
  `;

  const grid = container.querySelector("#hub-grid");

  function drawCards() {
    const filtered = state.projects.filter((p) => {
      if (favOnly && !p.is_favorite) return false;
      if (searchQ) {
        const q = searchQ.toLowerCase();
        const hay = `${p.name} ${p.description} ${(p.tags || []).join(" ")}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    if (!filtered.length) {
      grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">${t(lang, "no_projects")}</div>`;
      return;
    }

    grid.innerHTML = filtered
      .map(
        (p) => `
      <div class="project-card" data-id="${escapeHtml(p.id)}">
        <div>
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
            <h3 style="margin:0;font-size:16px;">${escapeHtml(p.name)}</h3>
            <button class="btn btn-ghost btn-sm btn-fav" data-id="${escapeHtml(p.id)}" title="Favorite">
              ${p.is_favorite ? "★" : "☆"}
            </button>
          </div>
          <p style="margin:6px 0 10px;color:var(--text-secondary);font-size:12.5px;min-height:34px;">
            ${escapeHtml(p.description || "—")}
          </p>
          <div style="display:flex;gap:5px;flex-wrap:wrap;">
            <span class="pill pill-accent">${escapeHtml(p.currency)}</span>
            ${(p.tags || []).map((tg) => `<span class="pill pill-muted">${escapeHtml(tg)}</span>`).join("")}
            ${p.health_score != null ? `<span class="pill ${p.health_score >= 85 ? "pill-emerald" : "pill-amber"}">Health ${p.health_score}</span>` : ""}
          </div>
        </div>

        <div class="project-card-stats">
          <div>
            <div class="stat-mini-label">${t(lang, "revenue")}</div>
            <div class="stat-mini-val">${p.total_revenue != null ? fmtCurrency(p.total_revenue, p.currency, lang) : "—"}</div>
          </div>
          <div>
            <div class="stat-mini-label">${t(lang, "rows")}</div>
            <div class="stat-mini-val">${fmtNumber(p.row_count, 0, lang)}</div>
          </div>
          <div>
            <div class="stat-mini-label">${t(lang, "files")}</div>
            <div class="stat-mini-val">${fmtNumber(p.file_count, 0, lang)}</div>
          </div>
        </div>

        <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;flex-wrap:wrap;">
          <button class="btn btn-primary btn-sm btn-open" data-id="${escapeHtml(p.id)}">${t(lang, "open_dashboard")}</button>
          <div style="display:flex;gap:4px;">
            <button class="btn btn-ghost btn-sm btn-sync" data-id="${escapeHtml(p.id)}" title="Sync">↻</button>
            <button class="btn btn-ghost btn-sm btn-dup" data-id="${escapeHtml(p.id)}" title="Duplicate">⧉</button>
            <a class="btn btn-ghost btn-sm" href="/api/v1/projects/${encodeURIComponent(p.id)}/export-bundle" title="Export .zip">↓</a>
            <button class="btn btn-ghost btn-sm btn-del" data-id="${escapeHtml(p.id)}" title="Delete">✕</button>
          </div>
        </div>
      </div>`
      )
      .join("");

    grid.querySelectorAll(".btn-open").forEach((b) =>
      b.addEventListener("click", () => onOpenProject(b.dataset.id))
    );
    grid.querySelectorAll(".btn-fav").forEach((b) =>
      b.addEventListener("click", async () => {
        const p = state.projects.find((x) => x.id === b.dataset.id);
        await api.updateProject(p.id, { is_favorite: !p.is_favorite });
        renderHub(container, onOpenProject);
      })
    );
    grid.querySelectorAll(".btn-sync").forEach((b) =>
      b.addEventListener("click", async () => {
        showToast(lang === "es" ? "Sincronizando..." : "Syncing...");
        const res = await api.syncProject(b.dataset.id);
        showToast(`${res.total_rows.toLocaleString()} rows (${res.total_files} files)`, "success");
        renderHub(container, onOpenProject);
      })
    );
    grid.querySelectorAll(".btn-dup").forEach((b) =>
      b.addEventListener("click", async () => {
        await api.duplicateProject(b.dataset.id);
        renderHub(container, onOpenProject);
      })
    );
    grid.querySelectorAll(".btn-del").forEach((b) =>
      b.addEventListener("click", async () => {
        if (!confirm(lang === "es" ? "¿Eliminar este proyecto?" : "Delete this project?")) return;
        await api.deleteProject(b.dataset.id);
        renderHub(container, onOpenProject);
      })
    );
  }

  drawCards();

  container.querySelector("#hub-search").addEventListener("input", (e) => {
    searchQ = e.target.value.trim();
    drawCards();
  });
  container.querySelector("#hub-fav-only").addEventListener("change", (e) => {
    favOnly = e.target.checked;
    drawCards();
  });
  container.querySelector("#hub-seed-demo").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      await api.seedDemo();
      showToast(lang === "es" ? "Proyectos demo listos" : "Demo projects seeded", "success");
      renderHub(container, onOpenProject);
    } catch (err) {
      showToast(err.message, "error");
      e.target.disabled = false;
    }
  });
  container.querySelector("#hub-import-zip").addEventListener("change", async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const proj = await api.importBundle(file);
      showToast(`${proj.name} imported`, "success");
      renderHub(container, onOpenProject);
    } catch (err) {
      showToast(err.message, "error");
    }
  });
  container.querySelector("#hub-new-project").addEventListener("click", () =>
    openNewProjectModal(async (newId) => {
      await onOpenProject(newId);
    })
  );
}

export function openNewProjectModal(onCreated) {
  const lang = state.lang;
  const backdrop = document.getElementById("modal-backdrop");
  const box = document.getElementById("modal-box");
  backdrop.classList.remove("hidden");

  let mode = "upload";
  let selectedFiles = [];
  let currentBrowsePath = "";

  box.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;">
      <h2 style="margin:0;font-size:18px;">${lang === "es" ? "Nuevo Proyecto de Ventas" : "New Sales Project"}</h2>
      <button id="m-close" class="btn btn-ghost btn-sm">✕</button>
    </div>

    <div style="display:grid;grid-template-columns:2fr 1fr;gap:10px;margin-bottom:12px;">
      <div>
        <label style="font-size:12px;color:var(--text-secondary);">${lang === "es" ? "Nombre del Proyecto" : "Project Name"}</label>
        <input id="m-name" class="input full-width" placeholder="${lang === "es" ? "Ej. Ventas Retail 2026" : "e.g. Retail Sales 2026"}" />
      </div>
      <div>
        <label style="font-size:12px;color:var(--text-secondary);">${lang === "es" ? "Moneda" : "Currency"}</label>
        <select id="m-currency" class="input full-width">
          <option value="USD">USD ($)</option>
          <option value="MXN">MXN ($)</option>
          <option value="EUR">EUR (€)</option>
          <option value="COP">COP ($)</option>
          <option value="ARS">ARS ($)</option>
          <option value="GBP">GBP (£)</option>
        </select>
      </div>
    </div>

    <div style="margin-bottom:14px;">
      <label style="font-size:12px;color:var(--text-secondary);">${lang === "es" ? "Descripción (opcional)" : "Description (optional)"}</label>
      <input id="m-desc" class="input full-width" placeholder="${lang === "es" ? "Sucursales, canales, periodo..." : "Branches, channels, period..."}" />
    </div>

    <div class="tabs-row">
      <button class="tab-btn active" data-mode="upload">${lang === "es" ? "Subir Archivos / Carpeta" : "Upload Files / Folder"}</button>
      <button class="tab-btn" data-mode="link">${lang === "es" ? "Vincular Carpeta Local (Auto-Sync)" : "Link Local Folder (Auto-Sync)"}</button>
    </div>

    <div id="m-panel-upload">
      <div id="m-dropzone" class="dropzone">
        <div style="font-weight:600;margin-bottom:6px;">${lang === "es" ? "Arrastra tus archivos de ventas aquí" : "Drag & drop sales files here"}</div>
        <div style="font-size:12px;color:var(--text-muted);margin-bottom:12px;">CSV, TSV, Excel (.xlsx/.xls), JSON, JSONL, Parquet</div>
        <div style="display:flex;justify-content:center;gap:8px;flex-wrap:wrap;">
          <label class="btn btn-secondary btn-sm" style="cursor:pointer;">
            ${lang === "es" ? "Elegir Archivos" : "Select Files"}
            <input id="m-file-input" type="file" multiple accept=".csv,.tsv,.txt,.xlsx,.xls,.json,.jsonl,.ndjson,.parquet" class="hidden" />
          </label>
          <label class="btn btn-secondary btn-sm" style="cursor:pointer;">
            ${lang === "es" ? "Elegir Carpeta" : "Select Folder"}
            <input id="m-dir-input" type="file" webkitdirectory directory multiple class="hidden" />
          </label>
        </div>
        <div id="m-file-count" style="margin-top:10px;font-size:12.5px;color:var(--emerald);font-weight:600;"></div>
      </div>
    </div>

    <div id="m-panel-link" class="hidden">
      <div style="display:flex;gap:8px;margin-bottom:8px;">
        <input id="m-folder-path" class="input" style="flex:1;" placeholder="/path/to/sales_folder" />
        <button id="m-native-pick" class="btn btn-secondary btn-sm hidden">${lang === "es" ? "Elegir..." : "Browse..."}</button>
        <button id="m-browse-up" class="btn btn-secondary btn-sm">↑ Up</button>
      </div>
      <div id="m-fs-status" style="font-size:12px;color:var(--text-secondary);margin-bottom:6px;"></div>
      <div id="m-fs-list" class="fs-list"></div>
    </div>

    <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:18px;">
      <button id="m-cancel" class="btn btn-ghost">${lang === "es" ? "Cancelar" : "Cancel"}</button>
      <button id="m-submit" class="btn btn-primary">${lang === "es" ? "Crear e Ingerir Datos" : "Create & Ingest Data"}</button>
    </div>
  `;

  const close = () => backdrop.classList.add("hidden");
  box.querySelector("#m-close").addEventListener("click", close);
  box.querySelector("#m-cancel").addEventListener("click", close);

  async function loadFs(path = "") {
    const data = await api.browseFs(path);
    currentBrowsePath = data.current_path;
    box.querySelector("#m-folder-path").value = data.current_path;
    box.querySelector("#m-fs-status").textContent = `${data.data_files_count} ${lang === "es" ? "archivos compatibles en esta carpeta" : "supported files in this folder"}`;
    const listEl = box.querySelector("#m-fs-list");
    listEl.innerHTML = data.directories
      .map((d) => `<button class="fs-item" data-path="${escapeHtml(d.path)}"><span>📁 ${escapeHtml(d.name)}</span><span>→</span></button>`)
      .join("") || `<div style="padding:12px;color:var(--text-muted);font-size:12px;">No subdirectories</div>`;
    listEl.querySelectorAll(".fs-item").forEach((btn) =>
      btn.addEventListener("click", () => loadFs(btn.dataset.path))
    );
    box.querySelector("#m-browse-up").onclick = () => {
      if (data.parent_path) loadFs(data.parent_path);
    };
  }

  const nativePickBtn = box.querySelector("#m-native-pick");
  if (window.pywebview?.api?.pick_folder) {
    nativePickBtn.classList.remove("hidden");
    nativePickBtn.addEventListener("click", async () => {
      const picked = await window.pywebview.api.pick_folder();
      if (picked) {
        box.querySelector("#m-folder-path").value = picked;
        loadFs(picked);
      }
    });
  }

  box.querySelectorAll(".tab-btn").forEach((btn) =>
    btn.addEventListener("click", () => {
      box.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      mode = btn.dataset.mode;
      box.querySelector("#m-panel-upload").classList.toggle("hidden", mode !== "upload");
      box.querySelector("#m-panel-link").classList.toggle("hidden", mode !== "link");
      if (mode === "link" && !currentBrowsePath) loadFs();
    })
  );

  const updateFilesCount = (files) => {
    selectedFiles = Array.from(files);
    box.querySelector("#m-file-count").textContent = selectedFiles.length
      ? `${selectedFiles.length} ${lang === "es" ? "archivo(s) listos" : "file(s) selected"}`
      : "";
    if (!box.querySelector("#m-name").value.trim() && selectedFiles.length) {
      const first = selectedFiles[0].webkitRelativePath || selectedFiles[0].name;
      box.querySelector("#m-name").value = first.split("/")[0].replace(/\.[^.]+$/, "");
    }
  };

  box.querySelector("#m-file-input").addEventListener("change", (e) => updateFilesCount(e.target.files));
  box.querySelector("#m-dir-input").addEventListener("change", (e) => updateFilesCount(e.target.files));

  const dz = box.querySelector("#m-dropzone");
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("dragover"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("dragover");
    if (e.dataTransfer?.files?.length) updateFilesCount(e.dataTransfer.files);
  });

  box.querySelector("#m-submit").addEventListener("click", async () => {
    const name = box.querySelector("#m-name").value.trim() || "Sales Project";
    const currency = box.querySelector("#m-currency").value;
    const description = box.querySelector("#m-desc").value.trim();
    const btn = box.querySelector("#m-submit");
    btn.disabled = true;
    btn.textContent = lang === "es" ? "Procesando..." : "Processing...";
    try {
      const proj = await api.createProject({ name, currency, description, locale: lang });
      if (mode === "upload") {
        if (!selectedFiles.length) throw new Error(lang === "es" ? "Selecciona al menos un archivo" : "Select at least one file");
        await api.uploadFiles(proj.id, selectedFiles);
      } else {
        const path = box.querySelector("#m-folder-path").value.trim();
        if (!path) throw new Error("Path required");
        await api.linkFolder(proj.id, path, true);
      }
      close();
      showToast(lang === "es" ? "Proyecto creado exitosamente" : "Project created successfully", "success");
      onCreated(proj.id);
    } catch (err) {
      showToast(err.message, "error");
      btn.disabled = false;
      btn.textContent = lang === "es" ? "Crear e Ingerir Datos" : "Create & Ingest Data";
    }
  });
}
