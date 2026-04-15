# Conviction Engine — 8/21 EMA Rules

**Source:** `backend/src/tyche/conviction/`

## Architecture

The conviction system uses a three-layer architecture to separate data-derived features from policy-specific logic:

```
conviction/
├── features.py    — ConvictionFeatureEngine + FeatureSignal + TrendState + GateResult
│                    Pure EMA/trend computation (8/21/50 EMA + RSI).
│                    Own cache + Parquet disk store.
│                    Shared by both options and stocks pipelines.
├── csp_policy.py  — CSPEligibilityPolicy
│                    Stateless CSP gate evaluation on FeatureSignal objects.
│                    No cache. Only used by options pipeline.
├── alerts.py      — PullbackAlert detection from ConvictionSignal objects.
│                    Drives stock buy recommendations and email notifications.
└── engine.py      — ConvictionEngine + ConvictionSignal (backward-compat wrapper)
                     Delegates to FeatureEngine + CSPPolicy. Re-exports TrendState,
                     GateResult, compute_ema, compute_slope for import compat.
```

**Why the split:**
- **Blast radius isolation:** Small batch doesn't overwrite full feature cache. Options and stocks pipelines have independent caches.
- **Config changes without recomputation:** CSP gate thresholds (extension cap, days-above range) can change without re-running expensive EMA computation. Policy is reapplied to cached features.
- **Clean separation of concerns:** `FeatureSignal` has no CSP fields (`csp_eligible`, `gate_results`). Stock pullback alerts consume features directly without CSP baggage.

**Import compatibility:** All existing `from tyche.conviction.engine import TrendState, ConvictionSignal, ...` statements continue to work. The wrapper re-exports everything.

## Purpose

The conviction engine is the primary stock screening gate. It computes 8-day, 21-day, and 50-day Exponential Moving Averages plus RSI(14) on daily closes, classifies the trend state, and determines whether a stock is eligible for selling Cash-Secured Puts via one of two paths: the **uptrend path** (stock above both EMAs) or the **pullback path** (stock pulling back to EMA support in a confirmed uptrend). The pullback path was validated through backtesting — 76.8% win rate across 35,324 trades on $4B+ market cap stocks with 5-day DTE and strikes 5% below the support EMA.

### Supplementary Indicators (Informational, Not Gates)

In addition to the core 8/21 EMA logic, `FeatureSignal` includes:

- **`ema_50` / `ema_50_slope`:** 50-day EMA value and 3-point regression slope. A rising 50-EMA confirms the structural trend is intact. A pullback to 8/21 EMA while the 50-EMA is falling may indicate the larger trend is reversing.
- **`rsi_14`:** 14-period RSI (Wilder smoothing). RSI 40–60 during a pullback = healthy pullback with structural strength. RSI < 30 = oversold/broken momentum. RSI > 70 = overbought.

These are **not** CSP eligibility gates — they are exposed as filterable columns in the UI conviction tables (inline DataTable column filters). The user applies them as discretionary filters when screening candidates.

## Trend State Classification

The engine classifies each stock into one of nine states based on price position relative to EMAs and their slopes:

| State | Condition | CSP Eligible | Deep Dip |
|---|---|---|---|
| `strong_uptrend` | Price above both EMAs, both slopes positive, price >1% above 8-EMA | Yes | No |
| `uptrend` | Price above both EMAs (not strong) | Yes | No |
| `pullback_to_8ema` | Price above 21-EMA but below 8-EMA, within 2% of 8-EMA | Yes | No |
| `pullback_to_21ema` | Price near 21-EMA (within 2%), 21-EMA slope positive | Yes | No |
| `oversold_21ema` | Price ≥ 5% below 21-EMA, 50-EMA slope > -0.3 | No | Yes |
| `oversold_50ema` | Price ≥ 5% below 50-EMA, 50-EMA slope > -0.3 | No | Yes |
| `consolidation` | Price between EMAs but not near either | No | No |
| `downtrend` | Price below both EMAs | No | No |
| `insufficient_data` | Fewer than 50 bars available | No | No |

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

| Path | Floor | Ceiling | Example ($100 EMA, $102 price) |
|---|---|---|---|
| Uptrend (A) | 15% below current price | 8-EMA | $86.70 → $100.00 |
| Pullback (B) | 5% below support EMA | 1% below support EMA | $95.00 → $99.00 |

**Path A (Uptrend):** The floor uses the standard `strike_range_pct` (15%) below current price. The ceiling is the 8-EMA — if assigned, you buy at or below the trend support level. The OTM filter (`strike < quote.last`) also applies, so strikes never exceed the current price.

