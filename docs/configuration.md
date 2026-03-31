# Configuration Reference

**Source:** `backend/src/tyche/config.py`

All settings are loaded from environment variables prefixed with `TYCHE_` using pydantic-settings. A `.env` file in the `backend/` directory is also supported.

## Broker

| Env Var | Type | Default | Description |
|---|---|---|---|
| `TYCHE_TRADIER_API_TOKEN` | str | `""` | Tradier API access token |
| `TYCHE_TRADIER_ACCOUNT_ID` | str | `""` | Tradier brokerage account ID |
| `TYCHE_TRADIER_SANDBOX` | bool | `true` | Use sandbox (paper) API. Set to `false` for production. |
| `TYCHE_TRADIER_BASE_URL` | str | `""` | Override broker base URL. Auto-selects sandbox/prod if empty. |

When `TRADIER_SANDBOX=true` (default), the base URL is `https://sandbox.tradier.com/v1`. When `false`, it is `https://api.tradier.com/v1`.

## LLM (Google Gemini)

| Env Var | Type | Default | Description |
|---|---|---|---|
| `TYCHE_GEMINI_API_KEY` | str | `""` | Google Gemini API key. If empty, LLM analysis is disabled. |
| `TYCHE_GEMINI_MODEL_FAST` | str | `gemini-3-flash-preview` | Fast model for quick analysis (fallback: gemini-2.5-flash) |
| `TYCHE_GEMINI_MODEL_DEEP` | str | `gemini-3.1-pro-preview` | Deep model for detailed reasoning (fallback: gemini-2.5-flash) |

## Market Data (Polygon.io)

| Env Var | Type | Default | Description |
|---|---|---|---|
| `TYCHE_POLYGON_API_KEY` | str | `""` | Polygon.io API key for historical data |
| `TYCHE_POLYGON_BASE_URL` | str | `https://api.polygon.io` | Polygon API base URL |
| `TYCHE_POLYGON_RATE_LIMIT_RPM` | int | `100` | Max requests per minute. Starter plan = 5, paid plans much higher. |

## Earnings Data

| Env Var | Type | Default | Description |
|---|---|---|---|
| `TYCHE_ALPHA_VANTAGE_KEY` | str | `demo` | Alpha Vantage API key for earnings calendar. "demo" works with rate limits. |
| `TYCHE_EARNINGS_OVERRIDES` | dict | `{}` | Manual earnings date overrides: `{"PL": "2026-06-15"}` |
| `TYCHE_EARNINGS_API_KEY` | str | `""` | Legacy alias for `ALPHA_VANTAGE_KEY` |

## Data Storage

| Env Var | Type | Default | Description |
|---|---|---|---|
| `TYCHE_DATA_DIR` | str | `data` | Directory for Parquet data files (relative to backend/) |
| `TYCHE_DB_DIR` | str | `db` | Directory for all SQLite database files (relative to backend/) |
| `TYCHE_DATABASE_URL` | str | `""` | SQLAlchemy async database URL. If empty, auto-resolves to `sqlite+aiosqlite:///{db_dir}/tyche.db` |
| `TYCHE_SCAN_RETENTION_COUNT` | int | `5` | Number of recent scans to retain. Older scans are cleaned up after each new scan. |

The `db/` directory contains all SQLite files:
- `tyche.db` — order intents
- `scans.db` — scan runs + pipeline stages
- `candidates.db` — scored option candidates
- `analyses.db` — LLM analysis records

The `data/` directory contains raw market data (Parquet files only).

## Universe Filtering

| Env Var | Type | Default | Description |
|---|---|---|---|
| `TYCHE_MIN_MARKET_CAP_MILLIONS` | float | `5000.0` | Minimum market cap in millions ($5B) |
| `TYCHE_MIN_AVG_VOLUME` | int | `500000` | Minimum 20-day average daily share volume |
| `TYCHE_MIN_STOCK_PRICE` | float | `15.0` | Minimum last closing price |
| `TYCHE_MIN_INSTITUTIONAL_PCT` | float | `0.40` | Minimum institutional ownership (40%) |
| `TYCHE_MIN_MARKET_CAP_BILLIONS` | float | `1.0` | Legacy market cap filter (used by risk engine) |

## Conviction Engine

| Env Var | Type | Default | Description |
|---|---|---|---|
| `TYCHE_EMA_FAST_PERIOD` | int | `8` | Fast EMA period (days) |
| `TYCHE_EMA_SLOW_PERIOD` | int | `21` | Slow EMA period (days) |
| `TYCHE_PULLBACK_PROXIMITY_PCT` | float | `2.0` | Max % distance to consider a "pullback" to an EMA |
| `TYCHE_MAX_EXTENSION_PCT` | float | `3.0` | Max % above 8-EMA for CSP eligibility (uptrend path only) |
| `TYCHE_MIN_DAYS_ABOVE_EMAS` | int | `5` | Minimum consecutive days above both EMAs (uptrend path only) |
| `TYCHE_MAX_DAYS_ABOVE_EMAS` | int | `10` | Maximum consecutive days above both EMAs (uptrend path only) |
| `TYCHE_BOOTSTRAP_DAYS` | int | `120` | Calendar days of history to fetch on bootstrap |

## Pullback CSP

