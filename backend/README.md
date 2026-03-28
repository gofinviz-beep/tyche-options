# Tyche Options — Backend

Options trading copilot for the Wheel Strategy (Cash-Secured Puts + Covered Calls). Uses 8/21 EMA conviction-based screening, MILP portfolio optimization, and LLM-assisted analysis to identify high-probability options trades on quality large-cap stocks.

## Quick Start

### 1. Install

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure

Copy the example env file and fill in your API keys:

```bash
cp .env.example .env
```

Required keys:
- `TYCHE_TRADIER_API_TOKEN` — Tradier brokerage API token
- `TYCHE_TRADIER_ACCOUNT_ID` — Tradier account ID
- `TYCHE_POLYGON_API_KEY` — Polygon.io API key (for historical data)

Optional:
- `TYCHE_GEMINI_API_KEY` — Google Gemini (enables LLM analysis)

See [docs/configuration.md](../docs/configuration.md) for the full settings reference.

### 3. Bootstrap Data

Fetch historical OHLCV data and ticker metadata from Polygon:

```bash
# Full bootstrap (120 days of history + ticker metadata)
python scripts/ingest_data.py --days 120 --meta

# Or fetch just missing days (auto-detects latest date in store)
python scripts/ingest_data.py

# Check what's in the store
python scripts/ingest_data.py --status
```

### 4. Run

```bash
uvicorn tyche.app:app --reload
```

## Scripts

### Data Ingestion

Fetch and manage OHLCV daily bars, 5-minute intraday bars, and ticker metadata from Polygon.io:

```bash
python scripts/ingest_data.py                             # Fetch missing daily bars
python scripts/ingest_data.py --from 2026-03-20           # From a specific date
python scripts/ingest_data.py --from 2026-03-20 --to 2026-03-25  # Specific range
python scripts/ingest_data.py --days 120 --meta           # Full bootstrap with metadata
python scripts/ingest_data.py --intraday                  # Also fetch 5-min bars for eligible tickers
python scripts/ingest_data.py --intraday --intraday-tickers AAPL,MSFT  # Specific tickers
python scripts/ingest_data.py --status                    # Show all store statuses
```

### Live Scan

Run the full conviction-to-allocation pipeline against live market data:

```bash
python scripts/live_scan.py
```

Identifies today's CSP and CC opportunities and produces an optimal portfolio allocation.

### Backtest

Validate the conviction strategy using historical data:

```bash
python scripts/backtest_ema.py
```

Runs per-trade simulations and a capital-aware portfolio simulation with equity curve and Sharpe ratio.

### Intraday Timing Backtest

Determine the optimal time of day for CSP entries using 5-minute intraday data:

```bash
python scripts/backtest_intraday.py                       # Run using cached intraday data
python scripts/backtest_intraday.py --fetch               # Fetch missing intraday data first
python scripts/backtest_intraday.py --from 2026-01-01     # Limit backtest date range
python scripts/backtest_intraday.py --status              # Show cached intraday data status
```

Samples price at 30-minute intervals (9:30 AM to 3:30 PM ET) and simulates CSP outcomes for each time slot.

## Tests

```bash
pytest
```

## Architecture

The system follows a conviction-first pipeline:

```
Universe filters (market cap, exchange, price, volume)
    → ConvictionEngine (8/21 EMA trend + extension cap + days-above streak)
    → Tradier (live quotes + options chains)
    → PortfolioAllocator (MILP optimizer)
    → Human approval → Order execution
```

Key technologies: FastAPI, Tradier, Polygon.io, Google Gemini, Apache Parquet, scipy MILP.

## Documentation

Comprehensive documentation lives in the `docs/` folder:

- [Architecture Overview](../docs/architecture.md) — System design, component map, data flow
- [Conviction Engine](../docs/conviction-engine.md) — 8/21 EMA rules, filters, backtest rationale
- [Portfolio Allocator](../docs/portfolio-allocator.md) — MILP formulation, constraints, risk weights
- [Data Pipeline](../docs/data-pipeline.md) — OHLCVStore, TickerMetaStore, Polygon bootstrap
- [Live Scan](../docs/live-scan.md) — Live scanning workflow and output format
- [Backtest](../docs/backtest.md) — Simulation methodology and metrics
- [Configuration](../docs/configuration.md) — Complete TYCHE_* environment variable reference
