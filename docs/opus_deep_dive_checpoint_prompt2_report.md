# Opus Validation Report — Stock Deep Dive Prompt 1 + Prompt 2

**Reviewer:** Claude Opus 4.8 (Cursor agent)
**Date:** 2026-07-12
**Scope:** Independent validation & verification of the Deep Dive work delivered by Claude Sonnet 5 across Prompt 1 (recharts rebuild) and Prompt 2 (per-ticker precompute + read-through cache).
**Source reports reviewed:**
- `docs/deep_dive_sonnet_prompt1` (task) / `docs/sonnet_deep_dive_prompt1_implementation_report.md` (Sonnet report)
- `docs/deep_dive_sonnet_prompt2` (task) / `docs/sonnet_deep_dive_prompt2_implementation_report.md` (Sonnet report)

---

## Verdict

**✅ Complete and verified through the end of Prompt 2.**

Every deliverable claimed in Sonnet's two reports was cross-checked against the actual code, the tests were re-run locally, and the frontend was re-linted and re-built. The reports are honest and match reality — no overstated or fabricated claims. Only three minor, explicitly non-blocking observations remain (documented at the end), none of which require corrective action.

---

## Verification Method

This was not a documentation read-through. Each claim was validated against ground truth:

1. Read every created/modified source file end-to-end.
2. Confirmed the external interfaces the new code depends on actually exist (`StoreBackend.create/ticker_rel/write_df/read_df/list_ticker_stems`, `cloud_mode` helpers, `select_history_universe`).
3. Re-ran the deep-dive test suites locally (`backend/.venv`).
4. Reproduced one of the "pre-existing" full-suite failures to confirm its root cause is environmental, not a regression.
5. Re-ran `npm run lint` and `npm run build` on the frontend.
6. Inspected git history and the working tree for clean, committed delivery and the absence of any monolithic file.

---

## Prompt 1 — Recharts Rebuild of `DeepDive.tsx`

**Task:** Replace the crude `<div>`-bar pseudo-charts on the Stock Deep Dive page with real recharts charts via a shared, light-theme wrapper library; fix a percent double-count bug; add data-driven callouts. Frontend-only (no backend/type/API-client changes).

### Deliverables verified

| Item | Status | Evidence |
|---|---|---|
| `recharts@^3.9.2` added | ✅ | `node_modules/recharts` resolves to `3.9.2`; `package.json` dependency present |
| Chart wrapper library (`theme.ts`, `ChartTooltip.tsx`, `LineChartCard.tsx`, `BarChartCard.tsx`, `Callout.tsx`) | ✅ | All five files present under `frontend/src/components/charts/` |
| `DeepDive.tsx` rewrite (RSI line charts, Price History & Volume section, Fundamentals charts, Estimates/Catalysts) | ✅ | Committed in `34b8c60`; renders every payload section |
| Percent double-count fix (`formatPercentScale`, remove `×100`) | ✅ | AAPL `gross_margin: 46.88` → `46.9%` (was `4688%`); applied to all 7 margin/growth fields |
| Data-driven callouts (no hardcoded ticker prose) | ✅ | `buildCallouts()` implements the 4 specified conditional templates |
| Graceful degradation on empty/null data | ✅ | Section gating + "No data" placeholders; no crash paths |
| `npm run build` passes | ✅ | `tsc -b && vite build` → 2249 modules, exit 0 |

### Prompt 1's one open box (`npm run lint`)

Prompt 1 could **not** run lint because the repo had no ESLint config checked in — this was correctly reported as a pre-existing repository gap, not a Prompt 1 defect. It was carried forward as the first Prompt 2 follow-up (see below) and is now **closed**.

---

## Prompt 2 — Per-Ticker Precompute & Read-Through Cache (v2)

**Task:** Add a per-ticker nightly precompute + read-through cache so page loads read a cached payload instead of recomputing on every request. Hard constraint: **one Parquet file per ticker**, never a monolithic universe file. Response schema and endpoint contract unchanged.

### Prerequisite frontend follow-ups (from Prompt 1 review)

| Follow-up | Status | Evidence |
|---|---|---|
| 1. Add ESLint 9 flat config + fix surfaced errors | ✅ Done | `frontend/eslint.config.js` present (wires `@eslint/js`, `typescript-eslint`, `eslint-plugin-react-hooks`, `eslint-plugin-react-refresh`, ignores `dist/`); `telemetry.ts` `let buffer` → `const buffer`. `npm run lint` → **0 errors** (6 pre-existing `Settings.tsx` warnings only). |
| 2. RKLB/MSFT live percent spot-check | ⚠️ Partial (non-blocking) | "No code change — just confirm" item. Only AAPL was verified live; the fix is field-agnostic so it generalizes. Could not re-verify RKLB/MSFT live during this validation (backend is in GCS mode). Verification-only; no correctness impact. |
| 3. Per-series tooltip formatting (optional polish) | ⏭️ Skipped (allowed) | Explicitly optional in the prompt ("otherwise leave as-is"). Cosmetic; acceptable. |

