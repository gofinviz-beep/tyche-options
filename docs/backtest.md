# Backtest Methodology

**Source:** `backend/scripts/backtest_ema.py`

## Purpose

The backtest validates the 8/21 EMA conviction strategy using historical data with production-identical filters. It runs two levels of simulation:

1. **Per-trade simulation** — Tests individual CSP trades in isolation across all backtest days
2. **Capital-aware portfolio simulation** — Tracks a real capital pool with overlapping positions, compounding, and the MILP allocator

## Per-Trade Simulation

### Setup

For each trading day in the backtest window (after a 30-day warmup period):

1. **Universe filter:** Load TickerMetaStore, filter to market cap >= $5B and valid exchange
2. **OHLCV load:** Read all qualified tickers' price data up to that date
3. **Conviction engine:** Run `ConvictionEngine.analyze()` with production parameters (extension <= 3%, days above 5-10)
4. **Pick top N:** Take the top 10 CSP-eligible stocks per day (sorted by conviction)
5. **Simulate CSP:** For each pick, simulate selling a put at ~5% OTM strike

### Trade Simulation

For each CSP-eligible stock on a given day:

```
entry_price  = last closing price
strike       = entry_price * (1 - 0.05)    # 5% OTM
premium      = strike * 0.015 * 100        # assume 1.5% of notional
dte          = 8 trading days
```

Track the stock forward for 8 trading days:
- Record the minimum price during the holding period
- Calculate maximum drawdown from entry
- Determine if the stock stayed above the strike (CSP expires worthless = win)
- If breached: calculate assignment loss = `(strike - min_price) * 100`

### CSPSimulation Record

Each simulated trade records:
- Entry date, symbol, conviction level, trend state
- Entry price, strike, 8-EMA, 21-EMA
- Extension percentage, days above both EMAs, market cap
- Exit date, exit price, min price during, max drawdown
- Whether it stayed above strike, forward return percentage

### Metrics Reported

The backtest reports breakdowns across multiple dimensions:

- **Overall:** Win rate, average P&L, total P&L, number of trades
- **By conviction level:** high, medium, low
- **By trend state:** strong_uptrend, uptrend, pullback_to_8ema, pullback_to_21ema
- **By extension bucket:** 0-1%, 1-2%, 2-3%, 3-5%, 5-8%, 8%+
- **By days above EMAs:** 0-2d, 3-5d, 5-8d, 8-10d, 10-15d, 15d+
- **By entry day of week:** Monday through Friday

### Day-of-Week Analysis

The backtest includes empirical day-of-week performance:

```python
for dow in range(5):
    stats([s for s in simulations if s.entry_date.weekday() == dow], day_names[dow])
```

**Key finding:** Tuesday and Wednesday entries showed the best win rates and P&L. Thursday and Friday entries performed worst, likely due to weekend theta decay uncertainty and pre-weekend position adjustments.

## Capital-Aware Portfolio Simulation

The `run_capital_simulation()` function provides portfolio-level backtesting:

### How It Works

Starting with a fixed capital pool (default $100,000):

1. For each trading day in the backtest window:
   - **Close expired positions:** Positions whose 8-day DTE has elapsed are settled. Winning CSPs return collateral + premium. Losing CSPs return collateral + premium - assignment loss.
   - **Calculate available capital:** Total capital minus locked collateral from open positions.
   - **Build candidates:** Convert today's per-trade simulations into `ScoredCandidate` objects (skip symbols already held).
   - **Run MILP allocator:** Use a `PortfolioAllocator` constrained to `max_positions - open_positions` slots to select optimal new entries.
   - **Open new positions:** Lock collateral, record entry.

2. Track portfolio-level metrics:
   - **Equity curve:** Daily total equity (cash + locked collateral)
   - **Daily returns:** For Sharpe ratio computation
   - **Peak equity / max drawdown:** Running high-water mark
   - **Capital utilization:** Locked collateral as percentage of equity

### Portfolio Metrics Reported

| Metric | Description |
|---|---|
| Starting capital | Initial capital pool |
| Final equity | Ending portfolio value |
| Total return | (final - starting) / starting * 100 |
| Annualized return | Total return scaled to 252 trading days |
| Sharpe ratio | avg_daily_return / std_daily_return * sqrt(252) |
| Max drawdown | Largest peak-to-trough decline as percentage |
| Trades executed | Total number of CSP entries across all days |
| Premium collected | Sum of all premiums received |
| Assignment losses | Sum of all assignment losses |
| Net P&L | Final equity - starting capital |
| Avg capital utilization | Mean daily locked collateral as % of equity |
| Equity curve | Monthly checkpoint snapshots |

## Running the Backtest

```bash
cd backend
source .venv/bin/activate
python scripts/backtest_ema.py
```

Requires:
- `data/ohlcv_daily.parquet` — At least 120 days of OHLCV data
- `data/ticker_meta.parquet` — Ticker metadata with market caps

No API keys needed — the backtest runs entirely on local Parquet data.

## Configuration

| Constant | Value | Description |
|---|---|---|
| `DTE` | 8 | Days to expiration for simulated CSPs |
| `OTM_PCT` | 0.05 | Out-of-the-money percentage for strike |
| `MIN_PRICE` | $15 | Minimum stock price |
| `MIN_MARKET_CAP` | $5 Billion | Minimum market cap |
| `MIN_VOLUME` | 500,000 | Minimum average daily volume |
| `TOP_N_PER_DAY` | 10 | Maximum picks per day |
| `PREMIUM_PCT` | 0.015 | Assumed premium as % of notional |
| Starting capital | $100,000 | For capital-aware simulation |
| Max positions | 8 | For capital-aware simulation |
| Max concentration | 25% | For capital-aware simulation |
