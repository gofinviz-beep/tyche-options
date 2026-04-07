# Data Pipeline

**Sources:**
- `backend/src/tyche/market_data/data_store.py`
- `backend/src/tyche/market_data/polygon.py`

## Overview

Tyche uses a Parquet-first local data layer for all historical market data. Four stores serve different purposes:

- **OHLCVStore** — Per-ticker daily OHLCV bars for all US equities (used by conviction engine and backtest)
- **IntradayStore** — Per-ticker 5-minute intraday bars (used by intraday timing backtest)
- **TickerMetaStore** — Per-ticker metadata: market cap, exchange, type (used for universe filtering)
- **OptionsChainStore** — Per-ticker options chain snapshots from Tradier (used for backtest validation with real premiums)

All are populated from Polygon.io during bootstrap and updated incrementally.

### Per-Ticker Partitioning

OHLCVStore and IntradayStore use **per-ticker Parquet files** — one file per symbol. This provides:

- **Zero write contention:** Different tickers can be written in parallel with no locking
- **Limited blast radius:** A corrupted file only affects one ticker, not the entire universe
- **O(1) single-ticker reads:** Reading AAPL's data touches only `AAPL.parquet`, not 5M rows
- **Efficient incremental updates:** Appending to a 100-row file vs. rewriting a 1.5M-row file

TickerMetaStore remains a single file because it is small (~5K rows) and always read in bulk.

### Migration from Legacy Layout

If a legacy single-file store (`ohlcv_daily.parquet` or `intraday_5min.parquet`) is detected, `ingest_data.py` auto-migrates it to per-ticker files on the next run. The old file is renamed to `.parquet.bak`.

## OHLCVStore

**Directory:** `data/ohlcv_daily/{TICKER}.parquet`

### Schema (per-ticker file)

| Column | Type | Description |
|---|---|---|
| date | date32 | Trading day |
| open | float64 | Opening price |
| high | float64 | Day high |
| low | float64 | Day low |
| close | float64 | Closing price |
| volume | int64 | Share volume |
| vwap | float64 | Volume-weighted average price |

### Deduplication

On every write, rows are deduplicated on `date` within the ticker's file. Re-fetching a date overwrites stale data.

### Key Operations

- `write_bars(bars)` — Group bars by ticker, write each ticker's file independently with dedup
- `read_ticker(ticker, start_date, end_date)` — Read one ticker's file directly, sorted by date ascending
- `read_tickers(tickers, start_date, end_date)` — Multi-ticker read, returns `dict[str, DataFrame]`
- `read_all()` — Combine all ticker files into one DataFrame (for cross-ticker views like screening)
- `get_all_tickers()` — List directory to get all ticker symbols
- `screen_universe(min_avg_volume, min_price, min_dollar_volume)` — Local screening using stored data

## TickerMetaStore

**File:** `data/ticker_meta.parquet`

### Schema

| Column | Type | Description |
|---|---|---|
| ticker | string | Stock symbol |
| name | string | Company name |
| market_cap | float64 | Market capitalization in dollars |
| exchange | string | Primary exchange MIC code (XNYS, XNAS, etc.) |
| type | string | Security type (CS = common stock) |
| last_updated | date32 | Date metadata was last refreshed |

### Key Operations

- `write_meta(tickers)` — Upsert ticker metadata, dedup on ticker symbol
- `get_market_caps(tickers?)` — Return `dict[str, float]` mapping ticker to market cap
- `get_exchanges(tickers?)` — Return `dict[str, str]` mapping ticker to exchange
- `update_market_caps(caps)` — Bulk-update market caps for existing tickers

### Why Persist Market Cap?

Market cap is critical for filtering "good companies" suitable for the Wheel Strategy. Polygon's bulk ticker endpoint (`/v3/reference/tickers`) does not return market cap on Starter plans, so individual `get_ticker_details` calls are used during bootstrap. Persisting the results avoids repeated API calls and enables backtesting with realistic filters.

## IntradayStore

**Directory:** `data/intraday_5min/{TICKER}.parquet`

Stores 5-minute intraday OHLCV bars, one file per ticker. Used by the intraday timing backtest to determine optimal time-of-day for CSP entries. Populated from Polygon's aggregate bars endpoint.

### Schema (per-ticker file)

| Column | Type | Description |
|---|---|---|
| timestamp | timestamp(us) | Bar timestamp (Eastern Time) |
| date | date32 | Trading day (derived from timestamp) |
| open | float64 | Bar open price |
| high | float64 | Bar high |
| low | float64 | Bar low |
| close | float64 | Bar close price |
| volume | int64 | Bar volume |
| vwap | float64 | Volume-weighted average price |
| num_transactions | int64 | Number of transactions in bar |

### Deduplication

Rows are deduplicated on `timestamp` within each ticker's file.

### Key Operations

