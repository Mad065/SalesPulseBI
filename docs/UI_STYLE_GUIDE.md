# SalesPulseBI — Guía de Sistema de Diseño y Mantenimiento UI

Esta guía documenta la arquitectura visual, tokens semánticos, patrones de layout moderno y reglas de accesibilidad financiera de **SalesPulseBI**. Todo nuevo módulo, vista, tabla o gráfica que se agregue al proyecto **debe** seguir estas especificaciones para mantener una interfaz cohesiva.

---

## 1. Filosofía y Principios Visuales

1. **Claridad Financiera de Alta Densidad**: La interfaz prioriza la legibilidad de cifras, comparativos temporales y distribuciones sin ruido visual ni dependencias CSS externas.
2. **Accesibilidad Financiera (Colorblind-Safe & WCAG AA)**:
   - **Nunca** depender únicamente de rojo vs. verde para indicar variaciones positivas o negativas.
   - Usar siempre los tokens semánticos `--fin-up` (Teal/Esmeralda) y `--fin-down` (Coral/Naranja) acompañados de indicadores direccionales explícitos (`▲` / `▼` y signo `+` / `-`).
   - Todo número financiero, métrica KPI, porcentaje o celda numérica de tabla debe aplicar `font-variant-numeric: tabular-nums` (`.tabular` o `.num`).
3. **Arquitectura Multi-Preset + Dual Theme**:
   - El elemento raíz `<html>` controla simultáneamente la polaridad de color (`data-theme="dark" | "light"`) y la personalidad estética (`data-style="precision" | "terminal" | "editorial" | "bento"`).
   - Ninguna vista debe escribir colores hexadecimales fijos (`#10b981`, `#6366f1`, etc.) ni en el DOM ni en configuraciones de Apache ECharts.

---

## 2. Los 4 Presets Visuales (`data-style`)

El estilo predeterminado de SalesPulseBI es **`precision`** (*Swiss Editorial & Precision FinTech*), pero todos los componentes deben verse impecables en cualquiera de los 4 presets:

| Preset (`data-style`) | Inspiración | Rasgos Clave | Tokens que Modifica |
| :--- | :--- | :--- | :--- |
| **`precision`** *(Default)* | Linear / Stripe | Bordes `1px` nítidos, radios `10px` (`0.625rem`), Azul Cobalto + Teal + Coral | `--accent: #3b82f6` (dark) / `#2563eb` (light), `--fin-up: #14b8a6`, `--fin-down: #fb923c` |
| **`terminal`** | TradingView / Koyfin | Alta densidad, radios técnicos (`4px–6px`), KPIs en `--font-mono`, borde superior de acento en tarjetas (`2px`) | `--font-kpi: var(--font-mono)`, `--card-border-top-width: 2px`, `--accent: #06b6d4` |
| **`editorial`** | Observable / Notion | Superficies cálidas papel marfil / carbón cálido, títulos en serif editorial (`--font-display`), acento Terracota | `--font-display`, `--bg-canvas: #121110` / `#faf7f2`, `--accent: #e07a5f` / `#c2410c` |
| **`bento`** | macOS Sequoia / Raycast | Superficies translúcidas con `backdrop-filter: blur(16px)`, radios amplios (`14px–18px`), acento Índigo | `--surface-blur: blur(16px)`, `--radius-lg: 1.125rem`, `--bg-surface` translúcido |

---

## 3. Arquitectura CSS y Capas (`@layer`)

Los estilos en `src/salespulsebi/web/static/css/` declaran un orden estricto de cascada en `tokens.css`:

```css
@layer base, tokens, layout, components, utilities;
```

