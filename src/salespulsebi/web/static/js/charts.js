import { state } from "./state.js";

const ACTIVE_CHARTS = new Set();
const CHART_OBSERVERS = new WeakMap();

function readToken(styles, name, fallback) {
  const val = styles.getPropertyValue(name)?.trim();
  return val || fallback;
}

export function palette() {
  const isDark = state.theme === "dark";
  const styles = getComputedStyle(document.documentElement);

  const accent = readToken(styles, "--accent", "#3b82f6");
  const up = readToken(styles, "--fin-up", "#14b8a6");
  const down = readToken(styles, "--fin-down", "#fb923c");
  const warn = readToken(styles, "--fin-warn", "#f59e0b");
  const info = readToken(styles, "--fin-info", "#06b6d4");

  const colors = [
    readToken(styles, "--chart-1", accent),
    readToken(styles, "--chart-2", up),
    readToken(styles, "--chart-3", down),
    readToken(styles, "--chart-4", "#8b5cf6"),
    readToken(styles, "--chart-5", info),
    readToken(styles, "--chart-6", warn),
    readToken(styles, "--chart-7", "#ec4899"),
    readToken(styles, "--chart-8", "#64748b"),
  ];

  return {
    text: readToken(styles, "--text-secondary", isDark ? "#cbd5e1" : "#334155"),
    textPrimary: readToken(styles, "--text-primary", isDark ? "#f8fafc" : "#0f172a"),
    muted: readToken(styles, "--text-muted", isDark ? "#64748b" : "#94a3b8"),
    grid: readToken(styles, "--border", isDark ? "#1e293b" : "#e2e8f0"),
    bg: readToken(styles, "--bg-surface", isDark ? "#0f1623" : "#ffffff"),
    elevated: readToken(styles, "--bg-elevated", isDark ? "#162032" : "#eef2f8"),
    accent,
    up,
    down,
    warn,
    info,
    colors,
  };
}

export function disposeChartsIn(container) {
  if (!container) return;
  for (const ch of Array.from(ACTIVE_CHARTS)) {
    const dom = ch.getDom();
    if (!dom || !document.body.contains(dom) || container.contains(dom)) {
      const ro = CHART_OBSERVERS.get(ch);
      if (ro) {
        ro.disconnect();
        CHART_OBSERVERS.delete(ch);
      }
      ch.dispose();
      ACTIVE_CHARTS.delete(ch);
    }
  }
}

function attachResizeObserver(chart, el) {
  if (typeof ResizeObserver === "undefined" || !el) return;
  const ro = new ResizeObserver(() => {
    try {
      chart.resize();
    } catch {}
  });
  ro.observe(el);
  CHART_OBSERVERS.set(chart, ro);
}

export function mountChart(el, option, onClick = null) {
  if (!el || typeof echarts === "undefined") return null;
  const existing = echarts.getInstanceByDom(el);
  if (existing) {
    const prevRo = CHART_OBSERVERS.get(existing);
    if (prevRo) prevRo.disconnect();
    existing.dispose();
    ACTIVE_CHARTS.delete(existing);
  }
  const p = palette();
  const chart = echarts.init(el);
  const merged = {
    backgroundColor: "transparent",
    color: p.colors,
    textStyle: { color: p.text, fontFamily: "inherit" },
    tooltip: {
      trigger: "axis",
      backgroundColor: p.elevated,
      borderColor: p.grid,
      textStyle: { color: p.textPrimary, fontSize: 12 },
    },
    grid: { left: 48, right: 20, top: 30, bottom: 32, containLabel: true },
    toolbox: {
      right: 6,
      top: 0,
      feature: { saveAsImage: { title: "PNG", pixelRatio: 2 } },
      iconStyle: { borderColor: p.muted },
    },
    ...option,
  };
  chart.setOption(merged);
  if (onClick) {
    chart.on("click", onClick);
  }
  attachResizeObserver(chart, el);
  ACTIVE_CHARTS.add(chart);
  return chart;
}

export function mountSparkline(el, values, positive = true) {
  if (!el || !values || values.length < 2 || typeof echarts === "undefined") return;
  const p = palette();
  const chart = echarts.init(el);
  const stroke = positive ? p.up : p.down;
  chart.setOption({
    animation: false,
    grid: { left: 1, right: 1, top: 2, bottom: 2 },
    xAxis: { type: "category", show: false, data: values.map((_, i) => i) },
    yAxis: { type: "value", show: false, min: (v) => v.min * 0.9, max: (v) => v.max * 1.05 },
    series: [
      {
        type: "line",
        data: values,
        symbol: "none",
        smooth: true,
        lineStyle: { width: 1.8, color: stroke },
        areaStyle: { color: stroke, opacity: 0.16 },
      },
    ],
  });
  attachResizeObserver(chart, el);
  ACTIVE_CHARTS.add(chart);
}

window.addEventListener("resize", () => {
  for (const ch of ACTIVE_CHARTS) {
    try {
      ch.resize();
    } catch {}
  }
});
