# Backend Agent Instructions

## Essentials

- Python 3.12+, FastAPI, pydantic-settings, structlog, OpenTelemetry
- Always run from the `backend/` directory — all relative paths assume it as cwd
- Use absolute imports: `from tyche.config import TycheSettings` (never relative)
- All settings via `TYCHE_*` env vars; `.env` is read by pydantic-settings directly — do NOT `source .env`

## Module Boundaries

- **Routes** (`api/routes/`) call services via `Depends()` from `api/deps.py` — never instantiate services directly
- **Workflows** (`workflow/`) orchestrate multi-step pipelines — they call engines, brokers, and agents
- **Engines** (`conviction/`, `strategy/`, `risk/`) are testable in isolation. Conviction uses a three-layer architecture: `features.py` (EMA computation + cache), `csp_policy.py` (stateless CSP gates), `engine.py` (backward-compat wrapper). All existing imports from `tyche.conviction.engine` continue to work.
- **Strategy** (`strategy/`) — `engine.py` parallelizes per-ticker scans via `asyncio.gather` + semaphore(10). `cash_secured_put.py` scores with 6 factors: `annualized_return × liquidity × dte × vrp × iv_rank × trend_confirm`. `allocator.py` adds mega-cap bonus to MILP risk weights.
- **Clients** (`broker/tradier/`, `analysis/client.py`, `market_data/polygon.py`) wrap external APIs with retry/error handling
- **Persistence** (`persistence/`) uses distributed SQLite with named engines — `register_engine("scans", url)` → `get_session("scans")`. Includes `position_repository.py` (stock positions), `backtest_repository.py` (pullback profiles), `conviction_repository.py` (snapshots/transitions + `get_latest_snapshot_date()` for holiday-safe date fallback).
- **Models** (`models/`) — `backtest.py` has both backtest data (`PullbackEvent`, `TickerPullbackProfile`) and position tracking (`StockPosition`, `ExitSignal`)

## Data Sources

- **Polygon.io** — historical OHLCV data, ticker metadata (bootstrap/backtest only)
- **Tradier** — live quotes, options chains, account operations, order execution
- **Gemini LLM** — qualitative analysis only; all numbers come from broker data via `_resolve_numeric()`

## Key Conventions

- All API routes are mounted under `/api/v1`; health endpoints at root (`/health`, `/health/ready`)
- LLM analysis is optional (off by default, toggle in Scanner UI), per-ticker parallel with semaphore control (`llm_concurrency` setting)
- Scanner pipeline has 10 timed stages — each records OTel histogram metrics
- `PipelineStage.duration_ms` and `MorningScanResult.total_duration_ms` track performance
- Error branches in workflows are swallowed with logging + OTel counter — pipeline continues
- `MockBroker` in `broker/mock.py` provides deterministic test data (PL + AAPL)

## Testing

- 952 unit tests in `tests/unit/`, run with `pytest`
- External APIs are always mocked — no network calls in tests
- Use `AsyncMock` for async broker/LLM calls, `MagicMock` for data stores
- `morning_scan.py`, `analysis/client.py` have 100% coverage; `exit_monitor.py` at 95%
- Conviction tests use `_fresh_uptrend()` helper to generate valid EMA streak data

## Observability

- structlog JSON with OTel `trace_id`/`span_id` injected into every log event
- OpenTelemetry histograms: `scanner.stage.duration`, `http.server.request.duration`, `llm.call.duration`
- OpenTelemetry counters: `scanner.errors`, `api.errors`
- GCP exporters when `TYCHE_GCP_PROJECT_ID` is set; console exporters otherwise
