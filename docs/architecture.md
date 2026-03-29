# Tyche Options — Architecture

**Status:** Living document (reflects the implemented system as of March 2026)

## Overview

Tyche Options is a laptop-based options trading copilot that combines deterministic screening rules with LLM-assisted analysis to identify Cash-Secured Put (CSP) and Covered Call (CC) opportunities using the Wheel Strategy. The system is built on a conviction-first architecture: stocks must pass through a multi-stage filter pipeline grounded in 8/21 EMA trend analysis before any options scanning occurs.

## Technology Stack

| Layer | Technology |
|---|---|
| API | FastAPI, Pydantic, uvicorn |
| Broker (real-time) | Tradier API — quotes, options chains, account ops, order execution |
| Market data (historical) | Polygon.io — grouped daily OHLCV bars, ticker reference metadata |
| Data stores | Apache Parquet via PyArrow (OHLCVStore, TickerMetaStore) |
| Conviction engine | pandas, numpy — 8/21 EMA trend classification |
| Portfolio optimization | scipy.optimize.milp (HiGHS MILP solver) |
| LLM analysis | Google Gemini (gemini-3-flash-preview / gemini-3.1-pro-preview) via google-genai |
| Database (operational) | SQLite + SQLAlchemy 2.0 async + Alembic |
| Scheduling | APScheduler |
| Logging | structlog (JSON structured logging) |
| Language | Python 3.12+ |

## Data Flow

```mermaid
flowchart TB
    subgraph bootstrap [Bootstrap — runs once / incrementally]
        PolygonBars["Polygon.io\nGrouped Daily Bars"] -->|"120 calendar days\nweekdays only"| OHLCVStore["OHLCVStore\nohlcv_daily.parquet"]
        PolygonRef["Polygon.io\nTicker Reference"] -->|"market cap, exchange, type"| TickerMeta["TickerMetaStore\nticker_meta.parquet"]
    end

    subgraph scan [Morning Scan / Live Scan Pipeline]
        TickerMeta -->|"market cap >= $5B\nvalid exchange"| UnivFilter["Universe Filter"]
        UnivFilter -->|"qualified tickers"| ConvEng["ConvictionEngine\n8/21 EMA"]
        OHLCVStore -->|"OHLCV DataFrames"| ConvEng
        ConvEng -->|"CSP-eligible tickers\nextension<=3%, 5-10d streak"| TradierQuotes["Tradier API\nLive Quotes"]
        TradierQuotes --> OptionsChain["Tradier API\nOptions Chains"]
        OptionsChain -->|"CSP candidates"| Allocator["PortfolioAllocator\nMILP Optimizer"]
        TradierPos["Tradier API\nBroker Positions"] -->|"CC candidates"| Allocator
        Allocator -->|"optimal allocation"| Output["Trade Recommendations"]
    end

    subgraph analysis [LLM Analysis — optional]
        Output --> Gemini["Google Gemini\nRanking + Rationale"]
        Gemini --> Approval["Human Approval"]
    end
```

## Component Map

```
backend/
├── scripts/
│   ├── backtest_ema.py          # Backtest: per-trade sim + capital-aware portfolio sim
│   └── live_scan.py             # Standalone live scanner (CLI, uses Tradier + conviction)
├── src/tyche/
│   ├── app.py                   # FastAPI application factory + lifespan
│   ├── config.py                # TycheSettings (all TYCHE_* env vars via pydantic-settings)
│   ├── exceptions.py            # Custom exception hierarchy
│   ├── logging.py               # structlog configuration
│   │
│   ├── api/
│   │   ├── deps.py              # Dependency injection — singleton providers for all services
│   │   └── routes/
│   │       ├── account.py       # GET /account/balances, /account/positions
│   │       ├── conviction.py    # GET /conviction/signals, POST /conviction/bootstrap
│   │       ├── events.py        # SSE endpoint for real-time events
│   │       ├── intents.py       # POST /intents/generate (order intent generation)
│   │       ├── monitor.py       # GET /monitor/status (active position monitor)
│   │       ├── orders.py        # POST /orders/preview, /orders/place
│   │       ├── scanner.py       # POST /scanner/scan (morning scan trigger)
│   │       ├── system.py        # GET /health, /system/status
│   │       └── watchlist.py     # GET/PUT /watchlist
│   │
│   ├── analysis/
│   │   ├── agent.py             # AnalysisAgent — orchestrates LLM calls for CSP analysis
│   │   ├── client.py            # GeminiClient — google-genai wrapper
│   │   ├── grounding.py         # Grounding rules injected into LLM prompts
│   │   └── prompts.py           # Prompt templates for CSP/CC analysis
│   │
│   ├── broker/
│   │   ├── base.py              # BrokerClient ABC + data models (Quote, Position, etc.)
│   │   ├── mock.py              # MockBroker for dev/testing
│   │   ├── polygon_adapter.py   # Polygon-to-BrokerClient adapter (unused in prod)
│   │   └── tradier/
│   │       ├── client.py        # TradierClient — full Tradier API implementation
│   │       └── symbols.py       # OCC symbol parsing utilities
│   │
│   ├── conviction/
│   │   └── engine.py            # ConvictionEngine — 8/21 EMA trend + conviction scoring
│   │
│   ├── market_data/
│   │   ├── data_store.py        # OHLCVStore + TickerMetaStore (Parquet) + bootstrap_ohlcv()
│   │   ├── earnings.py          # EarningsCalendarClient (Alpha Vantage)
│   │   ├── institutional.py     # Institutional ownership filter
│   │   ├── polygon.py           # PolygonClient — grouped daily, ticker reference, market caps
│   │   └── universe.py          # UniverseBuilder — watchlist screening
│   │
│   ├── models/                  # SQLAlchemy ORM models (account, candidate, journal, etc.)
│   ├── persistence/             # Database session management
│   ├── schemas/                 # Pydantic request/response schemas
│   │
│   ├── risk/
│   │   ├── engine.py            # RiskEngine — evaluates candidates against rule chain
│   │   ├── kill_switch.py       # Emergency kill switch
│   │   └── rules.py             # Individual risk rules (cash collateral, max positions, etc.)
│   │
│   ├── strategy/
│   │   ├── allocator.py         # PortfolioAllocator — MILP optimizer (scipy)
│   │   ├── engine.py            # StrategyEngine — orchestrates CSP/CC scanning
│   │   └── strategies/
│   │       ├── base.py          # ScoredCandidate dataclass + strategy ABC
│   │       ├── cash_secured_put.py  # CSP strategy implementation
│   │       └── covered_call.py      # CC strategy implementation
│   │
│   └── workflow/
│       ├── active_monitor.py    # Real-time position/order monitoring
│       ├── eod_journal.py       # End-of-day journaling
│       ├── intent_builder.py    # Order intent generation from recommendations
│       ├── morning_scan.py      # Full morning scan pipeline orchestration
│       ├── order_monitor.py     # Open order tracking
│       ├── scheduler.py         # APScheduler-based workflow scheduling
│       └── wheel_tracker.py     # Wheel strategy lifecycle tracking
│
├── tests/unit/                  # pytest unit tests for all major components
├── data/
│   ├── ohlcv_daily.parquet      # Cached OHLCV daily bars (gitignored)
│   └── ticker_meta.parquet      # Cached ticker metadata (gitignored)
└── pyproject.toml               # Dependencies and project metadata
```

