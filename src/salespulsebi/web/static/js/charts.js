import { state } from "./state.js";

const ACTIVE_CHARTS = new Set();

function palette() {
  const isDark = state.theme === "dark";
  return {
    text: isDark ? "#cbd5e1" : "#334155",
    muted: isDark ? "#64748b" : "#94a3b8",
    grid: isDark ? "#1e293b" : "#e2e8f0",
    bg: isDark ? "#111827" : "#ffffff",
    colors: ["#6366f1", "#10b981", "#06b6d4", "#f59e0b", "#ec4899", "#8b5cf6", "#14b8a6", "#f97316"],
  };
}

export function disposeChartsIn(container) {
  if (!container) return;
  for (const ch of Array.from(ACTIVE_CHARTS)) {
    const dom = ch.getDom();
    if (!dom || !document.body.contains(dom) || container.contains(dom)) {
      ch.dispose();
      ACTIVE_CHARTS.delete(ch);
    }
  }
}

export function mountChart(el, option, onClick = null) {
  if (!el || typeof echarts === "undefined") return null;
  const existing = echarts.getInstanceByDom(el);
  if (existing) {
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
      backgroundColor: state.theme === "dark" ? "#0f172a" : "#ffffff",
      borderColor: p.grid,
      textStyle: { color: p.text, fontSize: 12 },
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
  ACTIVE_CHARTS.add(chart);
  return chart;
}

export function mountSparkline(el, values, positive = true) {
  if (!el || !values || values.length < 2 || typeof echarts === "undefined") return;
  const chart = echarts.init(el);
  const stroke = positive ? "#10b981" : "#ef4444";
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
        areaStyle: { color: positive ? "rgba(16,185,129,0.16)" : "rgba(239,68,68,0.16)" },
      },
    ],
  });
  ACTIVE_CHARTS.add(chart);
}

window.addEventListener("resize", () => {
  for (const ch of ACTIVE_CHARTS) {
    try { ch.resize(); } catch {}
  }
});

export { palette };
