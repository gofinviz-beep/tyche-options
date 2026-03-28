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

## Intraday Time-of-Day Backtest

**Source:** `backend/scripts/backtest_intraday.py`

### Purpose

Empirically determines the optimal time of day for placing CSP orders. Rather than relying on general market wisdom, this backtest uses 5-minute intraday price data to simulate CSP entries at 30-minute intervals throughout the trading day and measures forward outcomes for each time slot.

### Methodology

1. **Find eligible stock-days:** Run the daily `ConvictionEngine` (same filters as production: $5B market cap, valid exchange, 3% extension cap, 5-10 day EMA streak) to identify all (ticker, date) pairs that were CSP-eligible during the backtest period.

2. **Load intraday data:** Read persisted 5-minute bars from `IntradayStore` for all eligible tickers.

3. **Simulate at each time slot:** For each eligible stock-day, sample the stock price at 13 time slots (every 30 minutes from 9:30 AM to 3:30 PM ET). At each slot:
   - Use the sampled price as the entry price
   - Calculate a 5% OTM strike
   - Assume 1.5% premium on notional
   - Track 8-day forward outcome using daily closes
   - Record win/loss, P&L, max drawdown, price position within the day's range, and volume profile

4. **Aggregate by time bucket:** Compute per-bucket statistics including win rate, average/median/total P&L, average max drawdown, price position, and cumulative volume percentage.

### Time Slots Analyzed (ET)

```
9:30 AM, 10:00 AM, 10:30 AM, 11:00 AM, 11:30 AM, 12:00 PM, 12:30 PM,
1:00 PM, 1:30 PM, 2:00 PM, 2:30 PM, 3:00 PM, 3:30 PM
```

### Results (90-day backtest, March 2026)

**Dataset:** 881 tickers, 4,849 eligible stock-days, 43,375 total simulations, 5,053,970 intraday bars.

| Time (ET) | Time (PT) | Trades | Win% | Avg P&L | Total P&L | Avg DD% |
|---|---|---|---|---|---|---|
| 9:30 AM | 6:30 AM | 4,849 | 77.6% | $75.60 | $366,571 | 2.81% |
| 10:00 AM | 7:00 AM | 4,849 | 77.6% | $75.76 | $367,374 | 2.80% |
| **10:30 AM** | **7:30 AM** | **4,849** | **77.4%** | **$76.64** | **$371,642** | **2.82%** |
| 11:00 AM | 8:00 AM | 4,849 | 77.4% | $76.36 | $370,291 | 2.84% |
| 11:30 AM | 8:30 AM | 4,848 | 77.1% | $74.35 | $360,429 | 2.87% |
| 12:00 PM | 9:00 AM | 4,849 | 77.1% | $74.21 | $359,833 | 2.89% |
| 12:30 PM | 9:30 AM | 4,849 | 77.4% | $75.31 | $365,197 | 2.85% |
| 1:00 PM | 10:00 AM | 4,848 | 77.7% | $74.72 | $362,220 | 2.87% |
| 1:30 PM | 10:30 AM | 1,394 | 75.0% | $31.11 | $43,365 | 3.10% |
| 2:00 PM | 11:00 AM | 977 | 74.1% | -$28.13 | -$27,479 | 2.94% |
| 2:30 PM | 11:30 AM | 781 | 72.2% | -$22.73 | -$17,756 | 3.23% |
| 3:00 PM | 12:00 PM | 741 | 73.8% | -$26.96 | -$19,974 | 3.01% |
| 3:30 PM | 12:30 PM | 692 | 69.8% | -$21.14 | -$14,628 | 3.44% |

### Key Findings

1. **Best single time slot: 10:30 AM ET (7:30 AM PT)** — Highest average P&L at $76.64/contract, 77.4% win rate, 2.82% average drawdown.

2. **The entire 9:30 AM - 1:00 PM ET window is strong.** Win rates hold at 77%+ and average P&L stays in the $74-77 range. The differences within this window are small, providing flexibility.

3. **After 1:00 PM ET, performance collapses.** Win rate drops from 77%+ to 69-75%, and average P&L turns negative. The 3:30 PM ET slot has the worst performance across all metrics.

4. **Volume matters.** By 1:00 PM ET, 99.7% of daily volume has traded. Afternoon slots have thin liquidity and wider spreads, explaining the degraded outcomes.

5. **Fewer trades fill in afternoon.** The sharp drop from ~4,849 to 1,394 trades at 1:30 PM indicates many stocks lack meaningful intraday data after midday.

6. **Price position stays neutral.** Morning entries occur near the middle of the day's range (49-51%), while afternoon entries drift slightly lower (47-50%).

### Recommendation

Place CSP orders between **9:30 AM - 1:00 PM ET (6:30 AM - 10:00 AM PT)**, with the optimal window being **10:00 - 11:00 AM ET (7:00 - 8:00 AM PT)**. Avoid placing orders after 1:00 PM ET.

### Running the Backtest

```bash
cd backend
source .venv/bin/activate

# Run using cached intraday data
python scripts/backtest_intraday.py

# Fetch missing intraday data first, then run
python scripts/backtest_intraday.py --fetch

# Limit date range
python scripts/backtest_intraday.py --from 2026-01-01

# Check cached data status
python scripts/backtest_intraday.py --status
```

Requires:
- `data/ohlcv_daily.parquet` — Daily OHLCV data
- `data/ticker_meta.parquet` — Ticker metadata with market caps
- `data/intraday_5min.parquet` — 5-minute intraday bars (fetched via `--fetch` or `ingest_data.py --intraday`)

### Data Ingestion for Intraday Bars

```bash
# Auto-discover CSP-eligible tickers and fetch their 5-min bars
python scripts/ingest_data.py --intraday

# Fetch specific tickers only
python scripts/ingest_data.py --intraday --intraday-tickers AAPL,MSFT,NVDA

# Check all store statuses including intraday
python scripts/ingest_data.py --status
```

### Re-running the Backtest

This backtest should be re-run periodically (monthly or quarterly) to validate that the optimal entry window has not shifted. Market microstructure changes, new regulations, or shifts in algorithmic trading patterns could alter the intraday dynamics.

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
