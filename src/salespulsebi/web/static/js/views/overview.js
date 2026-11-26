import { api } from "../api.js";
import { mountChart, mountSparkline, palette } from "../charts.js";
import {
  mountCustomPanelVisual,
  openCustomPanelModal,
  renderCustomPanelShell,
} from "../custom_panels.js";
import { fmtCurrency, fmtNumber, fmtPercent, t } from "../i18n.js";
import {
  animateFlip,
  announceReorder,
  getTabLayout,
  moveNodeBefore,
  renderManagedDashboard,
  saveTabLayout,
} from "../layout_manager.js";
import { ensureProjectSettings, state, toggleCrossFilter } from "../state.js";
import { escapeHtml, showToast } from "./hub.js";

const DEFAULT_KPI_ORDER = [
  "revenue",
  "orders",
  "aov",
  "units",
  "gross_profit",
  "gross_margin_pct",
  "unique_customers",
  "discount_rate_pct",
  "cancellation_rate_pct",
  "rows_count",
];

function formatKpiVal(key, val, currency, lang) {
  if (val === null || val === undefined) return "—";
  if (["revenue", "aov", "gross_profit"].includes(key)) return fmtCurrency(val, currency, lang);
  if (key.includes("pct")) return fmtPercent(val, 1, lang);
  return fmtNumber(val, 0, lang);
}

function formatKpiTitle(key, val, currency, lang) {
  if (val === null || val === undefined) return "";
  if (["revenue", "aov", "gross_profit"].includes(key)) {
    return fmtCurrency(val, currency, lang, false);
  }
  return "";
}

