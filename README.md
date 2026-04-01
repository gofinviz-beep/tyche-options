# Tyche Options

Options trading copilot for the Wheel Strategy (Cash-Secured Puts + Covered Calls) with integrated stock-buying on EMA pullbacks. Uses 8/21 EMA conviction-based stock screening, MILP portfolio optimization, LLM-assisted analysis, and data-driven exit signals (per-ticker historical bounce profiles) to manage both options trades and stock positions.

## Repository Structure

```
tyche-options/
├── backend/             Python 3.12+ FastAPI backend
│   ├── src/tyche/       Application code
│   │   ├── api/         FastAPI routes + middleware
│   │   ├── analysis/    Gemini LLM client + analysis agent
│   │   ├── broker/      Tradier client + mock broker
│   │   ├── conviction/  8/21 EMA conviction engine (features + CSP policy + compat wrapper)
│   │   ├── market_data/ Polygon client, data stores, earnings
│   │   ├── models/      SQLAlchemy ORM models (scan, conviction, backtest, positions)
│   │   ├── persistence/ Database engines + scan/conviction/position repositories
│   │   ├── risk/        Deterministic risk rules
│   │   ├── strategy/    CSP/CC engine + MILP allocator
│   │   ├── workflow/    Morning scan, exit monitor, order monitor, intent builder
│   │   ├── telemetry.py OpenTelemetry configuration
│   │   └── config.py    TYCHE_* env settings (pydantic-settings)
│   ├── tests/unit/      556 unit tests (70% coverage)
│   ├── scripts/         CLI tools (ingest_data, backtest_pullbacks, backtest_ema, live_scan)
│   └── db/              SQLite databases (gitignored)
├── frontend/            React + TypeScript + Vite
│   └── src/
│       ├── api/         Typed fetch wrapper with telemetry
│       ├── components/  Shared UI + ErrorBoundary
│       ├── hooks/       react-query hooks
│       ├── lib/         Telemetry batching
│       ├── pages/       Dashboard, Scanner, Conviction, Intents, etc.
│       └── navigation/  Modular sidebar
├── scripts/             Start/stop helpers
└── .cursor/rules/       Cursor AI rules (8 domain-specific .mdc files)
```

## Quick Start

### Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env       # Add API keys: TYCHE_TRADIER_*, TYCHE_POLYGON_*, TYCHE_GEMINI_*
python scripts/ingest_data.py --days 120 --meta   # Bootstrap market data
uvicorn tyche.app:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Both (convenience)

```bash
./scripts/start-all.sh     # Starts backend + frontend
./scripts/stop-all.sh      # Stops both
```

## Pipeline Overview

```
Market cap ≥ $5B → Exchange → Price ≥ $15 → Volume ≥ 500K
  → 8/21 EMA Conviction (trend + extension ≤ 3% + days-above 5-10)
  → Institutional ownership ≥ 40%
  → Tradier options chains (day-aware expiration targeting)
  → MILP Portfolio Allocator (delta-penalized risk weight)
  → Per-ticker parallel LLM analysis (Gemini)
  → Risk gate → OrderIntent → Persist to SQLite
```

## Key Design Decisions

- **LLM is qualitative only.** All numerical fields (strike, premium, collateral) come from broker data, never LLM.
- **Conviction-first filtering.** No Tradier API calls until a stock passes the EMA conviction engine.
- **Allocator runs before LLM.** MILP optimization uses delta penalty to compensate for missing LLM insight.
- **Combined conviction = min(EMA, LLM).** Never shows the optimistic signal when the other disagrees.
- **Preview-only by default.** Live order placement requires explicit opt-in.
- **Data-driven exit targets.** Per-ticker p75 bounce from historical backtest — not a one-size-fits-all percentage.
- **Stock positions are persisted.** Tracked in `backtest.db` with daily exit monitoring (profit target + 8-EMA stop loss).
- **Automated OHLCV refresh.** Daily at 4:02 PM ET after market close, with safety-net refresh before exit monitor.

## Configuration

All settings via `TYCHE_*` environment variables in `backend/.env`. See `backend/.env.example` for the full list.

| Variable | Required | Purpose |
|---|---|---|
| `TYCHE_TRADIER_API_TOKEN` | Yes | Tradier brokerage API token |
| `TYCHE_TRADIER_ACCOUNT_ID` | Yes | Tradier account ID |
| `TYCHE_POLYGON_API_KEY` | Yes | Polygon.io (historical data) |
| `TYCHE_GEMINI_API_KEY` | No | Google Gemini (LLM analysis) |
| `TYCHE_GCP_PROJECT_ID` | No | GCP project for Cloud Monitoring/Trace |

## Cursor AI Rules

Eight domain-specific rules in `.cursor/rules/`:

| Rule | Scope | Purpose |
|---|---|---|
| `architecture.mdc` | `backend/src/tyche/**` | Module map, pipeline, API routes, storage, scheduled jobs |
| `known-issues.mdc` | Always applied | Gotchas, workarounds, test notes |
| `trading-rules.mdc` | `backend/src/tyche/**` | Risk constraints, filter pipeline, day guidance |
| `conviction-rules.mdc` | `conviction/**`, `scripts/` | EMA thresholds, eligibility gates |
| `intent-risk-pipeline.mdc` | `workflow/`, `schemas/`, `api/routes/` | LLM guardrails, numerical validation |
| `testing-patterns.mdc` | `backend/tests/**` | Test conventions, fixtures, coverage targets |
| `frontend-patterns.mdc` | `frontend/src/**` | UI patterns, hooks, component conventions, stock positions UI |

Nested `AGENTS.md` files in `backend/` and `frontend/` provide directory-scoped context.
