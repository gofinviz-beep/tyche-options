# Frontend Agent Instructions

## Essentials

- React 18 + TypeScript, Vite, Tailwind CSS, @tanstack/react-query, react-router-dom, lucide-react
- Light theme only — modern financial broker aesthetic
- All API calls go through `api/client.ts` which prefixes `/api/v1`

## Architecture

- `api/client.ts` — typed `request<T>()` wrapper; measures timing via `performance.now()`, reports errors/slow requests to telemetry
- `hooks/useApi.ts` — react-query hooks for every API endpoint; mutations invalidate relevant query keys
- `types/index.ts` — TypeScript types matching backend response shapes exactly
- `lib/telemetry.ts` — batches error/timing/crash events, flushes to `POST /api/v1/telemetry/events` every 10s
- `components/ErrorBoundary.tsx` — class component wrapping `<App />`, catches render crashes

## Navigation

Modular sidebar defined in `navigation/modules.ts`. Sections: Options (Scanner, Conviction, Intents, Orders, Monitor, Settings), Research. Hybrid collapsibility — opening one section auto-collapses others.

## Pages

| Page | Key Hooks |
|---|---|
| Scanner (Options) | `useTriggerScan`, `useLatestScan`, `useScanHistory`, `useScanById` |
| Conviction (Options) | `useConvictionScan`, `useTriggerConvictionScan` |
| Intents | `useOrderIntents`, `useApproveIntent`, `useRejectIntent` |
| Orders | `useOpenOrders`, `useOrderIntents("executed")` |
| Monitor | `useTrackedPositions`, `useTrackPosition` |
| Settings | `useSystemConfig`, `useUpdateConfig` |
| Dashboard (Stocks) | `useActivePullbacks`, `useActivePositions`, `useCreatePosition`, `useExitPosition`, `useCheckExits`, `useRecentSignals` |
| Conviction (Stocks) | `useConvictionSnapshots`, `useBacktestProfile` |

## UI Conventions

- Conviction colors: high=emerald, medium=amber, low=red, none=gray
- Empty states show explanatory messages, never blank space
- `StatusBadge` for labels, `PLValue` for P&L numbers, `Card` for sections
- AllocationPlayground is client-side only — no backend call for what-if math
- Commission hardcoded at $0.65/contract

## Scanner Components

- `EntryTimingBanner` — day-of-week guidance (Tue/Wed=green, Thu/Fri=amber)
- `PipelineFunnel` — filter stage visualization with counts
- `AllocationSummaryCard` + `AllocationPlayground` — optimizer results + interactive customization
- `LlmAnalysesCard` — sorted by assignment comfort desc, then confidence desc

## Error Handling

- API errors reported via `telemetry.reportError()` in client.ts
- Network failures (status 0) tagged with `network_error: true`
- React crashes caught by ErrorBoundary → `telemetry.reportCrash()`
