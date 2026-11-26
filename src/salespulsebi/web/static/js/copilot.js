import { api } from "./api.js";
import { ensureProjectSettings, state } from "./state.js";
import { escapeHtml, showToast } from "./views/hub.js";

let isOpen = false;
let activeAbort = null;
let historyTurns = [];
let onNavigateTab = null;
let onRefreshProject = null;

/** Safe minimal markdown formatter (escapes all HTML first to prevent XSS). */
function renderSafeMarkdown(rawText) {
  const escaped = escapeHtml(rawText || "");
  return escaped
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\n\n/g, "</p><p>")
    .replace(/\n/g, "<br />");
}

function buildContextChips() {
  const proj = state.activeProject;
  const f = state.filters || {};
  if (!proj) return "";
  const chips = [
    `<span class="pill pill-accent">${escapeHtml(proj.currency)}</span>`,
    `<span class="pill pill-muted tabular">${escapeHtml(f.granularity || "month")}</span>`,
  ];
  if (f.date_from || f.date_to) {
    chips.push(
      `<span class="pill pill-muted tabular">${escapeHtml(f.date_from || "…")} → ${escapeHtml(f.date_to || "…")}</span>`
    );
  }
  for (const key of ["categories", "regions", "channels"]) {
    for (const v of f[key] || []) {
      chips.push(`<span class="pill pill-muted">${escapeHtml(v)}</span>`);
    }
  }
  return chips.join("");
}

function smartScrollBottom(el) {
  if (!el) return;
  const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
  if (distFromBottom < 90) {
    el.scrollTop = el.scrollHeight;
  }
}

function renderDrawerShell() {
  const drawer = document.getElementById("copilot-drawer");
  if (!drawer) return;
  const isEs = state.lang === "es";

  drawer.innerHTML = `
    <div class="copilot-header">
      <div>
        <h2 class="card-title" style="margin:0;">✦ ${isEs ? "Copiloto Financiero IA" : "AI Financial Copilot"}</h2>
        <p class="card-subtitle" id="copilot-provider-badge" style="margin:0;">${
          isEs ? "Cero aritmética alucinada · Motor DuckDB" : "Zero hallucinated math · DuckDB Engine"
        }</p>
      </div>
      <div class="card-toolbar">
        <button type="button" id="copilot-clear" class="btn btn-ghost btn-xs">${isEs ? "Limpiar" : "Clear"}</button>
        <button type="button" id="copilot-close" class="btn btn-ghost btn-sm" aria-label="${isEs ? "Cerrar Copiloto" : "Close Copilot"}">✕</button>
      </div>
    </div>
    <div id="copilot-context-strip" class="copilot-context-strip">${buildContextChips()}</div>
    <div id="copilot-messages" class="copilot-messages" aria-live="polite" aria-busy="false">
      <div class="copilot-msg copilot-msg-ai">
        <p style="margin:0;">${
          isEs
            ? "Pregúntame sobre ingresos, márgenes, pronósticos AutoML, anomalías, variación Precio-Volumen-Mix, documentos PDF o pídeme crear un gráfico para fijar en tu Overview."
            : "Ask about revenue, margins, AutoML forecasts, anomalies, Price-Volume-Mix drivers, uploaded PDFs, or ask me to create a panel to pin to your Overview."
        }</p>
        <div class="copilot-citations">
          <button type="button" class="pill pill-btn copilot-quick-q">${isEs ? "¿Por qué variaron los ingresos (PVM)?" : "What drove revenue changes (PVM)?"}</button>
          <button type="button" class="pill pill-btn copilot-quick-q">${isEs ? "Pronóstico AutoML del próximo trimestre" : "AutoML forecast for next quarter"}</button>
          <button type="button" class="pill pill-btn copilot-quick-q">${isEs ? "Crea un gráfico de ingresos por categoría" : "Create a revenue by category chart"}</button>
        </div>
      </div>
    </div>
    <div class="copilot-composer">
      <div class="copilot-composer-row">
        <textarea id="copilot-input" class="input" rows="2" maxlength="2000" placeholder="${
          isEs ? "Pregunta sobre tus métricas o documentos... (Enter envía)" : "Ask about your metrics or documents... (Enter sends)"
        }"></textarea>
        <button type="button" id="copilot-send" class="btn btn-primary btn-sm">${isEs ? "Enviar" : "Send"}</button>
        <button type="button" id="copilot-stop" class="btn btn-secondary btn-sm hidden">${isEs ? "Detener" : "Stop"}</button>
      </div>
    </div>
  `;

  drawer.querySelector("#copilot-close").onclick = () => setCopilotOpen(false);
  drawer.querySelector("#copilot-clear").onclick = () => {
    historyTurns = [];
    renderDrawerShell();
  };
  drawer.querySelector("#copilot-stop").onclick = () => {
    if (activeAbort) {
      activeAbort.abort();
      activeAbort = null;
    }
  };
  drawer.querySelectorAll(".copilot-quick-q").forEach((btn) =>
    btn.addEventListener("click", () => sendPrompt(btn.textContent.trim()))
  );

  const input = drawer.querySelector("#copilot-input");
  const sendBtn = drawer.querySelector("#copilot-send");
  sendBtn.onclick = () => {
    const text = input.value.trim();
    if (text) {
      input.value = "";
      sendPrompt(text);
    }
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendBtn.click();
    } else if (e.key === "Escape") {
      if (activeAbort) activeAbort.abort();
      else setCopilotOpen(false);
    }
  });

  // Update provider badge asynchronously
  api
    .aiStatus()
    .then((st) => {
      const badge = drawer.querySelector("#copilot-provider-badge");
      if (!badge) return;
      badge.textContent = st.enabled
        ? `${st.provider.toUpperCase()} (${st.reasoning_model}) · ${st.mode === "local" ? "100% Local" : "Cloud + PII Redact"}`
        : isEs
          ? "Modo Determinista Local (DuckDB + AutoML)"
          : "Local Deterministic Mode (DuckDB + AutoML)";
    })
    .catch(() => {});
}

