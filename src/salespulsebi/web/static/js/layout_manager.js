import { api } from "./api.js";
import { t } from "./i18n.js";
import { ensureProjectSettings, state } from "./state.js";
import { escapeHtml, showToast } from "./views/hub.js";

const prefersReducedMotion = () =>
  typeof window !== "undefined" &&
  window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;

export function moveNodeBefore(container, node, refNode) {
  if ("moveBefore" in Element.prototype) {
    container.moveBefore(node, refNode || null);
  } else if (refNode) {
    refNode.before(node);
  } else {
    container.append(node);
  }
}

let announceTimer = null;
export function announceReorder(message) {
  const statusEl = document.getElementById("reorder-status");
  if (!statusEl) return;
  clearTimeout(announceTimer);
  statusEl.textContent = "";
  announceTimer = setTimeout(() => {
    statusEl.textContent = message;
  }, 500);
}

export function getTabLayout(proj, tabKey) {
  const settings = ensureProjectSettings(proj);
  const raw = settings.layouts?.[tabKey] || {};
  return {
    panel_order: Array.isArray(raw.panel_order) ? [...raw.panel_order] : [],
    hidden_panels: Array.isArray(raw.hidden_panels) ? [...raw.hidden_panels] : [],
    panel_spans: raw.panel_spans && typeof raw.panel_spans === "object" ? { ...raw.panel_spans } : {},
    kpi_order: Array.isArray(raw.kpi_order) ? [...raw.kpi_order] : [],
    hidden_kpis: Array.isArray(raw.hidden_kpis) ? [...raw.hidden_kpis] : [],
  };
}

export async function saveTabLayout(proj, tabKey, nextTabLayout) {
  const settings = ensureProjectSettings(proj);
  settings.layouts[tabKey] = nextTabLayout;
  proj.settings = settings;
  const updated = await api.updateProject(proj.id, { settings });
  proj.settings = updated.settings || settings;
  return proj.settings;
}

export function animateFlip(nodes, oldRects) {
  if (prefersReducedMotion()) return;
  for (const el of nodes) {
    const prev = oldRects.get(el);
    if (!prev) continue;
    el.getAnimations?.().forEach((anim) => anim.finish());
    const next = el.getBoundingClientRect();
    const dx = prev.left - next.left;
    const dy = prev.top - next.top;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
    el.animate(
      [
        { translate: `${dx}px ${dy}px` },
        { translate: "0 0" },
      ],
      { duration: 220, easing: "cubic-bezier(0.22, 1, 0.36, 1)" }
    );
  }
}

