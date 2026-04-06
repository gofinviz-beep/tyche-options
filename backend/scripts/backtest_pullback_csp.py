"""Backtest CSP selling on EMA pullback events.

Simulates selling cash-secured puts when a stock pulls back to the 8-EMA
or 21-EMA within a confirmed uptrend.  For each pullback entry, three
strike offsets are tested relative to the support EMA:

  0%  — strike AT the EMA  (maximum premium, highest assignment rate)
  3%  — strike 3% below EMA (moderate cushion)
  5%  — strike 5% below EMA (conservative)

Entry conditions (all must hold):
  1. trend_state is pullback_to_8ema or pullback_to_21ema
  2. both EMA slopes positive (trend still intact)
  3. a prior uptrend existed — at least N consecutive days above both EMAs
     before the pullback started (configurable via --min-prior-streak)

Metrics mirror backtest_ema.py for apples-to-apples comparison:
  - win rate, avg / worst drawdown, simulated CSP P&L
  - breakdowns by pullback type, volume decline, prior streak, day of week

Uses production-identical filters:
  - Market cap >= $5B (from Polygon ticker reference)
  - Exchange in NYSE/NASDAQ
  - Min price >= $15

CLI flags (all optional — defaults reproduce legacy output):
  --dte N              Primary hold period (default: 8)
  --dte-alt N          Alternative hold period for comparison (default: 5)
  --min-prior-streak N Minimum prior streak days (default: 5)
  --premium-model      fixed_pct_by_offset (default) | fixed_pct | iv_proxy
  --execution-model    none (default) | optimistic | base | conservative
  --walk-forward       Enable rolling walk-forward analysis
  --train-days N       Walk-forward train window (default: 126)
  --test-days N        Walk-forward test window (default: 63)
  --print-assumptions  Print assumptions and exit
"""

import argparse
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, "src")

from tyche.backtest.assumptions import build_assumptions
from tyche.backtest.execution import (
    ExecutionModel,
    build_sensitivity_table,
    format_sensitivity_table,
    get_execution_model,
)
from tyche.backtest.premium import (
    MarketPremiumModel,
    PremiumModel,
    get_market_premium_model,
    get_premium_model,
)
from tyche.backtest.walk_forward import WalkForwardRunner, WindowResult
from tyche.config import get_settings
from tyche.market_data.data_store import OHLCVStore, OptionsChainStore, TickerMetaStore

# ── Constants ────────────────────────────────────────────────────────────

EMA_FAST = 8
EMA_SLOW = 21
PROXIMITY_PCT = 2.0
MIN_BARS = 50
MIN_PRICE = 15.0
MIN_MARKET_CAP = 4_000_000_000  # $4B
MIN_VOLUME = 500_000
VALID_EXCHANGES = {"XNYS", "XNAS", "XNMS", "XASE", "ARCX", "BATS"}

STRIKE_OFFSETS = [0.0, 3.0, 5.0]  # % below support EMA


# ── EMA / trend helpers (mirrors conviction/engine.py) ───────────────────

def compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def compute_slope(series: pd.Series, periods: int = 3) -> float:
    if len(series) < periods:
        return 0.0
    y = series.iloc[-periods:].values
    x = np.arange(periods, dtype=float)
    if np.std(y) == 0:
        return 0.0
    return float(np.polyfit(x, y, 1)[0])


def classify_trend(
    price: float,
    ema_8: float,
    ema_21: float,
    slope_8: float,
    slope_21: float,
    pct_to_8: float,
    pct_to_21: float,
) -> str:
    above_8 = price > ema_8
    above_21 = price > ema_21
    both_slopes_up = slope_8 > 0 and slope_21 > 0

    if above_8 and above_21:
        if both_slopes_up and pct_to_8 > 1.0:
            return "strong_uptrend"
        return "uptrend"

    if above_21 and not above_8:
        if abs(pct_to_8) <= PROXIMITY_PCT:
            return "pullback_to_8ema"
        if abs(pct_to_21) <= PROXIMITY_PCT:
            return "pullback_to_21ema"
        return "consolidation"

    if not above_21 and abs(pct_to_21) <= PROXIMITY_PCT and slope_21 > 0:
        return "pullback_to_21ema"

    if not above_8 and not above_21:
        return "downtrend"

    return "consolidation"