export async function renderOverview(container) {
  const proj = state.activeProject;
  const lang = state.lang;
  const isEs = lang === "es";
  const settings = ensureProjectSettings(proj);
  const customSpecs = settings.custom_panels || [];

  const [ov, insRes, customRes] = await Promise.all([
    api.analytics(proj.id, "overview", state.filters),
    api.analytics(proj.id, "insights", state.filters),
    customSpecs.length
      ? api.analytics(proj.id, "custom-panels", { filters: state.filters })
      : Promise.resolve({ panels: {}, available_columns: [] }),
  ]);

  const kpis = ov.kpis || {};
  const insights = insRes.insights || [];
  const customPayloads = customRes.panels || {};
  const availableColumns = customRes.available_columns || [];

  const tabLayout = getTabLayout(proj, "overview");
  const availableKpiKeys = DEFAULT_KPI_ORDER.filter((k) => k in kpis);
  const orderedKpiKeys = [
    ...tabLayout.kpi_order.filter((k) => availableKpiKeys.includes(k)),
    ...availableKpiKeys.filter((k) => !tabLayout.kpi_order.includes(k)),
  ];
  const hiddenKpiSet = new Set(tabLayout.hidden_kpis.filter((k) => availableKpiKeys.includes(k)));
  const visibleKpis = orderedKpiKeys.filter((k) => !hiddenKpiSet.has(k));
  const hiddenKpis = orderedKpiKeys.filter((k) => hiddenKpiSet.has(k));

  const extraKpiRestoreHtml = hiddenKpis.length
    ? `
      <div class="customize-restore-row">
        <span class="toolbar-label">${t(lang, "hidden_kpis")}</span>
        <div class="chips-row">
          ${hiddenKpis
            .map(
              (k) =>
                `<button type="button" class="chip chip-restore ov-restore-kpi" data-kpi="${escapeHtml(k)}">+ ${escapeHtml(t(lang, k))}</button>`
            )
            .join("")}
        </div>
      </div>`
    : "";

  const openBuilder = (existingSpec = null) =>
    openCustomPanelModal({
      proj,
      existingSpec,
      availableColumns,
      onSaved: async () => {
        await renderOverview(container);
      },
    });

  const deleteCustomSpec = async (specId) => {
    const nextSettings = ensureProjectSettings(proj);
    nextSettings.custom_panels = nextSettings.custom_panels.filter((p) => p.id !== specId);
    if (nextSettings.layouts?.overview) {
      const ovL = nextSettings.layouts.overview;
      ovL.panel_order = (ovL.panel_order || []).filter((id) => id !== specId);
      ovL.hidden_panels = (ovL.hidden_panels || []).filter((id) => id !== specId);
      if (ovL.panel_spans) delete ovL.panel_spans[specId];
    }
    const updated = await api.updateProject(proj.id, { settings: nextSettings });
    proj.settings = updated.settings || nextSettings;
    showToast(isEs ? "Panel personalizado eliminado" : "Custom panel removed", "success");
    await renderOverview(container);
  };

  const panels = [];

  // 1. KPI Strip Panel
  panels.push({
    id: "kpis",
    title: t(lang, "kpi_strip_title"),
    defaultSpan: 2,
    renderHtml: () => {
      if (!visibleKpis.length) {
        return `<div class="card"><div class="card-subtitle">${isEs ? "Todos los KPIs están ocultos. Restáuralos desde la barra superior." : "All KPIs are hidden. Restore them from the banner above."}</div></div>`;
      }
      return `
        <div class="grid-kpis">
          ${visibleKpis
            .map((k, idx) => {
              const item = kpis[k];
              const delta = item.delta_pct;
              let deltaBadge = `<span class="delta-badge delta-neu">—</span>`;
              if (delta !== null && delta !== undefined) {
                const isGood = k === "cancellation_rate_pct" ? delta <= 0 : delta >= 0;
                const arrow = delta >= 0 ? "▲" : "▼";
                const formattedDelta = fmtPercent(delta, 1, lang, true);
                deltaBadge = `<span class="delta-badge ${isGood ? "delta-pos" : "delta-neg"}"><span aria-hidden="true">${arrow}</span> ${formattedDelta}</span>`;
              }
              const exactTitle = formatKpiTitle(k, item.value, proj.currency, lang);
              const kpiTools = `<div class="kpi-mini-tools">
                <button type="button" class="panel-drag-handle kpi-drag-handle" data-kpi="${escapeHtml(k)}" aria-pressed="false" title="${escapeHtml((isEs ? "Arrastrar KPI " : "Drag KPI ") + t(lang, k))}" aria-label="${escapeHtml((isEs ? "Reordenar KPI " : "Reorder KPI ") + t(lang, k))}">⠿</button>
                <button type="button" class="btn btn-ghost btn-xs ov-kpi-left" data-kpi="${escapeHtml(k)}" ${idx === 0 ? "disabled" : ""} aria-label="Move KPI left">←</button>
                <button type="button" class="btn btn-ghost btn-xs ov-kpi-right" data-kpi="${escapeHtml(k)}" ${idx === visibleKpis.length - 1 ? "disabled" : ""} aria-label="Move KPI right">→</button>
                <button type="button" class="btn btn-ghost btn-xs ov-kpi-hide" data-kpi="${escapeHtml(k)}" aria-label="Hide KPI">✕</button>
              </div>`;
              return `
              <div class="kpi-card" data-kpi-key="${escapeHtml(k)}">
                <div class="kpi-head-row">
                  <div class="kpi-label">${t(lang, k)}</div>
                  ${kpiTools}
                </div>
                <div class="kpi-value" ${exactTitle ? `title="${escapeHtml(exactTitle)}"` : ""}>${formatKpiVal(k, item.value, proj.currency, lang)}</div>
                <div class="kpi-footer">
                  ${deltaBadge}
                  <div class="sparkline-box" id="spark-${k}"></div>
                </div>
              </div>`;
            })
            .join("")}
        </div>`;
    },
    mount: (panelEl) => {
      for (const k of visibleKpis) {
        if (kpis[k]?.sparkline?.length >= 2) {
          const d = kpis[k].delta_pct;
          const isGood = k === "cancellation_rate_pct" ? d === null || d <= 0 : d === null || d >= 0;
          mountSparkline(panelEl.querySelector(`#spark-${k}`), kpis[k].sparkline, isGood);
        }
      }

      const kpiGridEl = panelEl.querySelector(".grid-kpis");
      if (!kpiGridEl) return;

      const syncKpiButtons = () => {
        const cards = Array.from(kpiGridEl.querySelectorAll(":scope > .kpi-card"));
        cards.forEach((card, idx) => {
          const leftBtn = card.querySelector(".ov-kpi-left");
          const rightBtn = card.querySelector(".ov-kpi-right");
          if (leftBtn) leftBtn.disabled = idx === 0;
          if (rightBtn) rightBtn.disabled = idx === cards.length - 1;
        });
      };

      const persistKpiDomOrder = async (movedKey = null) => {
        const domKeys = Array.from(kpiGridEl.querySelectorAll(":scope > .kpi-card")).map(
          (el) => el.dataset.kpiKey
        );
        syncKpiButtons();
        const nextL = getTabLayout(proj, "overview");
        nextL.kpi_order = [...domKeys, ...hiddenKpis];
        if (movedKey) {
          const pos = domKeys.indexOf(movedKey);
          announceReorder(
            isEs
              ? `${t(lang, movedKey)} movido a la posición ${pos + 1} de ${domKeys.length}`
              : `${t(lang, movedKey)} moved to position ${pos + 1} of ${domKeys.length}`
          );
        }
        await saveTabLayout(proj, "overview", nextL);
      };

      const swapKpiCard = (cardEl, dir, focusSel = null) => {
        const cards = Array.from(kpiGridEl.querySelectorAll(":scope > .kpi-card"));
        const i = cards.indexOf(cardEl);
        const j = i + dir;
        if (i < 0 || j < 0 || j >= cards.length) return false;
        const oldRects = new Map(cards.map((el) => [el, el.getBoundingClientRect()]));
        const refNode = dir < 0 ? cards[j] : cards[j].nextElementSibling;
        moveNodeBefore(kpiGridEl, cardEl, refNode);
        animateFlip(cards, oldRects);
        if (focusSel) {
          cardEl.querySelector(focusSel)?.focus({ preventScroll: true });
        }
        persistKpiDomOrder(cardEl.dataset.kpiKey);
        return true;
      };

      kpiGridEl.querySelectorAll(":scope > .kpi-card").forEach((cardEl) => {
        const kKey = cardEl.dataset.kpiKey;
        cardEl.querySelector(".ov-kpi-left")?.addEventListener("click", () => {
          const moved = swapKpiCard(cardEl, -1, ".ov-kpi-left");
          if (moved && cardEl.querySelector(".ov-kpi-left")?.disabled) {
            cardEl.querySelector(".ov-kpi-right")?.focus({ preventScroll: true });
          }
        });
        cardEl.querySelector(".ov-kpi-right")?.addEventListener("click", () => {
          const moved = swapKpiCard(cardEl, 1, ".ov-kpi-right");
          if (moved && cardEl.querySelector(".ov-kpi-right")?.disabled) {
            cardEl.querySelector(".ov-kpi-left")?.focus({ preventScroll: true });
          }
        });
        cardEl.querySelector(".ov-kpi-hide")?.addEventListener("click", async () => {
          const nextL = getTabLayout(proj, "overview");
          if (!nextL.hidden_kpis.includes(kKey)) {
            nextL.hidden_kpis.push(kKey);
          }
          await saveTabLayout(proj, "overview", nextL);
          await renderOverview(container);
        });

        const handle = cardEl.querySelector(".kpi-drag-handle");
        if (!handle) return;

        let kbdGrabbed = false;
        let origNext = null;
        let ignoreKbdBlur = false;

        const setKpiGrabbed = (grabbed) => {
          kbdGrabbed = grabbed;
          handle.setAttribute("aria-pressed", String(grabbed));
          cardEl.classList.toggle("is-grabbed", grabbed);
        };

        handle.addEventListener("keydown", (e) => {
          if (e.key === " " || e.key === "Enter") {
            e.preventDefault();
            if (!kbdGrabbed) {
              origNext = cardEl.nextElementSibling;
              setKpiGrabbed(true);
              announceReorder(
                isEs
                  ? `${t(lang, kKey)} seleccionado. Usa flechas para mover, Espacio para soltar o Escape para cancelar.`
                  : `${t(lang, kKey)} grabbed. Use arrow keys to move, Space to drop, or Escape to cancel.`
              );
            } else {
              setKpiGrabbed(false);
              announceReorder(isEs ? `${t(lang, kKey)} soltado.` : `${t(lang, kKey)} dropped.`);
            }
            return;
          }
          if (!kbdGrabbed) return;
          if (e.key === "Escape") {
            e.preventDefault();
            ignoreKbdBlur = true;
            moveNodeBefore(kpiGridEl, cardEl, origNext);
            handle.focus({ preventScroll: true });
            ignoreKbdBlur = false;
            setKpiGrabbed(false);
            persistKpiDomOrder();
            announceReorder(isEs ? "Movimiento cancelado" : "Reorder cancelled");
            return;
          }
          if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
            e.preventDefault();
            ignoreKbdBlur = true;
            swapKpiCard(cardEl, -1, ".kpi-drag-handle");
            ignoreKbdBlur = false;
          } else if (e.key === "ArrowRight" || e.key === "ArrowDown") {
            e.preventDefault();
            ignoreKbdBlur = true;
            swapKpiCard(cardEl, 1, ".kpi-drag-handle");
            ignoreKbdBlur = false;
          }
        });

        handle.addEventListener("blur", () => {
          if (kbdGrabbed && !ignoreKbdBlur) {
            setKpiGrabbed(false);
          }
        });

        let activePointerId = null;
        let startX = 0;
        let startY = 0;
        let targetRefNode = undefined;

        const clearKpiDropMarkers = () => {
          kpiGridEl.querySelectorAll(".drop-target-before, .drop-target-after").forEach((el) => {
            el.classList.remove("drop-target-before", "drop-target-after");
          });
        };

        const cleanupKpiDrag = () => {
          activePointerId = null;
          targetRefNode = undefined;
          document.documentElement.classList.remove("is-dragging-panel");
          cardEl.classList.remove("is-dragging");
          cardEl.style.translate = "";
          clearKpiDropMarkers();
        };

        handle.addEventListener("pointerdown", (e) => {
          if (e.button !== 0 || activePointerId !== null) return;
          e.preventDefault();
          activePointerId = e.pointerId;
          startX = e.clientX;
          startY = e.clientY;
          targetRefNode = undefined;
          handle.setPointerCapture(e.pointerId);
          document.documentElement.classList.add("is-dragging-panel");
          cardEl.classList.add("is-dragging");
        });

        handle.addEventListener("pointermove", (e) => {
          if (e.pointerId !== activePointerId) return;
          cardEl.style.translate = `${e.clientX - startX}px ${e.clientY - startY}px`;
          clearKpiDropMarkers();
          const siblings = Array.from(kpiGridEl.querySelectorAll(":scope > .kpi-card")).filter(
            (el) => el !== cardEl
          );
          if (!siblings.length) return;
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
          if (foundRef && foundRef !== cardEl.nextElementSibling) {
            foundRef.classList.add("drop-target-before");
          } else if (!foundRef && cardEl.nextElementSibling !== null) {
            siblings[siblings.length - 1].classList.add("drop-target-after");
          }
        });

        handle.addEventListener("pointerup", (e) => {
          if (e.pointerId !== activePointerId) return;
          const shouldMove =
            targetRefNode !== undefined && targetRefNode !== cardEl.nextElementSibling;
          const refToApply = targetRefNode;
          const allCards = Array.from(kpiGridEl.querySelectorAll(":scope > .kpi-card"));
          cardEl.style.translate = "";
          const oldRects = new Map(allCards.map((el) => [el, el.getBoundingClientRect()]));
          cleanupKpiDrag();
          if (shouldMove) {
            moveNodeBefore(kpiGridEl, cardEl, refToApply);
            animateFlip(allCards, oldRects);
            handle.focus({ preventScroll: true });
            persistKpiDomOrder(kKey);
          }
        });

        handle.addEventListener("pointercancel", cleanupKpiDrag);
        handle.addEventListener("lostpointercapture", cleanupKpiDrag);
      });
    },
  });

  // 2. Automated Insights Panel
  if (insights.length) {
    panels.push({
      id: "insights",
      title: t(lang, "insights_title"),
      defaultSpan: 2,
      renderHtml: () => `
        <div class="card">
          <div class="card-header">
            <h3 class="card-title">✦ ${t(lang, "insights_title")}</h3>
            <span class="pill pill-accent">${fmtNumber(insights.length, 0, lang)}</span>
          </div>
          <div class="insights-grid">
            ${insights
              .map(
                (ins) => `
              <div class="insight-card sev-${escapeHtml(ins.severity)}">
                <div class="insight-title">${escapeHtml(ins.title)}</div>
                <div class="insight-body">${escapeHtml(ins.body)}</div>
              </div>`
              )
              .join("")}
          </div>
        </div>`,
      mount: () => {},
    });
  }

  // 3. Revenue & Orders Trend
  panels.push({
    id: "trend",
    title: t(lang, "rev_orders_trend"),
    defaultSpan: 1,
    renderHtml: () => `
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">${t(lang, "rev_orders_trend")}</h3>
        </div>
        <div id="ov-chart-trend" class="chart-box"></div>
      </div>`,
    mount: (panelEl) => {
      const p = palette();
      const trend = ov.trend || [];
      const el = panelEl.querySelector("#ov-chart-trend");
      if (trend.length) {
        mountChart(el, {
          legend: { data: [t(lang, "revenue"), t(lang, "orders")], textStyle: { color: p.text }, bottom: 0 },
          xAxis: { type: "category", data: trend.map((r) => r.period), axisLine: { lineStyle: { color: p.grid } } },
          yAxis: [
            { type: "value", splitLine: { lineStyle: { color: p.grid } } },
            { type: "value", splitLine: { show: false } },
          ],
          series: [
            {
              name: t(lang, "revenue"),
              type: "line",
              smooth: true,
              data: trend.map((r) => r.revenue),
              areaStyle: { opacity: 0.14 },
            },
            {
              name: t(lang, "orders"),
              type: "bar",
              yAxisIndex: 1,
              itemStyle: { opacity: 0.55 },
              data: trend.map((r) => r.orders),
            },
          ],
        });
      } else if (el) {
        el.innerHTML = `<div class="empty-state">No date column mapped</div>`;
      }
    },
  });

  // 4. Top Categories Donut
  panels.push({
    id: "top_categories",
    title: t(lang, "top_categories"),
    defaultSpan: 1,
    renderHtml: () => `
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">${t(lang, "top_categories")}</h3>
        </div>
        <div id="ov-chart-cats" class="chart-box"></div>
      </div>`,
    mount: (panelEl) => {
      const p = palette();
      const cats = ov.top_categories || [];
      const el = panelEl.querySelector("#ov-chart-cats");
      if (cats.length) {
        mountChart(
          el,
          {
            tooltip: { trigger: "item" },
            series: [
              {
                type: "pie",
                radius: ["45%", "74%"],
                itemStyle: { borderRadius: 6, borderColor: p.bg, borderWidth: 2 },
                label: { color: p.text, formatter: "{b}: {d}%" },
                data: cats.map((c) => ({ name: c.name, value: c.revenue })),
              },
            ],
          },
          (params) => toggleCrossFilter("categories", params.name)
        );
      } else if (el) {
        el.innerHTML = `<div class="empty-state">No category column mapped</div>`;
      }
    },
  });

  // 5. Top Products Horizontal Bar
  panels.push({
    id: "top_products",
    title: t(lang, "top_products"),
    defaultSpan: 1,
    renderHtml: () => `
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">${t(lang, "top_products")}</h3>
        </div>
        <div id="ov-chart-prods" class="chart-box"></div>
      </div>`,
    mount: (panelEl) => {
      const p = palette();
      const prods = [...(ov.top_products || [])].reverse();
      const el = panelEl.querySelector("#ov-chart-prods");
      if (prods.length) {
        mountChart(
          el,
          {
            grid: { left: 120, right: 24, top: 24, bottom: 24 },
            xAxis: { type: "value", splitLine: { lineStyle: { color: p.grid } } },
            yAxis: {
              type: "category",
              data: prods.map((x) => x.name),
              axisLabel: { width: 105, overflow: "truncate" },
            },
            series: [{ type: "bar", data: prods.map((x) => x.revenue), itemStyle: { borderRadius: [0, 5, 5, 0] } }],
          },
          (params) => toggleCrossFilter("products", params.name)
        );
      } else if (el) {
        el.innerHTML = `<div class="empty-state">No product column mapped</div>`;
      }
    },
  });

  // 6. Ticket Distribution & Percentiles
  panels.push({
    id: "ticket_dist",
    title: t(lang, "ticket_dist"),
    defaultSpan: 1,
    renderHtml: () => {
      const pct = ov.ticket_distribution?.percentiles || {};
      const pctTxt =
        pct.p50 != null
          ? `P50: ${fmtCurrency(pct.p50, proj.currency, lang)} · P90: ${fmtCurrency(pct.p90, proj.currency, lang)}`
          : "";
      return `
        <div class="card">
          <div class="card-header">
            <h3 class="card-title">${t(lang, "ticket_dist")}</h3>
            <span class="card-subtitle">${escapeHtml(pctTxt)}</span>
          </div>
          <div id="ov-chart-ticket" class="chart-box"></div>
        </div>`;
    },
    mount: (panelEl) => {
      const p = palette();
      const td = ov.ticket_distribution || {};
      const el = panelEl.querySelector("#ov-chart-ticket");
      if (td.histogram?.length) {
        mountChart(el, {
          xAxis: {
            type: "category",
            data: td.histogram.map((b) => b.label),
            axisLabel: { rotate: 28, fontSize: 10.5 },
          },
          yAxis: { type: "value", splitLine: { lineStyle: { color: p.grid } } },
          series: [
            {
              type: "bar",
              data: td.histogram.map((b) => b.count),
              itemStyle: { color: p.up, borderRadius: [4, 4, 0, 0] },
            },
          ],
        });
      } else if (el) {
        el.innerHTML = `<div class="empty-state">No ticket data</div>`;
      }
    },
  });

  // 7. User-Defined Custom Panels
  for (const spec of customSpecs) {
    panels.push({
      id: spec.id,
      title: spec.title,
      defaultSpan: spec.span || 1,
      isCustom: true,
      onEdit: () => openBuilder(spec),
      onDelete: () => deleteCustomSpec(spec.id),
      renderHtml: () => {
        if (spec.panel_type === "kpi") {
          const subtitle = `${spec.aggregation.toUpperCase()}${spec.y_column ? `(${spec.y_column})` : ""}`;
          return `
            <div class="card custom-kpi-panel" id="cp-body-${escapeHtml(spec.id)}">
              <div class="kpi-head-row">
                <div class="kpi-label">${escapeHtml(spec.title)}</div>
              </div>
              <div class="kpi-value tabular" data-cp-kpi-val>—</div>
              <div class="kpi-footer">
                <span class="delta-badge delta-neu" data-cp-kpi-delta>—</span>
                <span class="card-subtitle">${escapeHtml(subtitle)}</span>
              </div>
            </div>`;
        }
        return renderCustomPanelShell(spec);
      },
      mount: (panelEl) => {
        mountCustomPanelVisual(panelEl, spec, customPayloads[spec.id], proj, lang, true);
      },
    });
  }

  renderManagedDashboard({
    container,
    proj,
    tabKey: "overview",
    panels,
    extraBannerHtml: extraKpiRestoreHtml,
    onBindBanner: (rootEl, layoutObj) => {
      rootEl.querySelectorAll(".ov-restore-kpi").forEach((btn) =>
        btn.addEventListener("click", async () => {
          const k = btn.dataset.kpi;
          layoutObj.hidden_kpis = layoutObj.hidden_kpis.filter((x) => x !== k);
          await saveTabLayout(proj, "overview", layoutObj);
          await renderOverview(container);
        })
      );
    },
    onOpenNewPanel: () => openBuilder(null),
    onRerender: () => renderOverview(container),
  });

  return ov;
}