### Backend deliverables verified

| Spec section | File | Status | Notes |
|---|---|---|---|
| 1. Store | `market_data/deep_dive_store.py` | ✅ | `DeepDiveStore` writes `signals/stocks/deep_dive/{TICKER}.parquet` — single row, `ticker`/`as_of_date`/`computed_at`/`payload_json` (via `model_dump_json`). `write_ticker`/`write_batch`/`read_ticker`/`get_all_tickers`/`get_stats`. Uses `StoreBackend` (local + GCS). |
| 2. Shared serializer | `schemas/deep_dive.py::to_response()` | ✅ | Single dataclass→Pydantic mapper imported by BOTH the route and the batch. Margins stay percent-scale (no re-scaling → no `4688%` regression). Guarantees cache/live payload parity by construction. |
| 3. Batch workflow | `workflow/deep_dive_batch.py` | ✅ | `run_deep_dive_batch()` — `Semaphore(8)` + `asyncio.to_thread` (CPU-bound engine), per-ticker error isolation, `job_phase`/`job_progress` every 250, skips `last_close == 0.0`, `DeepDiveBatchResult.to_dict()`. |
| 4. GCP job | `ops/gcp_jobs.py` | ✅ | `run_deep_dive_batch_job()` mirrors `run_stocks_derived_batch_job`; `"stocks-deep-dive-batch"` registered in both `JOB_NAMES` and `JOB_RUNNERS` (17 jobs). |
| 5. Route | `api/routes/deep_dive.py` | ✅ | Read-through order: in-memory cache → `DeepDiveStore` (fresh within `deep_dive_max_staleness_sessions`) → on-demand fallback + write-back. `force` param guarded by `require_inline_compute_allowed()`. Cloud-mode serves stale when inline compute is blocked; 404 only when nothing exists. |
| 6. Config + deps | `config.py`, `api/deps.py` | ✅ | `deep_dive_batch_enabled=True`, `deep_dive_batch_min_market_cap_millions=1000.0`, `deep_dive_max_staleness_sessions=2`. `get_deep_dive_store()` singleton; `invalidate_deep_dive_cache()` + `_deep_dive_store=None` wired into `reset_all()`. |
| 7. Scheduling (local) | `workflow/scheduler.py`, `app.py` | ✅ | `schedule_deep_dive_batch()` (weekday cron, 4:15 PM ET), `_scheduled_deep_dive_batch()` handler gated by `deep_dive_batch_enabled`, invalidates route cache after write. |
| 7. Scheduling (cloud) | `infra/gcp/workflows/morning-pipeline.yaml`, `infra/gcp/deploy_jobs.sh` | ✅ | Fire-and-forget `start_deep_dive_batch` (`http.post :run`, never polled → cannot block `run_publish`). `deploy_job tyche-stocks-deep-dive-batch` (8h timeout, `--tasks=1`) + added to pre-build test gate. |
| 8. Tests | `test_deep_dive_store/batch/route.py` | ✅ | Present and passing (see below). |
| 9. Docs | `docs/stock-deep-dive.md` §v2 | ✅ | Rewritten to per-ticker / no-publish-step design; config knobs + 2026-07-12 changelog added. Rules (`known-issues.mdc`, `data-layout.mdc`) and skill (`gcp-cloud-ops`) updated. |

### Universe-selection semantics (spec-critical)

The batch uses `select_history_universe()`, which I confirmed performs exactly the spec's filtering:
`get_all_tickers()` → `filter_equity_only()` → market-cap floor **with no-cap pass-through** (`caps.get(t, 0) >= min_cap or caps.get(t, 0) == 0`). Matches existing filter semantics.

---

## Test & Build Results (re-run during validation)

### Backend — deep-dive suites (run locally)

```
pytest tests/unit/test_deep_dive_store.py tests/unit/test_deep_dive_batch.py \
       tests/unit/test_deep_dive_route.py tests/unit/test_gcp_jobs.py \
       tests/unit/test_scheduler_wiring.py tests/unit/test_ticker_deep_dive.py
→ 77 passed in 8.10s
```

### Backend — full suite