def is_volume_declining(volumes: pd.Series, idx: int, lookback: int = 5) -> bool:
    if idx < lookback:
        return False
    recent_avg = volumes.iloc[idx - lookback : idx].mean()
    return bool(volumes.iloc[idx] < recent_avg)


def compute_prior_streak(above_both: pd.Series, pullback_idx: int) -> int:
    """Count the consecutive-above-both-EMAs streak that ended just before
    the pullback.  Scans backwards from pullback_idx - 1."""
    streak = 0
    for i in range(pullback_idx - 1, -1, -1):
        if above_both.iloc[i]:
            streak += 1
        else:
            break
    return streak


# ── Simulation data class ────────────────────────────────────────────────

@dataclass
class PullbackCSPSim:
    entry_date: date
    symbol: str
    pullback_type: str         # "8ema" or "21ema"
    entry_price: float
    support_ema: float         # EMA value at entry (8 or 21 depending on type)
    ema_8: float
    ema_21: float
    ema_8_slope: float
    ema_21_slope: float
    strike_offset_pct: float   # 0, 3, or 5
    strike: float
    prior_streak: int
    volume_declining: bool
    premium_pct: float = 0.015
    market_cap: float = 0.0

    exit_date: date | None = None
    exit_price: float = 0.0
    min_price_during: float = 0.0
    max_drawdown_pct: float = 0.0
    stayed_above_strike: bool = False
    forward_return_pct: float = 0.0


# ── P&L helper ───────────────────────────────────────────────────────────

def _compute_pnl(
    sims: list[PullbackCSPSim],
    exec_model: ExecutionModel,
) -> float:
    """Compute total P&L in percentage terms, applying execution friction."""
    total = 0.0
    for s in sims:
        raw_prem_pct = s.premium_pct * 100
        adj_prem_pct = exec_model.adjust_premium(raw_prem_pct, contracts=1)
        if s.stayed_above_strike:
            total += adj_prem_pct
        else:
            loss = ((s.strike - s.min_price_during) / s.strike) * 100
            total += adj_prem_pct - loss
    return total


# ── Main backtest ────────────────────────────────────────────────────────

