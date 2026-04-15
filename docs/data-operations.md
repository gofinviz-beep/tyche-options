# Data Operations Runbook

Complete reference for all automated and manual data pipelines, their schedules, dependencies, and troubleshooting.

## Schedule Overview

All times are US/Eastern. Weekday-only jobs do not fire on weekends or market holidays.

### Daily (Weekdays, After Market Close)

| Time ET | Job | Config Knob | API Cost | Runtime |
|---------|-----|-------------|----------|---------|
| 09:35 | Morning Scan | always on | Tradier (options chains) | ~30s |
| 16:00 | Daily Digest | `daily_digest_enabled` | None | <1s |
| 16:02 | OHLCV Refresh | always on | Polygon (grouped bars) | ~10s |
| 16:05 | Exit Monitor | always on | None (reads OHLCV) | <5s |
| 16:08 | **Conviction Batch** | `conviction_batch_after_ohlcv` | None (reads OHLCV) | ~30-60s |
| 16:10 | Options Snapshot | `options_snapshot_enabled` | Tradier (120 RPM) | ~30 min |
| 16:45 | **Bridge Tradier IV** | `bridge_tradier_iv_enabled` | None (reads snapshot) | ~60s |

### Daily (Nightly, All Days)

| Time ET | Job | Config Knob | API Cost | Runtime |
|---------|-----|-------------|----------|---------|
| 02:00 | **S3 Flatfile Options Ingest** | `flatfile_ingest_enabled` | Massive S3 (download) | ~10-30 min |

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
- Updates `data/ticker_meta.parquet` with type, market cap, exchange info
- Backfills missing market caps via per-ticker Polygon snapshot API
- Ensures newly-listed stocks appear in the universe

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
   ```

3. **Start the backend:**
   ```bash
   ./scripts/start-backend.sh
   ```
   All scheduled jobs will begin firing at their configured times.

## Troubleshooting

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
