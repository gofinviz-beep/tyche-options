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
| `TYCHE_DATABASE_URL` | str | `sqlite+aiosqlite:///tyche.db` | SQLAlchemy async database URL |

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
| `TYCHE_MAX_EXTENSION_PCT` | float | `3.0` | Max % above 8-EMA for CSP eligibility |
| `TYCHE_MIN_DAYS_ABOVE_EMAS` | int | `5` | Minimum consecutive days above both EMAs |
| `TYCHE_MAX_DAYS_ABOVE_EMAS` | int | `10` | Maximum consecutive days above both EMAs |
| `TYCHE_BOOTSTRAP_DAYS` | int | `120` | Calendar days of history to fetch on bootstrap |

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

# Risk
TYCHE_AVAILABLE_CAPITAL=100000.0
TYCHE_MAX_OPEN_POSITIONS=8
TYCHE_MAX_CONCENTRATION_PER_TICKER_PCT=25.0
TYCHE_PREVIEW_ONLY_MODE=true

# Conviction
TYCHE_MAX_EXTENSION_PCT=3.0
TYCHE_MIN_DAYS_ABOVE_EMAS=5
TYCHE_MAX_DAYS_ABOVE_EMAS=10
```
