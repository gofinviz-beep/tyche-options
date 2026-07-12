# Stock Deep Dive — Multi-Timeframe Technical + Fundamental Analysis

**Status:** v1 implemented — **2026-07-01**; v2 (per-ticker precompute + read-through cache) implemented — **2026-07-12**; v3 (universe-wide Screener Index / "Diamond Finder") implemented — **2026-07-12**

## Purpose

Per-ticker on-demand analysis combining multi-timeframe RSI (daily, weekly, monthly, quarterly), EMA stack scoring, MACD, Bollinger Bands, period returns, volume profile, quarterly fundamentals, analyst estimates, and demand catalysts into a single comprehensive view. Designed to answer "should I enter this stock now, or wait for a better setup?" by providing technical timing signals alongside fundamental quality context.

## Why Multi-Timeframe RSI?

- **Daily RSI** — entry/exit timing (pullback to 40-50, overbought at 70+)
- **Weekly RSI** — confirms trend momentum; divergence from daily = caution
- **Monthly RSI** — identifies sector rotation and intermediate-term trend shifts
- **Quarterly RSI** — structural breakout detection; RSI 60+ can indicate a breakout that will persist over months; wait for daily RSI pullback to enter

The combination avoids false signals from any single timeframe. A stock with quarterly RSI 65 but daily RSI 35 is a strong entry candidate (structural uptrend + short-term pullback). A stock with daily RSI 75 and quarterly RSI 40 is extended within a weak long-term trend — avoid.

---

## Architecture

### v1 — On-Demand Single-Ticker

```
Frontend (DeepDive.tsx)
  → GET /api/v1/stocks/deep-dive/{ticker}
    → TickerDeepDiveEngine.analyze(ticker)
      → OHLCVStore.read_ticker()    — daily bars
      → TickerMetaStore             — name, sector, market cap, inst own
      → FundamentalsStore           — quarterly financials (Finnhub)
      → EstimatesStore              — analyst consensus (Finnhub)
      → CatalystSignalStore         — demand catalysts (news/8-K/Benzinga)
    → TickerDeepDiveResponse (Pydantic, via schemas/deep_dive.py::to_response())
  → React page renders sections
```

Computation is synchronous (single ticker = fast). GCS cloud mode is supported via `require_inline_compute_allowed()` / `cloud_inline_compute_blocked()` guards.

### v2 — Per-Ticker Precompute + Read-Through Cache (Current)

Nightly batch (`workflow/deep_dive_batch.py::run_deep_dive_batch`) computes the full deep-dive payload for the equity universe ≥ `deep_dive_batch_min_market_cap_millions` and persists **one Parquet file per ticker** — `signals/stocks/deep_dive/{TICKER}.parquet` (`market_data/deep_dive_store.py::DeepDiveStore`). This is intentionally **not** a monolithic universe file (unlike `conviction.parquet`) — v3's screener index depends on the per-ticker layout, and the per-ticker payload is large (price history, volume bars, fundamentals, estimates, catalysts).

Each Parquet file has a single row with columns `ticker`, `as_of_date`, `computed_at`, and `payload_json` (the full `TickerDeepDiveResponse.model_dump_json()`). Storing the serialized payload as a JSON string sidesteps fragile nested-array Parquet schemas and is schema-evolution safe — adding a response field never requires a migration.

**There is no publish-JSON step.** The route reads `DeepDiveStore` directly, which works identically in local and GCS modes (`StoreBackend` resolves the backend from `TycheSettings.data_backend`).

`GET /stocks/deep-dive/{ticker}` resolution order:

1. **In-memory cache**, keyed by `(ticker, latest_ohlcv_session_date)` — cleared by `deps.reset_all()` and on every batch run.
2. **`DeepDiveStore.read_ticker(ticker)`** — if present and its `as_of_date` is within `deep_dive_max_staleness_sessions` trading sessions of the latest OHLCV session, serve it and populate the cache.
3. **On-demand fallback** — cache miss or stale, and inline compute is allowed: `TickerDeepDiveEngine.analyze()` (the v1 path), serialized via the same shared `to_response()`, then written back into the store.
4. **Cloud-mode stale serve** — if inline compute is blocked (`TYCHE_DATA_BACKEND=gcs` without `TYCHE_ALLOW_INLINE_SCAN=true`) but a precomputed payload exists, it is served **even if stale** (its original `as_of_date` is preserved). Only 404s when nothing has ever been computed for the ticker.