## Key Architectural Patterns

### Dependency Injection

All service instances are created as singletons in `api/deps.py` and injected via FastAPI's `Depends()`. This includes broker clients, conviction engine, data stores, risk engine, portfolio allocator, and the LLM analysis agent. A `reset_all()` function clears all singletons for testing.

### Hybrid Market Data

Two separate APIs serve different purposes:
- **Polygon.io** provides historical daily OHLCV bars (grouped daily endpoint) and ticker reference metadata (market cap, exchange, type). This data is cached locally in Parquet files and used by the conviction engine and backtest.
- **Tradier** provides real-time quotes, options chains, account balances, positions, and order execution. It is the broker of record.

### Conviction-First Filtering

The conviction engine acts as the primary gate. No stock reaches options scanning without first passing through:
1. Universe filters (market cap, exchange, price, volume)
2. EMA trend classification
3. CSP eligibility check (trend state + extension cap + days-above streak)

This reduces API calls to Tradier and ensures only high-conviction candidates are evaluated.

### Parquet-First Data Layer

Historical data is stored in local Parquet files rather than a database. This enables:
- Fast columnar reads for EMA computation across thousands of tickers
- Easy backtest integration (same data, same filters)
- No database migration needed for schema changes to analytical data
- Incremental bootstrap (only fetch missing dates)

## Scanner vs Conviction Engine

These are two distinct scans that serve different purposes in the pipeline:

### Conviction Engine Scan (`GET /conviction/scan`)

**Purpose:** EMA-based trend analysis only — no broker calls, no options chains, no LLM.

- Reads from local OHLCV Parquet data (no network calls)
- Computes 8/21 EMAs and classifies trend states
- Returns CSP-eligible tickers with conviction levels
- Fast (sub-second for full universe of 13K+ tickers)
- Input: watchlist symbols, or blank for full universe screen
- Output: trend state, conviction level, EMA values, eligibility flag

**When to use:** To check which stocks currently have strong technicals before deciding what to scan with the full Scanner.

### Scanner (`POST /scanner/scan`)

**Purpose:** Full end-to-end scan pipeline — broker calls, options chains, LLM analysis, intent creation.

- Calls Tradier API for live quotes and options chains (network-dependent)
- Runs the conviction engine internally as a filter
- Fetches real option contracts with bid/ask/greeks
- Sends candidates to LLM (Gemini) for thesis/risk analysis
- Runs the intent risk pipeline (deterministic risk gate)
- Creates OrderIntent records in the database
- Slow (30s–2min depending on universe size and API latency)
- Input: watchlist symbols, or blank for dynamic universe discovery
- Output: scored candidates, LLM analyses, created intents

**When to use:** To generate actionable trade recommendations with real pricing that you can approve/reject on the Intents page.

### Pipeline Relationship

```
Conviction Scan ──► trend signals (informational)

Scanner ──► conviction filter ──► broker quotes ──► options chains
        ──► LLM analysis ──► risk gate ──► OrderIntents (actionable)
```

The Scanner calls the Conviction Engine internally — you don't need to run conviction first.

## Related Documentation

- [Conviction Engine Rules](conviction-engine.md)
- [Intent Risk Pipeline](intent-risk-pipeline.md)
- [Portfolio Allocator](portfolio-allocator.md)
- [Data Pipeline](data-pipeline.md)
- [Live Scan Workflow](live-scan.md)
- [Backtest Methodology](backtest.md)
- [Configuration Reference](configuration.md)
