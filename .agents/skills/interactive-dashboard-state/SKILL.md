---
name: interactive-dashboard-state
description: >-
  Structures global frontend state (base currency, time window, active benchmarks)
  with TanStack Query caching to avoid redundant reloads and keep views synchronized.
  Use when designing frontend state management, filters, URL sync, or data fetching.
---

# Interactive Dashboard State & Query Caching

Interactive financial dashboards suffer from cascading re-fetches, stale cross-widget filters, and flickering charts unless global filter state and server cache are cleanly separated.

## Core Rules

1. **Separate UI/Filter State from Server State**:
   - **Global Filter State** (base currency, date range / time window, active benchmarks, granularity, selected account/project): Store in URL search params (for shareable/bookmarkable state) mirrored with a lightweight store (e.g., Zustand or React Context).
   - **Server State** (prices, KPIs, ledger rows, charts): Manage exclusively with **TanStack Query** (`useQuery`), never duplicate fetched API data into a global store.
2. **Deterministic Query Keys**:
   - Include **every** active global filter that affects the response in the query key:
     `["portfolio", "performance", { projectId, currency, from, to, benchmark, interval }]`.
   - Normalize key objects (stable property order, ISO date strings) so identical filters always hit the cache.
3. **Cache Tuning & Smooth Transitions**:
   - Set `staleTime` by data volatility: `Infinity` for closed historical series, `30_000`–`60_000` ms for intraday summaries, `0` only when live-invalidated via WS/SSE.
   - Use `placeholderData: keepPreviousData` on range/currency switches so charts and tables stay visible without layout-jumping spinners while new data loads.
   - Enable `AbortSignal` (`signal` passed from `queryFn` to `fetch`) so rapid filter scrubbing cancels superseded HTTP requests.
4. **Selective Invalidation & Optimistic Updates**:
   - On mutations (e.g., uploading a file, posting an entry, changing settings), call `queryClient.invalidateQueries({ queryKey: [...] })` scoped to affected prefixes—avoid nuking the entire cache.
   - Prefetch adjacent views/tabs (`queryClient.prefetchQuery`) on hover or likely next step when payloads are small.

## Verification Checklist

- Verify every global filter affecting an endpoint is present in its TanStack `queryKey`.
- Verify `fetch` receives TanStack Query's `signal` for automatic request cancellation.
- Verify switching time windows uses `keepPreviousData` (no blank flicker).
