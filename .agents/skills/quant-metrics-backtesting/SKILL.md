---
name: quant-metrics-backtesting
description: >-
  Standardizes risk and performance formulas (CAGR, Sharpe, Sortino, Max Drawdown,
  VaR, Free Cash Flow) to prevent calculation discrepancies. Use when implementing
  portfolio analytics, KPI calculations, risk metrics, or backtesting engines.
---

# Quantitative Metrics & Backtesting Standards

Small differences in annualization factors, return conventions, or risk-free rate handling produce conflicting metrics across dashboards and reports. Follow one canonical definition per metric.

## Standard Formulas & Conventions

1. **Return Conventions**:
   - Specify explicitly whether a function uses **simple returns** $R_t = \frac{P_t - P_{t-1}}{P_{t-1}}$ (additive across portfolio weights) or **log returns** $r_t = \ln(P_t / P_{t-1})$ (additive across time). Do not mix them.
   - Default annualization factor: `252` for daily trading-day series, `12` for monthly, `52` for weekly, `365` for 24/7 crypto/calendar series. Always accept `periods_per_year` as an explicit parameter.
2. **CAGR (Compound Annual Growth Rate)**:
   - $\text{CAGR} = \left(\frac{V_{\text{end}}}{V_{\text{start}}}\right)^{\frac{1}{T}} - 1$, where $T = \frac{\text{calendar\_days}}{365.25}$ (or $\frac{n}{\text{periods\_per\_year}}$). Guard $V_{\text{start}} \le 0$ and $T \le 0$.
3. **Sharpe Ratio**:
   - $\text{Sharpe} = \frac{\mathbb{E}[R_p - R_f]}{\sigma(R_p - R_f)} \times \sqrt{\text{periods\_per\_year}}$, where $R_f$ is the per-period risk-free rate (converted from annual $R_{f,\text{annual}}$ via $(1 + R_{f,\text{annual}})^{1/P} - 1$ or $R_{f,\text{annual}} / P$).
   - Use sample standard deviation (`ddof=1`). Return `None`/`NaN` (not `inf`) if $\sigma = 0$ or $n < 2$.
4. **Sortino Ratio**:
   - $\text{Sortino} = \frac{\mathbb{E}[R_p - \text{MAR}]}{\text{DD}_{\text{MAR}}} \times \sqrt{\text{periods\_per\_year}}$, where downside deviation $\text{DD}_{\text{MAR}} = \sqrt{\frac{1}{n}\sum_{t=1}^{n} \min(0, R_{p,t} - \text{MAR})^2}$ (computed over all $n$ observations, not just negative ones).
5. **Max Drawdown (MDD)**:
   - Compute running peak $M_t = \max_{s \le t} V_s$ on the cumulative equity curve, drawdown $D_t = \frac{V_t - M_t}{M_t} \le 0$, and $\text{MDD} = \min_t D_t$. Report both magnitude and peak/trough/recovery timestamps.
6. **Value at Risk (VaR)**:
   - State method (`historical` quantile vs `parametric` normal) and confidence level (e.g., 95% or 99%) plus horizon (1-day). Historical 95% VaR is the 5th percentile loss.
7. **Free Cash Flow (FCF)**:
   - $\text{FCF} = \text{Operating Cash Flow} - \text{CapEx}$ (using exact `Decimal` arithmetic on financial statement line items).

## Verification Checklist

- Document `periods_per_year`, `risk_free_rate`, and `ddof` in every metric docstring.
- Guard all denominators (`std == 0`, `T <= 0`, `V_start <= 0`, empty series) against `ZeroDivisionError` and `inf`.
- Validate formulas against hand-calculated or audited golden test cases.
