# v3 Stock Screener ("Diamond Finder") Implementation Report

## Summary

Added a universe-wide Stock Screener on top of the v2 Stock Deep Dive precompute:
a nightly batch that extracts compact scalar signals (multi-timeframe RSI, EMA
stack, returns, market cap/institutional/sector, and the "Diamond Finder"
`setup_score`/`setup_label` composite) for every equity-universe ticker into a
**single** queryable Parquet index (`signals/stocks/screener_index.parquet`),
a filter/sort API (`GET /stocks/screener`), and a frontend page with RSI/score
range filters, Setup/Sector multiselects, an Above-200-SMA boolean, and five
preset "recipe" buttons that encode the strategy's core thesis — **buy strong
stocks in confirmed structural uptrends when they pull back to support, don't
chase**. The batch prefers the already-computed v2 `DeepDiveStore` payload per
ticker and falls back to the inline `TickerDeepDiveEngine` when absent, so it
works correctly today even though the deep-dive batch has not yet been run
against the live GCS bucket. The screener is fully standalone: it never reads
or writes the conviction SQLite snapshots or its 5-layer cache, and the index
is one compact scalar-only file — never per-ticker screener files, never the
large deep-dive JSON payloads.

## Files Created

| File | Purpose |
|---|---|
| `backend/src/tyche/market_data/screener_index_store.py` | `ScreenerIndexStore` — single-file Parquet store (`signals/stocks/screener_index.parquet`) mirroring the `alpha_signals.parquet`/`conviction_signals.parquet` idiom. `write(rows, ctx=None) -> int` overwrites the whole index; `read(ctx=None) -> pd.DataFrame \| None`. `load_screener_rows(ctx, rel_path)` helper returns `(records, as_of_date, computed_at)` with `sanitize_json_records()` applied for Pydantic/JSON compatibility (NaN → `None`), reused by both the route's GCS read path and the publish step. |
| `backend/src/tyche/workflow/screener_index_batch.py` | `run_screener_index_batch(...)` — the nightly precompute. Diamond Finder scoring implemented verbatim: `clamp()`, the four `setup_score` components (`_structural_trend_score` 0-40, `_entry_timing_score` 0-30 via `_daily_rsi_component` + `_proximity_component`, `_quality_score` 0-20 via `_market_cap_component`, `_momentum_score` 0-10 via `_ret_3m_component`), `compute_setup_score()` (sum + anti-chase 0.6× haircut + clamp/round), `compute_setup_label()` (the ordered 6-row label table, first match wins), and `build_screener_row()` (maps a `TickerDeepDiveResponse`/`TickerDeepDive` → scalar row dict, mapping the upper-case `returns` keys `"1M"/"3M"/"6M"/"1Y"` to `ret_1m`/`ret_3m`/`ret_6m`/`ret_1y`). `_process_one()` prefers `deep_dive_store.read_ticker(ticker)`, falls back to `TickerDeepDiveEngine.analyze(ticker)`. `Semaphore(8)`-bounded `asyncio.gather` + `asyncio.to_thread` per ticker, per-ticker error isolation, `job_phase`/`job_progress` every 250 tickers, single `ScreenerIndexStore.write(...)` at the end. `ScreenerIndexResult.to_dict()` reports `tickers_indexed`/`tickers_skipped`/`tickers_written`. |
| `backend/src/tyche/schemas/screener.py` | `ScreenerRow` (all 32 index columns + `setup_score`/`setup_label`, sane defaults so partial/legacy rows still validate) and `ScreenerResponse` (`scanned_at`, `as_of_date`, `computed_at`, `total`, `stale`, `rows`). |
| `backend/src/tyche/api/routes/screener.py` | `GET /stocks/screener` — pure read over `get_stocks_screener_scan()`, filters/sorts in pandas across all 14 query params from the spec (`q_rsi_min/max`, `m_rsi_min/max`, `w_rsi_min/max`, `d_rsi_min/max`, `above_sma200`, `stack_score_min`, `ext_max_pct`, `min_market_cap_millions`, `sector`, `setup_label` comma-separated, `setup_score_min`, `sort`, `desc`, `limit`). Returns **200 + empty `rows` with `stale=true`** when the index is missing or empty — never a 500. `sanitize_json_records()` guards against NaN before `ScreenerRow.model_validate()`. |
| `backend/tests/unit/test_screener_index_store.py` | 14 tests: write/read round-trip, single-file layout (overwrite doesn't accumulate files), all spec columns present, empty-write no-op, `load_screener_rows()` sanitizes NaN and returns `as_of_date`/`computed_at`, missing-file → `([], None, None)`. |
| `backend/tests/unit/test_screener_index_batch.py` | 26 tests across `clamp`, each `setup_score` component in isolation, `compute_setup_score` (including the anti-chase haircut), `compute_setup_label` (all six rows of the ordered table, including a crafted **Prime Pullback fixture scoring ≥ 70** and a crafted **overbought/weak fixture haircut + labeled "Overextended"**), `build_screener_row` (returns-key mapping, `None` on zero close), `DeepDiveStore`-present vs fallback-to-engine branches, cap-floor filtering, per-ticker error isolation, and single-file persistence via `run_screener_index_batch`. |
| `backend/tests/unit/test_screener_routes.py` | 12 tests: missing/empty index → 200 + `stale=true`, each RSI range filter, `above_sma200`, `stack_score_min`, `ext_max_pct`, `min_market_cap_millions`, `sector`, `setup_label` (single + comma-separated), `setup_score_min`, custom `sort`/`desc`, `limit`. Mocks `get_stocks_screener_scan` directly (deep-dive route test pattern) so it doesn't inherit the pre-existing GCS-`.env` test-isolation flakiness. |
| `docs/sonnet_deep_dive_prompt3_implementation_report.md` | This report. |

## Files Modified

| File | Change |
|---|---|
| `backend/src/tyche/config.py` | Added `screener_index_batch_enabled: bool = True` and `screener_index_min_market_cap_millions: float = 1000.0`. |
| `backend/src/tyche/api/deps.py` | Added `_screener_index_store` singleton + `get_screener_index_store()` provider; wired `_screener_index_store = None` into `reset_all()`. |
| `backend/src/tyche/app.py` | `_scheduled_deep_dive_batch()` now chains `await _scheduled_screener_index_batch()` immediately after a successful run, gated on `settings.screener_index_batch_enabled` — the screener batch depends on the deep-dive batch's output so it runs as a continuation rather than a separately-cron-scheduled job (no new `workflow/scheduler.py` method was needed for this reason). Added the new `_scheduled_screener_index_batch()` handler (resolves `OHLCVStore`/`TickerMetaStore`/`DeepDiveStore`/`FundamentalsStore`/`EstimatesStore`/`CatalystSignalStore`, calls `run_screener_index_batch`, logs a summary; standalone — no conviction-cache invalidation). Registered `screener` in the router import list and `app.include_router(screener.router, prefix="/api/v1")` next to `deep_dive.router`. |
| `backend/src/tyche/ops/gcp_jobs.py` | Added `"stocks-screener-index-batch"` to `JOB_NAMES` (17 → 18) immediately after `"stocks-deep-dive-batch"`. Added `run_screener_index_batch_job(...)` mirroring `run_deep_dive_batch_job` — resolves stores directly (not via `deps.py`, matching the GCP job pattern), calls `run_screener_index_batch`, writes a `RunManifest` (`output_paths=[SCREENER_INDEX_REL]`, `status="failed"` if `tickers_written == 0`). Registered in `_JOB_RUNNERS`. |
| `backend/src/tyche/persistence/published_route_registry.py` | Added `"stocks_screener": "stocks_screener.json"` to `ROUTE_FILES` and `"stocks_screener": "/stocks/screener"` to `ROUTE_PATHS`. |
| `backend/src/tyche/persistence/published_routes.py` | Added `get_stocks_screener_scan(settings, ctx)`, mirroring `get_stocks_deep_dips_scan` exactly: prefers `load_published_route("stocks_screener", ...)` when `_prefer_published(settings)` (GCS mode), parses into `ScreenerResponse`; otherwise falls back to `first_existing_path((SCREENER_INDEX_REL,), ctx=ctx)` + `load_screener_rows()`, sorts by `setup_score` desc, and builds a fresh `ScreenerResponse`. Returns `None` when neither source has data (route turns this into 200 + empty + `stale=true`). |
| `backend/src/tyche/workflow/publish_signals.py` | Added `_STOCKS_SCREENER_CANDIDATES = ("signals/stocks/screener_index.parquet",)` and `publish_stocks_screener(config, run_id, settings)` (mirrors `publish_stocks_deep_dips`): reads the index via `load_screener_rows`, builds a `ScreenerResponse`, writes `published/routes/stocks_screener.json` via `_write_route_artifact`, returns a `RoutePublishResult`. Wired into `run_publish_signals()` right after the `stocks_history` phase (`log_job_phase` start/complete, `routes.append(screener)`, `job_manifest.input_paths.extend(screener.source_paths)`). |
| `infra/gcp/workflows/morning-pipeline.yaml` | Added a fire-and-forget `start_screener_index_batch` step (bare `http.post :run`, try/except → `sys.log` WARNING on failure, never polled) immediately after `start_deep_dive_batch` and before `parallel_morning`. It is safe to fire without waiting on `deep-dive-batch` to finish because the screener batch's per-ticker fallback (inline `TickerDeepDiveEngine`) makes it correct even if the deep-dive payloads for a given ticker aren't ready yet — it just costs an extra inline compute for that ticker instead of a free store read. Does not block `run_publish`. |
| `infra/gcp/deploy_jobs.sh` | Added `deploy_job tyche-stocks-screener-index-batch stocks-screener-index-batch 4 8Gi "${TIMEOUT_8H}" 1 "TYCHE_INGEST_WINDOW=morning"` right after the deep-dive-batch deploy line (8h timeout, `--tasks=1`, matching every other batch job). |
| `backend/tests/unit/test_gcp_jobs.py` | Bumped `test_job_names_match_spec`'s `JOB_NAMES` count assertion from 17 → 18. |
| `backend/tests/unit/test_publish_signals.py` | Bumped the published-route-count assertions in `test_publishes_alpha_and_manifest` and `test_runs_inside_active_event_loop` from 15 → 16. |
| `frontend/src/types/index.ts` | Added `ScreenerRow`, `ScreenerResponse`, `ScreenerParams` interfaces at the end of the file, matching the backend schema field-for-field. |
| `frontend/src/api/client.ts` | Added `api.stocks.getScreener(params?: ScreenerParams)` — builds a `URLSearchParams` from any defined/non-empty param and calls `GET /stocks/screener`. |
| `frontend/src/hooks/useApi.ts` | Added `useScreener(params?: ScreenerParams)` — react-query with `staleTime: Infinity`, `refetchOnWindowFocus: false` (same caching posture as `useAlphaScan`; the index only changes once nightly). |
| `frontend/src/config/modules.ts` | Added the `Gem` lucide icon import and a `{ path: "/screener", label: "Screener", icon: Gem }` entry under the Stocks module, placed right after "Dashboard" and before "Directional Alpha". |
| `frontend/src/App.tsx` | Imported `Screener` and added `<Route path="/stocks/screener" element={<Screener />} />` next to the Alpha route. |

## Files Created (frontend)

| File | Purpose |
|---|---|
| `frontend/src/pages/stocks/Screener.tsx` | The Screener page, modeled on `Alpha.tsx`. `DataTable` columns: Ticker (links to `/stocks/deep-dive?ticker=X`), Setup (color-coded badge), Score, Q/M/W/D-RSI, Stack, % vs 8-EMA, Above 200-SMA, 3M/6M Return, Off 52w High, Mkt Cap, Inst Own, Sector — each with an inline `DataTable` filter (range/min/max on the RSI+score+ext columns, multiselect on Setup label and Sector, boolean on Above 200-SMA). Five preset recipe buttons (`RECIPES` array) plus a "Custom / All" clear button; clicking a recipe replaces the full query-param filter state sent to `useScreener` (server-side filtering) and updates the Min Mkt Cap selector to the recipe's recommended floor. A help banner under the recipe row surfaces each recipe's `hint` text (the calibration guidance from the spec), with a distinct amber styling + "Speculative" callout for **Deep Reversal**. `exportToExcel()` TSV export mirrors Alpha's pattern. Setup-label color map: Prime Pullback = emerald, Structural Uptrend = blue, Emerging Breakout = violet, Watch/Base Building = gray, Overextended/Weak Structure = red. |

## Design Decisions

### Server-side recipes + client-side DataTable filters (two filtering layers)

`DataTable`'s inline column filters (`ColumnFilterConfig`) are purely local
React state with no prop for a parent to seed/control initial values — they
can't be driven programmatically by a "recipe" button without modifying the
shared component (out of scope; it's used by six other pages). Instead, each
preset recipe sets the **API query params** (`ScreenerParams`) passed to
`useScreener`, so the backend does the real filtering/sorting against the
full index and returns only matching rows; `DataTable`'s own inline filters
then let the user narrow *within* that already-filtered result set (e.g.
narrow "Structural Breakout Pulling Back" results further by Sector). This
keeps the heavy lifting server-side (thousands of tickers → hundreds of rows)
and reuses `DataTable` unmodified, exactly like `Alpha.tsx`'s `minCapM`
state already does for market cap.

### `returns` dict key casing

`TickerDeepDive.returns` uses upper-case keys (`"1M"`, `"3M"`, `"6M"`, `"1Y"`,
plus `"1W"`/`"2W"` which the screener doesn't need). `build_screener_row()`
reads `returns.get("3M")` etc. explicitly — verified by
`test_screener_index_batch.py::TestBuildScreenerRow::test_maps_uppercase_return_keys`
(a lower-case-keyed returns dict must map to `None`, not silently succeed).

### `DeepDiveStore` preference + inline-engine fallback, tested both ways

`_process_one()` calls `deep_dive_store.read_ticker(ticker)` first; if that
returns `None` (empty store — true today, since the deep-dive batch has
never run against the live GCS bucket), it falls back to
`TickerDeepDiveEngine(...).analyze(ticker)`. Both code paths are exercised in
`test_screener_index_batch.py` (`TestDeepDiveStorePreference`,
`TestEngineFallback`) with `TickerDeepDiveEngine` imported at module level
(not inside the function) specifically so it's patchable via
`mocker.patch("tyche.workflow.screener_index_batch.TickerDeepDiveEngine")` in
tests — a local import inside `run_screener_index_batch` would have been
unpatchable at the class level.

### Standalone from conviction (hard constraint, honored)

The screener never imports `tyche.conviction.*`, never touches
`conviction.db` or `ConvictionFeatureEngine`'s in-memory/derived caches, and
`deps.reset_all()`'s screener-store reset (`_screener_index_store = None`) is
independent of `invalidate_conviction_cache()`. The index itself
(`screener_index.parquet`) is a brand-new single file with no relationship to
`conviction_signals.parquet`.

### Diamond Finder formulas implemented verbatim

`compute_setup_score()` and `compute_setup_label()` are line-for-line
translations of the spec's formulas — no attempt to "improve" thresholds
(that's explicitly Prompt 3b's job, left untouched). Crafted fixtures prove
both edges:

- **Prime Pullback fixture** (`rsi_quarterly=62`, `rsi_monthly=55`,
  `last_close > sma_200`, `slope_ema_21=0.4`, `rsi_daily=42`,
  `pct_vs_ema_8=1.0`, `market_cap=$50B`, `institutional_pct=65`,
  `last_close > ema_50`, `rsi_weekly=55`, `ret_3m=15`) scores **≥ 70** and
  labels **"Prime Pullback"**.
- **Overbought/weak fixture** (`rsi_daily=78`, `pct_vs_ema_8=15`,
  `rsi_quarterly=42`) triggers the anti-chase **0.6× haircut** and labels
  **"Overextended"**.

## Verification

- `cd backend && pytest tests/unit/test_screener_index_store.py tests/unit/test_screener_index_batch.py tests/unit/test_screener_routes.py` — **52 passed**.
- Targeted regression run on every backend file touched or read from this session: `pytest tests/unit/test_config.py tests/unit/test_scheduler_wiring.py tests/unit/test_ticker_deep_dive.py tests/unit/test_gcp_jobs_subprocess.py tests/unit/test_alpha.py tests/unit/test_gcp_jobs.py tests/unit/test_publish_signals.py tests/unit/test_deep_dive_store.py tests/unit/test_deep_dive_batch.py tests/unit/test_deep_dive_route.py tests/unit/test_published_routes.py` — all green, no new failures.
- `create_app()` import/wiring sanity check (`from tyche.app import create_app; create_app()`) succeeds with the new `screener` router mounted — 75 total routes registered, no import cycles.
- `cd frontend && npm run build` — **exit 0** (TypeScript project build + Vite bundle).
- `cd frontend && npm run lint` — **exit 0**, 6 pre-existing warnings in `Settings.tsx` (unrelated `react-hooks/exhaustive-deps`), zero errors, zero warnings in any new/modified file.
- `ReadLints` over every new/modified frontend file — zero errors.
- Per the acceptance bar set for this session ("all new screener tests green + no NEW failures introduced," not "zero failures in the whole suite" — the ~74 pre-existing GCS-`.env`-isolation failures documented in Prompt 2's report are unrelated and untouched), the full `pytest tests/unit/` run was **not** used as the pass/fail gate; the targeted runs above are the verification evidence.

## Acceptance Criteria Checklist

- [x] Nightly batch writes a single `signals/stocks/screener_index.parquet` (one row per ticker, scalar columns only) — `test_screener_index_batch.py::TestSingleFilePersistence`, `test_screener_index_store.py::TestSingleFileLayout`.
- [x] Batch reads the v2 `DeepDiveStore` where present (no recompute), falls back to the inline engine otherwise — both branches unit-tested.
- [x] `setup_score`/`setup_label` match the spec formulas exactly — Prime Pullback fixture scores ≥ 70 and labels correctly; overbought/weak fixture is haircut and labeled "Overextended".
- [x] `GET /stocks/screener` filters/sorts correctly across all params and returns 200 + empty (`stale=true`) on a missing/empty index.
- [x] Frontend Screener page: `DataTable` with RSI/score range filters, Setup + Sector multiselect, Above-200-SMA boolean, five preset recipe buttons (+ Custom/All), rows linking to `/stocks/deep-dive?ticker=X`; nav entry ("Screener", `Gem` icon) + `/stocks/screener` route.
- [x] Screener works in GCS cloud mode via published JSON — `get_stocks_screener_scan()` prefers `published/routes/stocks_screener.json` when `_prefer_published(settings)`, falls back to the raw signal Parquet; `publish_stocks_screener()` wired into `run_publish_signals()`.
- [x] `screener_index_batch_enabled=false` disables the batch cleanly — the chain in `_scheduled_deep_dive_batch()` is gated behind `if settings.screener_index_batch_enabled:`; the batch is never invoked and the index stays whatever it was (route falls back to empty/stale if never populated).
- [x] Conviction SQLite/5-layer cache untouched — no imports of `tyche.conviction.*` anywhere in the new files; `deps.reset_all()` resets the screener store independently.
- [x] Prompt 3b (backtest calibration) left alone — no changes to `docs/stock-deep-dive.md`'s Prompt 3b section, no weight-tuning code added.
- [x] `cd backend && pytest tests/unit/test_screener_*.py` and `cd frontend && npm run build && npm run lint` both pass (see Verification).