### Reglas de Cascada y Selectores Modernos
- **Sin resets globales en `*`**: No utilizar `* { ... }`. El reset de `box-sizing: border-box` vive en `@layer base` usando `:where(...)` con especificidad cero.
- **Propiedades Lógicas**: Preferir `inline-size`, `block-size`, `min-block-size`, `max-inline-size`, `padding-inline`, `padding-block`, `margin-inline-start`, `inset-block-start` y `border-inline-start` sobre propiedades físicas (`width`, `height`, `padding-left`, `top`).
- **Sin alturas fijas en elementos con texto**: Nunca fijar `height` o `block-size` en botones, inputs, píldoras, barra superior o tarjetas. Usar `min-block-size` y `padding-block` para respetar el escalado de texto del usuario (WCAG 1.4.12).
- **Evitar sobreescrituras con `:not()` y `:has()`**:
  - Usar `.btn:hover:not(:disabled)` en lugar de sobreescribir `:disabled` después de `:hover`.
  - Usar `.fs-item:not(:last-child)` en lugar de quitar el borde en `:last-child`.
  - Usar `.checkbox-pill:has(:checked)` para estilizar contenedores según el estado de su `<input>` hijo sin manipular clases desde JavaScript.
- **Mezcla de color segura**: Para generar tintes suaves (`--accent-soft`, `--fin-up-soft`), usar siempre `color-mix(in oklab, var(--token) 15%, transparent)` (evitar `in oklch` al mezclar con neutros o `transparent`).

---

## 4. Referencia de Design Tokens (`tokens.css`)

### Tipografía y Escala (`rem`)
| Token | Uso | Valor |
| :--- | :--- | :--- |
| `--font-sans` | Texto general de interfaz | `"Inter", -apple-system, BlinkMacSystemFont, sans-serif` |
| `--font-mono` | Código, esquemas y métricas técnicas | `"JetBrains Mono", "SFMono-Regular", ui-monospace, monospace` |
| `--font-kpi` | Números de tarjetas KPI y celdas `.num` | `var(--font-sans)` (`var(--font-mono)` en `terminal`) |
| `--font-display` | Títulos editoriales y cabeceras de tarjeta | `var(--font-sans)` (Serif en `editorial`) |
| `--text-2xs` | Etiquetas uppercase, cabeceras `<th>`, badges | `0.6875rem` |
| `--text-xs` | Subtítulos, controles `.btn-sm` / `.input-sm` | `0.75rem` |
| `--text-sm` | Celdas de tabla, navegación lateral, botones | `0.8125rem` |
| `--text-base` | Cuerpo base | `0.875rem` |
| `--text-md` | Títulos de tarjeta (`.card-title`) | `0.9375rem` |
| `--text-xl` | Valor principal de KPI (`.kpi-value`) | `clamp(1.25rem, 1.1rem + 0.5vw, 1.5rem)` |
| `--text-2xl` | Título Hero (`.hub-hero-title`) | `clamp(1.375rem, 1.15rem + 0.85vw, 1.85rem)` |

### Superficies y Semántica Financiera
| Token | Propósito |
| :--- | :--- |
| `--bg-canvas` | Fondo principal de la ventana (`<body>`) |
| `--bg-surface` | Fondo de tarjetas (`.card`, `.kpi-card`, `.sidebar`, `.table-wrap`) |
| `--bg-elevated` | Fondo de inputs, cabeceras de tabla `<thead>`, bloques secundarios |
| `--bg-hover` | Estado hover de filas, botones secundarios e ítems de menú |
| `--bg-glass` | Superficie translúcida para `.topbar` y `.filter-bar` sticky |
| `--border` / `--border-strong` | Bordes sutiles de contenedor y bordes de controles interactivos (contraste `>= 3:1`) |
| `--accent` / `--accent-soft` | Color primario de acción, pestaña activa y chips de filtro |
| `--fin-up` / `--fin-up-soft` | Variación positiva (`▲`), salud óptima (`>= 85`), Clase A |
| `--fin-down` / `--fin-down-soft` | Variación negativa (`▼`), alertas, acciones destructivas |
| `--fin-warn` / `--fin-warn-soft` | Advertencias, anomalías moderadas, líneas de referencia |
| `--chart-1` .. `--chart-8` | Paleta categórica de 8 colores sincronizada con Apache ECharts |

---

## 5. Recetas de Componentes Estándar

