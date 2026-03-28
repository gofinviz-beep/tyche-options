# Data Pipeline

**Sources:**
- `backend/src/tyche/market_data/data_store.py`
- `backend/src/tyche/market_data/polygon.py`

## Overview

Tyche uses a Parquet-first local data layer for all historical market data. Three stores serve different purposes:

- **OHLCVStore** — Per-ticker daily OHLCV bars for all US equities (used by conviction engine and backtest)
- **IntradayStore** — Per-ticker 5-minute intraday bars (used by intraday timing backtest)
- **TickerMetaStore** — Per-ticker metadata: market cap, exchange, type (used for universe filtering)

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

The `bootstrap_ohlcv()` function in `data_store.py` orchestrates the full data population:

```mermaid
flowchart TD
    Start["bootstrap_ohlcv()"] --> CheckExisting["Check OHLCVStore\nfor latest date"]
    CheckExisting -->|"No data"| FetchAll["Fetch 120 calendar days\n(~80 trading days)"]
    CheckExisting -->|"Has data"| FetchIncremental["Fetch from latest_date+1\nto yesterday"]
    FetchAll --> FetchLoop["For each weekday:\nPolygon get_grouped_daily()"]
    FetchIncremental --> FetchLoop
    FetchLoop --> WriteOHLCV["Write bars to\nohlcv_daily.parquet"]
    WriteOHLCV --> FetchMeta["Fetch ticker reference\nPolygon get_tickers()"]
    FetchMeta --> WriteMeta["Write metadata to\nticker_meta.parquet"]
    WriteMeta --> Done["Return stats:\ndates_fetched, bars_stored,\ntickers_found, tickers_meta"]
```

### Triggering Bootstrap

Via API:
```bash
curl -X POST http://localhost:8000/conviction/bootstrap
```

Via Python:
```python
from tyche.market_data.data_store import bootstrap_ohlcv, OHLCVStore, TickerMetaStore
from tyche.market_data.polygon import PolygonClient

polygon = PolygonClient(api_key="...")
store = OHLCVStore(data_dir="data")
meta = TickerMetaStore(data_dir="data")
result = await bootstrap_ohlcv(polygon, store, days=120, meta_store=meta)
```

### Incremental Updates

If the store already contains data, bootstrap only fetches dates after the latest stored date. This makes it safe to run repeatedly — it will only fetch missing days.

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
└── ticker_meta.parquet      # ~1MB (single file, ticker reference metadata)
```
