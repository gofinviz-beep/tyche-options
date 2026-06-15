# Data Operations Runbook

Complete reference for all automated and manual data pipelines, their schedules, dependencies, and troubleshooting.

## GCP Cloud Mode (production batch)

When `TYCHE_DATA_BACKEND=gcs`, **batch ingest runs in Cloud Run Jobs** — not on the laptop. The local APScheduler is auto-disabled (`scheduler_enabled=false` unless overridden in `config.db`).

| Window (PT) | Days | Workflow | Jobs (parallel unless noted) |
|-------------|------|----------|------------------------------|
| **6:00 PM** | **Mon–Fri** | `tyche-evening-pipeline` | ingest-data, ingest-demand-data, ingest-news, ingest-edgar |
| **2:30 AM** | **Tue–Sat** | `tyche-morning-pipeline` | options-flatfiles + alpha-batch → demand-gate (optional) → publish-signals → audit |

**Evening does not include demand gate** — only `ingest-data`, `ingest-demand-data`, `ingest-news`, `ingest-edgar`. Gate is morning-only (optional ML retrain after fresh estimates). **Publish does not require gate**; it requires alpha-batch.

**Manual recovery order:** alpha-batch succeeded → `tyche-run-demand-gate` (optional, ~4–8h) → `tyche-publish-signals` → `tyche-audit-snapshots`. Flatfiles can finish in parallel; not a publish prerequisite.

