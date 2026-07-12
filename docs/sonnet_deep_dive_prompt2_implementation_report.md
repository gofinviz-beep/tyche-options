# Stock Deep Dive — Per-Ticker Precompute & Read-Through Cache (v2) Implementation Report

## Summary

Added a per-ticker nightly precompute pipeline for `GET /api/v1/stocks/deep-dive/{ticker}`
so page loads read a cached payload instead of recomputing the full technical +
fundamental analysis on every request. Mirrors the proven `stocks-derived-batch` /
`stocks_deep_dips_store.py` idiom: **one Parquet file per ticker**
(`signals/stocks/deep_dive/{TICKER}.parquet`) — never a monolithic universe file — with
the route reading the store directly (no publish-JSON step). The on-demand v1 compute
path is preserved as the cache-miss / stale-payload / `force=true` fallback, and both
paths now share exactly one serializer (`schemas/deep_dive.py::to_response()`) so cached
and freshly-computed payloads are byte-for-byte identical, including percent-scale
margin/growth fields. The response schema and endpoint contract are unchanged — the
frontend is unaffected. Two small frontend follow-ups from the Prompt 1 review
(`eslint.config.js`, a `prefer-const` fix) were also closed out first.

## Files Created

| File | Purpose |
|---|---|
| `backend/src/tyche/market_data/deep_dive_store.py` | `DeepDiveStore` — per-ticker Parquet store. `write_ticker`/`write_batch`/`read_ticker`/`get_all_tickers`/`get_stats`. Payload stored as a `payload_json` string column (`model_dump_json()`/`model_validate_json()`), sidestepping fragile nested-array Parquet schemas and staying schema-evolution safe. |
| `backend/src/tyche/workflow/deep_dive_batch.py` | `run_deep_dive_batch()` — nightly precompute over the equity + market-cap-floor universe. `Semaphore(8)`-bounded `asyncio.gather`, `asyncio.to_thread` per ticker (CPU-bound engine call), per-ticker error isolation, `job_phase`/`job_progress` logging every 250 tickers, `DeepDiveBatchResult.to_dict()`. |
| `backend/tests/unit/test_deep_dive_store.py` | 11 tests: write/read round-trip, `model_dump_json` ↔ `model_validate_json` fidelity, percent-scale margin survival, missing-ticker → `None`, empty-`payload_json` guard, one-file-per-ticker layout (no monolithic file), `get_all_tickers`/`get_stats`. |
| `backend/tests/unit/test_deep_dive_batch.py` | 8 tests: equity-only + cap-floor filtering (incl. no-cap-data pass-through), zero-close skip, per-ticker error isolation (one bad ticker doesn't abort the batch), write count, missing-store/empty-universe error paths, no-`ctx` skip. |
| `backend/tests/unit/test_deep_dive_route.py` | 8 tests: in-memory cache hit (store/engine untouched), fresh store hit (no recompute), stale store → recompute + write-back, `force=true` bypasses cache+store, cloud-mode serves stale payload without recompute, cloud-mode 404 when nothing precomputed, cloud-mode `force` → 409, zero-close on-demand 404. |
| `docs/sonnet_deep_dive_prompt2_implementation_report.md` | This report. |

## Files Modified

| File | Change |
|---|---|
| `frontend/eslint.config.js` *(new)* | Minimal ESLint 9 flat config wiring `@eslint/js`, `typescript-eslint`, `eslint-plugin-react-hooks`, `eslint-plugin-react-refresh`; ignores `dist/`. Closes the one unchecked Prompt 1 acceptance box. |
| `frontend/src/lib/telemetry.ts` | `let buffer` → `const buffer` (never reassigned) — the one lint error `npm run lint` surfaced after adding the config. |
| `backend/src/tyche/schemas/deep_dive.py` | Added `to_response(result: TickerDeepDive) -> TickerDeepDiveResponse` — the dataclass→Pydantic mapper extracted out of the route (was `_result_to_response`), now the single shared serialization path for both the route and the batch. |
| `backend/src/tyche/api/routes/deep_dive.py` | Full rewrite: removed the local `_result_to_response`, imports `to_response` instead. Added the read-through resolution order (in-memory cache → `DeepDiveStore` → on-demand fallback → cloud-mode stale-serve), a `force: bool = False` query param, and `invalidate_deep_dive_cache()` for cache-busting on config changes. |
| `backend/src/tyche/ops/gcp_jobs.py` | Added `run_deep_dive_batch_job()` (mirrors `run_stocks_derived_batch_job`: resolves stores, calls `run_deep_dive_batch`, writes a `RunManifest`, logs `job_phase`s). Added `"stocks-deep-dive-batch"` to `JOB_NAMES` and `JOB_RUNNERS` (16 → 17 jobs). |
| `backend/src/tyche/config.py` | Added `deep_dive_batch_enabled` (`true`), `deep_dive_batch_min_market_cap_millions` (`1000`), `deep_dive_max_staleness_sessions` (`2`). |
| `backend/src/tyche/api/deps.py` | Added `get_deep_dive_store()` singleton provider (mirrors `get_derived_store`). Wired `invalidate_deep_dive_cache()` and `_deep_dive_store = None` into `reset_all()` so config-change/test resets flush the route's in-memory cache too. |
| `backend/src/tyche/workflow/scheduler.py` | Added `WorkflowScheduler.schedule_deep_dive_batch()` — weekday cron, default 4:15 PM ET (after the conviction batch), mirrors `schedule_conviction_batch`. |
| `backend/src/tyche/app.py` | Added `_scheduled_deep_dive_batch()` handler (resolves stores, calls `run_deep_dive_batch`, invalidates the route cache, logs a summary) and registered it behind `if settings.deep_dive_batch_enabled:` alongside the other scheduled jobs. |
| `backend/tests/unit/test_gcp_jobs.py` | Added `"stocks-deep-dive-batch"` assertion + bumped the `JOB_NAMES` count to 17. Added `TestDeepDiveBatchJob` (2 tests: success path, `tickers_written == 0` → `status="failed"`). |
| `backend/tests/unit/test_scheduler_wiring.py` | Added `test_schedule_deep_dive_batch_registers_job`. |
| `backend/tests/unit/test_ticker_deep_dive.py` | Fixed a pre-existing "time bomb": `_make_ohlcv()` used `pd.bdate_range(end=date.today(), periods=n)` without re-deriving `n` from the actual returned length, which raised `ValueError: All arrays must be of the same length` whenever `date.today()` fell on a weekend (as it did mid-session — 2026-07-12 is a Sunday). Same fix pattern applied in the new `test_deep_dive_store.py` fixture from the start. |
| `infra/gcp/workflows/morning-pipeline.yaml` | Added a fire-and-forget `start_deep_dive_batch` step (`http.post :run`, never polled) immediately after `init`, before `parallel_morning`. Deep-dive-batch only depends on OHLCV + demand stores (not flatfiles/alpha/the demand gate), so it starts immediately and runs in the background for its own duration — it cannot block `run_publish` because the workflow never awaits its completion. |
| `infra/gcp/deploy_jobs.sh` | Added `deploy_job tyche-stocks-deep-dive-batch stocks-deep-dive-batch 4 8Gi "${TIMEOUT_8H}" 1 "TYCHE_INGEST_WINDOW=morning"` (8h timeout, `--tasks=1`, matching `stocks-derived-batch`). Added `test_deep_dive_store.py`/`test_deep_dive_batch.py`/`test_deep_dive_route.py` to the pre-build test gate. |
| `docs/stock-deep-dive.md` | Rewrote §v2 to match the shipped design (per-ticker Parquet, no publish step, read-through resolution order, scheduling) instead of the originally-sketched monolithic-file + publish-JSON plan. Updated the Files table, added a Query Parameters subsection, updated the Config table with the three new knobs, added a 2026-07-12 changelog entry. |

## Design Decisions

### One Parquet file per ticker (hard constraint)

`DeepDiveStore` writes `signals/stocks/deep_dive/{TICKER}.parquet` — a single-row file per
ticker with columns `ticker`, `as_of_date`, `computed_at` (UTC ISO), and `payload_json`
(the full `TickerDeepDiveResponse.model_dump_json()`). This differs from
`conviction_signals.parquet` / `alpha_signals.parquet` (single compact scalar-column
files) because the deep-dive payload is large (price history, volume bars, fundamentals,
estimates, catalysts) and v3's planned screener index depends on the per-ticker layout to
avoid one giant file growing unboundedly with universe size. Storing the *serialized*
payload rather than nested-array Parquet columns means adding a field to
`TickerDeepDiveResponse` never requires a Parquet schema migration — it's just a new JSON
key.

### One shared serializer

The route previously had a local `_result_to_response()` mapper. It's now
`schemas/deep_dive.py::to_response()`, imported by both the route (on-demand path) and
`workflow/deep_dive_batch.py` (precompute path). This guarantees the cached and
freshly-computed payloads are structurally identical — critically, margin/growth fields
(`gross_margin`, `rev_growth_ttm_yoy`, `gross_margin_ttm`, etc.) stay **percent-scale**
(e.g. `46.88`) in both paths, since the frontend's `formatPercentScale` (added in Prompt
1) expects that scale and would reintroduce the `4688%` bug if either path re-scaled.