**Path B (Pullback):** Strikes are bounded tightly between `pullback_strike_offset_pct` (5%) below and `pullback_strike_ceiling_pct` (1%) below the support EMA. This ensures assignment occurs at a meaningful discount to the EMA support — not at the EMA itself (too close to the bounce zone) and not too deep OTM (where premium is negligible).

### Earliest Expiration Filter

After collecting all candidates from both paths across all tickers, the engine filters to **only the single earliest expiration date** across the entire corpus. For example, if some tickers have April 2nd weekly options and others only have April 17th monthlies, only the April 2nd candidates survive.

This maximizes capital recycling speed — get in, collect premium, get out, repeat. Controlled by `TYCHE_EARLIEST_EXPIRATION_ONLY` (default: `true`).

## Pre-Conviction Universe Filters

Before the conviction engine runs, stocks are filtered by:

| Filter | Threshold | Source |
|---|---|---|
| Market cap | >= $4 Billion | TickerMetaStore (Polygon) |
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
| Pullback strike offset (floor) | `TYCHE_PULLBACK_STRIKE_OFFSET_PCT` | 5.0% | Pullback (B) |
| Pullback strike ceiling | `TYCHE_PULLBACK_STRIKE_CEILING_PCT` | 1.0% | Pullback (B) |
| Earliest expiration only | `TYCHE_EARLIEST_EXPIRATION_ONLY` | true | Both |

## Updating Thresholds

If you change any conviction threshold:
1. Update the env var or `config.py` default
2. Re-run the appropriate backtest:
   - Uptrend path: `cd backend && python scripts/backtest_ema.py`
   - Pullback path: `cd backend && python scripts/backtest_pullback_csp.py`
3. Compare win rate, P&L, and category breakdowns against the baseline
4. Both backtests use `ConvictionEngine` (or `ConvictionFeatureEngine` + `CSPEligibilityPolicy`), so results are production-identical
5. Feature computation thresholds (`pullback_proximity_pct`, `min_bars`) live in `ConvictionFeatureEngine`. CSP gate thresholds (`max_extension_pct`, `min_days_above_emas`, etc.) live in `CSPEligibilityPolicy`. Config changes to CSP gates take effect on next evaluation without re-running EMA computation (split cache design).

## Deep Dip Recovery Signals

When a stock is in an `OVERSOLD_21EMA` or `OVERSOLD_50EMA` state, the `/stocks/deep-dips` endpoint applies backtest-validated recovery thresholds to assess whether the dip is a buying opportunity:

### Market Context (`_compute_market_context`)

Computed once per scan from SPY OHLCV + universe-wide dip count:

- `concurrent_dips`: number of stocks in the universe currently dipping below EMAs
- `market_dip_breadth`: concurrent_dips / total_universe
- `spy_return_5d`, `spy_drawdown_from_high`, `spy_rsi_14`: SPY health metrics
- `is_broad_selloff`: true when concurrent_dips ≥ 100

### Recovery Assessment (`_assess_recovery_signal`)

Per-ticker, applies 5 threshold checks derived from backtest analysis on 176K deep dip rows:

1. **RSI 30-50** — stabilization sweet spot
2. **21-EMA slope > -0.5** — trend not structurally broken
3. **Broad selloff** — 100+ concurrent dips (market-driven, not stock-specific)
4. **Market cap ≥ $20B** — mega-cap recovery reliability
5. **Dip classification: low/medium risk** — no insider cluster selling or extreme news

`meets_all_thresholds` = all 5 pass → ~55-58% recovery in 20d, ~73-75% in 40d.
`actionable` = RSI + slope + risk + (broad OR large cap) → ~45-52% in 20d.
Neither = baseline ~42% — not compelling, skip.

### Integration with Covered Call Strategy

When actionable, the recovery signal includes `suggested_cc_dte`:
- All thresholds: 14-30 DTE, strike near 21-EMA (aggressive)
- Partial: 21-45 DTE, strike below 21-EMA (conservative)

This complements the CSP pipeline — CSPs are sold when stocks are *above* EMAs; deep dip buys + covered calls target stocks *below* EMAs.

## Compute Once, Serve All Day — Caching Architecture

Conviction data is expensive to compute (EMA calculation across 3,500+ tickers, OHLCV Parquet reads) but changes only when new OHLCV data arrives (once daily at 4 PM ET). The system uses a "compute once, serve all day" architecture with `conviction.db` as the source of truth.

### Data Flow