- **Deploy:** `infra/gcp/README.md`
- **Spec:** `docs/tyche_gcp_minimal_migration_spec_v2.md`
- **Manifests:** `gs://tyche-data-prod/runs/{job_name}/{run_id}/manifest.json`
- **Intelligence:** Parquet rollups only in cloud (no `news.db`). Checkpoints: `signals/intelligence/_checkpoints/`
- **Demand guidance:** manifest `extra.guidance_tickers_fetched` vs `guidance_catalysts_written`
- **Live progress:** Cloud Logging `job_phase` / `job_progress` events from `tyche/ops/job_progress.py` — see `infra/gcp/README.md` § Observability. Subprocess jobs (flatfiles, demand gate) stream stdout line-by-line (no end-of-job buffer).
- **Demand gate memory:** see [Demand gate memory (Cloud Run)](#demand-gate-memory-cloud-run) below.
- **Ingest session dates:** Pacific (`America/Los_Angeles`) via `market_data/ingest_dates.py` — evening jobs → Pacific today, morning → yesterday. Cloud Run sets `TYCHE_INGEST_WINDOW`; works on laptop in any host timezone.
- **Published JSON NaN:** intelligence Parquet rows with missing datetimes must be sanitized before Pydantic validation. `json_io.sanitize_for_json()` on write; `published_routes` sanitizes on read (legacy GCS `NaN` tokens). Backend restart suffices; re-publish optional.
- **TODO:** multi-task sharding for faster GCS ingest (spec §21)

Local backend reads `published/routes/*.json` and `signals/` from GCS via ADC (`gcloud auth application-default login`).

### Demand gate memory (Cloud Run)

Authoritative detail: **`docs/tyche_gcp_minimal_migration_spec_v2.md` §10.1**.

`tyche-run-demand-gate` is a two-phase job with different memory profiles:

| Phase | Typical peak | Cloud Run | Code path |
|-------|--------------|-----------|-----------|
| **Dataset build** | ~16 GiB | fits at 16 GiB | `build_dataset()` + demand augmenters |
| **Walk-forward XGBoost** | ~24–32 GiB | **32 GiB** deployed | `run_demand_baselines()` / `walk_forward_evaluate()` |

**Dataset build optimizations** (`ml/dataset.py`, `ml/features.py`, `ml/panel_memory.py`):

1. **Chunked concat** — flush every 64 tickers (`DATASET_CHUNK_TICKERS`); never hold ~9k ticker frames in RAM.
2. **In-place augmenters** — demand/relational feature functions mutate the panel (no `all_features.copy()` / per-ticker `pd.concat(out)`).
3. **Downcast** — `float64→float32`, `ticker→category` via `downcast_panel()`.
4. **Parquet checkpoint** — optional round-trip at `ml/_checkpoints/demand_gate_base_panel.parquet` (GCS jobs only) to drop pandas fragmentation.

**Walk-forward optimizations** (`ml/xgb_baseline.py`):

1. **`slim_dataset_for_training()`** — project to `date`, label columns, and feature cols only (~100 vs ~120+).
2. **`_walk_forward_frame()`** — column-slim slice + boolean date masks (no full-panel `dropna().copy()`).
3. **`_prepare_feature_matrix()`** — float32 numpy matrices with NaN→-999 sentinel (no DataFrame copy per window).

**Reuse cached build** (skips ~90 min GCS I/O when `ml/alpha_dataset.parquet` exists):

```bash
# Job env or local:
TYCHE_DEMAND_GATE_REUSE_DATASET=true
```

**Deploy:** `./infra/gcp/deploy_jobs.sh --build` (job is **8 CPU / 32 GiB**). Exit **-9** = SIGKILL/OOM — check which phase failed in Cloud Logging (`build_dataset` vs `walk_forward`).

**Validated on cloud (2026-06-14, execution `tyche-run-demand-gate-hd5zr`):** `TYCHE_DEMAND_GATE_REUSE_DATASET=true` → 4,781,559 rows in ~22s; 6 walk-forward runs (~1h 47m) + 3 model promotions (~9m); total **~2.3h** at 32 GiB. Verdict: all sustained targets **GO** (lift +0.0127 / +0.0073 / +0.0080 AUC); artifacts at `ml/alpha_results/demand_gate_verdict.json` and `ml/models/big_move_sustained_*.json`.

**Local scripts still work** with `TYCHE_DATA_BACKEND=local` (writes `backend/data/`) or `gcs` (writes bucket via ADC). Examples:

```bash
cd backend
.venv/bin/python scripts/ingest_data.py --no-conviction
.venv/bin/python scripts/ingest_demand_data.py --no-fundamentals --no-short-interest --no-guidance  # estimates only
.venv/bin/python scripts/ingest_options_flatfiles.py --from-ohlcv --include-today --days-back 3
.venv/bin/python scripts/run_demand_gate.py
```

`ingest_data.py` passes `storage_context_from_settings()` to OHLCV/meta/intraday stores (required after `_MetadataCache` GCS migration).

---

## Schedule Overview (local APScheduler)

All times are US/Eastern. **Disabled when `TYCHE_DATA_BACKEND=gcs`.** Weekday-only jobs do not fire on weekends or market holidays.

### Daily (Weekdays, After Market Close)

| Time ET | Job | Config Knob | API Cost | Runtime |
|---------|-----|-------------|----------|---------|
| 09:35 | Morning Scan | always on | Tradier (options chains) | ~30s |
| 16:00 | Daily Digest | `daily_digest_enabled` | None | <1s |
| 16:02 | OHLCV Refresh (+ market-cap reprice) | always on | Polygon (grouped bars) | ~10s |
| 16:05 | Exit Monitor | always on | None (reads OHLCV) | <5s |
| 16:08 | **Conviction Batch** | `conviction_batch_after_ohlcv` | None (reads OHLCV) | ~30-60s |
| 16:10 | Options Snapshot | `options_snapshot_enabled` | Tradier (120 RPM) | ~30 min |
| 16:20 | **Directional Alpha Batch** | `alpha_batch_enabled` | None (reads OHLCV) | ~1-3 min |
| 16:45 | **Bridge Tradier IV** | `bridge_tradier_iv_enabled` | None (reads snapshot) | ~60s |

### Daily (Nightly, All Days)

| Time ET | Job | Config Knob | API Cost | Runtime |
|---------|-----|-------------|----------|---------|
| 02:00 | **S3 Flatfile Options Ingest** | `flatfile_ingest_enabled` | Massive S3 (download) | ~10-30 min |
| 03:00 | **Demand Data Refresh** | `demand_data_enabled` | Finnhub + Polygon + Benzinga | ~30-60 min |

The S3 flat file job runs every day (including weekends) to catch up on any missed days. It uses `--days-back 3` to cover weekends and the script's `completed_dates` check makes re-runs idempotent. Requires `TYCHE_MASSIVE_S3_ACCESS_KEY` and `TYCHE_MASSIVE_S3_SECRET_KEY` in `.env`.

### Intraday

| Interval | Job | Config Knob | API Cost | Runtime |
|----------|-----|-------------|----------|---------|
| Every 4h | News Ingest | `news_ingestion_enabled` | Polygon + Finnhub + Gemini Flash Lite | ~5 min |
| Every 24h | EDGAR Ingest | `edgar_ingestion_enabled` | SEC EDGAR (free) + Gemini Flash Lite | ~10 min |

### Weekly

| When | Job | Config Knob | API Cost | Runtime |
|------|-----|-------------|----------|---------|
| Sunday 02:00 | **Ticker Meta Refresh** | `weekly_meta_refresh_enabled` | Polygon (reference API) | ~5-10 min |

### Monthly

| When | Job | Config Knob | API Cost | Runtime |
|------|-----|-------------|----------|---------|
| 28th, 22:00 | **Correlation Refresh** | `correlation_refresh_enabled` | None (reads OHLCV) | ~2-5 min |
| 1st, 02:00 | ML Retrain | `ml_retrain_enabled` | None (CPU-bound XGBoost) | ~5-15 min |

### Quarterly (March, June, September, December)

| When | Job | Config Knob | API Cost | Runtime |
|------|-----|-------------|----------|---------|
| 1st, 03:00 | **ETF Constituents** | `etf_refresh_enabled` | yfinance (free) | ~2 min |
| 1st, 03:30 | **Sector/SIC + Institutional** | `quarterly_meta_refresh_enabled` | Polygon + yfinance | ~15-30 min |

## Dependency Graph

```
Daily After Close:
  OHLCV Refresh (16:02)
    └─→ Conviction Batch (16:08) — needs fresh OHLCV prices
    └─→ Exit Monitor (16:05) — refreshes OHLCV as safety net
  Options Snapshot (16:10)
    └─→ Bridge Tradier IV (16:45) — needs snapshot data written

Nightly:
  S3 Flatfile Options Ingest (02:00 AM)
    → Downloads previous day's options flat file from Massive S3
    → Extracts ATM IV → Recomputes IV Rank, VRP (DerivedMetricsStore)
    → Feeds morning conviction scan (IV Rank, VRP columns)

Monthly/ML:
  Correlation Refresh (28th)
    └─→ ML Retrain (1st) — uses correlation features in dataset

Quarterly:
  ETF Constituents (1st, 03:00)
    └─→ used by ML Retrain for ETF membership features
  Sector + Institutional (1st, 03:30)
    └─→ used by conviction pipeline for sector context
```

## What Each Job Does

### OHLCV Refresh
- Calls `bootstrap_ohlcv(polygon, store, days=5, include_today=True)`
- Fetches last 5 days of daily bars from Polygon grouped endpoint
- Writes to `data/ohlcv/*.parquet`
- Foundation for all conviction and ML pipelines
- **Then reprices market caps:** `recompute_market_caps_from_shares()` sets `market_cap = shares_outstanding × latest close` in `ticker_meta.parquet` (no extra API calls — uses the close just fetched). This keeps market cap price-current daily; see "Live Market Cap" below.

### Directional Alpha Batch
- Runs `run_alpha_batch()` across the equity universe (common-stock only, build-net floor `alpha_min_market_cap_millions`, default $250M)
- `AlphaScoreEngine` composites momentum / relative-strength / trend-quality / breakout / volume-thrust factors + the `BreakoutPredictor` ML big-move probabilities into a 0–100 Alpha score with a horizon tag (Swing/Trend/Thematic)
- Persists to `data/alpha_signals.parquet` (peak) and `data/alpha_signals_sustained.parquet` (sustained) via `AlphaSignalStore(variant=...)`
- Serves the Directional Alpha page (`GET /alpha/scan?variant=sustained|peak`); the page applies its own market-cap floor (default $1B) at read time
- Feature build reads D-FUND/D-EST from on-disk Parquet — run demand ingest + alpha batch after any fundamentals repair
- Gracefully degrades to rules-only scoring when no big-move model artifacts exist

### Demand Data Refresh
- Runs `ingest_demand_data()` from `workflow/demand_data.py` (scheduled 03:00 ET when `demand_data_enabled`)
- **Fundamentals:** Finnhub `/stock/financials` (standardized, primary) → as-reported quarterly → as-reported annual; dual-class aliasing via `dual_class.py` (GOOG→GOOGL, etc.)
- **Estimates:** Finnhub Estimates-1 (revisions, surprises, recommendations, price targets) with same dual-class fetch
- **Short interest:** Polygon
- **Guidance → catalysts:** Benzinga via Massive/Polygon key
- Writes to `data/fundamentals/`, `data/estimates/`, `data/short_interest/`, `data/catalyst_signals/`
- Manual full-universe re-ingest: `python scripts/ingest_demand_data.py`

### Demand Data Audit (manual)
- `python scripts/audit_demand_coverage.py` — Parquet hygiene for the alpha universe ($250M+); outputs `data/ml/demand_audit_report.csv` + `demand_audit_summary.json`
- `--repair` / `--repair-ingest-gaps-only` — re-fetch tickers with ingest gaps (not source-empty names)
- `python scripts/audit_finnhub_vs_edgar.py` — compares SEC 10-Q/10-K filing dates vs Finnhub store for STALE tickers; **note:** compares store filing dates only — Jan-FYE false STALE and pre-standardized quarterly-only logic can overstate lag; use after fundamentals fix for directional signal only

### Conviction Batch
- Runs `run_conviction_batch()` across the full equity universe
- Filters: market cap ≥ $500M (batch threshold), price ≥ $5, avg vol ≥ 500K
- Computes EMA 8/21/50, RSI(14), trend state, conviction level/score
- Upserts to `conviction.db` → `conviction_snapshots` table (source of truth for all page loads)
- Detects state transitions (e.g., uptrend → pullback) → `conviction_transitions` table
- Persists signals to `data/conviction_signals.parquet`
- Clears route-level caches (`invalidate_conviction_cache(clear_engine=False)`) — page loads now read fresh snapshots from DB
- Frontend detects the version bump via `GET /conviction/version` polling and invalidates React Query caches

### Options Snapshot
- Fetches live put chains from Tradier for all equity tickers with market cap ≥ $4B
- Writes to `data/options_chains/{TICKER}.parquet` with `snapshot_date` column
- Rate limited at 120 RPM (Tradier hard cap)
- ~30 minutes for ~1,100 tickers

### Bridge Tradier IV
- Reads today's Tradier snapshot from `OptionsChainStore`
- Extracts ATM put IV per ticker (Tradier provides `implied_volatility` directly)
- Writes to `data/options_iv/{TICKER}.parquet` (HistoricalIVStore)
- Recomputes derived metrics: IV Rank (252d window), IV Percentile, RV 20d, VRP
- Writes to `data/derived/{TICKER}.parquet` (DerivedMetricsStore)
- Keeps IV Rank/VRP current same-day without waiting for Massive flat files

### Correlation Refresh
- Computes 60-day rolling pairwise return correlations for all equities with market cap ≥ $4B
- Computes rolling betas against SPY and QQQ
- Writes to `data/correlations.parquet` and `data/betas.parquet`
- Used as ML features for CSP safety model

### ETF Constituents
- Merges static curated lists (SPY, QQQ, DIA, XLK, XLF, XLE, XLV, SMH, SOXX, XLI) with yfinance weight data
- Writes to `data/etf_constituents.parquet`
- Used as ML features (ETF membership count, SPY/QQQ weight, etc.)

### ML Retrain
- Builds dataset from OHLCV + derived metrics + ETF features + correlation features
- Trains XGBoost model on `csp_win_5d` target with walk-forward splits
- Saves production model to `data/ml/csp_win_5d.json` + `_meta.json`
- `CSPSafetyPredictor` picks up new model on next `deps.reset_all()`

### News Ingest
- Fetches articles from Polygon News + Finnhub
- Persists to `data/news_articles/{TICKER}.parquet`
- Classifies sentiment with Gemini Flash Lite (queue + workers pattern)
- Rebuilds aggregate signals in `news.db` → `news_signals` table

### EDGAR Ingest
- Fetches 8-K filings and Form 4 insider transactions from SEC EDGAR
- 8-K: persisted to `data/filings_8k/{TICKER}.parquet`, classified with Gemini
- Form 4: parsed XML, persisted to `data/insider_transactions/{TICKER}.parquet`
- Cluster sell detection (3+ insiders selling in 7 days)
- Rebuilds signals in `news.db` → `filing_signals` table

### Ticker Meta Refresh (Weekly)
- Fetches active common stock reference data from Polygon (`/v3/reference/tickers`)
- Updates `data/ticker_meta.parquet` with type, exchange info, and **shares outstanding** (`weighted_shares_outstanding`)
- Refreshes shares outstanding (slow-moving — buybacks/issuance) so the daily `shares × close` reprice stays accurate, then reprices caps
- Polygon's static `market_cap` is kept only as a fallback for names with no OHLCV to reprice; the dedicated market-cap backfill is **no longer run here** (superseded by the daily reprice)
- Ensures newly-listed stocks appear in the universe

### Live Market Cap (shares × close)
- **Problem:** Polygon's `market_cap` reference field lags by *months* (it is not re-priced as the stock moves), so refreshing it weekly just re-pulls a stale number. Example: MU read $413B while its live cap was >$1T.
- **Fix:** store `shares_outstanding` and derive `market_cap = shares × latest daily close`. Shares barely change, so the daily close drives freshness — effectively daily/intraday-grade caps with zero extra API calls.
- The derived cap is written back into `ticker_meta.parquet`'s `market_cap`, so every consumer (conviction, scanner, deep-dips, alpha, and the $4B/$1B floors) reads the current value with no downstream changes.
- One-time / manual backfill of shares + caps for the whole universe: `python scripts/backfill_shares_caps.py` (or `--tickers MU,NVDA`).

### Quarterly Meta Refresh
- SIC/sector data: backfills sector codes from Polygon ticker details API
- Institutional ownership: fetches from yfinance for tickers missing ownership data
- Both update `data/ticker_meta.parquet`

## Manual Override Commands

Every automated job can be triggered manually via CLI or API:

### Via CLI (backend directory)

```bash
# OHLCV refresh
python scripts/ingest_data.py --ohlcv

# Conviction batch
python scripts/ingest_data.py --conviction

# Options snapshot (Tradier)
python scripts/ingest_options.py --from-ohlcv

# Bridge Tradier IV
python scripts/bridge_tradier_iv.py
python scripts/bridge_tradier_iv.py --date 2026-04-10

# ETF constituents
python scripts/ingest_data.py --etf

# Correlations
python scripts/ingest_data.py --correlations

# Ticker metadata
python scripts/ingest_data.py --meta

# Shares outstanding + live market-cap reprice (shares × close)
python scripts/backfill_shares_caps.py

# Directional alpha — train big-move models (walk-forward + save)
python scripts/train_alpha.py --save-model

# Institutional ownership
python scripts/ingest_data.py --institutional --no-conviction

# SIC/sector
python scripts/ingest_data.py --sic --no-conviction

# ML retrain
python scripts/train_baselines.py --dataset data/ml/dataset.parquet --targets csp_win_5d --save-model

# Massive S3 flat files (overnight options data)
python scripts/ingest_options_flatfiles.py --include-today

# News ingest
curl -X POST http://localhost:8000/api/v1/news/ingest

# EDGAR ingest
curl -X POST http://localhost:8000/api/v1/filings/ingest
```

### Via API

```bash
# Trigger OHLCV refresh
curl -X POST http://localhost:8000/api/v1/stocks/ohlcv/refresh

# Trigger conviction batch
curl -X POST http://localhost:8000/api/v1/stocks/conviction/refresh

# Trigger ML retrain
curl -X POST http://localhost:8000/api/v1/system/ml/retrain

# Recompute directional alpha signals
curl -X POST http://localhost:8000/api/v1/alpha/recompute

# Check ML model info
curl http://localhost:8000/api/v1/system/ml/model-info

# Check scheduled job status
curl http://localhost:8000/api/v1/system/scheduler/status
```

## First-Time Setup

1. **Configure secrets in `.env`:**
   ```bash
   TYCHE_TRADIER_API_TOKEN=your_token
   TYCHE_TRADIER_ACCOUNT_ID=your_account
   TYCHE_POLYGON_API_KEY=your_key
   TYCHE_GEMINI_API_KEY=your_key
   TYCHE_MASSIVE_S3_ACCESS_KEY=your_key   # Optional, for options flat files
   TYCHE_MASSIVE_S3_SECRET_KEY=your_key
   TYCHE_FINNHUB_API_KEY=your_key         # Optional, for news
   TYCHE_EDGAR_USER_AGENT_EMAIL=you@example.com  # Required for SEC EDGAR
   ```

2. **Initial data ingestion (run once, in order):**
   ```bash
   cd backend

   # 1. Ticker metadata (types, market caps, exchanges)
   python scripts/ingest_data.py --meta

   # 2. OHLCV price history (120 days by default)
   python scripts/ingest_data.py --ohlcv

   # 3. SIC sector codes
   python scripts/ingest_data.py --sic --no-conviction

   # 4. Institutional ownership
   python scripts/ingest_data.py --institutional --no-conviction

   # 5. ETF constituents
   python scripts/ingest_data.py --etf

   # 6. Correlations
   python scripts/ingest_data.py --correlations

   # 7. Options chain snapshot (Tradier — ~30 min)
   python scripts/ingest_options.py --from-ohlcv

   # 8. Bridge Tradier IV
   python scripts/bridge_tradier_iv.py

   # 9. Historical options data (Massive S3 — ~30-60 min, optional)
   python scripts/ingest_options_flatfiles.py

   # 10. Conviction batch
   python scripts/ingest_data.py --conviction

   # 11. Train ML model
   python scripts/train_baselines.py --dataset data/ml/dataset.parquet --targets csp_win_5d --save-model

   # 12. Demand data (Directional Alpha D-FUND/D-EST) — requires TYCHE_FINNHUB_API_KEY
   python scripts/ingest_demand_data.py
   python scripts/audit_demand_coverage.py

   # 13. Directional Alpha models + batch
   python scripts/run_demand_gate.py
   python scripts/train_alpha.py --feature-set momentum
   curl -X POST http://localhost:8000/api/v1/alpha/recompute
   ```

3. **Start the backend:**
   ```bash
   ./scripts/start-backend.sh
   ```
   All scheduled jobs will begin firing at their configured times.

## Troubleshooting

### Cloud Run job appears idle (no logs for hours)

- **Check Cloud Logging** (not just the manifest): filter `jsonPayload.event="job_progress"` or `jsonPayload.job="ingest-options-flatfiles"`.
- **Expected phases:** flatfiles → `preload_ohlcv` (can take 30–60 min on GCS), then `download_dates`, then `iv_extraction`. Alpha → `build_features` every 250 tickers.
- **If only `gcp_job_start`:** image may be stale — redeploy with `./infra/gcp/deploy_jobs.sh --build`.
- **Post-hoc summary:** `gsutil cat gs://tyche-data-prod/runs/{job_name}/*/manifest.json | tail -1`

### Conviction data is stale
- **Check:** Look at the `as_of_date` on the Stocks Conviction page, or `curl http://localhost:8000/api/v1/conviction/version`.
- **Fix:** `curl -X POST http://localhost:8000/api/v1/stocks/conviction/refresh`
- **Root cause:** The OHLCV refresh at 16:02 did NOT trigger conviction batch (now fixed with `conviction_batch_after_ohlcv` job at 16:08).

### Conviction pages slow after backend restart
- **Expected:** Sub-second if `conviction.db` has data (lazy deps skip heavy I/O).
- **If slow:** Check that `conviction.db` exists and has recent snapshots. Run `sqlite3 db/conviction.db "SELECT COUNT(*), MAX(as_of_date) FROM conviction_snapshots;"`.
- **Root cause (fixed):** Before April 2026, `ConvictionEngine` and `OHLCVStore` were eagerly resolved via FastAPI `Depends()`, blocking the event loop with 30-40s of Parquet I/O even when the DB had cached data. Now uses lazy dependency resolution — heavy objects only initialized if the DB path misses.

### CSP Safety shows "—"
- **Check:** `curl http://localhost:8000/api/v1/system/ml/model-info`
- **Fix:** Run `python scripts/train_baselines.py --dataset data/ml/dataset.parquet --targets csp_win_5d --save-model`, then restart backend.
- **Root cause:** Model artifact `data/ml/csp_win_5d.json` does not exist.

### IV Rank / VRP shows null
- **Check:** Look for `data/derived/{TICKER}.parquet` files.
- **Fix:** Run `python scripts/bridge_tradier_iv.py` or `python scripts/ingest_options_flatfiles.py --include-today`.
- **Root cause:** No IV data available for that ticker. Needs either Tradier snapshot + bridge, or Massive flat files.

### Options snapshot taking too long
- **Expected:** ~30 min for ~1,100 tickers at 120 RPM.
- **Check:** Backend logs for `options_snapshot_starting` and progress messages.
- **Config:** Increase `options_snapshot_min_market_cap` to reduce the ticker count.

### News/EDGAR pipeline errors
- **Check:** Backend logs for `scheduled_news_ingest_failed` or `scheduled_edgar_ingest_failed`.
- **Fix:** Ensure API keys are set (`TYCHE_FINNHUB_API_KEY`, `TYCHE_EDGAR_USER_AGENT_EMAIL`).
- **Note:** Both run as background tasks — `POST /news/ingest` and `POST /filings/ingest` return immediately.

### Correlation data missing
- **Check:** Look for `data/correlations.parquet` and `data/betas.parquet`.
- **Fix:** `python scripts/ingest_data.py --correlations`
- **Note:** Requires OHLCV data for ≥60 trading days.

### ETF data missing
- **Check:** Look for `data/etf_constituents.parquet`.
- **Fix:** `python scripts/ingest_data.py --etf`

### Weekly meta not updating
- **Config:** `weekly_meta_refresh_enabled` must be `true` (default).
- **Check:** Requires `TYCHE_POLYGON_API_KEY` in `.env`.

### Market cap looks stale / wrong (e.g. $413B vs $1T)
- **Root cause:** Polygon's `market_cap` field lags by months; the derived `shares × close` reprice needs `shares_outstanding` present.
- **Check:** `python -c "import sys; sys.path.insert(0,'src'); from tyche.market_data.data_store import TickerMetaStore; s=TickerMetaStore('data'); print(s.get_shares_outstanding(['MU']))"` — empty means shares were never backfilled.
- **Fix:** `python scripts/backfill_shares_caps.py` (populates shares + reprices). Thereafter the daily OHLCV refresh keeps caps current automatically.

### Directional Alpha page empty
- **Check:** `data/alpha_signals.parquet` exists and the page's Min Mkt Cap floor isn't above the build-net floor (`alpha_min_market_cap_millions`, $250M).
- **Fix:** `curl -X POST http://localhost:8000/api/v1/alpha/recompute` (background). Big-move ML probabilities require trained artifacts (`python scripts/train_alpha.py --save-model`); without them the page runs rules-only.

### D-FUND looks stale / demand scores untrusted
- **Check:** `python scripts/audit_demand_coverage.py` — review `fund_status`, `fund_latest_period`, `both_ok` in summary JSON.
- **Fix:** Full re-ingest `python scripts/ingest_demand_data.py`, re-audit, then retrain (`run_demand_gate.py` + `train_alpha.py`) and re-run alpha batch. Restart backend to reload model artifacts.
- **False STALE:** Jan-FYE tickers (e.g. PL) may show STALE when `filing_date = period_end` even with current quarter data — verify `fund_latest_period`, not filing age alone.
- **Dual-class:** GOOG/BRK.B/etc. fetch via canonical symbol (`GOOGL`, `BRK.A`) — data should match primary class.

## Cost Summary

| API Provider | Jobs Using It | Billing |
|-------------|---------------|---------|
| **Polygon.io** | OHLCV, Ticker Meta, SIC, Morning Scan (conviction) | $29/mo stocks plan |
| **Massive S3** | Options flat files (optional) | $29/mo options plan |
| **Tradier** | Options Snapshot, Morning Scan (chains), Bridge IV | Free with brokerage account |
| **yfinance** | ETF weights, institutional ownership | Free |
| **SEC EDGAR** | 8-K filings, Form 4 insider transactions | Free (public data) |
| **Finnhub** | News articles | Free tier available |
| **Google Gemini** | News/EDGAR classification | Pay-per-use (Flash Lite) |

## Configuration Reference

All config knobs are editable via the Settings UI (`PATCH /api/v1/system/config`) or `config.db`:

| Setting | Default | Purpose |
|---------|---------|---------|
| `conviction_batch_after_ohlcv` | `true` | Run conviction batch after daily OHLCV refresh |
| `alpha_batch_enabled` | `true` | Run the directional alpha batch (16:20 ET) |
| `alpha_min_market_cap_millions` | `250.0` | Build-net market-cap floor for the alpha universe ($M) |
| `bridge_tradier_iv_enabled` | `true` | Bridge Tradier snapshots into IV/derived pipeline |
| `correlation_refresh_enabled` | `true` | Monthly 60d correlation matrix refresh |
| `etf_refresh_enabled` | `true` | Quarterly ETF constituent list refresh |
| `quarterly_meta_refresh_enabled` | `true` | Quarterly sector + institutional refresh |
| `weekly_meta_refresh_enabled` | `true` | Weekly ticker reference data refresh |
| `ml_retrain_enabled` | `true` | Monthly XGBoost model retraining |
| `ml_retrain_day_of_month` | `1` | Day of month for ML retrain |
| `ml_retrain_time` | `02:00` | Time (ET) for ML retrain |
| `options_snapshot_enabled` | `true` | Daily Tradier chain snapshot |
| `options_snapshot_time` | `16:10` | Time (ET) for options snapshot |
| `news_ingestion_enabled` | `false` | News pipeline (set `true` to activate) |
| `news_ingest_interval_minutes` | `240` | News fetch frequency |
| `edgar_ingestion_enabled` | `false` | EDGAR pipeline (set `true` to activate) |
| `edgar_ingest_interval_minutes` | `1440` | EDGAR fetch frequency |
| `daily_digest_enabled` | `false` | Daily email digest |
