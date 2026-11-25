---
name: web-financial-ui
description: >-
  Establishes specialized financial visualization libraries (TradingView Lightweight
  Charts, Apache ECharts), Intl.NumberFormat localization, stable loading states,
  and colorblind-safe palettes for candlesticks and variations. Use when building
  or updating frontend charts, KPI cards, financial tables, or dashboard UI.
---

# Web Financial UI

Financial interfaces require high information density without visual clutter, exact formatting alignment, and accessibility for all users.

## Core Rules

1. **Specialized Charting Libraries**:
   - **Time-series, OHLCV candlesticks, area/line price charts**: Use **TradingView Lightweight Charts** (`lightweight-charts`) for 60fps pan/zoom, crosshair sync, and clean time scales.
   - **Analytics, KPIs, heatmaps, treemaps, distributions, multi-axis comparisons**: Use **Apache ECharts** (`echarts`) with responsive resize observers and proper disposal on unmount.
   - Avoid generic or heavy DOM/SVG-only chart libraries for dense financial series (>500 points).
2. **Number & Currency Formatting**:
   - Always use `Intl.NumberFormat` (never manual string concatenation like `'$' + val.toFixed(2)`).
   - Provide explicit `style: "currency"`, `currency` (ISO 4217), and `minimumFractionDigits` / `maximumFractionDigits`.
   - Use `style: "percent"` for returns/ratios and `notation: "compact"` for large KPI summaries (with full exact value in tooltip/`title`).
   - Apply CSS `font-variant-numeric: tabular-nums;` on all tables, tickers, prices, and KPI counters so columns align vertically and numbers do not jitter during live updates.
3. **Loading, Empty & Error States**:
   - Render layout-preserving **skeletons** with fixed dimensions matching the final chart/card to prevent Cumulative Layout Shift (CLS).
   - Provide explicit, actionable **empty states** (e.g., "No trades in selected range — Reset filters") and **error states** with a retry button.
4. **Colorblind-Safe Palettes & Accessibility**:
   - NEVER rely on red vs. green alone to convey positive/negative variations or up/down candlesticks.
   - Offer or default to a colorblind-safe palette (e.g., **Blue `#2563EB` / Orange `#EA580C`** or **Teal `#0D9488` / Coral `#E11D48`**) and pair color with directional indicators (`+`/`-`, ▲/▼ arrows, or hollow vs. filled candles).
   - Ensure text/background contrast meets **WCAG AA** (`>= 4.5:1` for normal text, `>= 3:1` for large text and chart elements) in both light and dark themes.

## Verification Checklist

- Verify `tabular-nums` is applied to all numeric table cells and live price readouts.
- Verify no hardcoded `$` or `%` string formatting bypasses `Intl.NumberFormat`.
- Check positive/negative indicators include a non-color cue (`+`/`-`, ▲/▼) and meet WCAG AA contrast.