`force=true` bypasses the cache and store entirely and recomputes (409 in cloud mode with inline compute blocked, mirroring `/stocks/deep-dips?force=true`).

Because the route and the batch share one serializer (`schemas/deep_dive.py::to_response()`), a cached payload and a freshly-computed payload are byte-for-byte the same shape — margins/growth stay percent-scale (e.g. `46.88`) either way.

**Scheduling:**
- **Local (APScheduler):** `WorkflowScheduler.schedule_deep_dive_batch()`, default 4:15 PM ET weekdays — chained after the OHLCV refresh + conviction batch, gated by `deep_dive_batch_enabled`. Handler: `app.py::_scheduled_deep_dive_batch`.
- **Cloud:** `tyche-stocks-deep-dive-batch` Cloud Run Job (`ops/gcp_jobs.py::run_deep_dive_batch_job`, registered in `JOB_RUNNERS`). Fired fire-and-forget at the very start of `infra/gcp/workflows/morning-pipeline.yaml` (depends only on OHLCV + demand stores, not the demand gate/flatfiles/alpha) — the workflow never polls it, so it cannot block `run_publish`.

### v3 — Screener Index (Shipped)

**Clarification:** v3 does NOT introduce multi-timeframe RSI — that already lives in Deep Dive (v1). v3 **reuses** Deep Dive's existing per-ticker RSI + EMA-stack signals and exposes them as filterable/sortable columns across the *whole universe*, so you can scan thousands of tickers for setups like "quarterly RSI ≥ 60, daily RSI 35–50, above 200-SMA" and jump into any name's Deep Dive to confirm.

The screener is a compact **single Parquet index** (`signals/stocks/screener_index.parquet`) — one row per ticker, scalar columns only (like `conviction_signals.parquet` / `alpha_signals.parquet`). This is distinct from the large per-ticker deep-dive payloads (`signals/stocks/deep_dive/{TICKER}.parquet`), which stay one-file-per-ticker. A screener must query one table, so the index is intentionally a single compact file; sharding it per-ticker would defeat the purpose.