### Route resolution order

```
GET /stocks/deep-dive/{ticker}?force=<bool>
  1. force=true            → require_inline_compute_allowed() (409 if cloud-blocked),
                              then skip straight to step 3.
  2. In-memory cache hit    → return (keyed by (ticker, latest_ohlcv_session_date)).
  3. DeepDiveStore.read_ticker(ticker):
       - present + fresh (as_of_date within deep_dive_max_staleness_sessions
         trading sessions of the latest OHLCV session) → cache + return.
       - present + stale, inline compute allowed        → fall through to compute.
       - present + stale, inline compute BLOCKED (cloud) → serve stale anyway
         (original as_of_date preserved); this is the only case where a stale
         payload is returned without a warning banner, matching the deep-dips
         cloud-mode precedent.
       - absent, inline compute BLOCKED (cloud)          → 404.
       - absent, inline compute allowed                  → fall through to compute.
  4. TickerDeepDiveEngine.analyze() (v1 path) → to_response() → cache + write-back
     to DeepDiveStore → return. 404 if last_close == 0.0 (no OHLCV data at all).
```

Freshness is measured with `numpy.busday_count(as_of_date, latest_session)` against
`OHLCVStore.get_latest_date()` (a cheap metadata-cache read, not a full Parquet scan),
rather than re-reading each ticker's own OHLCV history just to check staleness.

