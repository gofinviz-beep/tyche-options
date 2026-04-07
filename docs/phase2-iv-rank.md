# Phase 2: IV Rank from Historical Options Data

## Status

| Sub-phase | Status | Notes |
|-----------|--------|-------|
| 2a: Data Ingestion (REST) | **Implemented** | `ingest_options_history.py`, ATM puts only, ~50K API calls |
| 2a-v2: Data Ingestion (Flat Files) | **Implemented** | `ingest_options_flatfiles.py`, full chain, S3 bulk download, 30–60 min |
| 2b: FeatureSignal integration | **Implemented** | `iv_rank`, `iv_percentile`, `atm_iv`, `vrp` in full stack + UI |
| 2c: CSP gate (optional) | Planned | VRP gate + IV Rank floor + allocator bonus behind config flags |

## Prerequisites

- Massive.com / Polygon.io Options plan ($29/month) — provides 2 years of historical options contract data
- Tradier is already capturing daily options snapshots with `implied_volatility` in the schema
- Same Polygon API key works for both stock and options data (`TYCHE_POLYGON_API_KEY`)
- **Flat files**: S3 credentials (`TYCHE_MASSIVE_S3_ACCESS_KEY`, `TYCHE_MASSIVE_S3_SECRET_KEY`) from the Massive dashboard — separate from the API key

## Key Definitions

- **IV (Implied Volatility):** Market's expected future price movement, embedded in option prices. Backed out from the option's market price via Black-Scholes inverse. Higher IV = options are more expensive. Already stored in `OPTIONS_CHAIN_SCHEMA.implied_volatility` from Tradier snapshots.
- **RV (Realized Volatility):** Actual historical price movement. Annualized std dev of daily log returns from OHLCV closes. Already computed by `IVProxyPremiumModel._realised_vol()` in `backtest/premium.py`.
- **VRP (Volatility Risk Premium):** IV minus RV. When positive (typical), options are overpriced vs actual movement — selling premium has positive expected value. Wider VRP = better time to sell CSPs.
- **IV Rank:** `(current_IV - 52wk_low_IV) / (52wk_high_IV - 52wk_low_IV)`. Ranges 0–100. IV Rank > 50 means IV is in the upper half of its annual range — premium is rich relative to history. Research (Tastytrade 10+ year study) shows selling CSPs when IV Rank > 50 improves win rates by 5–10% and average P&L by 20–30%.

## Important: Historical IV Is Not in the API

The Polygon/Massive API provides `implied_volatility` on the **live snapshot** endpoint (`GET /v3/snapshot/options/{underlying}`) but **not** on historical daily bars (`GET /v2/aggs/ticker/{optionsTicker}/range/1/day/{from}/{to}`). Historical bars only return OHLCV. Therefore, IV is back-computed from option close prices using Black-Scholes inverse (`scipy.optimize.brentq`).

## Phase 2a: Data Ingestion — REST API (Legacy)

### Script: `backend/scripts/ingest_options_history.py`

For each ticker in the filtered universe (market cap >= $4B):
1. List historical put contracts via `/v3/reference/options/contracts` with strike range filtering
2. For each trading day, select the nearest ATM put with ~30 DTE (in-memory from OHLCV closes)
3. Fetch contract daily OHLCV via `/v2/aggs/ticker/{optionsTicker}/range/1/day/{from}/{to}`
4. Compute IV from option close price using Black-Scholes inverse (`scipy.optimize.brentq`)
5. Persist to `HistoricalIVStore` → `data/options_iv/{TICKER}.parquet`
6. Compute derived metrics (IV Rank, IV Percentile, RV, VRP) → `data/derived/{TICKER}.parquet`

**Limitation**: ~50,000 API calls at 500 RPM = 2+ days. Only captures ATM puts (~30 DTE), not full chain.

## Phase 2a-v2: Data Ingestion — S3 Flat Files (Primary)

### Script: `backend/scripts/ingest_options_flatfiles.py`

Downloads daily compressed CSV files from Massive's S3-compatible endpoint (`us_options_opra/day_aggs_v1`). Each file contains ALL US options contracts traded that day. Stores full options chain per ticker, then extracts ATM put IV for the existing pipeline.

#### Two-phase pipeline:
1. **Download + persist**: For each trading day, download `.csv.gz`, filter to ticker universe, parse OCC tickers, persist ALL options data (puts + calls, all strikes) to `OptionsHistoryStore` → `data/options_history/{TICKER}.parquet`
2. **IV extraction + derived**: For each ticker, select ATM put with ~30 DTE from stored data, compute IV via Black-Scholes, persist to `HistoricalIVStore` + `DerivedMetricsStore`

### Usage

