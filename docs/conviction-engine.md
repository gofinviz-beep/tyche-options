# Conviction Engine — 8/21 EMA Rules

**Source:** `backend/src/tyche/conviction/engine.py`

## Purpose

The conviction engine is the primary stock screening gate. It computes 8-day and 21-day Exponential Moving Averages on daily closes, classifies the trend state, and determines whether a stock is eligible for selling Cash-Secured Puts via one of two paths: the **uptrend path** (stock above both EMAs) or the **pullback path** (stock pulling back to EMA support in a confirmed uptrend). The pullback path was validated through backtesting — 76.8% win rate across 35,324 trades on $5B+ market cap stocks with 5-day DTE and strikes 5% below the support EMA.

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

A stock can become CSP-eligible through **one of two paths**. Both share Gate 1 (trend state), but diverge at Gates 2 and 3.

### Gate 1: Trend State (both paths)

Must be one of: `strong_uptrend`, `uptrend`, `pullback_to_8ema`, `pullback_to_21ema`.

---

### Path A — Uptrend CSP (stock above both EMAs)

#### Gate 2: Extension Cap (<= 3%)

The stock's price must not be more than 3% above its 8-day EMA.

```
extension_pct = (price - ema_8) / ema_8 * 100
if extension_pct > 3.0: csp_eligible = False
```

**Backtest evidence:** Stocks extended >3% above 8-EMA showed significantly worse CSP outcomes. The 1-2% extension bucket had a 69% win rate vs. 44% for the 5-8% bucket. Over-extended stocks have high snapback risk — selling puts near the top leads to frequent assignment.

#### Gate 3a: Days Above Both EMAs (5-10 days)

The stock must have been continuously above both EMAs for 5 to 10 consecutive trading days.

```
if not (5 <= days_above_both_emas <= 10): csp_eligible = False
```

**Rationale:**
- **< 5 days:** Trend not confirmed. Too early to have confidence the uptrend is real.
- **> 10 days:** Overdue for a reversal or consolidation. The best CSP entries are in the sweet spot of a confirmed but not exhausted trend.

---

### Path B — Pullback CSP (stock pulling back to EMA support)

Applies when the trend state is `pullback_to_8ema` or `pullback_to_21ema`. Requires `pullback_csp_enabled=true` (default).

#### Gate 2: Extension Cap (auto-pass)

The extension cap is bypassed on the pullback path — the stock is pulling back, not extended.

#### Gate 3b: Prior Streak + Rising 21-EMA

Instead of checking the current streak above both EMAs (which is 0 during a pullback), the engine looks **backward** to find the uptrend that preceded the pullback:

```python
prior_streak = _compute_prior_streak(above_both_series)
pullback_eligible = prior_streak >= min_prior_streak and ema_21_slope > 0
```

`_compute_prior_streak` skips trailing `False` values (the current pullback), then counts consecutive `True` values (the prior uptrend run).

**Requirements:**
- **Prior streak >= 5 days** (configurable via `min_prior_streak`): The stock must have been above both EMAs for at least 5 days before the pullback started, confirming it was in a real uptrend.
- **21-EMA slope > 0**: The longer-term trend must still be rising, not rolling over.

**Backtest evidence (2024-2026, $5B+ market cap):**
- 5% below support EMA, 5-day DTE: **76.8% win rate** across 35,324 trades
- At-the-money pullback CSPs (0% offset) showed very high assignment rates — avoided
- 8-EMA pullbacks outperformed 21-EMA pullbacks
- Quality large-caps (RSG, DTE, WMT) showed 75-81% win rates

### Strike Selection by Path

| Path | Reference Price | Strike Range |
|---|---|---|
| Uptrend (A) | 8-EMA | Floor = 8-EMA × (1 - `strike_range_pct`/100). Default: 15% below. |
| Pullback (B) | Support EMA (21-EMA or 8-EMA) | Floor = support_EMA × (1 - `pullback_strike_offset_pct`/100). Ceiling = support_EMA. Default: 5% below. |

For pullback CSPs, strikes are bounded tightly: no deeper than 5% below the support EMA, and no higher than the EMA itself (to avoid selling ITM puts).

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

## Conviction Sorting

In `analyze_batch`, signals are sorted for optimal trade selection:

1. **Conviction level** (high → medium → low → none)
2. **Pullback priority** (21-EMA pullback > 8-EMA pullback > uptrend — pullbacks are safer per backtest)
3. **Prior streak** (longer prior uptrend = stronger setup, descending)

## Assignment Philosophy

If assigned on a pullback CSP, the stock was bought at 5% below a rising EMA on a quality large-cap name — exactly where you'd want to own it anyway. Enter the wheel: sell covered calls on the assigned shares.

## Configuration

All thresholds are configurable via environment variables (see [Configuration Reference](configuration.md)):

| Setting | Env Var | Default | Path |
|---|---|---|---|
| Fast EMA period | `TYCHE_EMA_FAST_PERIOD` | 8 | Both |
| Slow EMA period | `TYCHE_EMA_SLOW_PERIOD` | 21 | Both |
| Pullback proximity | `TYCHE_PULLBACK_PROXIMITY_PCT` | 2.0% | Both |
| Max extension | `TYCHE_MAX_EXTENSION_PCT` | 3.0% | Uptrend (A) |
| Min days above EMAs | `TYCHE_MIN_DAYS_ABOVE_EMAS` | 5 | Uptrend (A) |
| Max days above EMAs | `TYCHE_MAX_DAYS_ABOVE_EMAS` | 10 | Uptrend (A) |
| Bootstrap days | `TYCHE_BOOTSTRAP_DAYS` | 120 | Both |
| Pullback CSP enabled | `TYCHE_PULLBACK_CSP_ENABLED` | true | Pullback (B) |
| Min prior streak | `TYCHE_MIN_PRIOR_STREAK` | 5 | Pullback (B) |
| Pullback strike offset | `TYCHE_PULLBACK_STRIKE_OFFSET_PCT` | 5.0% | Pullback (B) |

## Updating Thresholds

If you change any conviction threshold:
1. Update the env var or `config.py` default
2. Re-run the appropriate backtest:
   - Uptrend path: `cd backend && python scripts/backtest_ema.py`
   - Pullback path: `cd backend && python scripts/backtest_pullback_csp.py`
3. Compare win rate, P&L, and category breakdowns against the baseline
4. Both backtests use the same `ConvictionEngine` class, so results are production-identical