def run_backtest(
    dte: int,
    min_prior_streak: int,
    dte_alt: int | None,
    premium_model: PremiumModel,
    exec_model: ExecutionModel,
    walk_forward: bool = False,
    train_days: int = 126,
    test_days: int = 63,
) -> None:
    settings = get_settings()
    store = OHLCVStore(data_dir=settings.data_dir)
    meta_store = TickerMetaStore(data_dir=settings.data_dir)

    if not store.exists:
        print("ERROR: OHLCV store is empty. Run bootstrap first.")
        return

    assumptions = build_assumptions(
        "backtest_pullback_csp.py",
        min_market_cap=MIN_MARKET_CAP,
        min_price=MIN_PRICE,
        min_volume=MIN_VOLUME,
        valid_exchanges=sorted(VALID_EXCHANGES),
        dte=dte,
        strike_offsets=STRIKE_OFFSETS,
        premium_model=premium_model,
        execution_model=exec_model,
        ema_fast=EMA_FAST,
        ema_slow=EMA_SLOW,
        min_prior_streak=min_prior_streak,
        walk_forward_enabled=walk_forward,
        train_days=train_days if walk_forward else None,
        test_days=test_days if walk_forward else None,
    )
    assumptions.print_summary()

    # ── Load metadata for filtering ──────────────────────────────────
    all_market_caps: dict[str, float] = {}
    all_exchanges: dict[str, str] = {}
    if meta_store.exists:
        all_market_caps = meta_store.get_market_caps()
        all_exchanges = meta_store.get_exchanges()
        print(f"Ticker metadata: {len(all_market_caps)} tickers with market cap")
    else:
        print("WARNING: ticker_meta.parquet not found — skipping market-cap filter")

    has_cap_data = any(v > 0 for v in all_market_caps.values())

    qualified_tickers: set[str] = set()
    if all_exchanges:
        for ticker, exchange in all_exchanges.items():
            if exchange not in VALID_EXCHANGES:
                continue
            cap = all_market_caps.get(ticker, 0)
            if has_cap_data and cap < MIN_MARKET_CAP:
                continue
            qualified_tickers.add(ticker)
        cap_msg = f", mkt_cap >= ${MIN_MARKET_CAP/1e9:.0f}B" if has_cap_data else " (no cap data — skipped)"
        print(f"Tickers passing exchange{cap_msg} filter: {len(qualified_tickers)}")
    else:
        qualified_tickers = set(store.get_all_tickers())
        print(f"No metadata filter — using all {len(qualified_tickers)} tickers")

    # ── Load OHLCV ───────────────────────────────────────────────────
    all_ohlcv_tickers = set(store.get_all_tickers())
    target_tickers = qualified_tickers & all_ohlcv_tickers
    ticker_data = store.read_tickers(list(target_tickers))

    warmup = MIN_BARS
    ticker_frames: dict[str, pd.DataFrame] = {}
    for ticker, df in ticker_data.items():
        if len(df) >= warmup:
            ticker_frames[ticker] = df.sort_values("date").reset_index(drop=True)

    all_dates: set[date] = set()
    for df in ticker_frames.values():
        all_dates.update(df["date"].unique())
    all_dates_sorted = sorted(all_dates)

    if len(all_dates_sorted) < warmup + dte:
        print("ERROR: Not enough trading days for backtest.")
        return

    print(f"\nTickers with sufficient data: {len(ticker_frames)}")
    print(f"Date range: {all_dates_sorted[0]} to {all_dates_sorted[-1]}")
    print(f"Total trading days: {len(all_dates_sorted)}")
    print(f"Premium model: {premium_model.name} | Execution: {exec_model.mode}")

    dte_list = [dte]
    if dte_alt and dte_alt != dte:
        dte_list.append(dte_alt)

    for current_dte in dte_list:
        print(f"\n{'#' * 80}")
        print(f"# PULLBACK CSP BACKTEST — DTE={current_dte}, "
              f"min_prior_streak={min_prior_streak}")
        print(f"{'#' * 80}")

        if walk_forward:
            _run_walk_forward_pullback(
                ticker_frames, all_dates_sorted, all_market_caps,
                current_dte, min_prior_streak, warmup,
                premium_model, exec_model, train_days, test_days,
            )
        else:
            _run_for_dte(
                ticker_frames, all_dates_sorted, all_market_caps,
                current_dte, min_prior_streak, warmup,
                premium_model, exec_model,
            )

    if isinstance(premium_model, MarketPremiumModel):
        desc = premium_model.describe()
        print(f"\n--- Market Premium Model Stats ---")
        print(f"  Hits (real data):  {desc['hits']}")
        print(f"  Misses (fallback): {desc['misses']}")
        print(f"  Hit rate:          {desc['hit_rate_pct']:.1f}%")
        print(f"  Fallback model:    {desc['fallback']}")

    if exec_model.mode != "none":
        print("\n--- Execution Model Sensitivity ---")
        table = build_sensitivity_table(sample_premium=150.0)
        print(format_sensitivity_table(table))