```bash
# Full run from OHLCV universe (recommended: run in screen)
cd backend
screen -S flatfile-ingest
python scripts/ingest_options_flatfiles.py --from-ohlcv --concurrency 8

# Specific tickers
python scripts/ingest_options_flatfiles.py --tickers AAPL,MSFT,GOOGL --force

# Only download raw data (skip IV computation)
python scripts/ingest_options_flatfiles.py --from-ohlcv --skip-iv

# Dry run
python scripts/ingest_options_flatfiles.py --from-ohlcv --dry-run

# 1 year of history instead of 2
python scripts/ingest_options_flatfiles.py --from-ohlcv --days-back 365
```

### S3 Configuration

```bash
# Add to .env (separate from TYCHE_POLYGON_API_KEY)
TYCHE_MASSIVE_S3_ACCESS_KEY=your_access_key
TYCHE_MASSIVE_S3_SECRET_KEY=your_secret_key
```

### Modules

| Module | Purpose |
|--------|---------|
| `market_data/occ_parser.py` | `parse_occ_ticker()`, `extract_underlying()`, `parse_occ_columns()` |
| `market_data/options_history_store.py` | `OptionsHistoryStore` — per-ticker Parquet at `data/options_history/` |
| `market_data/iv_calculator.py` | `bs_put_price()`, `compute_iv()`, `compute_iv_batch()` |
| `market_data/historical_iv_store.py` | `HistoricalIVStore` — per-ticker Parquet at `data/options_iv/` |
| `market_data/derived_store.py` | `DerivedMetricsStore` — per-ticker Parquet at `data/derived/` |

### Performance

- ~500 trading days × ~30MB compressed = ~15GB download
- 8 concurrent downloads: 30–60 minutes total (vs. 2+ days via REST)
- Resumable: `_progress.json` tracks completed dates

### Storage Schemas

**OptionsHistoryStore** (`data/options_history/{TICKER}.parquet`):
`date`, `option_ticker`, `underlying`, `expiration`, `strike`, `option_type`, `open`, `close`, `high`, `low`, `volume`, `transactions`, `dte`

**HistoricalIVStore** (`data/options_iv/{TICKER}.parquet`):
`date`, `strike`, `expiration`, `contract_ticker`, `option_close`, `underlying_close`, `dte`, `implied_volatility`

**DerivedMetricsStore** (`data/derived/{TICKER}.parquet`):
`date`, `atm_iv`, `iv_rank`, `iv_percentile`, `rv_20d`, `vrp`

## Phase 2b: Integration (Implemented)

Four derived IV metrics are wired through the full conviction stack:

| Field | Type | Description |
|-------|------|-------------|
| `iv_rank` | `float \| None` | Position of ATM IV in 252-day range (0–100) |
| `iv_percentile` | `float \| None` | % of past 252 days with lower IV (0–100) |
| `atm_iv` | `float \| None` | Current ATM put implied volatility |
| `vrp` | `float \| None` | Volatility Risk Premium = ATM IV − RV(20d) |

**Data flow**: `DerivedMetricsStore.read_latest_batch()` → `ConvictionFeatureEngine` (bulk-loaded per `analyze_batch`) → `FeatureSignal` → `ConvictionSignal` → `ConvictionSnapshot` (SQLite) → API schemas → Frontend UI.

**Frontend**: IV Rank and VRP are sortable + filterable columns on Options Conviction, Stocks Conviction, and Pullback Dashboard. Detail panels show all five metrics (ATM IV, IV Rank, IV Percentile, RV 20d, VRP). Color coding: IV Rank < 20 = green (cheap), > 80 = red (expensive); VRP > 0 = green (overpriced), < 0 = red.

**Backward compatible**: All IV fields default to `None`. SQLite columns auto-migrate on startup. Tickers without derived data show "—" in the UI.

## Phase 2c: CSP Gate (Planned)

Add IV Rank and VRP gates to `CSPEligibilityPolicy` behind config flags. Based on analysis of live data:

- **VRP gate**: Reject tickers with VRP < 0 (configurable `min_vrp_pct`, default 0). Negative VRP means selling underpriced insurance — realized vol exceeds implied vol.
- **IV Rank floor**: Reject tickers with IV Rank < 25 (configurable `min_iv_rank`, default 25). Very low IV Rank means thin premiums.
- **VRP bonus in allocator**: Weight high-VRP candidates higher in the MILP objective, similar to the existing delta penalty and pullback path bonus.
- **LLM prompt update**: Flag negative VRP as a risk factor in the analysis prompt.

Off by default until backtested. The scanner currently passes IV metrics to the LLM as context but does not use them for filtering or ranking.

## Why 2 Years Is Enough

- IV Rank uses a 52-week (1 year) rolling window
- 2 years gives 1 year of warm-up for the IV Rank computation + 1 full year of tradeable signals to backtest
- Tradier daily snapshots already include IV — history grows daily for free after the initial 2-year backfill
