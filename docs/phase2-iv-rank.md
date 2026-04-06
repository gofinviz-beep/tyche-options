# Phase 2: IV Rank from Historical Options Data

## Status: Not Started

## Prerequisites

- Massive.com Options plan ($29/month) — provides 2 years of historical options contract data
- Tradier is already capturing daily options snapshots with `implied_volatility` in the schema

## Key Definitions

- **IV (Implied Volatility):** Market's expected future price movement, embedded in option prices. Backed out from the option's market price via Black-Scholes inverse. Higher IV = options are more expensive. Already stored in `OPTIONS_CHAIN_SCHEMA.implied_volatility` from Tradier snapshots.
- **RV (Realized Volatility):** Actual historical price movement. Annualized std dev of daily log returns from OHLCV closes. Already computed by `IVProxyPremiumModel._realised_vol()` in `backtest/premium.py`.
- **VRP (Volatility Risk Premium):** IV minus RV. When positive (typical), options are overpriced vs actual movement — selling premium has positive expected value. Wider VRP = better time to sell CSPs.
- **IV Rank:** `(current_IV - 52wk_low_IV) / (52wk_high_IV - 52wk_low_IV)`. Ranges 0–100. IV Rank > 50 means IV is in the upper half of its annual range — premium is rich relative to history. Research (Tastytrade 10+ year study) shows selling CSPs when IV Rank > 50 improves win rates by 5–10% and average P&L by 20–30%.

## Data Ingestion

New script: `backend/scripts/ingest_options_history.py`

For each ticker in the filtered universe (market cap >= $4B, institutional ownership >= 60%):
1. List historical put contracts via Massive.com `/v3/reference/options/contracts`
2. For each trading day, identify the nearest ATM put with ~30 DTE
3. Fetch contract daily OHLCV via `/v2/aggs/ticker/{optionsTicker}/range/1/day/{from}/{to}`
4. Back-compute IV from option close price using Black-Scholes inverse (`py_vollib` or `scipy.optimize.brentq`) — skip this step if the API returns IV directly
5. Write to `OptionsChainStore` (existing schema already has `implied_volatility` column)

## DerivedMetricsStore

New store: `backend/src/tyche/market_data/derived_store.py`

Per-ticker Parquet files at `data/derived/{TICKER}.parquet`:
- `date` — trading day
- `atm_iv` — ATM put implied volatility
- `iv_rank` — 52-week IV Rank (0–100)
- `iv_percentile` — % of past-year days with lower IV (0–100)
- `rv_20d` — 20-day realized volatility (annualized)
- `vrp` — IV minus RV

Pre-computed during ingestion because computing IV Rank requires loading full options history per ticker — too expensive for on-the-fly conviction engine computation. Updated incrementally as new Tradier snapshots arrive.

## Integration

Add `iv_rank: float | None` to `FeatureSignal`. Read from `DerivedMetricsStore` for the `as_of_date`. When no options data exists, field is `None`. Expose as another filterable column in the conviction table UI (same pattern as 50-EMA and RSI).

Optionally, add an IV Rank gate to `CSPEligibilityPolicy` behind a config flag (`require_iv_rank: bool = False`, `min_iv_rank: float = 50.0`). Off by default until data is available and backtested.

## Why 2 Years Is Enough

- IV Rank uses a 52-week (1 year) rolling window
- 2 years gives 1 year of warm-up for the IV Rank computation + 1 full year of tradeable signals to backtest
- Tradier daily snapshots already include IV — history grows daily for free after the initial 2-year backfill