- `write_bars(bars)` — Group bars by ticker, write each ticker's file independently with dedup
- `read_ticker(ticker, start_date, end_date)` — Read one ticker's file directly, sorted by timestamp
- `read_tickers(tickers, start_date, end_date)` — Multi-ticker read, returns `dict[str, DataFrame]`
- `get_tickers()` — List directory to get all ticker symbols with intraday data
- `get_dates_for_ticker(ticker)` — Sorted list of dates with data for a given ticker

## Polygon.io Integration

**Source:** `backend/src/tyche/market_data/polygon.py`

### API Endpoints Used

| Method | Endpoint | Purpose |
|---|---|---|
| `get_grouped_daily(date)` | `/v2/aggs/grouped/locale/us/market/stocks/{date}` | All US equity daily bars for one date |
| `get_tickers(market, active, type)` | `/v3/reference/tickers` | Paginated ticker reference (name, exchange, type) |
| `get_ticker_details(ticker)` | `/v3/reference/tickers/{ticker}` | Single ticker details (includes market cap) |
| `get_batch_market_caps(tickers)` | Individual `get_ticker_details` calls | Batch market cap fetch with rate limiting |
| `get_aggregate_bars(ticker, from, to)` | `/v2/aggs/ticker/{ticker}/range/5/minute/{from}/{to}` | 5-minute intraday bars for a single ticker |

### Rate Limiting

The Polygon client respects `TYCHE_POLYGON_RATE_LIMIT_RPM` (default 100 RPM). The `get_batch_market_caps` method includes 0.7s sleep between calls to stay within limits on Starter plans.

### Pagination

The `get_tickers` method handles cursor-based pagination via `next_url`. The API key is appended to the `next_url` for authentication.

## Bootstrap Flow

### OHLCV (Price Bars)

`bootstrap_ohlcv()` in `data_store.py` fetches grouped daily bars only — it does **not** touch ticker metadata:

```mermaid
flowchart TD
    Start["bootstrap_ohlcv()"] --> CheckExisting["Check OHLCVStore\nfor latest date"]
    CheckExisting -->|"No data"| FetchAll["Fetch 120 calendar days\n(~80 trading days)"]
    CheckExisting -->|"Has data"| FetchIncremental["Fetch from latest_date+1\nto yesterday"]
    FetchAll --> FetchLoop["For each weekday:\nPolygon get_grouped_daily()"]
    FetchIncremental --> FetchLoop
    FetchLoop --> WriteOHLCV["Write bars to\nohlcv_daily.parquet"]
    WriteOHLCV --> Done["Return stats:\ndates_fetched, bars_stored,\ntickers_found"]
```

### Ticker Metadata (Market Cap, Type, Exchange)

Ticker reference metadata is managed **separately** via `refresh_ticker_meta()` or `ingest_data.py --meta`. This data changes infrequently — market cap, type, and exchange don't need daily refreshes. Running metadata refresh on every OHLCV pull previously caused market caps to be overwritten with zeros (the Polygon list API omits market cap).

```bash
# Refresh metadata explicitly (infrequent — weekly or on demand)
python scripts/ingest_data.py --meta
```

`write_meta()` preserves existing positive market caps when incoming values are zero, as defense-in-depth.

### Triggering Bootstrap

Via API:
```bash
curl -X POST http://localhost:8000/conviction/bootstrap
```

Via Python:
```python
from tyche.market_data.data_store import bootstrap_ohlcv, OHLCVStore
from tyche.market_data.polygon import PolygonClient

polygon = PolygonClient(api_key="...")
store = OHLCVStore(data_dir="data")
result = await bootstrap_ohlcv(polygon, store, days=120)
```

### Incremental Updates

If the store already contains data, bootstrap only fetches dates after the latest stored date. This makes it safe to run repeatedly — it will only fetch missing days.

### Fetching Today's Data

By default, `bootstrap_ohlcv()` stops at yesterday (`end = date.today() - 1`). To include today's data (after market close), use `include_today=True`:

```python
result = await bootstrap_ohlcv(polygon, store, days=5, include_today=True)
```

Or via API:
```bash
curl -X POST http://localhost:8000/api/v1/stocks/ohlcv/refresh
```

### Scheduled Refresh

The OHLCV refresh is scheduled automatically via APScheduler:

| Time (ET) | Job | Behavior |
|---|---|---|
| 4:02 PM | `ohlcv_refresh` | Calls `bootstrap_ohlcv(include_today=True)` after market close |
| 4:05 PM | `exit_monitor` | Also calls `bootstrap_ohlcv(include_today=True)` as safety net before checking positions |
| 4:10 PM | `options_snapshot` | Captures live options chains from Tradier for large-cap tickers |

This ensures the exit monitor always operates on fresh data and options chains are captured while Tradier still serves closing data.

> **Note:** None of these scheduled jobs touch `ticker_meta.parquet`. Metadata refresh is a separate, infrequent operation.

### Historical Data Scripts