| Env Var | Type | Default | Description |
|---|---|---|---|
| `TYCHE_PULLBACK_CSP_ENABLED` | bool | `true` | Enable the pullback CSP eligibility path. When disabled, only the uptrend path (Gate 3a) is active. |
| `TYCHE_MIN_PRIOR_STREAK` | int | `5` | Minimum days the stock must have been above both EMAs before the pullback for pullback CSP eligibility. |
| `TYCHE_PULLBACK_STRIKE_OFFSET_PCT` | float | `5.0` | For pullback CSPs, only consider strikes within this % below the support EMA. E.g., 5% means for a stock with 21-EMA at $100, strikes between $95 and $100 are considered. |

## Capital and Risk Limits

| Env Var | Type | Default | Description |
|---|---|---|---|
| `TYCHE_AVAILABLE_CAPITAL` | float | `100000.0` | Available cash for CSP collateral |
| `TYCHE_MAX_RISK_PER_TRADE_PCT` | float | `5.0` | Max risk per individual trade (% of capital) |
| `TYCHE_MAX_ACCOUNT_EXPOSURE_PCT` | float | `70.0` | Max total account exposure (% of capital) |
| `TYCHE_MAX_CONCENTRATION_PER_TICKER_PCT` | float | `25.0` | Max collateral in any single ticker (% of capital) |
| `TYCHE_MAX_OPEN_POSITIONS` | int | `8` | Maximum simultaneous open positions |
| `TYCHE_MAX_NEW_TRADES_PER_DAY` | int | `3` | Maximum new trades per day |
| `TYCHE_MAX_CONTRACTS_PER_POSITION` | int | `40` | Maximum contracts per single position |
| `TYCHE_PREVIEW_ONLY_MODE` | bool | `true` | When true, orders are previewed but not executed |

## Wheel Strategy Settings

| Env Var | Type | Default | Description |
|---|---|---|---|
| `TYCHE_CSP_TARGET_DTE_MIN` | int | `3` | Minimum DTE for CSP candidates |
| `TYCHE_CSP_TARGET_DTE_MAX` | int | `14` | Maximum DTE for CSP candidates |
| `TYCHE_CC_TARGET_DTE_MIN` | int | `3` | Minimum DTE for CC candidates |
| `TYCHE_CC_TARGET_DTE_MAX` | int | `14` | Maximum DTE for CC candidates |
| `TYCHE_MIN_ANNUALIZED_RETURN_PCT` | float | `15.0` | Minimum annualized return threshold |
| `TYCHE_MAX_EXPIRATION_DATES` | int | `2` | Maximum number of expiration dates to scan per ticker. Limits API calls and irrelevant data. |
| `TYCHE_STRIKE_RANGE_PCT` | float | `15.0` | Only consider put strikes within this % below the 8-EMA. E.g., 15% means for a stock with 8-EMA at $100, only strikes >= $85 are scanned. |
| `TYCHE_LLM_CONCURRENCY` | int | `5` | Maximum parallel LLM analysis calls during scanner pipeline. Controls Gemini API rate. |

## Workflow Scheduling

| Env Var | Type | Default | Description |
|---|---|---|---|
| `TYCHE_MORNING_SCAN_TIME` | str | `09:35` | Time to run morning scan (HH:MM, market hours) |
| `TYCHE_ORDER_MONITOR_INTERVAL_MIN` | int | `15` | Interval for order status monitoring (minutes) |
| `TYCHE_MIDDAY_REVIEW_TIME` | str | `12:30` | Time for midday position review |
| `TYCHE_EOD_JOURNAL_TIME` | str | `15:50` | Time for end-of-day journaling |

## Watchlist

| Env Var | Type | Default | Description |
|---|---|---|---|
| `TYCHE_WATCHLIST_SYMBOLS` | list[str] | `[]` | Comma-separated stock symbols. If empty, dynamic universe from Parquet data is used. |

## Example .env File

```bash
# Broker
TYCHE_TRADIER_API_TOKEN=your_tradier_token_here
TYCHE_TRADIER_ACCOUNT_ID=your_account_id
TYCHE_TRADIER_SANDBOX=false

# Market Data
TYCHE_POLYGON_API_KEY=your_polygon_key_here

# LLM (optional)
TYCHE_GEMINI_API_KEY=your_gemini_key_here

# Storage (auto-resolves; override only if needed)
TYCHE_DB_DIR=db
TYCHE_DATABASE_URL=

# Risk
TYCHE_AVAILABLE_CAPITAL=100000.0
TYCHE_MAX_OPEN_POSITIONS=8
TYCHE_MAX_CONCENTRATION_PER_TICKER_PCT=25.0
TYCHE_PREVIEW_ONLY_MODE=true

# Conviction (uptrend path)
TYCHE_MAX_EXTENSION_PCT=3.0
TYCHE_MIN_DAYS_ABOVE_EMAS=5
TYCHE_MAX_DAYS_ABOVE_EMAS=10

# Pullback CSP (primary path)
TYCHE_PULLBACK_CSP_ENABLED=true
TYCHE_MIN_PRIOR_STREAK=5
TYCHE_PULLBACK_STRIKE_OFFSET_PCT=5.0

# Scanner Options
TYCHE_MAX_EXPIRATION_DATES=2
TYCHE_STRIKE_RANGE_PCT=15.0
TYCHE_LLM_CONCURRENCY=5
TYCHE_SCAN_RETENTION_COUNT=5
```