def _scan_pullbacks(
    ticker_frames: dict[str, pd.DataFrame],
    scan_dates: list[date],
    all_market_caps: dict[str, float],
    dte: int,
    min_prior_streak: int,
    warmup: int,
    premium_model: PremiumModel,
) -> list[PullbackCSPSim]:
    """Core scan loop — extract pullback simulations from the given date range."""
    simulations: list[PullbackCSPSim] = []

    for ticker, df in ticker_frames.items():
        close = df["close"].astype(float)
        volume = df["volume"].astype(float)
        dates = df["date"]

        ema_8 = compute_ema(close, EMA_FAST)
        ema_21 = compute_ema(close, EMA_SLOW)
        above_both = (close > ema_8) & (close > ema_21)

        n = len(df)

        for i in range(warmup, n):
            price = close.iloc[i]
            vol = volume.iloc[i]

            if price < MIN_PRICE or vol < MIN_VOLUME:
                continue

            e8 = ema_8.iloc[i]
            e21 = ema_21.iloc[i]

            slope_8 = compute_slope(ema_8.iloc[max(0, i - 2) : i + 1])
            slope_21 = compute_slope(ema_21.iloc[max(0, i - 2) : i + 1])

            if slope_8 <= 0 or slope_21 <= 0:
                continue

            pct_to_8 = ((price - e8) / e8 * 100) if e8 else 0
            pct_to_21 = ((price - e21) / e21 * 100) if e21 else 0

            trend = classify_trend(price, e8, e21, slope_8, slope_21, pct_to_8, pct_to_21)

            if trend not in ("pullback_to_8ema", "pullback_to_21ema"):
                continue

            prior_streak_val = compute_prior_streak(above_both, i)
            if prior_streak_val < min_prior_streak:
                continue

            scan_date = dates.iloc[i]
            if scan_dates and scan_date not in scan_dates:
                continue

            pullback_type = "8ema" if trend == "pullback_to_8ema" else "21ema"
            support_ema = e8 if pullback_type == "8ema" else e21
            vol_declining = is_volume_declining(volume, i)
            cap = all_market_caps.get(ticker, 0)

            forward = df.iloc[i + 1 : i + 1 + dte]
            if len(forward) < dte - 1:
                continue

            exit_price = float(forward["close"].iloc[-1])
            fwd_return = ((exit_price - price) / price) * 100

            ohlcv_up_to = df.iloc[: i + 1]

            for offset in STRIKE_OFFSETS:
                strike = round(support_ema * (1 - offset / 100), 2)

                prem_pct = premium_model.premium_pct(
                    strike=strike,
                    underlying_price=price,
                    dte=dte,
                    ohlcv=ohlcv_up_to,
                    strike_offset_pct=offset,
                    ticker=ticker,
                    snapshot_date=scan_date,
                )

                min_price = float(forward["low"].min())
                max_dd = ((min_price - price) / price) * 100

                simulations.append(PullbackCSPSim(
                    entry_date=scan_date,
                    symbol=ticker,
                    pullback_type=pullback_type,
                    entry_price=price,
                    support_ema=round(support_ema, 4),
                    ema_8=round(e8, 4),
                    ema_21=round(e21, 4),
                    ema_8_slope=round(slope_8, 6),
                    ema_21_slope=round(slope_21, 6),
                    strike_offset_pct=offset,
                    strike=strike,
                    prior_streak=prior_streak_val,
                    volume_declining=vol_declining,
                    premium_pct=prem_pct,
                    market_cap=cap,
                    exit_date=forward["date"].iloc[-1],
                    exit_price=exit_price,
                    min_price_during=min_price,
                    max_drawdown_pct=max_dd,
                    stayed_above_strike=min_price > strike,
                    forward_return_pct=fwd_return,
                ))

    return simulations


