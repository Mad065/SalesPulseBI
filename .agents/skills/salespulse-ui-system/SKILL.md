---
name: salespulse-ui-system
description: >-
  Enforces the SalesPulseBI UI Design System, 4 visual style presets (precision,
  terminal, editorial, bento), CSS cascade layers (@layer), semantic tokens,
  colorblind-safe financial indicators, sticky responsive tables, and dynamic
  ECharts theme synchronization. Use whenever creating or modifying HTML, CSS,
  charts, or frontend views in src/salespulsebi/web/.
---

# SalesPulseBI UI Design System

Whenever you create, update, or refactor any frontend code in `src/salespulsebi/web/`, you **MUST** consult and follow the full specification in `docs/UI_STYLE_GUIDE.md` alongside `web-financial-ui`.

## Mandatory Rules

1. **Multi-Preset & Dual-Theme Token Architecture (`tokens.css`)**:
   - The `<html data-theme="dark|light" data-style="precision|terminal|editorial|bento">` root controls all visual tokens.
   - Default preset is `precision` (*Swiss Editorial & Precision FinTech*).
   - **Never** hardcode hex colors (`#10b981`, `#6366f1`, `#ef4444`, etc.) in HTML, CSS rules, or JavaScript ECharts options. Always reference CSS custom properties (`--accent`, `--fin-up`, `--fin-down`, `--fin-warn`, `--fin-info`, `--bg-surface`, `--bg-elevated`, `--border`, `--text-primary`, `--text-secondary`) or `palette()` from `charts.js`.

2. **Modern CSS Architecture (`@layer`, Logical Properties, Container Queries & Subgrid)**:
   - Place all rules in their designated cascade layer: `@layer base, tokens, layout, components, utilities;`.
   - Do not use global `*` resets; use `:where(...)` in `@layer base`.
   - Use logical properties (`inline-size`, `block-size`, `min-block-size`, `padding-inline`, `padding-block`, `margin-inline-start`, `inset-block-start`).
   - Never set fixed `height` / `block-size` on text-bearing elements (buttons, inputs, pills, topbar, cards); use `min-block-size` and padding.
   - Use `@container main-view (inline-size < ...)` for dashboard grids and `grid-template-rows: subgrid` on multi-row card grids (`.project-card`).
   - Use `:has(:checked)` for parent state styling (`.checkbox-pill`) and `:not(:disabled)` / `:not(:last-child)` instead of rule overrides.

3. **Financial Formatting & Colorblind-Safe Accessibility**:
   - Format all currencies with `fmtCurrency`, numbers with `fmtNumber`, and percentages with `fmtPercent` (`Intl.NumberFormat` in `src/salespulsebi/web/static/js/i18n.js`). Never concatenate `'$'` or `'%'` manually.
   - Apply `.num` or `.tabular` (`font-variant-numeric: tabular-nums`) to all KPI values, delta badges, dates, and numeric table cells.
   - Pair every positive/negative variation with a directional cue (`▲` / `▼` and signed percentage) using `.delta-pos` (`--fin-up`) and `.delta-neg` (`--fin-down`).

4. **Accessible Responsive Data Tables & ECharts**:
   - Wrap every `.data-table` in `<div class="table-wrap" tabindex="0" role="region" aria-label="...">` so sticky `<thead>` headers, keyboard scrolling, and `scrollbar-gutter: stable` work consistently.
   - Mount all charts via `mountChart` / `mountSparkline` in `charts.js` using colors from `palette()`.