| Script | Purpose |
|---|---|
| `scripts/ingest_data.py` | Primary OHLCV + meta bootstrap from Polygon |
| `scripts/ingest_options.py` | Options chain snapshots from Tradier (daily or backfill) |
| `scripts/ingest_infiniti.py` | Ingest historical OHLCV from local infiniti Parquet store |
| `scripts/bridge_ohlcv_gap.py` | Fill gaps between infiniti data and Polygon data |
| `scripts/backtest_pullbacks.py` | Scan history for pullback events, compute per-ticker bounce profiles |

## OptionsChainStore

**Directory:** `data/options_chains/{TICKER}.parquet`

**Sources:**
- `backend/src/tyche/market_data/data_store.py` (store)
- `backend/src/tyche/workflow/options_snapshot.py` (ingestion workflow)
- `backend/scripts/ingest_options.py` (CLI)

Stores daily snapshots of live options chain data from Tradier. Each ticker has its own Parquet file containing all snapshot dates. Designed to accumulate data over time for backtest validation using real market premiums.

### Schema (per-ticker file)

| Column | Type | Description |
|---|---|---|
| snapshot_date | date32 | Date the chain was captured |
| expiration | date32 | Contract expiration date |
| strike | float64 | Strike price |
| option_type | string | `put` or `call` |
| bid | float64 | Best bid price |
| ask | float64 | Best ask price |
| mid | float64 | Midpoint of bid/ask |
| last | float64 | Last traded price |
| volume | int64 | Daily contract volume |
| open_interest | int64 | Open interest |
| implied_volatility | float64 | Implied volatility |
| delta | float64 | Delta Greek |
| gamma | float64 | Gamma Greek |
| theta | float64 | Theta Greek |
| vega | float64 | Vega Greek |
| rho | float64 | Rho Greek |
| underlying_price | float64 | Underlying stock price at snapshot time |

### Deduplication

Rows are deduplicated on `(snapshot_date, expiration, strike, option_type)` within each ticker's file. Re-running ingestion for the same date safely overwrites stale data.

### Key Operations

- `write_chains(ticker, contracts, snapshot_date)` — Write contracts for one ticker on one date
- `read_ticker(ticker, snapshot_date?)` — Read a ticker's chains, optionally for a specific date
- `get_nearest_snapshot_date(ticker, target_date, max_gap_days)` — Find nearest available snapshot
- `get_put_premium(ticker, snapshot_date, strike, dte, strike_tolerance_pct)` — Look up actual put premium for backtest
- `list_tickers()` — All tickers with stored chain data
- `list_snapshot_dates(ticker?)` — All snapshot dates across all or one ticker
- `get_stats()` — Summary: ticker count, snapshot date count, total rows

### Ingestion

Options chains are ingested from Tradier using the shared `run_options_snapshot()` workflow:

```bash
# Snapshot today's chains for all large-cap tickers in the OHLCV store
python scripts/ingest_options.py --from-ohlcv --min-market-cap 5e9

# Snapshot specific tickers
python scripts/ingest_options.py --tickers AAPL,MSFT,NVDA

# Dry run (estimate API calls without fetching)
python scripts/ingest_options.py --from-ohlcv --dry-run

# Show current store status
python scripts/ingest_options.py --status
```

### Scheduled Snapshot

| Time (ET) | Job | Behavior |
|---|---|---|
| 4:10 PM | `options_snapshot` | Captures put chains for all tickers in OHLCVStore with market cap ≥ $5B. Runs Mon-Fri after OHLCV refresh and exit monitor. |

Controlled by `TYCHE_OPTIONS_SNAPSHOT_ENABLED` (default `true`). The snapshot uses a token-bucket rate limiter capped at `options_snapshot_rpm` (default 120, Tradier hard limit).

### Integration with Backtests

The `MarketPremiumModel` in `backend/src/tyche/backtest/premium.py` uses `OptionsChainStore` to look up real put premiums for a given ticker, date, strike, and DTE. When no matching chain data is found (e.g., historical dates before snapshots began), it falls back to a configurable simulation model (default: `iv_proxy`).

```bash
# Run CSP backtest with real market premiums where available
python scripts/backtest_pullback_csp.py --premium-source market
```

The model reports hit/miss stats at the end, showing what fraction of trades used real data.

## Data Directory

All Parquet files live under `backend/data/` (configurable via `TYCHE_DATA_DIR`). This directory is gitignored to avoid committing large binary files.

```
backend/data/
├── ohlcv_daily/             # Per-ticker daily bars
│   ├── AAPL.parquet         # ~10KB per ticker (120 days of daily bars)
│   ├── MSFT.parquet
│   └── ... (~13,000 tickers)
├── intraday_5min/           # Per-ticker 5-min bars
│   ├── AAPL.parquet         # ~100-500KB per ticker (90 days of 5-min bars)
│   ├── MSFT.parquet
│   └── ... (~881 eligible tickers)
├── options_chains/          # Per-ticker options chain snapshots
│   ├── AAPL.parquet         # ~50-200KB per ticker (grows with daily snapshots)
│   ├── MSFT.parquet
│   └── ... (~1,100 large-cap tickers)
└── ticker_meta.parquet      # ~1MB (single file, ticker reference metadata)
```