async function pinProposedPanel(panelSpec) {
  const proj = state.activeProject;
  if (!proj || !panelSpec) return;
  const settings = ensureProjectSettings(proj);
  const exists = settings.custom_panels.some((p) => p.id === panelSpec.id);
  const spec = exists ? { ...panelSpec, id: `${panelSpec.id}_${Date.now().toString(36)}` } : panelSpec;
  settings.custom_panels.push(spec);
  await api.updateProject(proj.id, { settings });
  showToast(state.lang === "es" ? "Panel fijado en Overview" : "Panel pinned to Overview", "success");
  if (typeof onRefreshProject === "function") await onRefreshProject();
  if (typeof onNavigateTab === "function") onNavigateTab("overview");
}

async function sendPrompt(message) {
  const proj = state.activeProject;
  if (!proj) return;
  const drawer = document.getElementById("copilot-drawer");
  const listEl = drawer?.querySelector("#copilot-messages");
  const sendBtn = drawer?.querySelector("#copilot-send");
  const stopBtn = drawer?.querySelector("#copilot-stop");
  if (!listEl) return;

  if (activeAbort) activeAbort.abort();
  activeAbort = new AbortController();

  const ctxStrip = drawer.querySelector("#copilot-context-strip");
  if (ctxStrip) ctxStrip.innerHTML = buildContextChips();

  // Append user bubble
  const userDiv = document.createElement("div");
  userDiv.className = "copilot-msg copilot-msg-user";
  userDiv.textContent = message;
  listEl.appendChild(userDiv);

  // Append assistant streaming container
  const aiDiv = document.createElement("div");
  aiDiv.className = "copilot-msg copilot-msg-ai";
  aiDiv.innerHTML = `
    <div class="copilot-status-step"><span aria-hidden="true">◌</span> <span class="status-txt">${
      state.lang === "es" ? "Iniciando análisis…" : "Starting analysis…"
    }</span></div>
    <div class="copilot-answer tabular"><p style="margin:0;"></p></div>
    <div class="copilot-tools"></div>
    <div class="copilot-citations hidden"></div>
    <div class="copilot-panel-action hidden" style="margin-block-start:0.5rem;"></div>
  `;
  listEl.appendChild(aiDiv);
  listEl.setAttribute("aria-busy", "true");
  smartScrollBottom(listEl);

  sendBtn?.classList.add("hidden");
  stopBtn?.classList.remove("hidden");

  const statusTxt = aiDiv.querySelector(".status-txt");
  const statusWrap = aiDiv.querySelector(".copilot-status-step");
  const answerEl = aiDiv.querySelector(".copilot-answer");
  const toolsEl = aiDiv.querySelector(".copilot-tools");
  const citesEl = aiDiv.querySelector(".copilot-citations");
  const panelActEl = aiDiv.querySelector(".copilot-panel-action");

  let accumulated = "";
  const toolTraces = [];

  try {
    const res = await fetch(`/api/v1/projects/${proj.id}/ai/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        filters: state.filters,
        history: historyTurns.slice(-6),
        include_documents: true,
        lang: state.lang,
      }),
      signal: activeAbort.signal,
    });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    const handleFrame = (eventName, dataStr) => {
      let data = {};
      try {
        data = JSON.parse(dataStr);
      } catch {
        return;
      }
      if (eventName === "status") {
        if (statusTxt) statusTxt.textContent = data.step || "";
      } else if (eventName === "tool_call") {
        toolTraces.push(data);
        toolsEl.innerHTML = `
          <details class="copilot-tool-details">
            <summary>${state.lang === "es" ? "Auditoría de herramientas" : "Tool audit"} (${toolTraces.length})</summary>
            <pre>${escapeHtml(
              toolTraces
                .map(
                  (tr) =>
                    `${tr.ok ? "✓" : "✕"} ${tr.tool_name} (${tr.duration_ms}ms${tr.row_count !== null && tr.row_count !== undefined ? `, ${tr.row_count} rows` : ""})${tr.sql ? `\n  SQL: ${tr.sql}` : ""}`
                )
                .join("\n")
            )}</pre>
          </details>`;
      } else if (eventName === "delta") {
        accumulated += data.text || "";
        answerEl.innerHTML = `<p style="margin:0;">${renderSafeMarkdown(accumulated)}</p>`;
        smartScrollBottom(listEl);
      } else if (eventName === "citations" && Array.isArray(data.items) && data.items.length) {
        citesEl.classList.remove("hidden");
        citesEl.innerHTML = data.items
          .map(
            (c) =>
              `<button type="button" class="pill pill-accent pill-btn" data-cite-tab="${escapeHtml(c.target_tab || "overview")}">${escapeHtml(c.label)}</button>`
          )
          .join("");
        citesEl.querySelectorAll("[data-cite-tab]").forEach((b) =>
          b.addEventListener("click", () => {
            if (typeof onNavigateTab === "function") onNavigateTab(b.dataset.citeTab);
          })
        );
      } else if (eventName === "panel_spec" && data.panel) {
        panelActEl.classList.remove("hidden");
        panelActEl.innerHTML = `<button type="button" class="btn btn-primary btn-xs">✦ ${
          state.lang === "es" ? `Fijar "${escapeHtml(data.panel.title)}" en Overview` : `Pin "${escapeHtml(data.panel.title)}" to Overview`
        }</button>`;
        panelActEl.querySelector("button").onclick = () => pinProposedPanel(data.panel);
      } else if (eventName === "done") {
        if (statusWrap) {
          statusWrap.innerHTML = `<span class="pill pill-muted tabular">${escapeHtml(data.provider || "local")} · ${data.latency_ms || 0}ms</span>`;
        }
      }
    };

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() || "";
      for (const frame of frames) {
        let evName = "message";
        let dataLine = "";
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) evName = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLine += line.slice(5).trim();
        }
        if (dataLine) handleFrame(evName, dataLine);
      }
    }

    historyTurns.push({ role: "user", content: message });
    if (accumulated) historyTurns.push({ role: "assistant", content: accumulated.slice(0, 1800) });
  } catch (err) {
    if (err.name === "AbortError") {
      if (statusTxt) statusTxt.textContent = state.lang === "es" ? "Generación detenida." : "Generation stopped.";
    } else {
      answerEl.innerHTML = `<p class="delta-neg" style="margin:0;">${escapeHtml(err.message)}</p>`;
    }
  } finally {
    activeAbort = null;
    listEl.setAttribute("aria-busy", "false");
    sendBtn?.classList.remove("hidden");
    stopBtn?.classList.add("hidden");
    smartScrollBottom(listEl);
  }
}

export function setCopilotOpen(next) {
  const drawer = document.getElementById("copilot-drawer");
  const toggleBtn = document.getElementById("btn-copilot-toggle");
  if (!drawer) return;
  isOpen = Boolean(next) && Boolean(state.activeProject);
  drawer.classList.toggle("hidden", !isOpen);
  toggleBtn?.setAttribute("aria-expanded", String(isOpen));
  if (isOpen) {
    if (!drawer.innerHTML.trim()) renderDrawerShell();
    else {
      const ctxStrip = drawer.querySelector("#copilot-context-strip");
      if (ctxStrip) ctxStrip.innerHTML = buildContextChips();
    }
    drawer.querySelector("#copilot-input")?.focus();
  } else if (activeAbort) {
    activeAbort.abort();
    activeAbort = null;
  }
}

export function initCopilot({ navigateTab, refreshProject } = {}) {
  onNavigateTab = navigateTab || null;
  onRefreshProject = refreshProject || null;
  const toggleBtn = document.getElementById("btn-copilot-toggle");
  toggleBtn?.addEventListener("click", () => setCopilotOpen(!isOpen));

  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      if (!state.activeProject) return;
      e.preventDefault();
      setCopilotOpen(!isOpen);
    }
  });
}

export function resetCopilotForProject() {
  historyTurns = [];
  const drawer = document.getElementById("copilot-drawer");
  if (drawer) drawer.innerHTML = "";
  if (isOpen) renderDrawerShell();
}
