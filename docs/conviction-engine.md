# Conviction Engine — 8/21 EMA Rules

**Source:** `backend/src/tyche/conviction/engine.py`

## Purpose

The conviction engine is the primary stock screening gate. It computes 8-day and 21-day Exponential Moving Averages on daily closes, classifies the trend state, and determines whether a stock is eligible for selling Cash-Secured Puts. Every threshold has been validated through backtesting (670+ trades over 90 days).

## Trend State Classification

The engine classifies each stock into one of seven states based on price position relative to EMAs and their slopes:

| State | Condition | CSP Eligible |
|---|---|---|
| `strong_uptrend` | Price above both EMAs, both slopes positive, price >1% above 8-EMA | Yes |
| `uptrend` | Price above both EMAs (not strong) | Yes |
| `pullback_to_8ema` | Price above 21-EMA but below 8-EMA, within 2% of 8-EMA | Yes |
| `pullback_to_21ema` | Price near 21-EMA (within 2%), 21-EMA slope positive | Yes |
| `consolidation` | Price between EMAs but not near either | No |
| `downtrend` | Price below both EMAs | No |
| `insufficient_data` | Fewer than 50 bars available | No |

### Classification Logic

```python
if price > ema_8 and price > ema_21:
    if both_slopes_up and pct_to_8 > 1.0:
        return STRONG_UPTREND
    return UPTREND
elif price > ema_21 and price <= ema_8:
    if within 2% of 8-EMA:  return PULLBACK_TO_8EMA
    if within 2% of 21-EMA: return PULLBACK_TO_21EMA
    return CONSOLIDATION
elif price <= ema_21 and within 2% of 21-EMA and slope_21 > 0:
    return PULLBACK_TO_21EMA
elif price < ema_8 and price < ema_21:
    return DOWNTREND
else:
    return CONSOLIDATION
```

## Conviction Scoring

Each trend state maps to a conviction level used for risk weighting in the portfolio allocator:

| Trend State | Conviction | Condition |
|---|---|---|
| `strong_uptrend` | **high** | if streak >= 5 days |
| `strong_uptrend` | medium | if streak < 5 days |
| `uptrend` | medium | if 21-EMA slope > 0 |
| `uptrend` | low | if 21-EMA slope <= 0 |
| `pullback_to_21ema` | **high** | if 21-EMA slope > 0 AND volume declining on pullback |
| `pullback_to_21ema` | medium | if 21-EMA slope > 0 |
| `pullback_to_21ema` | low | otherwise |
| `pullback_to_8ema` | medium | if 21-EMA slope > 0 |
| `pullback_to_8ema` | low | otherwise |
| `consolidation` | low | always |
| `downtrend` | none | always |

## CSP Eligibility Filters

A stock must pass **all three gates** to be CSP-eligible:

### Gate 1: Trend State

Must be one of: `strong_uptrend`, `uptrend`, `pullback_to_8ema`, `pullback_to_21ema`.

### Gate 2: Extension Cap (<= 3%)

The stock's price must not be more than 3% above its 8-day EMA.

```
extension_pct = (price - ema_8) / ema_8 * 100
if extension_pct > 3.0: csp_eligible = False
```

**Backtest evidence:** Stocks extended >3% above 8-EMA showed significantly worse CSP outcomes. The 1-2% extension bucket had a 69% win rate vs. 44% for the 5-8% bucket. Over-extended stocks have high snapback risk — selling puts near the top leads to frequent assignment.

### Gate 3: Days Above Both EMAs (5-10 days)

The stock must have been continuously above both EMAs for 5 to 10 consecutive trading days.

```
if not (5 <= days_above_both_emas <= 10): csp_eligible = False
```

**Rationale:**
- **< 5 days:** Trend not confirmed. Too early to have confidence the uptrend is real.
- **> 10 days:** Overdue for a reversal or consolidation. The best CSP entries are in the sweet spot of a confirmed but not exhausted trend.

## Pre-Conviction Universe Filters

Before the conviction engine runs, stocks are filtered by:

| Filter | Threshold | Source |
|---|---|---|
| Market cap | >= $5 Billion | TickerMetaStore (Polygon) |
| Exchange | NYSE, NASDAQ (XNYS, XNAS, XNMS, XASE, ARCX, BATS) | TickerMetaStore |
| Price | >= $15 | OHLCVStore (latest close) |
| Average volume | >= 500,000 shares/day | OHLCVStore (20-day avg) |
| Minimum bars | >= 50 trading days of data | OHLCVStore |

## Volume Declining on Pullback

An additional bullish signal checked during conviction assessment. If the stock is pulling back toward the 8-EMA with declining volume (below average), it suggests sellers are exhausted — a favorable setup for CSP selling.

Checked over a 5-day lookback window:
- Is any recent close below the 8-EMA? (pullback detected)
- Is average recent volume below the prior 10-day average? (volume declining)

Both must be true. This upgrades `pullback_to_21ema` from medium to **high** conviction.

## Configuration

All thresholds are configurable via environment variables (see [Configuration Reference](configuration.md)):

| Setting | Env Var | Default |
|---|---|---|
| Fast EMA period | `TYCHE_EMA_FAST_PERIOD` | 8 |
| Slow EMA period | `TYCHE_EMA_SLOW_PERIOD` | 21 |
| Pullback proximity | `TYCHE_PULLBACK_PROXIMITY_PCT` | 2.0% |
| Max extension | `TYCHE_MAX_EXTENSION_PCT` | 3.0% |
| Min days above EMAs | `TYCHE_MIN_DAYS_ABOVE_EMAS` | 5 |
| Max days above EMAs | `TYCHE_MAX_DAYS_ABOVE_EMAS` | 10 |
| Bootstrap days | `TYCHE_BOOTSTRAP_DAYS` | 120 |

## Updating Thresholds

If you change any conviction threshold:
1. Update the env var or `config.py` default
2. Re-run the backtest: `cd backend && python scripts/backtest_ema.py`
3. Compare win rate, P&L, and category breakdowns against the baseline
4. The backtest uses the same `ConvictionEngine` class, so results are production-identical