def _run_for_dte(
    ticker_frames: dict[str, pd.DataFrame],
    all_dates_sorted: list[date],
    all_market_caps: dict[str, float],
    dte: int,
    min_prior_streak: int,
    warmup: int,
    premium_model: PremiumModel,
    exec_model: ExecutionModel,
) -> None:
    simulations = _scan_pullbacks(
        ticker_frames, [], all_market_caps,
        dte, min_prior_streak, warmup, premium_model,
    )

    if not simulations:
        print("\nNo simulations generated!")
        return

    print(f"\nTotal simulations (entries x {len(STRIKE_OFFSETS)} offsets): {len(simulations)}")
    print()

    # Per-offset summary
    print("=" * 100)
    print(f"{'PULLBACK CSP RESULTS':^100}")
    print("=" * 100)

    for offset in STRIKE_OFFSETS:
        subset = [s for s in simulations if s.strike_offset_pct == offset]
        label = (
            "STRIKE AT EMA (0% OTM)" if offset == 0.0
            else f"STRIKE {offset:.0f}% BELOW EMA"
        )
        _print_scenario_summary(subset, label, exec_model)
        print()

    # ── Breakdowns (on the 3% offset as the baseline comparison) ─────
    baseline = [s for s in simulations if s.strike_offset_pct == 3.0]
    if not baseline:
        baseline = simulations

    print("\n" + "=" * 100)
    print("BREAKDOWNS (3% below EMA scenario)")
    print("=" * 100)

    print(f"\n--- By Pullback Type ---")
    _stats([s for s in baseline if s.pullback_type == "8ema"], "PULLBACK TO 8-EMA", exec_model)
    _stats([s for s in baseline if s.pullback_type == "21ema"], "PULLBACK TO 21-EMA", exec_model)

    print(f"\n--- By Volume on Pullback ---")
    _stats([s for s in baseline if s.volume_declining], "Volume DECLINING", exec_model)
    _stats([s for s in baseline if not s.volume_declining], "Volume NOT declining", exec_model)

    print(f"\n--- By Prior Streak (days above both EMAs before pullback) ---")
    for lo, hi, label in [
        (3, 5, "3-4 days"),
        (5, 10, "5-9 days"),
        (10, 20, "10-19 days"),
        (20, 999, "20+ days"),
    ]:
        _stats([s for s in baseline if lo <= s.prior_streak < hi], label, exec_model)

    print(f"\n--- By Entry Day of Week ---")
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    for dow in range(5):
        _stats(
            [s for s in baseline if s.entry_date.weekday() == dow],
            day_names[dow], exec_model,
        )

    print(f"\n--- By Market Cap ---")
    for lo, hi, label in [
        (0, 5e9, "<$5B (no metadata)"),
        (5e9, 10e9, "$5B-10B"),
        (10e9, 50e9, "$10B-50B"),
        (50e9, 200e9, "$50B-200B"),
        (200e9, 1e15, "$200B+"),
    ]:
        _stats([s for s in baseline if lo <= s.market_cap < hi], label, exec_model)

    print(f"\n--- HIGH-CONVICTION: 21-EMA pullback + Volume Declining ---")
    for offset in STRIKE_OFFSETS:
        label = f"  offset={offset:.0f}%"
        subset = [
            s for s in simulations
            if s.strike_offset_pct == offset
            and s.pullback_type == "21ema"
            and s.volume_declining
        ]
        _stats(subset, label, exec_model)

    print(f"\n--- 10 Worst Trades (3% below EMA) ---")
    worst = sorted(baseline, key=lambda s: s.max_drawdown_pct)[:10]
    for s in worst:
        cap_b = s.market_cap / 1e9
        outcome = "SAFE" if s.stayed_above_strike else "ASSIGNED"
        print(
            f"  {s.entry_date} {s.symbol:6s} | ${s.entry_price:7.2f}→${s.exit_price:7.2f} "
            f"({s.forward_return_pct:+5.1f}%) | strike=${s.strike:7.2f} "
            f"min=${s.min_price_during:7.2f} dd={s.max_drawdown_pct:+.1f}% [{outcome}] "
            f"| {s.pullback_type} streak={s.prior_streak} cap=${cap_b:.1f}B"
        )

    print(f"\n--- Top 20 Most Frequent Symbols (3% below EMA) ---")
    freq: dict[str, int] = defaultdict(int)
    sym_wins: dict[str, int] = defaultdict(int)
    for s in baseline:
        freq[s.symbol] += 1
        if s.stayed_above_strike:
            sym_wins[s.symbol] += 1

    top_syms = sorted(freq.items(), key=lambda x: -x[1])[:20]
    for sym, count in top_syms:
        wr = sym_wins[sym] / count * 100
        cap_b = all_market_caps.get(sym, 0) / 1e9
        print(f"  {sym:6s}: {count:3d} picks | win={wr:5.1f}% | cap=${cap_b:.1f}B")

    # ── Side-by-side comparison table ────────────────────────────────
    print("\n" + "=" * 100)
    print("COMPARISON TABLE — Pullback CSP vs Uptrend CSP Baseline")
    print("=" * 100)
    print(
        f"\n  {'Scenario':<40s} {'Trades':>7s} {'Win%':>7s} "
        f"{'AvgP&L':>8s} {'WorstDD':>9s} {'AvgDD':>8s} {'CumP&L':>10s}"
    )
    print("  " + "-" * 90)

    for offset in STRIKE_OFFSETS:
        subset = [s for s in simulations if s.strike_offset_pct == offset]
        label = f"Pullback CSP @ {offset:.0f}% below EMA"
        _comparison_row(subset, label, exec_model)

    for offset in STRIKE_OFFSETS:
        subset = [
            s for s in simulations
            if s.strike_offset_pct == offset
            and s.pullback_type == "21ema"
            and s.volume_declining
        ]
        label = f"  21EMA+VolDecl @ {offset:.0f}% below"
        _comparison_row(subset, label, exec_model)

    print(
        f"\n  {'Uptrend CSP baseline (backtest_ema)':40s} "
        f"{'---':>7s} {'~69%':>7s} {'---':>8s} {'---':>9s} {'---':>8s} {'---':>10s}"
    )
    print("  (run backtest_ema.py for exact baseline numbers)")