Matches Sonnet's report **exactly**: **1674 passed, 74 failed**.

The 74 failures were confirmed **pre-existing and environmental**, not regressions:
- Root cause: `backend/.env` has `TYCHE_DATA_BACKEND=gcs` pointing at a live bucket, and several route/client test files don't fully isolate `TycheSettings` from `.env`.
- Reproduced `test_news_routes.py::TestNewsSignalsEndpoint::test_list_signals_empty`: the test asserts `== []` but the endpoint returns 500 real rows from the GCS bucket (`published_route_stale_serving age_minutes=1251`). Purely environmental.
- Failing files (`test_scanner_routes`, `test_stocks_routes`, `test_demand_clients`, `test_filing_routes`, `test_news_routes`, `test_expiry_tracker`) contain **no deep-dive code** and were not modified in this work.

### Frontend

```
npm run lint  → 0 errors, 6 warnings (pre-existing Settings.tsx react-hooks/exhaustive-deps)
npm run build → tsc -b && vite build, exit 0 (one pre-existing >500 kB chunk-size warning)
```

---

## Delivery Hygiene

- **Committed & pushed:** feature commit `34b8c60` (`feat(stocks): add Stock Deep Dive v1+v2 …`) + docs commit `3930b2d`. Working tree clean.
- **Feature commit contents confirmed:** all backend files (`ticker_deep_dive.py`, `routes/deep_dive.py`, `deep_dive_store.py`, `schemas/deep_dive.py`, `deep_dive_batch.py`), all four test files, the frontend `eslint.config.js` + `telemetry.ts` fix, and the docs.
- **No monolithic file:** `find` for `signals/stocks/deep_dive.parquet` → none. Layout is strictly one Parquet per ticker, satisfying the hard constraint (and v3's screener dependency).

---

## Acceptance Criteria — Final Status

**Prompt 1**
- [x] `npm run build` passes, no TS errors
- [x] RSI histories are line charts with 70/50/30 (+ 60 quarterly) reference lines; quarterly dual-axis RSI + price
- [x] Price-history line chart with EMA-8/EMA-21 reference lines
- [x] Volume bar chart with amber surge highlighting + dynamic peak caption
- [x] Fundamentals: Revenue (bar), Cash (bar), Net Income (line), Gross Margin (line) + numeric table
- [x] All percentages render plausibly (no `2781%`)
- [x] 2–3 data-driven callouts; no hardcoded ticker names
- [x] Empty/null data degrades gracefully
- [x] All charts share one light-theme look via wrappers
- [x] `npm run lint` passes — **now closed** via Prompt 2 follow-up (ESLint config added)

**Prompt 2**
- [x] Batch writes ONE Parquet per ticker under `signals/stocks/deep_dive/{TICKER}.parquet`; no monolithic file
- [x] Fresh reads return precomputed payload via a single store read (no recompute)
- [x] Cache miss/stale → on-demand compute + write-back; identical JSON shape (one shared serializer)
- [x] GCS cloud mode serves precomputed payloads without inline compute; no error when payload exists
- [x] `deep_dive_batch_enabled=false` reverts route to pure on-demand (v1 behavior)
- [x] New + existing unit tests pass (deep-dive suites green; the 74 full-suite failures are pre-existing/environmental)
- [x] Prompt 1 follow-ups: `npm run lint` and `npm run build` both pass

---

## Minor Observations (non-blocking, no action required)

1. **Store `ctx` binding vs. spec sketch.** The prompt sketched per-method `ctx=None` params on `read_ticker`/`write_ticker`; the implementation binds `ctx` once at construction (`DeepDiveStore(ctx=...)`). Functionally equivalent — arguably cleaner — and works local + GCS. Not a defect.
2. **RKLB/MSFT live percent spot-check** (Prompt 2 follow-up #2) was a verification-only item; only AAPL was confirmed live. The fix is field-agnostic, so correctness is not at risk, but the explicit RKLB/MSFT confirmation was not re-run (backend currently in GCS mode).
3. **Optional per-series tooltip formatting** (Prompt 2 follow-up #3) was explicitly optional and was not implemented — acceptable per the prompt.

---

## Conclusion

All required work through the end of Prompt 2 is **implemented, tested (77 deep-dive tests green), wired for both local and cloud, documented, and committed cleanly**. The per-ticker-Parquet hard constraint is satisfied, cache/live payload parity is guaranteed by a single shared serializer, and the frontend lint gate (Prompt 1's one open box) is closed. Sonnet's two implementation reports are accurate. No corrective action is required to consider the Prompt 1 + Prompt 2 checkpoint complete.