export function renderManagedDashboard({
  container,
  proj,
  tabKey,
  panels,
  extraBannerHtml = "",
  onBindBanner = null,
  onOpenNewPanel = null,
  onRerender = null,
}) {
  const lang = state.lang;
  const isEs = lang === "es";
  const layout = getTabLayout(proj, tabKey);

  const panelMap = new Map(panels.map((p) => [p.id, p]));
  const orderedIds = [
    ...layout.panel_order.filter((id) => panelMap.has(id)),
    ...panels.map((p) => p.id).filter((id) => !layout.panel_order.includes(id)),
  ];

  const hiddenSet = new Set(layout.hidden_panels.filter((id) => panelMap.has(id)));
  const visiblePanels = orderedIds.filter((id) => !hiddenSet.has(id)).map((id) => panelMap.get(id));
  const hiddenPanels = orderedIds.filter((id) => hiddenSet.has(id)).map((id) => panelMap.get(id));

  const hasCustomLayout =
    layout.panel_order.length > 0 ||
    layout.hidden_panels.length > 0 ||
    Object.keys(layout.panel_spans).length > 0 ||
    layout.kpi_order.length > 0 ||
    layout.hidden_kpis.length > 0;

  const showUtilityBar =
    Boolean(onOpenNewPanel) ||
    hiddenPanels.length > 0 ||
    Boolean(extraBannerHtml.trim()) ||
    hasCustomLayout;

  const utilityBarHtml = showUtilityBar
    ? `
    <div class="dashboard-utility-bar" role="region" aria-label="${escapeHtml(t(lang, "customize_view"))}">
      <div class="dashboard-utility-left">
        ${
          hiddenPanels.length
            ? `
          <div class="customize-restore-row">
            <span class="toolbar-label">${t(lang, "hidden_panels")}</span>
            <div class="chips-row">
              ${hiddenPanels
                .map(
                  (hp) =>
                    `<button type="button" class="chip chip-restore lm-restore-panel" data-id="${escapeHtml(hp.id)}">+ ${escapeHtml(hp.title)}</button>`
                )
                .join("")}
            </div>
          </div>`
            : ""
        }
        ${extraBannerHtml}
        ${
          !hiddenPanels.length && !extraBannerHtml.trim()
            ? `<span class="card-subtitle">⠿ ${escapeHtml(t(lang, "customize_hint"))}</span>`
            : ""
        }
      </div>
      <div class="dashboard-utility-right">
        ${
          onOpenNewPanel
            ? `<button type="button" id="lm-new-panel" class="btn btn-primary btn-xs">${t(lang, "new_custom_panel")}</button>`
            : ""
        }
        ${
          hasCustomLayout
            ? `<button type="button" id="lm-reset-layout" class="btn btn-secondary btn-xs">↺ ${t(lang, "reset_layout")}</button>`
            : ""
        }
      </div>
    </div>`
    : "";

  const panelsHtml = visiblePanels
    .map((p) => {
      const span = layout.panel_spans[p.id] || p.defaultSpan || 1;
      const controlsHtml = `
        <div class="panel-customize-bar">
          <div class="panel-customize-left">
            <button
              type="button"
              class="panel-drag-handle"
              aria-pressed="false"
              aria-label="${escapeHtml((isEs ? "Reordenar panel " : "Reorder panel ") + p.title)}"
            >⠿</button>
            <span class="panel-customize-label">${escapeHtml(p.title)}</span>
            <span class="panel-kbd-hint" aria-hidden="true">${escapeHtml(t(lang, "kbd_reorder_idle"))}</span>
          </div>
          <div class="panel-customize-actions">
            <button type="button" class="btn btn-ghost btn-xs lm-move-prev" title="${escapeHtml(t(lang, "move_up"))}" aria-label="${escapeHtml(t(lang, "move_up") + " " + p.title)}">↑</button>
            <button type="button" class="btn btn-ghost btn-xs lm-move-next" title="${escapeHtml(t(lang, "move_down"))}" aria-label="${escapeHtml(t(lang, "move_down") + " " + p.title)}">↓</button>
            <button type="button" class="btn btn-ghost btn-xs lm-toggle-span" data-span="${span}" title="${isEs ? "Cambiar ancho (50% / 100%)" : "Toggle width (50% / 100%)"}">
              ${span === 2 ? "100% → 50%" : "50% → 100%"}
            </button>
            ${
              p.isCustom
                ? `<button type="button" class="btn btn-ghost btn-xs lm-edit-custom">${escapeHtml(t(lang, "edit_panel"))}</button>
                   <button type="button" class="btn btn-danger btn-xs lm-delete-custom">${escapeHtml(t(lang, "delete_panel"))}</button>`
                : ""
            }
            <button type="button" class="btn btn-ghost btn-xs lm-hide-panel">${escapeHtml(t(lang, "hide_panel"))}</button>
          </div>
        </div>`;

      return `
      <article class="dashboard-panel" data-panel-id="${escapeHtml(p.id)}" data-span="${span}">
        ${controlsHtml}
        ${p.renderHtml()}
      </article>`;
    })
    .join("");

  const emptyStateHtml = !visiblePanels.length
    ? `<div class="empty-state">
         <h3>${isEs ? "Todos los paneles están ocultos" : "All panels are currently hidden"}</h3>
         <button type="button" id="lm-empty-reset" class="btn btn-primary btn-sm">${t(lang, "reset_layout")}</button>
       </div>`
    : "";

  container.innerHTML = `
    ${utilityBarHtml}
    <div class="dashboard-grid is-customizing" id="lm-grid">
      ${panelsHtml}
    </div>
    ${emptyStateHtml}
  `;

  const gridEl = container.querySelector("#lm-grid");

  // Mount each visible panel's charts / interactions
  for (const p of visiblePanels) {
    const panelEl = gridEl.querySelector(`[data-panel-id="${CSS.escape(p.id)}"]`);
    if (panelEl && typeof p.mount === "function") {
      p.mount(panelEl);
    }
  }

  if (typeof onBindBanner === "function") {
    onBindBanner(container, layout);
  }

  container.querySelector("#lm-empty-reset")?.addEventListener("click", async () => {
    await saveTabLayout(proj, tabKey, {
      panel_order: [],
      hidden_panels: [],
      panel_spans: {},
      kpi_order: [],
      hidden_kpis: [],
    });
    if (onRerender) onRerender();
  });

  // Utility bar handlers
  container.querySelector("#lm-new-panel")?.addEventListener("click", () => {
    if (onOpenNewPanel) onOpenNewPanel();
  });

  container.querySelector("#lm-reset-layout")?.addEventListener("click", async () => {
    await saveTabLayout(proj, tabKey, {
      panel_order: [],
      hidden_panels: [],
      panel_spans: {},
      kpi_order: [],
      hidden_kpis: [],
    });
    showToast(isEs ? "Vista restaurada por defecto" : "Layout restored to default", "success");
    if (onRerender) onRerender();
  });

  container.querySelectorAll(".lm-restore-panel").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const pid = btn.dataset.id;
      layout.hidden_panels = layout.hidden_panels.filter((x) => x !== pid);
      await saveTabLayout(proj, tabKey, layout);
      if (onRerender) onRerender();
    })
  );

  function syncButtonsDisabledState() {
    const items = Array.from(gridEl.querySelectorAll(":scope > .dashboard-panel"));
    items.forEach((el, idx) => {
      const prevBtn = el.querySelector(".lm-move-prev");
      const nextBtn = el.querySelector(".lm-move-next");
      if (prevBtn) prevBtn.disabled = idx === 0;
      if (nextBtn) nextBtn.disabled = idx === items.length - 1;
    });
  }
  syncButtonsDisabledState();

  async function persistCurrentDomOrder(movedTitle = null) {
    const domIds = Array.from(gridEl.querySelectorAll(":scope > .dashboard-panel")).map(
      (el) => el.dataset.panelId
    );
    const hiddenIds = Array.from(hiddenSet);
    layout.panel_order = [...domIds, ...hiddenIds];
    syncButtonsDisabledState();
    if (movedTitle) {
      const pos = domIds.indexOf(
        Array.from(gridEl.querySelectorAll(":scope > .dashboard-panel")).find(
          (el) => panelMap.get(el.dataset.panelId)?.title === movedTitle
        )?.dataset.panelId
      );
      announceReorder(
        isEs
          ? `${movedTitle} movido a la posición ${pos + 1} de ${domIds.length}`
          : `${movedTitle} moved to position ${pos + 1} of ${domIds.length}`
      );
    }
    await saveTabLayout(proj, tabKey, layout);
  }

  function swapPanelStep(panelEl, direction, focusSelector = null) {
    const items = Array.from(gridEl.querySelectorAll(":scope > .dashboard-panel"));
    const idx = items.indexOf(panelEl);
    const targetIdx = idx + direction;
    if (idx < 0 || targetIdx < 0 || targetIdx >= items.length) return false;

    const oldRects = new Map(items.map((el) => [el, el.getBoundingClientRect()]));
    const refNode = direction < 0 ? items[targetIdx] : items[targetIdx].nextElementSibling;

    const doMove = () => {
      moveNodeBefore(gridEl, panelEl, refNode);
    };

    if (!prefersReducedMotion() && gridEl.startViewTransition) {
      gridEl.startViewTransition(doMove);
    } else {
      doMove();
      panelEl.scrollIntoView({ block: "nearest", inline: "nearest" });
      animateFlip(items, oldRects);
    }

    if (focusSelector) {
      const focusTarget = panelEl.querySelector(focusSelector);
      focusTarget?.focus({ preventScroll: true });
    }

    const pTitle = panelMap.get(panelEl.dataset.panelId)?.title || "";
    persistCurrentDomOrder(pTitle);
    return true;
  }

  // Per-panel controls: ↑ / ↓, span toggle, hide, custom edit/delete, keyboard & pointer drag
  gridEl.querySelectorAll(":scope > .dashboard-panel").forEach((panelEl) => {
    const pid = panelEl.dataset.panelId;
    const pObj = panelMap.get(pid);

    panelEl.querySelector(".lm-move-prev")?.addEventListener("click", () => {
      const moved = swapPanelStep(panelEl, -1, ".lm-move-prev");
      if (moved && panelEl.querySelector(".lm-move-prev")?.disabled) {
        panelEl.querySelector(".lm-move-next")?.focus({ preventScroll: true });
      }
    });

    panelEl.querySelector(".lm-move-next")?.addEventListener("click", () => {
      const moved = swapPanelStep(panelEl, 1, ".lm-move-next");
      if (moved && panelEl.querySelector(".lm-move-next")?.disabled) {
        panelEl.querySelector(".lm-move-prev")?.focus({ preventScroll: true });
      }
    });

    panelEl.querySelector(".lm-toggle-span")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      const curr = Number(panelEl.dataset.span) === 2 ? 2 : 1;
      const next = curr === 2 ? 1 : 2;
      panelEl.dataset.span = String(next);
      btn.dataset.span = String(next);
      btn.textContent = next === 2 ? "100% → 50%" : "50% → 100%";
      layout.panel_spans[pid] = next;
      window.dispatchEvent(new Event("resize"));
      announceReorder(
        isEs
          ? `${pObj?.title || pid} ajustado al ${next === 2 ? "100%" : "50%"} de ancho`
          : `${pObj?.title || pid} resized to ${next === 2 ? "100%" : "50%"} width`
      );
      await saveTabLayout(proj, tabKey, layout);
    });

    panelEl.addEventListener("focusin", (e) => {
      if (
        e.target instanceof HTMLElement &&
        e.target.matches(":focus-visible") &&
        !(panelEl.getAnimations?.().length > 0)
      ) {
        panelEl.scrollIntoView({ block: "nearest", inline: "nearest" });
      }
    });

    panelEl.querySelector(".lm-hide-panel")?.addEventListener("click", async () => {
      if (!layout.hidden_panels.includes(pid)) {
        layout.hidden_panels.push(pid);
      }
      await saveTabLayout(proj, tabKey, layout);
      if (onRerender) onRerender();
    });

    panelEl.querySelector(".lm-edit-custom")?.addEventListener("click", () => {
      if (pObj?.onEdit) pObj.onEdit();
    });

    panelEl.querySelector(".lm-delete-custom")?.addEventListener("click", async () => {
      if (pObj?.onDelete) await pObj.onDelete();
    });

    // Keyboard Grab-Move-Drop on .panel-drag-handle
    const handle = panelEl.querySelector(".panel-drag-handle");
    const hintEl = panelEl.querySelector(".panel-kbd-hint");
    if (!handle) return;

    let kbdGrabbed = false;
    let originalNextSibling = null;
    let ignoreBlur = false;

    function setKbdGrabbed(grabbed) {
      kbdGrabbed = grabbed;
      handle.setAttribute("aria-pressed", String(grabbed));
      panelEl.classList.toggle("is-grabbed", grabbed);
      if (hintEl) {
        hintEl.textContent = t(lang, grabbed ? "kbd_reorder_grabbed" : "kbd_reorder_idle");
      }
    }

    handle.addEventListener("keydown", (e) => {
      if (e.key === " " || e.key === "Enter") {
        e.preventDefault();
        if (!kbdGrabbed) {
          originalNextSibling = panelEl.nextElementSibling;
          setKbdGrabbed(true);
          announceReorder(
            isEs
              ? `${pObj?.title} seleccionado. Usa las flechas para mover, Espacio para soltar o Escape para cancelar.`
              : `${pObj?.title} grabbed. Use arrow keys to move, Space to drop, or Escape to cancel.`
          );
        } else {
          setKbdGrabbed(false);
          announceReorder(
            isEs ? `${pObj?.title} colocado en su nueva posición.` : `${pObj?.title} dropped.`
          );
        }
        return;
      }

      if (!kbdGrabbed) return;

      if (e.key === "Escape") {
        e.preventDefault();
        ignoreBlur = true;
        moveNodeBefore(gridEl, panelEl, originalNextSibling);
        panelEl.scrollIntoView({ block: "nearest", inline: "nearest" });
        handle.focus({ preventScroll: true });
        ignoreBlur = false;
        setKbdGrabbed(false);
        persistCurrentDomOrder();
        announceReorder(isEs ? "Movimiento cancelado" : "Reorder cancelled");
        return;
      }

      if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
        e.preventDefault();
        ignoreBlur = true;
        swapPanelStep(panelEl, -1, ".panel-drag-handle");
        ignoreBlur = false;
      } else if (e.key === "ArrowDown" || e.key === "ArrowRight") {
        e.preventDefault();
        ignoreBlur = true;
        swapPanelStep(panelEl, 1, ".panel-drag-handle");
        ignoreBlur = false;
      }
    });

    handle.addEventListener("blur", () => {
      if (kbdGrabbed && !ignoreBlur) {
        setKbdGrabbed(false);
      }
    });

    // Pointer Drag & Drop on dedicated .panel-drag-handle
    let activePointerId = null;
    let startX = 0;
    let startY = 0;
    let targetRefNode = undefined;

    function clearDropMarkers() {
      gridEl.querySelectorAll(".drop-target-before, .drop-target-after").forEach((el) => {
        el.classList.remove("drop-target-before", "drop-target-after");
      });
    }

    function cleanupDrag() {
      activePointerId = null;
      targetRefNode = undefined;
      document.documentElement.classList.remove("is-dragging-panel");
      panelEl.classList.remove("is-dragging");
      panelEl.style.translate = "";
      clearDropMarkers();
      window.removeEventListener("keydown", onEscCancelDrag);
    }

    function onEscCancelDrag(ev) {
      if (ev.key === "Escape" && activePointerId !== null) {
        ev.preventDefault();
        try {
          if (handle.hasPointerCapture(activePointerId)) {
            handle.releasePointerCapture(activePointerId);
          }
        } catch {}
        cleanupDrag();
        announceReorder(isEs ? "Movimiento cancelado" : "Reorder cancelled");
      }
    }

    handle.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || activePointerId !== null) return;
      e.preventDefault();
      activePointerId = e.pointerId;
      startX = e.clientX;
      startY = e.clientY;
      targetRefNode = undefined;
      handle.setPointerCapture(e.pointerId);
      document.documentElement.classList.add("is-dragging-panel");
      panelEl.classList.add("is-dragging");
      window.addEventListener("keydown", onEscCancelDrag);
    });

    handle.addEventListener("pointermove", (e) => {
      if (e.pointerId !== activePointerId) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      panelEl.style.translate = `${dx}px ${dy}px`;

      clearDropMarkers();
      const siblings = Array.from(gridEl.querySelectorAll(":scope > .dashboard-panel")).filter(
        (el) => el !== panelEl
      );
      if (!siblings.length) return;

      // Determine insertion slot by comparing pointer coordinates against layout midpoints
      let foundRef = null;
      for (const sib of siblings) {
        const r = sib.getBoundingClientRect();
        const midY = r.top + r.height / 2;
        const midX = r.left + r.width / 2;
        const sameRow = e.clientY >= r.top && e.clientY <= r.bottom;
        if (e.clientY < midY - r.height * 0.25 || (sameRow && e.clientX < midX)) {
          foundRef = sib;
          break;
        }
      }

      targetRefNode = foundRef;
      if (foundRef && foundRef !== panelEl.nextElementSibling) {
        foundRef.classList.add("drop-target-before");
      } else if (!foundRef && panelEl.nextElementSibling !== null) {
        siblings[siblings.length - 1].classList.add("drop-target-after");
      }
    });

    handle.addEventListener("pointerup", (e) => {
      if (e.pointerId !== activePointerId) return;
      const shouldMove =
        targetRefNode !== undefined && targetRefNode !== panelEl.nextElementSibling;
      const refToApply = targetRefNode;

      const allItems = Array.from(gridEl.querySelectorAll(":scope > .dashboard-panel"));
      panelEl.style.translate = "";
      const oldRects = new Map(allItems.map((el) => [el, el.getBoundingClientRect()]));

      cleanupDrag();

      if (shouldMove) {
        moveNodeBefore(gridEl, panelEl, refToApply);
        panelEl.scrollIntoView({ block: "nearest", inline: "nearest" });
        animateFlip(allItems, oldRects);
        handle.focus({ preventScroll: true });
        persistCurrentDomOrder(pObj?.title || pid);
      }
    });

    handle.addEventListener("pointercancel", (e) => {
      if (e.pointerId !== activePointerId) return;
      cleanupDrag();
    });

    handle.addEventListener("lostpointercapture", () => {
      if (activePointerId !== null) {
        cleanupDrag();
      }
    });
  });
}
