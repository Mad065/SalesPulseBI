import { api } from "../api.js";
import { fmtNumber, t } from "../i18n.js";
import { state } from "../state.js";
import { escapeHtml, showToast } from "./hub.js";

const ROLES = [
  "date",
  "revenue",
  "quantity",
  "unit_price",
  "cost",
  "discount",
  "tax",
  "product",
  "category",
  "customer",
  "order_id",
  "region",
  "channel",
  "salesperson",
  "payment_method",
  "status",
];

export async function renderDataStudio(container, onRecomputed) {
  const proj = state.activeProject;
  const lang = state.lang;
  const [detail, quality] = await Promise.all([
    api.getProject(proj.id),
    api.analytics(proj.id, "quality", {}).catch(() => null),
  ]);

  const mapping = detail.mapping || {};
  const roleMap = mapping.role_map || {};
  const rules = mapping.cleaning_rules || {};
  const profiles = mapping.detected_schema || [];
  const rawCols = profiles.map((c) => c.name);

  let page = 1;
  let sortBy = null;
  let sortDir = "desc";

  container.innerHTML = `
    <div class="grid-2">
      <!-- Sources & Sync -->
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">${t(lang, "sources_sync_title")}</h3>
          <div style="display:flex;gap:6px;">
            <label class="btn btn-secondary btn-sm" style="cursor:pointer;">
              + Files
              <input id="ds-add-files" type="file" multiple accept=".csv,.tsv,.txt,.xlsx,.xls,.json,.jsonl,.ndjson,.parquet" class="hidden" />
            </label>
            <button id="ds-sync-btn" class="btn btn-primary btn-sm">↻ ${t(lang, "sync_now")}</button>
          </div>
        </div>
        <div class="table-wrap" style="max-height:240px;">
          <table class="data-table">
            <thead><tr><th>Type</th><th>Path</th><th class="num">Rows</th><th>Status</th><th></th></tr></thead>
            <tbody>
              ${(detail.sources || [])
                .map(
                  (s) => `
                <tr>
                  <td><span class="pill pill-muted">${escapeHtml(s.source_type)}</span></td>
                  <td title="${escapeHtml(s.path)}" style="max-width:220px;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(s.path.split("/").slice(-2).join("/"))}</td>
                  <td class="num">${fmtNumber(s.row_count, 0, lang)}</td>
                  <td><span class="pill ${s.status === "ready" ? "pill-emerald" : "pill-amber"}">${escapeHtml(s.status)}</span></td>
                  <td><button class="btn btn-ghost btn-sm ds-del-src" data-id="${escapeHtml(s.id)}">✕</button></td>
                </tr>`
                )
                .join("")}
            </tbody>
          </table>
        </div>
      </div>

      <!-- Data Health -->
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">${t(lang, "health_title")}</h3>
          <span class="pill ${(quality?.health_score ?? 100) >= 85 ? "pill-emerald" : "pill-amber"}">
            Score: ${quality?.health_score ?? "—"}/100
          </span>
        </div>
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px;">
          <div class="kpi-card"><div class="kpi-label">Clean Rows</div><div class="kpi-value" style="font-size:17px;">${fmtNumber(quality?.total_rows, 0, lang)}</div></div>
          <div class="kpi-card"><div class="kpi-label">Duplicates Removed</div><div class="kpi-value" style="font-size:17px;">${fmtNumber(quality?.ingestion_stats?.duplicates_removed || 0, 0, lang)}</div></div>
          <div class="kpi-card"><div class="kpi-label">Date Gaps</div><div class="kpi-value" style="font-size:17px;">${fmtNumber(quality?.date_gap_count || 0, 0, lang)}</div></div>
        </div>
        <div class="table-wrap" style="max-height:145px;">
          <table class="data-table">
            <thead><tr><th>Column</th><th>Type</th><th class="num">Null %</th><th class="num">Distinct</th></tr></thead>
            <tbody>
              ${(quality?.column_health || [])
                .map(
                  (c) => `
                <tr>
                  <td><code>${escapeHtml(c.column)}</code></td>
                  <td>${escapeHtml(c.type)}</td>
                  <td class="num">${c.null_pct}%</td>
                  <td class="num">${fmtNumber(c.distinct_count, 0, lang)}</td>
                </tr>`
                )
                .join("")}
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Mapping Studio -->
    <div class="card" style="margin-bottom:16px;">
      <div class="card-header">
        <h3 class="card-title">${t(lang, "mapping_title")}</h3>
        <button id="ds-save-map" class="btn btn-primary btn-sm">${t(lang, "save_recompute")}</button>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px;margin-bottom:12px;">
        ${ROLES.map(
          (role) => `
          <div>
            <label style="font-size:11.5px;font-weight:600;color:var(--text-secondary);display:block;margin-bottom:3px;">${role}</label>
            <select class="input full-width input-sm ds-role-select" data-role="${role}">
              <option value="">— auto / unmapped —</option>
              ${rawCols
                .map(
                  (col) =>
                    `<option value="${escapeHtml(col)}" ${roleMap[role] === col ? "selected" : ""}>${escapeHtml(col)}</option>`
                )
                .join("")}
            </select>
          </div>`
        ).join("")}
      </div>
      <div style="display:flex;gap:14px;flex-wrap:wrap;align-items:center;padding-top:10px;border-top:1px solid var(--border);">
        <label class="checkbox-pill"><input id="ds-dedup" type="checkbox" ${rules.deduplicate ? "checked" : ""} /> Deduplicate</label>
        <label class="checkbox-pill"><input id="ds-neg" type="checkbox" ${rules.exclude_negative_revenue ? "checked" : ""} /> Exclude Negative Revenue</label>
      </div>
    </div>

    <!-- Paginated Data Explorer -->
    <div class="card">
      <div class="card-header">
        <h3 class="card-title">${t(lang, "explorer_title")}</h3>
        <div id="ds-page-info" class="card-subtitle"></div>
      </div>
      <div id="ds-table-wrap" class="table-wrap"></div>
      <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:10px;">
        <button id="ds-prev" class="btn btn-secondary btn-sm">← Prev</button>
        <button id="ds-next" class="btn btn-secondary btn-sm">Next →</button>
      </div>
    </div>
  `;

  async function loadTable() {
    const res = await api.analytics(proj.id, "table", {
      filters: state.filters,
      page,
      page_size: 25,
      sort_by: sortBy,
      sort_dir: sortDir,
    });
    const cols = res.columns || [];
    const totalPages = Math.max(1, Math.ceil((res.total || 0) / res.page_size));
    container.querySelector("#ds-page-info").textContent = `${fmtNumber(res.total, 0, lang)} rows · Page ${page}/${totalPages}`;
    container.querySelector("#ds-prev").disabled = page <= 1;
    container.querySelector("#ds-next").disabled = page >= totalPages;

    container.querySelector("#ds-table-wrap").innerHTML = `
      <table class="data-table">
        <thead>
          <tr>
            ${cols
              .map(
                (c) =>
                  `<th class="ds-sort-col" data-col="${escapeHtml(c)}" style="cursor:pointer;">${escapeHtml(c)} ${sortBy === c ? (sortDir === "asc" ? "▲" : "▼") : ""}</th>`
              )
              .join("")}
          </tr>
        </thead>
        <tbody>
          ${(res.rows || [])
            .map(
              (r) => `
            <tr>
              ${cols.map((c) => `<td>${escapeHtml(r[c] ?? "")}</td>`).join("")}
            </tr>`
            )
            .join("")}
        </tbody>
      </table>`;

    container.querySelectorAll(".ds-sort-col").forEach((th) =>
      th.addEventListener("click", () => {
        const col = th.dataset.col;
        if (sortBy === col) sortDir = sortDir === "asc" ? "desc" : "asc";
        else {
          sortBy = col;
          sortDir = "desc";
        }
        loadTable();
      })
    );
  }

  container.querySelector("#ds-prev").onclick = () => {
    if (page > 1) { page--; loadTable(); }
  };
  container.querySelector("#ds-next").onclick = () => { page++; loadTable(); };

  container.querySelector("#ds-sync-btn").onclick = async () => {
    const res = await api.syncProject(proj.id);
    showToast(`Synced ${res.total_rows.toLocaleString()} rows`, "success");
    onRecomputed();
  };

  container.querySelector("#ds-add-files").addEventListener("change", async (e) => {
    if (!e.target.files?.length) return;
    await api.uploadFiles(proj.id, e.target.files);
    showToast("Files added", "success");
    onRecomputed();
  });

  container.querySelectorAll(".ds-del-src").forEach((btn) =>
    btn.addEventListener("click", async () => {
      await api.removeSource(proj.id, btn.dataset.id);
      onRecomputed();
    })
  );

  container.querySelector("#ds-save-map").onclick = async () => {
    const newMap = {};
    container.querySelectorAll(".ds-role-select").forEach((sel) => {
      if (sel.value) newMap[sel.dataset.role] = sel.value;
    });
    const newRules = {
      ...rules,
      deduplicate: container.querySelector("#ds-dedup").checked,
      exclude_negative_revenue: container.querySelector("#ds-neg").checked,
    };
    await api.updateMapping(proj.id, { role_map: newMap, cleaning_rules: newRules });
    showToast(lang === "es" ? "Mapeo recalculado" : "Mapping recomputed", "success");
    onRecomputed();
  };

  await loadTable();
}
