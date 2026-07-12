# Frontend Agent Instructions

## Essentials

- React 18 + TypeScript, Vite, Tailwind CSS, @tanstack/react-query, react-router-dom, lucide-react
- Light theme only — modern financial broker aesthetic
- All API calls go through `api/client.ts` which prefixes `/api/v1`
- Dev server binds `127.0.0.1:5173` (IPv4 only); API proxy → `127.0.0.1:8000`. Set `VITE_NO_HMR=1` to disable HMR.

## Architecture

- `api/client.ts` — typed `request<T>()` wrapper; measures timing via `performance.now()`, reports errors/slow requests to telemetry
- `hooks/useApi.ts` — react-query hooks for every API endpoint; mutations invalidate relevant query keys
- `types/index.ts` — TypeScript types matching backend response shapes exactly
- `lib/telemetry.ts` — batches error/timing/crash events, flushes to `POST /api/v1/telemetry/events` every 10s
- `components/ErrorBoundary.tsx` — class component wrapping `<App />`, catches render crashes

## Navigation

Modular sidebar defined in `config/modules.ts`. Sections: Options (Scanner, Conviction, Explore, Monitor, Settings), Stocks (Dashboard, Directional Alpha, Deep Dive, Conviction, Deep Dips, History), Intelligence, Research. Hybrid collapsibility — opening one section auto-collapses others.

## Pages

| Page | Key Hooks |
|---|---|
| Scanner (Options) | `useTriggerScan` (accepts `enableLlm`, `availableCapital`), `useLatestScan`, `useScanHistory`, `useScanById` |
| Conviction (Options) | `useConvictionScan`, `useTriggerConvictionScan` |
| Covered Calls | `useActivePositions`, `useCCAnalysis` |
| Monitor | `useTrackedPositions`, `useTrackPosition` |
| Settings | `useSystemConfig`, `useUpdateConfig` |
| Dashboard (Stocks) | `useActivePullbacks`, `useActivePositions`, `useCreatePosition`, `useExitPosition`, `useCheckExits`, `useRecentSignals` |
| Conviction (Stocks) | `useConvictionSnapshots`, `useBacktestProfile` |
| Deep Dips | `useDeepDips` |
| Deep Dive | `useTickerDeepDive` |
| Directional Alpha | `useAlphaScan`, `useRecomputeAlpha` |

## UI Conventions

- Conviction colors: high=emerald, medium=amber, low=red, none=gray
- Empty states show explanatory messages, never blank space
- `StatusBadge` for labels, `PLValue` for P&L numbers, `Card` for sections
- AllocationPlayground is client-side only — no backend call for what-if math
- Commission hardcoded at $0.65/contract
- Settings watchlist is **highlight-only** — blank Scanner scans full universe

## Scanner Components

- `EntryTimingBanner` — day-of-week guidance (Tue/Wed=green, Thu/Fri=amber)
- `PipelineFunnel` — filter stage visualization with counts
- **Deploy Capital** input — per-scan `available_capital` override for MILP allocator
- `AllocationSummaryCard` + `AllocationPlayground` — optimizer results + interactive customization
- `LlmAnalysesCard` — sorted by assignment comfort desc, then confidence desc
- LLM toggle switch (brain icon) next to "Run Scan" button — off by default to save time/cost

## Covered Calls Page

- Positions from `GET /stocks/positions/active` (shared with Stocks Dashboard)
- `DeepDivePanel` shows `TechnicalContextCard` with EMAs and `ema_21_slope` badge (informational — sell signal still extension-based)
- Live quote/premium overlay when Tradier broker available

## Directional Alpha Page (`pages/stocks/Alpha.tsx`)

- Big-move buy signals — Alpha score (0–100), horizon tag (Swing/Trend/Thematic), ML move probability, momentum/RS/return columns, market cap + institutional ownership.
- **Min Mkt Cap** selector (presets $250M–$10B) persisted to `localStorage` (`tyche_alpha_min_market_cap_m`, default $1B) and passed to `useAlphaScan({ minMarketCapMillions })`.
- Sortable columns: Alpha, Move Prob, RS vs SPY (6m), Return (6m), Off 52w High, Price, Mkt Cap, Inst Own (nulls sort last).
- **Horizon** uses a `multiselect` filter (empty = All; toggle any of Swing/Trend/Thematic). Signal uses `multiselect` too.
- Expandable row shows per-factor breakdown bars + per-horizon ML probabilities.
- `useRecomputeAlpha` triggers `POST /alpha/recompute` (background batch).

## Error Handling

- API errors reported via `telemetry.reportError()` in client.ts
- Network failures (status 0) tagged with `network_error: true`
- React crashes caught by ErrorBoundary → `telemetry.reportCrash()`