Integration points:
- Batch (`stocks-screener-index-batch`, `workflow/screener_index_batch.py::run_screener_index_batch`) prefers the v2 `DeepDiveStore` per ticker and falls back to an inline `TickerDeepDiveEngine.analyze()` when the store has no payload yet — extracts scalar signals → `ScreenerIndexStore` (single Parquet). Chained after the v2 deep-dive batch locally (`app.py::_scheduled_screener_index_batch`) and fire-and-forget in `morning-pipeline.yaml` (same pattern as the deep-dive batch — does not block `run_publish`).
- Standalone store — does **not** touch conviction SQLite snapshots or its 5-layer cache. Registered in the GCS publish convention (`published/routes/stocks_screener.json`, `ROUTE_PATHS["stocks_screener"]`, `publish_stocks_screener` in `workflow/publish_signals.py`) mirroring `publish_stocks_deep_dips`.
- API: `GET /stocks/screener?q_rsi_min=60&d_rsi_min=35&d_rsi_max=50&above_sma200=true&setup_label=Prime%20Pullback` (`api/routes/screener.py`).
- Screener page (`frontend/src/pages/stocks/Screener.tsx`) with DataTable multiselect/range filters + preset "recipe" buttons (Prime Pullback, Structural Breakout, Emerging Breakout, Deep Reversal, Avoid List); each row links to its Deep Dive page. Nav entry under Stocks → Screener (Gem icon).
- Each row carries a composite `setup_score` (0–100) and `setup_label` — the "diamond finder" (see [v3 Screener — Diamond Finder Calibration](#v3-screener--diamond-finder-calibration)). Implemented verbatim per the calibration spec below; unit-tested against Prime Pullback / Overextended fixtures.
- Return keys from `TickerDeepDiveResponse.returns` are upper-case (`"1M"`, `"3M"`, `"6M"`, `"1Y"`, plus `"1W"`/`"2W"`) — the batch maps them to lower-case `ret_1m`/`ret_3m`/`ret_6m`/`ret_1y` index columns.
- Full implementation write-up: `docs/sonnet_deep_dive_prompt3_implementation_report.md`.

---

## Files

| File | Purpose |
|------|---------|
| `backend/src/tyche/analysis/ticker_deep_dive.py` | `TickerDeepDiveEngine` — all computation logic |
| `backend/src/tyche/schemas/deep_dive.py` | Pydantic response schemas + shared `to_response()` serializer |
| `backend/src/tyche/market_data/deep_dive_store.py` | `DeepDiveStore` — per-ticker Parquet store (`signals/stocks/deep_dive/{TICKER}.parquet`) |
| `backend/src/tyche/workflow/deep_dive_batch.py` | `run_deep_dive_batch()` — nightly precompute over the filtered universe |
| `backend/src/tyche/api/routes/deep_dive.py` | `GET /stocks/deep-dive/{ticker}` route — cache → store → on-demand fallback |
| `backend/src/tyche/ops/gcp_jobs.py` | `run_deep_dive_batch_job()` — Cloud Run Job entry (`stocks-deep-dive-batch`) |
| `backend/tests/unit/test_ticker_deep_dive.py` | 36 unit tests (engine) |
| `backend/tests/unit/test_deep_dive_store.py` | Store round-trip, fidelity, one-file-per-ticker layout |
| `backend/tests/unit/test_deep_dive_batch.py` | Batch universe filtering, error isolation, write count |
| `backend/tests/unit/test_deep_dive_route.py` | Cache/store/fallback/force/cloud-mode route tests |
| `frontend/src/pages/stocks/DeepDive.tsx` | React page with RSI cards, EMA stack, MACD, BB, fundamentals table |
| `frontend/src/types/index.ts` | TypeScript types (`TickerDeepDive`, etc.) |
| `frontend/src/hooks/useApi.ts` | `useTickerDeepDive(ticker)` hook |
| `frontend/src/api/client.ts` | `api.stocks.getDeepDive(ticker)` |
| `frontend/src/config/modules.ts` | Nav entry under Stocks → Deep Dive |
| `backend/src/tyche/market_data/screener_index_store.py` | `ScreenerIndexStore` — single-file universe index (`signals/stocks/screener_index.parquet`) + `load_screener_rows()` |
| `backend/src/tyche/workflow/screener_index_batch.py` | `run_screener_index_batch()` — Diamond Finder `setup_score`/`setup_label`, DeepDiveStore-preferred + inline-engine-fallback row extraction |
| `backend/src/tyche/schemas/screener.py` | `ScreenerRow` / `ScreenerResponse` Pydantic schemas |
| `backend/src/tyche/api/routes/screener.py` | `GET /stocks/screener` route — server-side filter/sort over the index |
| `backend/src/tyche/persistence/published_routes.py` | `get_stocks_screener_scan()` — published-JSON-first read (GCS mode) |
| `backend/src/tyche/ops/gcp_jobs.py` | `run_screener_index_batch_job()` — Cloud Run Job entry (`stocks-screener-index-batch`) |
| `backend/tests/unit/test_screener_index_store.py` | Store round-trip, single-file layout, overwrite behavior |
| `backend/tests/unit/test_screener_index_batch.py` | `setup_score`/`setup_label` fixtures, store-preference + inline fallback, filtering, error isolation |
| `backend/tests/unit/test_screener_routes.py` | Route filter/sort coverage (RSI ranges, booleans, market cap, sector, setup label) |
| `frontend/src/pages/stocks/Screener.tsx` | React page — DataTable + preset recipes + Excel export |

## API

### `GET /api/v1/stocks/deep-dive/{ticker}`

Returns `TickerDeepDiveResponse` with:

| Section | Fields |
|---------|--------|
| **Header** | `ticker`, `name`, `sector`, `last_close`, `market_cap`, `institutional_pct`, `high_52w`, `low_52w`, `pct_off_52w_high`, `as_of_date` |
| **RSI** | `daily`, `weekly`, `monthly`, `quarterly` + history arrays (last 12 periods each) |
| **EMA Stack** | 8/21/50 EMAs + 200 SMA, % distance, slopes, days-above counts, stack score (0-3) |
| **MACD** | `macd_line`, `signal_line`, `histogram` |
| **Bollinger** | `upper`, `middle`, `lower`, `width_pct`, `pct_b` |
| **Returns** | 1W, 2W, 1M, 3M, 6M, 1Y period returns |
| **Price History** | Weekly closes for last 2 years (chart data) |
| **Volume** | Last 60 daily bars in millions |
| **Fundamentals** | Last 6 quarters: revenue, margins, EPS, cash, debt |
| **Estimates** | Price targets, analyst count, forward EPS/revenue consensus |
| **Catalysts** | Last 15 demand/policy events with signed impact |

### Query Parameters

| Param | Default | Purpose |
|---|---|---|
| `force` | `false` | Bypass the in-memory cache and `DeepDiveStore`, recompute on-demand, and write back. 409s in cloud mode when inline compute is blocked. |

### Error Responses

| Status | Condition |
|--------|-----------|
| 404 | No OHLCV data for the ticker (fresh compute), or no precomputed payload exists yet in cloud mode with inline compute blocked |
| 409 | `force=true` in cloud mode without `TYCHE_ALLOW_INLINE_SCAN=true` |

## Engine Design

`TickerDeepDiveEngine` follows the `CCAnalysisEngine` pattern:

- **Stateless** — instantiated per-request with store references
- **No caching** — v1 doesn't need it (single-ticker latency ~200ms local, ~2s GCS)
- **Graceful degradation** — each data source is optional; engine returns partial results when stores are absent or empty
- **Shared primitives** — reuses `compute_ema()`, `compute_rsi()`, `compute_slope()` from `conviction/features.py`

### RSI Computation

Uses Wilder-style EMA smoothing (`alpha = 1/period`) matching the existing `compute_rsi()` in the conviction engine. Weekly/monthly/quarterly RSI is computed by first resampling OHLCV closes to the target frequency (Friday week-end, month-end, quarter-end) and then applying the same RSI formula to the resampled series.

### Multi-Timeframe RSI Reading Guide

| Daily RSI | Quarterly RSI | Interpretation |
|-----------|---------------|----------------|
| 35-45 | 60+ | **Strong entry** — structural breakout pulling back |
| 70+ | 60+ | Extended — wait for daily to cool to 40-50 |
| 35-45 | 40-55 | Neutral — no structural trend yet |
| 70+ | 40-55 | Short-term spike, weak structure — avoid |
| < 30 | < 40 | Deeply oversold, weak structure — could be value trap or recovery |

## Tests

36 unit tests in `test_ticker_deep_dive.py`:

| Class | Tests | Coverage |
|-------|-------|----------|
| `TestBasicAnalysis` | 4 | Result structure, metadata, 52W stats, as_of_date |
| `TestMultiTimeframeRSI` | 7 | All 4 timeframes in range, history entries populated |
| `TestEMAStack` | 6 | EMA values, SMA200, pct/slopes, stack score, days-above |
| `TestMACD` | 1 | MACD line, signal, histogram computed |
| `TestBollingerBands` | 1 | Bands, width, %B computed |
| `TestReturns` | 1 | Period returns populated |
| `TestPriceHistory` | 1 | Weekly price history entries |
| `TestVolumeHistory` | 1 | 60 daily volume bars |
| `TestFundamentals` | 2 | Populated + graceful degradation |
| `TestEstimates` | 3 | PT/analysts populated, forward EPS, graceful degradation |
| `TestCatalysts` | 2 | Events populated + graceful degradation |
| `TestInsufficientData` | 3 | Short data, None, empty DataFrame |
| `TestGracefulDegradation` | 4 | OHLCV-only, empty stores |

## Config

| Setting | Default | Purpose |
|---|---|---|
| `deep_dive_batch_enabled` | `true` | Feature flag for the nightly batch precompute (local scheduler + informs cloud wiring). When `false`, the route reverts to pure on-demand (v1 behavior) — the store is simply never populated by the batch. |
| `deep_dive_batch_min_market_cap_millions` | `1000` | Market-cap floor for the batch universe (tickers with no cap data still pass through, matching existing filter semantics). |
| `deep_dive_max_staleness_sessions` | `2` | A precomputed payload is served as "fresh" when its `as_of_date` is within N trading sessions of the latest OHLCV session; otherwise the route recomputes inline (or serves stale in cloud mode when inline compute is blocked). |
| `screener_index_batch_enabled` | `true` | Feature flag for the v3 screener index batch (chained after the deep-dive batch, local + cloud). |
| `screener_index_min_market_cap_millions` | `1000` | Market-cap floor for the screener index universe. |

v3 will add:
- `screener_index_batch_enabled` — feature flag for the screener index batch
- `screener_index_min_market_cap_millions` — market cap floor for the index universe

## v3 Screener — Diamond Finder Calibration

The screener's job is to narrow thousands of tickers to a shortlist of high-conviction "buy strength on a dip" setups, per the core strategy thesis (`.cursor/rules/strategy-philosophy.mdc`): **buy strong stocks in confirmed structural uptrends when they pull back to support — don't chase.** Each index row carries a deterministic `setup_score` (0–100) and a `setup_label`.

### The core diamond pattern

*High quarterly RSI (≥ 60) + low-ish daily RSI (35–50) + price on/near the 8- or 21-EMA + above the 200-SMA.* The high timeframe confirms a real uptrend; the daily RSI says it is on sale right now. This intersection of **strong structure** and **cooled timing** is the backtest-validated entry.

### `setup_score` (0–100) — deterministic

Compute four components, sum, then apply an anti-chase haircut. Use `clamp(x, 0, 1)`.

**A. Structural trend (0–40)** — is this a real high-timeframe uptrend?
- Quarterly RSI: `clamp((rsi_quarterly - 45) / (65 - 45), 0, 1) * 20` (0 at ≤ 45, full 20 at ≥ 65)
- Monthly RSI: `clamp((rsi_monthly - 45) / (60 - 45), 0, 1) * 8`
- Above 200-SMA: `7` if `last_close > sma_200` (and `sma_200 > 0`) else `0`
- 21-EMA slope rising: `clamp(slope_ema_21 / 0.5, 0, 1) * 5`

**B. Entry timing (0–30)** — pulling back to a buy zone (not extended, not a falling knife)?
- Daily-RSI sweet spot (0–18): `18` if `35 ≤ rsi_daily ≤ 50`; ramp `0→18` over `25→35`; decay `18→0` over `50→65`; else `0`.
- Proximity to support (0–12): from `pct_vs_ema_8` — `12` if `-3% ≤ pct_vs_ema_8 ≤ +5%`; `10` if price is below the 8-EMA but above the 21-EMA (deeper healthy pullback); decay to `0` as `pct_vs_ema_8` rises `+5%→+12%`; `0` when `> 12%`.

**C. Quality (0–20)** — institutional-grade?
- Market cap: `≥ $10B → 8`, `$4–10B → 6`, `$1–4B → 4`, `< $1B → 2`, no data → `1`
- Institutional ownership: `clamp(institutional_pct / 60, 0, 1) * 7`
- Above 50-EMA: `5` if `last_close > ema_50` else `0`

**D. Momentum confirmation (0–10)**
- Weekly RSI ≥ 50: `+5`
- 3M return band: `+5` if `0 < ret_3m ≤ 40`; `+2` if `ret_3m > 40`; else `0`

**Anti-chase haircut:** if `rsi_daily ≥ 70 AND pct_vs_ema_8 > 10` → multiply the total by `0.6`. Clamp final to `[0, 100]`, round 1 dp.

### `setup_label` (evaluate in order; first match wins)

| Label | Condition | Meaning |
|---|---|---|
| **Prime Pullback** (diamond) | `setup_score ≥ 70 AND rsi_quarterly ≥ 58 AND 35 ≤ rsi_daily ≤ 52 AND last_close > sma_200` | Strong structure + cooled timing — the buy |
| **Structural Uptrend** | `setup_score ≥ 60 AND rsi_quarterly ≥ 55 AND last_close > sma_200` | Confirmed trend, timing not perfect |
| **Emerging Breakout** | `50 ≤ rsi_quarterly < 60 AND 40 ≤ rsi_daily ≤ 60 AND last_close > sma_200` | Fresh quarterly-RSI 50→60 regime change |
| **Overextended** | `rsi_daily ≥ 70 AND rsi_quarterly < 55` | Spike on weak structure — avoid |
| **Weak Structure** | `rsi_quarterly < 40 AND rsi_monthly < 45` | Value-trap risk — avoid |
| **Watch / Base Building** | everything else | On the watchlist |

### Preset recipes (frontend buttons → filter state)

1. **Diamond — Prime Pullback** (default, highest conviction): `setup_label=Prime Pullback`, `q_rsi_min=58`, `d_rsi ∈ [35,52]`, `above_sma200=true`, `stack_score_min=2`, `ext_max_pct=6`, `min_market_cap=4000`, sort `setup_score desc`.
2. **Structural Breakout Pulling Back**: `q_rsi_min=55`, `d_rsi ∈ [35,55]`, `above_sma200=true`, `ext_max_pct=8`, `min_market_cap=1000`.
3. **Emerging Breakout (early)**: `q_rsi ∈ [50,60]`, `d_rsi ∈ [40,60]`, `above_sma200=true` — catches the quarterly regime cross before it is obvious.
4. **Deep Reversal (higher risk)**: `q_rsi ∈ [40,55]`, `d_rsi_max=35`, `above_sma200` off — reclaim plays; pair with catalysts; label as speculative.
5. **What to AVOID** (teaching tool): `setup_label ∈ {Overextended, Weak Structure}`.

### How to calibrate the funnel

- **Widen** (too few results): drop `q_rsi_min` 60→55, allow `min_market_cap` to $1B, raise `ext_max_pct` to 8–10, or include "Emerging Breakout".
- **Tighten to only diamonds** (too noisy): raise `q_rsi_min` to 62–65, require `stack_score_min=3`, `institutional_pct ≥ 50`, `ext_max_pct ≤ 5`, `setup_score_min ≥ 75`.
- **Timing rule (critical):** a name with quarterly RSI 65 but daily RSI 75 is NOT a buy today — it is a watchlist item. Re-run the screen daily and act when daily RSI cools into 35–50 while quarterly stays ≥ 60. "Prime Pullback" is exactly that intersection.
- **Traps:** daily RSI ≥ 70 with quarterly < 55 = short-term spike on weak structure (mean-reversion risk). Quarterly < 40 and falling = value trap — wait for the quarterly RSI to base and cross 50 before trusting a reversal.
- **Always confirm in the Deep Dive:** the screener finds candidates; open `/stocks/deep-dive?ticker=X` to confirm fundamentals (revenue/cash trend), estimates (price-target upside), and catalysts before committing.

---

## Prompt 3b — Backtest calibration of screener weights (do AFTER v3 ships)

The `setup_score` weights and label thresholds above are principled but hand-set. Prompt 3b validates and tunes them against historical forward returns. Run this in its own Sonnet session once v3 is live and the index has data.

> **Task: backtest and calibrate the screener's `setup_score` weights and `setup_label` thresholds against historical forward returns (`tyche-options`, run from `backend/`).**
>
> **Goal:** measure whether higher `setup_score` / the "Prime Pullback" label actually predict better forward returns, then recommend (and make configurable) improved weights/thresholds. Leakage-safe, walk-forward, no per-ticker overfitting.
>
> **Read first:** `backend/src/tyche/analysis/ticker_deep_dive.py`, `backend/src/tyche/workflow/screener_index_batch.py` (the `setup_score`/`setup_label` logic), `backend/src/tyche/ml/features.py` (vectorized EMA/RSI/slope extraction over full history — reuse this, do NOT call the per-ticker engine per date = O(N²)), `backend/scripts/backtest_deep_dip.py` and `backtest_pullback_csp.py` (existing backtest idioms + walk-forward), `.cursor/rules/strategy-philosophy.mdc`.
>
> **Step 1 — make weights configurable (no more hardcoding).** Extract the `setup_score` component weights, the RSI sweet-spot bounds, and the label thresholds into a single `ScreenerWeights` dataclass / dict (e.g. `backend/src/tyche/strategy/screener_weights.py`) with the current values as defaults. Both `screener_index_batch.py` and the backtest import from here so tuning changes one place.
>
> **Step 2 — build `backend/scripts/backtest_screener.py`.** For a set of historical as-of dates (monthly or weekly steps, e.g. 2018→present), for each equity ticker (≥ $4B cap, `filter_equity_only`): reconstruct the scalar screener signals AS OF that date using ONLY data up to that date (vectorized from `ml/features.py` — daily/weekly/monthly/quarterly RSI, EMA stack, slopes, returns), compute `setup_score` + `setup_label`, then join **forward** returns at +20 / +40 / +60 trading days. Strictly leakage-safe: signals use `[..as_of]`, outcomes use `(as_of..as_of+h]`.
>
> **Step 3 — report.** Aggregate: win rate (forward return > 0), mean/median forward return, and max adverse excursion — broken down by (a) `setup_label`, (b) `setup_score` decile, (c) quarterly-RSI bucket × daily-RSI bucket, (d) market-cap tier. Include base rates (all tickers, no filter) so lift is visible. Save results to `data/ml/screener_backtest/` (Parquet + a summary JSON) and print a table. Walk-forward across time (report per-period stability, not just pooled) — recall the deep-dip finding that pooled edges can be regime-driven.
>
> **Step 4 — recommend.** Output a short section: does `setup_score` monotonically rank forward returns? Does "Prime Pullback" beat "Structural Uptrend" beats base rate? Suggest concrete weight/threshold adjustments (e.g. "quarterly-RSI ≥ 62 lifts +40d win rate from X% to Y%; recommend raising the label floor"). Do NOT auto-apply — surface the recommendation and let a human update `ScreenerWeights`. Warn against per-ticker overfitting; prefer robust, cross-sectional thresholds.
>
> **Constraints:** no network/API calls (local OHLCV store only); vectorized (no per-date engine calls); add `tests/unit/test_backtest_screener.py` covering leakage-safety (a signal date never uses future bars) and the aggregation math on a small synthetic panel.
>
> **Acceptance:** script runs end-to-end on the local universe; produces the breakdown tables + saved artifacts; weights are config-driven; leakage test passes; `pytest tests/unit/` green.

---

## Changelog

- **2026-07-12:** v3 (Screener Index / "Diamond Finder") implemented — single-file `screener_index.parquet` universe index, nightly batch chained after v2's deep-dive batch (DeepDiveStore-preferred + inline `TickerDeepDiveEngine` fallback), `GET /stocks/screener` with server-side filters, published-JSON GCS read path (`stocks_screener` route, mirrors `stocks_deep_dips`), Cloud Run job (`stocks-screener-index-batch`, fire-and-forget in `morning-pipeline.yaml`), and the frontend Screener page with preset recipes + Excel export. `setup_score`/`setup_label` implemented verbatim per the Diamond Finder calibration below, unit-tested against crafted fixtures. Standalone from conviction SQLite/cache. Full write-up: `docs/sonnet_deep_dive_prompt3_implementation_report.md`. Prompt 3b (backtest calibration of the weights) intentionally deferred.
- **2026-07-12:** v2 implemented — per-ticker precompute (`DeepDiveStore`, one Parquet per ticker at `signals/stocks/deep_dive/{TICKER}.parquet`, no monolithic file), nightly batch (`run_deep_dive_batch`), Cloud Run job (`stocks-deep-dive-batch`), read-through cache with cloud-mode stale-serve + `force` param on the route, shared `to_response()` serializer (route + batch parity), local APScheduler + fire-and-forget cloud morning-pipeline wiring. No publish-JSON step — route reads the store directly.
- **2026-07-11:** Documented v3 screener design ("diamond finder") — `setup_score`/`setup_label` calibration, preset recipes, funnel-tuning guidance, and Prompt 3b (backtest calibration of screener weights). Clarified that multi-timeframe RSI already lives in v1; v3 reuses it at universe scale via a single compact index Parquet. Added v2/v3 config knobs.
- **2026-07-01:** v1 implemented — on-demand deep dive with multi-timeframe RSI, EMA stack, MACD, Bollinger Bands, fundamentals, estimates, catalysts. 36 unit tests. Frontend page with sidebar nav entry.