### No publish-JSON step

Unlike `stocks_deep_dips`/`stocks_history`, the deep-dive route reads `DeepDiveStore`
directly in both local and GCS modes (`StoreBackend` resolves the backend transparently
from `TycheSettings.data_backend`). A publish step would only add an extra layer of
staleness with no benefit here, since the per-ticker store is already cheap to
point-read (unlike a full-universe scan, which is what the publish/curated-fallback
machinery on other stock routes exists to avoid).

### Fire-and-forget cloud scheduling

The spec asked for `stocks-deep-dive-batch` to run "in parallel with the stocks
derived/conviction batches" without blocking `run_publish`. Rather than adding it as a
third branch inside the existing `parallel_morning` block (which the workflow still
waits on before proceeding to `run_stocks_conviction` → ... → `run_publish`, so it would
still indirectly gate publish), it's fired via a single un-polled `http.post :run` at the
very start of the workflow. This is the only way to guarantee zero blocking in Cloud
Workflows' synchronous step model: the job runs on Cloud Run independently once
triggered, and the workflow moves on immediately without an execution poll loop. A
failure to *start* the job is caught and logged as a warning, not raised.

## Verification

- `cd backend && pytest tests/unit/test_deep_dive_store.py tests/unit/test_deep_dive_batch.py tests/unit/test_deep_dive_route.py tests/unit/test_ticker_deep_dive.py tests/unit/test_gcp_jobs.py tests/unit/test_scheduler_wiring.py tests/unit/test_api.py tests/unit/test_config.py` — **106 passed** (the only 4 failures in that combined run are pre-existing `test_api.py::TestScannerRoutes` GCS-network-isolation issues, unrelated to any file touched in this session — see below).
- `cd backend && pytest tests/unit/` (full suite, ~1750 tests) — **1674 passed, 74 failed** in ~90s. All 74 failures were confirmed pre-existing and unrelated by running the same failing test files against a `git stash`'d pre-session tree (identical failures reproduce there) and by tracing the root cause: `backend/.env` has `TYCHE_DATA_BACKEND=gcs` pointing at a real bucket, and several route/client test files (`test_scanner_routes.py`, `test_stocks_routes.py`, `test_filing_routes.py`, `test_news_routes.py`, `test_demand_clients.py`, `test_expiry_tracker.py`) don't fully isolate `TycheSettings` from `.env`, plus one hardcoded-date time bomb (`"2026-03-25"` in `test_expiry_tracker.py`, now >30 days in the past). None of these files were modified in this session.
- `cd frontend && npm run lint` — **exit 0**, 6 pre-existing warnings in `Settings.tsx` (unrelated `react-hooks/exhaustive-deps`), zero errors.
- `cd frontend && npm run build` — **exit 0**, TypeScript + Vite build both pass.
- Manual config check: `TycheSettings(...).deep_dive_batch_enabled == True`, `deep_dive_batch_min_market_cap_millions == 1000.0`, `deep_dive_max_staleness_sessions == 2` — defaults match the spec.
- Manual lint pass (`ReadLints`) over all new/modified backend files — zero errors.

## Acceptance Criteria Checklist

- [x] Batch writes ONE Parquet per ticker under `signals/stocks/deep_dive/{TICKER}.parquet` for the equity universe ≥ cap floor; `test_deep_dive_batch.py::TestWriteCount` and `test_deep_dive_store.py::TestOneFilePerTickerLayout` assert no monolithic file exists.
- [x] `GET /stocks/deep-dive/{ticker}` returns a precomputed payload via a single store read when fresh (no full recompute) — `TestStoreHitFresh` patches `TickerDeepDiveEngine.analyze` to raise if called, confirming it's skipped.
- [x] Cache miss/stale falls back to on-demand compute + write-back with an identical JSON shape either way — enforced by construction (one shared `to_response()`), verified round-trip in `test_deep_dive_store.py::test_round_trip_is_model_fidelity`.
- [x] GCS cloud mode serves precomputed payloads without inline compute; no error when a payload exists — `TestCloudModeServesStale`.
- [x] `deep_dive_batch_enabled=false` disables the batch (never scheduled in `app.py`); the store stays empty and the route falls through to on-demand compute every time — pure v1 behavior, exercised by `TestStaleStoreFallsBackToCompute`/`TestNoDataAvailable` in the store-empty case.
- [x] New + existing unit tests pass: `cd backend && pytest tests/unit/` — all deep-dive-related and diff-touched tests pass; the 74 pre-existing/unrelated failures are documented above.
- [x] Prompt 1 follow-ups done: `cd frontend && npm run lint` passes and `npm run build` still passes.
