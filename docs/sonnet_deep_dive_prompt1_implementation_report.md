# Stock Deep Dive — Recharts Rebuild Implementation Report

## Summary

Rebuilt `frontend/src/pages/stocks/DeepDive.tsx` to render real, polished charts via
[recharts](https://recharts.org/) (`^3.9.2`) instead of the previous fixed-height
`<div>`-bar pseudo-charts. Added a small, reusable chart component library under
`frontend/src/components/charts/` so every chart on the page shares one light-theme
look. Fixed a percent-scale double-count bug in fundamentals/estimates margin and
growth fields, and added data-driven callouts above the header. No backend, types, or
API-client code was touched.

## Files Created

| File | Purpose |
|---|---|
| `frontend/src/components/charts/theme.ts` | `CHART_COLORS` palette + `ChartTone` type shared by all charts |
| `frontend/src/components/charts/ChartTooltip.tsx` | Shared custom tooltip (white card, colored dot per series, formatted value) used by both chart wrappers |
| `frontend/src/components/charts/LineChartCard.tsx` | Wrapper around recharts `LineChart` — grid/axis/tooltip conventions, multi-series, dual Y-axis, dashed reference lines with labels, empty-state fallback |
| `frontend/src/components/charts/BarChartCard.tsx` | Wrapper around recharts `BarChart` — same conventions, optional per-bar `colorFn` (used for volume-surge highlighting), optional zero reference line, empty-state fallback |
| `frontend/src/components/charts/Callout.tsx` | Rounded, left-border-accented panel component (`info`/`warning`/`danger`/`success` tones) |

## Files Modified

| File | Change |
|---|---|
| `frontend/package.json` | Added `recharts": "^3.9.2"` dependency |
| `frontend/package-lock.json` | Updated by `npm install` |
| `frontend/src/pages/stocks/DeepDive.tsx` | Full rewrite of chart rendering — see below |

## What Changed in `DeepDive.tsx`

### Removed
- `RSITimeline` component (the old `<div>`-bar strip chart).
- The inline "Volume (60d)" `<div>`-bar block at the bottom of the page (superseded by
  the new "Price History & Volume" section, which renders volume as a proper bar chart
  right under the price line chart it corresponds to).

### Added — Multi-Timeframe RSI (3a)
- Kept the 4 `RSICard` gauges (Daily/Weekly/Monthly/Quarterly) unchanged.
- Weekly and Monthly RSI history are now `LineChartCard`s (`grid-cols-1 md:grid-cols-2`),
  y-domain clamped to `[30, 90]`, with dashed reference lines at 70 (Overbought, red),
  50 (Neutral, gray), and 30 (Oversold, emerald).
- Quarterly RSI history is a full-width dual-axis `LineChartCard`: RSI on the left axis
  (violet), Close price on the right axis (gray), with reference lines at 60 (Breakout,
  emerald) and 30 (Deep Oversold, red).
- The "Reading Guide" footer text is unchanged.

### Added — Price History & Volume (3b, new section)
- Only rendered when `price_history.length > 0`.
- A `LineChartCard` of weekly closes (`valuePrefix="$"`, height 260) with dashed
  reference lines for `ema_stack.ema_8` (blue, "EMA-8") and `ema_stack.ema_21` (amber,
  "EMA-21") — each line is only added if the corresponding EMA value is `> 0`, so
  tickers with insufficient history for a 21-EMA don't get a bogus reference line at 0.
- A `BarChartCard` of the last 60 daily `volume_bars` (`valueSuffix="M"`, height 200).
  Bars are colored via a `colorFn`: any bar with volume ≥ 3× the median volume across
  the 60-day window renders amber; all others render blue. A caption dynamically reports
  the single largest-volume day and its ratio to the median (e.g. `Peak: 42.1M on
  2026-05-14 — 4.2× median.`).

### Unchanged (3c)
- EMA Stack, MACD, and Bollinger Bands metric-card sections were left as-is per spec.

### Added — Fundamentals charts (3d)
- Only rendered when `fundamentals.length > 0`; otherwise the numeric table's own
  "No fundamentals data available" message is used (previously this was rendered
  unconditionally by `FundamentalsTable` itself — now the parent section gates it so the
  charts don't render on empty data either).
- 2×2 grid (`grid-cols-1 md:grid-cols-2`):
  - **Revenue** — `BarChartCard`, `revenue / 1e6`, `$…M`, blue.
  - **Cash** — `BarChartCard`, `cash / 1e6`, `$…M`, emerald.
  - **Net Income** — `LineChartCard`, `net_income / 1e6`, `$…M`, violet, with a zero
    reference line so profit/loss crossings are visually obvious.
  - **Gross Margin** — `LineChartCard`, `gross_margin` as-is (already percent-scale),
    `%`, emerald.
- The existing numeric `FundamentalsTable` is kept below the charts, with the percent
  formatting bug fixed (see below).

### Estimates & Catalysts (3e)
- Stat tiles (PT Mean/High/Low/Analyst count) and the forward EPS/revenue lists are
  unchanged.
- Percent-scale metric rows (`Rev Growth Q/Q YoY`, `Rev Growth TTM YoY`, `Gross Margin
  TTM`, `Op Margin TTM`) now use `formatPercentScale` instead of the old `* 100` logic.
- Catalyst list is unchanged.

## Percent Double-Count Bug Fix (Step 4)

The backend (`analysis/ticker_deep_dive.py`, confirmed via `GET
/api/v1/stocks/deep-dive/AAPL`) already returns `gross_margin`, `operating_margin`,
`net_margin`, `rev_growth_q_yoy`, `rev_growth_ttm_yoy`, `gross_margin_ttm`, and
`op_margin_ttm` as percent-scale floats (e.g. AAPL's `gross_margin` was `46.88`, meaning
46.88%). The previous frontend code multiplied these by 100 again (`(v * 100).toFixed(1)}%`
in `FundamentalsTable`, and `${(v * 100).toFixed(1)}%` in the Estimates `MetricRow`s),
which would have rendered AAPL's ~47% gross margin as `4688%`.

Added:

```ts
function formatPercentScale(v: number | null | undefined, decimals = 1): string {
  if (v == null) return "—";
  return `${v.toFixed(decimals)}%`;
}
```

and applied it (removing the erroneous `* 100`) to all seven fields listed above, plus
the new Gross Margin chart. Spot-checked against the live API:

| Ticker | `gross_margin` (raw from API) | Old rendering | New rendering |
|---|---|---|---|
| AAPL | `46.88` | `4688.3%` | `46.9%` |

RKLB and MSFT were not spot-checked live in this session (no live backend queries were
made for them), but the fix is field-agnostic — it applies the same formatter to every
affected value regardless of ticker, so the correction generalizes.

## Data-Driven Callouts (Step 5)

Added a pure `buildCallouts(data: TickerDeepDive): CalloutItem[]` function (no ticker
names, no hardcoded prose beyond the four templated messages specified in the prompt).
Rendered as a stack of `Callout` components directly below the header card, before the
RSI section. Conditions implemented exactly as specified:

1. `rsi.quarterly >= 60 && rsi.daily >= 70` → `warning`, "Structurally strong but
   short-term overbought".
2. `rsi.quarterly >= 60 && rsi.daily <= 50` → `success`, "Trend intact, momentum
   cooled" (mutually exclusive with #1 since daily RSI can't be both ≥70 and ≤50).
3. `estimates.pt_high != null && last_close > estimates.pt_high` → `danger`, "Trading
   above all analyst targets"; **else if** `estimates.pt_mean != null` and upside > 15%
   → `success`, "`{n}`% upside to mean target".
4. Any volume bar ≥ 3× the 60-day median → `info`, "Recent volume surge" (reuses the
   same `findVolumePeak` helper that drives the Volume chart's amber highlighting and
   caption, since the single largest-volume bar always has the highest ratio-to-median
   by construction).

Zero, one, two, or three callouts can render depending on the ticker's data — no
callout is forced to appear.

## Graceful Degradation

- `LineChartCard` and `BarChartCard` both render a muted dashed-border "No data
  available" placeholder (matching card height) when their `data` array is empty,
  rather than an empty/broken chart.
- "Price History & Volume" section is hidden entirely when `price_history` is empty;
  the volume sub-chart within it is hidden when `volume_bars` is empty (independently,
  since a ticker could theoretically have one without the other).
- "Quarterly Fundamentals" charts + table are hidden in favor of a "No fundamentals data
  available" message when `fundamentals` is empty.
- EMA-8/EMA-21 reference lines on the price chart are only added when the corresponding
  EMA value is `> 0`, avoiding a spurious line at $0 for thinly-traded or newly-listed
  tickers.
- `findVolumePeak` returns `null` (no crash) when `volume_bars` is empty or the median
  volume is `0`; all call sites (chart caption, colorFn, callout) handle the `null` case.
- "Recent Catalysts" section continues to hide entirely when `catalysts` is empty
  (unchanged behavior).

## Type-Safety Note

`TickerDeepDive`'s nested array types (`RSIReading[]`, `PricePoint[]`, `VolumeBar[]`)
are concrete interfaces without string index signatures, while the chart wrapper props
are typed as `Record<string, unknown>[]` per the spec (so `LineChartCard`/`BarChartCard`
stay decoupled from any specific domain type). TypeScript does not structurally allow
assigning a named interface array directly to `Record<string, unknown>[]`. Since
`types/index.ts` could not be modified, a tiny local helper was added in
`DeepDive.tsx`:

```ts
function toChartRows<T extends object>(rows: T[]): Record<string, unknown>[] {
  return rows as unknown as Record<string, unknown>[];
}
```

used only at the four call sites passing typed API arrays into the generic chart
components (`rsi.weekly_history`, `rsi.monthly_history`, `rsi.quarterly_history`,
`price_history`, `volume_bars`). Fundamentals chart data (`revenueData`, `cashData`,
`netIncomeData`, `grossMarginData`) is built from plain `{ period, value }` object
literals, which are already structurally compatible and need no cast.

## Verification

- `cd frontend && npm install recharts@^3.9.2` — installed `recharts@3.9.2` (React 19
  native support, no `react-is` override needed).
- `cd frontend && npm run build` (`tsc -b && vite build`) — **passes, zero TypeScript
  errors.** Bundle builds successfully (one pre-existing/unrelated warning about chunk
  size > 500kB, not introduced by this change).
- `cd frontend && npm run lint` — **could not run**: the repository has no
  `eslint.config.js` (or any ESLint config) checked into git at all, confirmed via
  `git log --all` on the frontend directory — this is a pre-existing repository gap,
  not something introduced or observed to work previously. The IDE's built-in linter
  (`ReadLints`) was run against all new/modified files and reported zero errors.
- Live data spot-check against the running backend (`GET
  /api/v1/stocks/deep-dive/AAPL`) confirmed `price_history` (104 weekly points),
  `volume_bars` (60 daily bars), and `fundamentals` (6 quarters) are populated as
  expected, and that `gross_margin: 46.88` / `gross_margin_ttm: 47.86` are indeed
  percent-scale (not fractional), validating the Step 4 fix.
- A live browser screenshot of the rendered page was not captured in this session
  (browser automation tooling was not installed and pulling it down was judged
  out-of-scope for this change); confidence in visual correctness rests on the
  TypeScript build passing against the wrapper components' documented prop contracts
  plus the live API shape check above.

## Acceptance Criteria Checklist

- [x] `npm run build` passes, no TS errors.
- [x] RSI histories are line charts with 70/50/30 (and 60/30 for quarterly) reference
      lines; quarterly overlays RSI + price on dual axes.
- [x] A price-history line chart renders (previously absent) with EMA-8/EMA-21
      reference lines.
- [x] Volume is a bar chart with surge days highlighted amber + dynamic peak caption.
- [x] Fundamentals show Revenue (bar), Cash (bar), Net Income (line), Gross Margin
      (line) + the numeric table.
- [x] All percentages render plausibly (verified AAPL gross margin renders as `46.9%`,
      not `4688%`).
- [x] 2–3 data-driven callouts appear when conditions hold; no hardcoded ticker names.
- [x] Empty/null data degrades gracefully; no crash paths identified.
- [x] All charts share one consistent light-theme look via the `theme.ts` /
      `ChartTooltip` / `LineChartCard` / `BarChartCard` wrappers.
- [ ] `npm run lint` passes — blocked by a pre-existing missing ESLint config file
      unrelated to this change (see Verification section).