def _run_walk_forward_pullback(
    ticker_frames: dict[str, pd.DataFrame],
    all_dates_sorted: list[date],
    all_market_caps: dict[str, float],
    dte: int,
    min_prior_streak: int,
    warmup: int,
    premium_model: PremiumModel,
    exec_model: ExecutionModel,
    train_days: int,
    test_days: int,
) -> None:
    """Execute walk-forward analysis for pullback CSP strategy."""

    def run_window(
        train_dates: list[date],
        test_dates: list[date],
        **ctx: object,
    ) -> WindowResult:
        test_set = set(test_dates)
        sims = _scan_pullbacks(
            ticker_frames, list(test_set), all_market_caps,
            dte, min_prior_streak, warmup, premium_model,
        )
        baseline = [s for s in sims if s.strike_offset_pct == 5.0]
        total = len(baseline)
        wins = sum(1 for s in baseline if s.stayed_above_strike)
        pnl = _compute_pnl(baseline, exec_model)
        avg_pnl = pnl / total if total else 0.0
        worst_dd = min((s.max_drawdown_pct for s in baseline), default=0.0)

        return WindowResult(
            window_id=0,
            train_start=train_dates[0],
            train_end=train_dates[-1],
            test_start=test_dates[0],
            test_end=test_dates[-1],
            total_trades=total,
            wins=wins,
            win_rate=(wins / total * 100) if total else 0.0,
            avg_pnl_pct=avg_pnl,
            cumulative_pnl_pct=pnl,
            max_drawdown_pct=worst_dd,
            sharpe=0.0,
        )

    runner = WalkForwardRunner(train_days=train_days, test_days=test_days)
    try:
        summary = runner.run(all_dates_sorted, run_fn=run_window)
    except ValueError as e:
        print(f"\nWalk-forward failed: {e}")
        return

    summary.print_report()


# ── Reporting helpers ────────────────────────────────────────────────────

def _print_scenario_summary(
    sims: list[PullbackCSPSim],
    label: str,
    exec_model: ExecutionModel,
) -> None:
    if not sims:
        print(f"\n  {label}: no data")
        return

    n = len(sims)
    wins = sum(1 for s in sims if s.stayed_above_strike)
    win_rate = wins / n * 100
    avg_return = sum(s.forward_return_pct for s in sims) / n
    avg_dd = sum(s.max_drawdown_pct for s in sims) / n
    worst_dd = min(s.max_drawdown_pct for s in sims)
    median_return = float(np.median([s.forward_return_pct for s in sims]))

    total_pnl_pct = _compute_pnl(sims, exec_model)
    avg_pnl = total_pnl_pct / n

    unique_symbols = len(set(s.symbol for s in sims))

    print(f"  {label}")
    print(f"    Trades: {n}  |  Unique symbols: {unique_symbols}")
    print(f"    Win rate (put expires OTM): {win_rate:.1f}%")
    print(f"    Assignment rate: {(n - wins) / n * 100:.1f}%")
    print(f"    Avg forward return: {avg_return:+.2f}%"
          f"  |  Median: {median_return:+.2f}%")
    print(f"    Avg max drawdown: {avg_dd:+.2f}%  |  Worst: {worst_dd:+.2f}%")
    print(f"    Avg trade P&L: {avg_pnl:+.2f}%  |  Cumulative: {total_pnl_pct:+.1f}%")


def _stats(
    sims: list[PullbackCSPSim],
    label: str,
    exec_model: ExecutionModel,
) -> None:
    if not sims:
        print(f"  {label:35s}: no data")
        return
    n = len(sims)
    w = sum(1 for s in sims if s.stayed_above_strike)
    wr = w / n * 100
    ar = sum(s.forward_return_pct for s in sims) / n
    ad = sum(s.max_drawdown_pct for s in sims) / n
    wd = min(s.max_drawdown_pct for s in sims)

    pnl = _compute_pnl(sims, exec_model)
    avg_pnl = pnl / n

    print(
        f"  {label:35s}: {n:5d} trades | win={wr:5.1f}% | "
        f"avg_ret={ar:+6.2f}% | avg_dd={ad:+6.2f}% | "
        f"worst_dd={wd:+7.2f}% | avg_pnl={avg_pnl:+5.2f}%"
    )


