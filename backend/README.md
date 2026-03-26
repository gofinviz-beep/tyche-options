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

Fetch 120 days of historical OHLCV data and ticker metadata from Polygon:

```bash
# Via API
uvicorn tyche.app:app --reload
curl -X POST http://localhost:8000/conviction/bootstrap

# Or via Python
python -c "
import asyncio
from tyche.market_data.data_store import bootstrap_ohlcv, OHLCVStore, TickerMetaStore
from tyche.market_data.polygon import PolygonClient
from tyche.config import get_settings

async def main():
    s = get_settings()
    p = PolygonClient(api_key=s.polygon_api_key)
    r = await bootstrap_ohlcv(p, OHLCVStore(data_dir=s.data_dir), meta_store=TickerMetaStore(data_dir=s.data_dir))
    print(r)
    await p.close()

asyncio.run(main())
"
```

### 4. Run

```bash
uvicorn tyche.app:app --reload
```

## Scripts

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