```
Daily 16:02  OHLCV Refresh (Polygon grouped bars)
       │
Daily 16:08  Conviction Batch (run_conviction_batch)
       │     └─ Computes EMA/RSI/trend for full equity universe
       │     └─ Upserts results to conviction.db → conviction_snapshots
       │     └─ Detects transitions → conviction_transitions
       │     └─ Persists to conviction_signals.parquet
       │     └─ Clears route-level caches (invalidate_conviction_cache)
       │     └─ Bumps version (last_computed_at) for frontend polling
       │
  All day     Pages read from conviction.db (sub-second)
              └─ GET /conviction/scan → reads conviction_snapshots
              └─ GET /stocks/conviction/snapshots → reads conviction_snapshots
              └─ GET /stocks/deep-dips → in-memory cache by date
```

### Version-Based Cache Invalidation

The frontend polls `GET /conviction/version` every 5 minutes. This returns `last_computed_at` from `conviction.db`. When the version changes (e.g., after the 16:08 batch completes), the frontend invalidates all conviction-dependent React Query caches, triggering fresh reads from the DB.

```
Frontend hooks:
  useConvictionVersion()     → polls every 5 min, invalidates on change
  useConvictionScan()        → staleTime: Infinity, refetchOnWindowFocus: false
  useConvictionSnapshots()   → staleTime: Infinity, refetchOnWindowFocus: false
  useDeepDips()              → staleTime: Infinity, refetchOnWindowFocus: false
  useActivePullbacks()       → staleTime: Infinity, refetchOnWindowFocus: false
  useStockRecommendations()  → staleTime: Infinity, refetchOnWindowFocus: false
```

### Lazy Dependency Resolution (Cold-Start Protection)

On backend restart, the first page load must NOT block on heavy I/O. The conviction routes use lazy dependency resolution — heavy objects (`ConvictionEngine`, `OHLCVStore`) are only initialized if the fast DB/cache path misses:

```python
# routes/conviction.py — GET /conviction/scan
async def scan_conviction(
    settings = Depends(get_settings),        # lightweight
    meta_store = Depends(get_ticker_meta_store),  # lightweight
    # NOTE: no Depends(get_conviction_engine) or Depends(get_data_store)
):
    # Fast path: read from conviction.db (sub-second)
    response = await _build_scan_from_db(...)
    if response is not None:
        return response  # <-- returns here on warm DB, no heavy I/O

    # Slow path: only reached if DB is empty or force=True
    store = get_data_store(settings)      # NOW we initialize OHLCVStore
    engine = get_conviction_engine(settings)  # NOW we initialize engine
    # ... full live compute ...
```

Without this, `Depends(get_data_store)` eagerly calls `OHLCVStore.__init__()` which triggers `read_all()` (13,000+ Parquet files, 30-40s blocking I/O), stalling the entire uvicorn event loop even when `conviction.db` has fresh data.

### Cache Layers

| Layer | Location | Eviction | Purpose |
|---|---|---|---|
| 1. In-memory feature cache | `ConvictionFeatureEngine._cache` | OHLCV date change or `reset_all()` | Per-ticker `FeatureSignal` keyed by `(ticker, as_of_date)` |
| 2. In-memory derived cache | `_derived_cache` | `reset_all()` | IV Rank/VRP batch data |
| 3. Parquet signal store | `conviction_signals.parquet` | `reset_all()` | Warm-on-restart for feature engine |
| 4. SQLite snapshots | `conviction.db` → `conviction_snapshots` | Never evicted (upsert on each batch) | **Source of truth** for all page loads |
| 5. Route-level caches | `_scan_cache`, `_deep_dip_cache` | Conviction batch completion | Serialized API responses for instant re-serve |

### What Triggers Invalidation

| Event | Layers Cleared | Trigger |
|---|---|---|
| OHLCV refresh (16:02) | All (1-5) via `deps.reset_all()` | New price data arrived |
| Conviction batch (16:08) | 5 only (route caches) | Batch writes fresh snapshots to Layer 4 |
| Config change (Settings UI) | All (1-5) via `deps.reset_all()` | User changed thresholds |
| Manual "Refresh Conviction" button | 5 only, then triggers batch | User requests re-scan |

### Disk Cache (ConvictionSignalStore)

Feature signals are optionally persisted to `data/conviction_signals.parquet` via the `ConvictionSignalStore`:

- **Format:** Parquet (consistent with OHLCVStore, TickerMetaStore)
- **Contents:** Data-derived fields only (8/21/50 EMAs, slopes, RSI, volumes, streaks) — no CSP gates or conviction levels
- **Eviction:** Auto-invalidated when OHLCV date changes. Explicit `clear()` on data refresh.
- **Warm-on-restart:** `ConvictionFeatureEngine.analyze_batch()` loads from disk if in-memory cache is empty
- **Config-safe:** CSP gates are recomputed from stored features using current settings, so config changes take effect without cache flush