def _comparison_row(
    sims: list[PullbackCSPSim],
    label: str,
    exec_model: ExecutionModel,
) -> None:
    if not sims:
        print(f"  {label:<40s} {'0':>7s} {'---':>7s} {'---':>8s} {'---':>9s} {'---':>8s} {'---':>10s}")
        return
    n = len(sims)
    w = sum(1 for s in sims if s.stayed_above_strike)
    wr = w / n * 100
    ad = sum(s.max_drawdown_pct for s in sims) / n
    wd = min(s.max_drawdown_pct for s in sims)

    pnl = _compute_pnl(sims, exec_model)
    avg_pnl = pnl / n

    print(
        f"  {label:<40s} {n:>7d} {wr:>6.1f}% "
        f"{avg_pnl:>+7.2f}% {wd:>+8.2f}% {ad:>+7.2f}% {pnl:>+9.1f}%"
    )


# ── CLI ──────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest CSP selling on EMA pullbacks"
    )
    parser.add_argument(
        "--dte", type=int, default=8,
        help="Primary hold period in trading days (default: 8)",
    )
    parser.add_argument(
        "--dte-alt", type=int, default=5,
        help="Alternative hold period for comparison (default: 5)",
    )
    parser.add_argument(
        "--min-prior-streak", type=int, default=5,
        help="Minimum consecutive days above both EMAs before the pullback (default: 5)",
    )
    parser.add_argument(
        "--premium-model", default="fixed_pct_by_offset",
        choices=["fixed_pct_by_offset", "fixed_pct", "iv_proxy"],
        help="Simulation premium model (default: fixed_pct_by_offset). "
             "Ignored when --premium-source=market.",
    )
    parser.add_argument(
        "--premium-source", default="simulated",
        choices=["simulated", "market"],
        help="Use 'market' for real options chain data with simulation fallback, "
             "or 'simulated' for pure simulation (default: simulated)",
    )
    parser.add_argument(
        "--execution-model", default="none",
        choices=["none", "optimistic", "base", "conservative"],
        help="Execution friction model (default: none = legacy behavior)",
    )
    parser.add_argument(
        "--walk-forward", action="store_true",
        help="Enable rolling walk-forward analysis",
    )
    parser.add_argument(
        "--train-days", type=int, default=126,
        help="Walk-forward train window in trading days (default: 126 ≈ 6 months)",
    )
    parser.add_argument(
        "--test-days", type=int, default=63,
        help="Walk-forward test window in trading days (default: 63 ≈ 3 months)",
    )
    parser.add_argument(
        "--print-assumptions", action="store_true",
        help="Print assumptions block and exit without running backtest",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    settings = get_settings()

    if args.premium_source == "market":
        options_store = OptionsChainStore(data_dir=settings.data_dir)
        if not options_store.exists:
            print("WARNING: No options chain data found. "
                  "Run scripts/ingest_options.py first.")
            print("         Falling back to simulation model.\n")
            pm = get_premium_model(args.premium_model)
        else:
            store_stats = options_store.get_stats()
            print(f"Market premium source: {store_stats.get('ticker_count', 0)} tickers, "
                  f"{store_stats.get('snapshot_dates', 0)} snapshot dates")
            pm = get_market_premium_model(
                options_store, fallback_name=args.premium_model
            )
    else:
        pm = get_premium_model(args.premium_model)

    em = get_execution_model(args.execution_model)

    if args.print_assumptions:
        assumptions = build_assumptions(
            "backtest_pullback_csp.py",
            min_market_cap=MIN_MARKET_CAP,
            min_price=MIN_PRICE,
            min_volume=MIN_VOLUME,
            valid_exchanges=sorted(VALID_EXCHANGES),
            dte=args.dte,
            strike_offsets=STRIKE_OFFSETS,
            premium_model=pm,
            execution_model=em,
            ema_fast=EMA_FAST,
            ema_slow=EMA_SLOW,
            min_prior_streak=args.min_prior_streak,
            walk_forward_enabled=args.walk_forward,
            train_days=args.train_days if args.walk_forward else None,
            test_days=args.test_days if args.walk_forward else None,
        )
        assumptions.print_summary()
        sys.exit(0)

    run_backtest(
        args.dte,
        args.min_prior_streak,
        args.dte_alt,
        premium_model=pm,
        exec_model=em,
        walk_forward=args.walk_forward,
        train_days=args.train_days,
        test_days=args.test_days,
    )