### A. Tarjeta KPI Financiera (`.kpi-card`)
```html
<div class="kpi-card">
  <div class="kpi-label">Ingresos Netos</div>
  <div class="kpi-value" title="$1,245,890.45">$1,245,890</div>
  <div class="kpi-footer">
    <span class="delta-badge delta-pos">
      <span aria-hidden="true">▲</span> +14.2%
    </span>
    <div class="sparkline-box" id="spark-revenue"></div>
  </div>
</div>
```

### B. Tarjeta de Contenido con Toolbar (`.card`)
```html
<div class="card card-spaced">
  <div class="card-header">
    <h3 class="card-title">Título del Módulo</h3>
    <div class="card-toolbar">
      <label class="toolbar-label" for="ctrl-id">Filtro</label>
      <select id="ctrl-id" class="input input-sm">...</select>
    </div>
  </div>
  <div id="chart-id" class="chart-box"></div>
</div>
```

### C. Tabla de Datos Responsiva con Cabecera Sticky (`.table-wrap`)
Todo contenedor de tabla debe ser navegable por teclado (`tabindex="0"`), declarar `role="region"` y un `aria-label` descriptivo:

```html
<div class="table-wrap table-wrap-scroll" tabindex="0" role="region" aria-label="Catálogo de Productos">
  <table class="data-table">
    <thead>
      <tr>
        <th>Producto</th>
        <th>ABC</th>
        <th class="num">Ingresos</th>
        <th class="num">Margen</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>Monitor Pro 27"</td>
        <td><span class="pill pill-emerald">A</span></td>
        <td class="num">$48,200</td>
        <td class="num">34.5%</td>
      </tr>
    </tbody>
  </table>
</div>
```

---

## 6. Reglas para Formateo y Gráficas (JavaScript & ECharts)

1. **Formateo con `Intl.NumberFormat` (`i18n.js`)**:
   - **Monedas**: Usar siempre `fmtCurrency(val, currency, lang)` (o `fmtCurrency(val, currency, lang, true)` para notación compacta con el valor exacto en el atributo `title`).
   - **Cantidades**: Usar `fmtNumber(val, decimals, lang)`.
   - **Porcentajes**: Usar `fmtPercent(val, decimals, lang, signed)` — nunca concatenar `+ "%"` manualmente.
2. **Gráficas Apache ECharts (`charts.js`)**:
   - Montar todas las gráficas mediante `mountChart(el, option, onClick)` y `mountSparkline(el, values, isGood)`.
   - Obtener colores exclusivamente a través de `const p = palette();` (`p.accent`, `p.up`, `p.down`, `p.warn`, `p.info`, `p.grid`, `p.elevated`, `p.text`, `p.colors`).
   - `charts.js` lee automáticamente los tokens CSS vigentes mediante `getComputedStyle(document.documentElement)` y gestiona `ResizeObserver` + limpieza de memoria (`disposeChartsIn`).

---

## 7. Checklist de Verificación para Nuevas Vistas

Antes de dar por terminada cualquier modificación en `src/salespulsebi/web/`:

- [ ] ¿Se utilizaron clases semánticas de `layout.css` y `components.css` en lugar de estilos `style="..."` inline?
- [ ] ¿Todas las cifras, fechas, porcentajes y columnas numéricas tienen `.num` o `.tabular` (`font-variant-numeric: tabular-nums`)?
- [ ] ¿Todos los valores monetarios y porcentuales pasan por `fmtCurrency`, `fmtNumber` o `fmtPercent` (`Intl.NumberFormat`)?
- [ ] ¿Los indicadores de variación positiva/negativa incluyen un símbolo no cromático (`▲` / `▼` o `+` / `-`) además de `--fin-up` / `--fin-down`?
- [ ] ¿Las gráficas ECharts usan `palette()` sin colores hexadecimales fijos?
- [ ] ¿Las tablas están envueltas en `.table-wrap` con `tabindex="0" role="region" aria-label="..."`?
- [ ] ¿La vista se visualiza correctamente en los 4 presets (`precision`, `terminal`, `editorial`, `bento`) tanto en modo `dark` como en modo `light`?
