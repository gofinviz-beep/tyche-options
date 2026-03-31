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
  - win rate, avg / worst drawdown, simulated CSP P&L (1.5% premium model)
  - breakdowns by pullback type, volume decline, prior streak, day of week

Usage:
    cd backend && python scripts/backtest_pullback_csp.py [--dte 8] [--min-prior-streak 5]
"""

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, "src")

from tyche.config import TycheSettings
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore

# ── Constants ────────────────────────────────────────────────────────────

EMA_FAST = 8
EMA_SLOW = 21
PROXIMITY_PCT = 2.0
MIN_BARS = 50
MIN_PRICE = 15.0
MIN_MARKET_CAP = 5_000_000_000  # $5B
MIN_VOLUME = 500_000
VALID_EXCHANGES = {"XNYS", "XNAS", "XNMS", "XASE", "ARCX", "BATS"}

STRIKE_OFFSETS = [0.0, 3.0, 5.0]  # % below support EMA

PREMIUM_PCT_BY_OFFSET = {
    0.0: 0.025,   # ATM-at-support: ~2.5% premium
    3.0: 0.015,   # 3% OTM: ~1.5% premium (matches backtest_ema.py)
    5.0: 0.010,   # 5% OTM: ~1.0% premium
}


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
    market_cap: float = 0.0

    exit_date: date | None = None
    exit_price: float = 0.0
    min_price_during: float = 0.0
    max_drawdown_pct: float = 0.0
    stayed_above_strike: bool = False
    forward_return_pct: float = 0.0


# ── Main backtest ────────────────────────────────────────────────────────

def run_backtest(dte: int, min_prior_streak: int, dte_alt: int | None) -> None:
    settings = TycheSettings()
    store = OHLCVStore(data_dir=settings.data_dir)
    meta_store = TickerMetaStore(data_dir=settings.data_dir)

    if not store.exists:
        print("ERROR: OHLCV store is empty. Run bootstrap first.")
        return

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

    # Build master date list across all tickers
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

    dte_list = [dte]
    if dte_alt and dte_alt != dte:
        dte_list.append(dte_alt)

    for current_dte in dte_list:
        print(f"\n{'#' * 80}")
        print(f"# PULLBACK CSP BACKTEST — DTE={current_dte}, "
              f"min_prior_streak={min_prior_streak}")
        print(f"{'#' * 80}")
        _run_for_dte(
            ticker_frames, all_dates_sorted, all_market_caps,
            current_dte, min_prior_streak, warmup,
        )


def _run_for_dte(
    ticker_frames: dict[str, pd.DataFrame],
    all_dates_sorted: list[date],
    all_market_caps: dict[str, float],
    dte: int,
    min_prior_streak: int,
    warmup: int,
) -> None:
    simulations: list[PullbackCSPSim] = []
    pullback_day_count = 0

    for ticker, df in ticker_frames.items():
        close = df["close"].astype(float)
        low = df["low"].astype(float)
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

            prior_streak = compute_prior_streak(above_both, i)
            if prior_streak < min_prior_streak:
                continue

            pullback_type = "8ema" if trend == "pullback_to_8ema" else "21ema"
            support_ema = e8 if pullback_type == "8ema" else e21
            vol_declining = is_volume_declining(volume, i)
            scan_date = dates.iloc[i]
            cap = all_market_caps.get(ticker, 0)

            pullback_day_count += 1

            # Forward window
            forward = df.iloc[i + 1 : i + 1 + dte]
            if len(forward) < dte - 1:
                continue

            exit_price = float(forward["close"].iloc[-1])
            fwd_return = ((exit_price - price) / price) * 100

            for offset in STRIKE_OFFSETS:
                strike = round(support_ema * (1 - offset / 100), 2)

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
                    prior_streak=prior_streak,
                    volume_declining=vol_declining,
                    market_cap=cap,
                    exit_date=forward["date"].iloc[-1],
                    exit_price=exit_price,
                    min_price_during=min_price,
                    max_drawdown_pct=max_dd,
                    stayed_above_strike=min_price > strike,
                    forward_return_pct=fwd_return,
                ))

    # ── Report ───────────────────────────────────────────────────────
    print(f"\nPullback entry days found: {pullback_day_count}")

    if not simulations:
        print("No simulations generated!")
        return

    print(f"Total simulations (entries x {len(STRIKE_OFFSETS)} offsets): {len(simulations)}")
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
        _print_scenario_summary(subset, label, offset)
        print()

    # ── Breakdowns (on the 3% offset as the baseline comparison) ─────
    baseline = [s for s in simulations if s.strike_offset_pct == 3.0]
    if not baseline:
        baseline = simulations

    print("\n" + "=" * 100)
    print("BREAKDOWNS (3% below EMA scenario)")
    print("=" * 100)

    # By pullback type
    print(f"\n--- By Pullback Type ---")
    _stats([s for s in baseline if s.pullback_type == "8ema"], "PULLBACK TO 8-EMA", 3.0)
    _stats([s for s in baseline if s.pullback_type == "21ema"], "PULLBACK TO 21-EMA", 3.0)

    # By volume declining
    print(f"\n--- By Volume on Pullback ---")
    _stats([s for s in baseline if s.volume_declining], "Volume DECLINING", 3.0)
    _stats([s for s in baseline if not s.volume_declining], "Volume NOT declining", 3.0)

    # By prior streak
    print(f"\n--- By Prior Streak (days above both EMAs before pullback) ---")
    for lo, hi, label in [
        (3, 5, "3-4 days"),
        (5, 10, "5-9 days"),
        (10, 20, "10-19 days"),
        (20, 999, "20+ days"),
    ]:
        _stats([s for s in baseline if lo <= s.prior_streak < hi], label, 3.0)

    # By day of week
    print(f"\n--- By Entry Day of Week ---")
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    for dow in range(5):
        _stats(
            [s for s in baseline if s.entry_date.weekday() == dow],
            day_names[dow], 3.0,
        )

    # By market cap
    print(f"\n--- By Market Cap ---")
    for lo, hi, label in [
        (0, 5e9, "<$5B (no metadata)"),
        (5e9, 10e9, "$5B-10B"),
        (10e9, 50e9, "$10B-50B"),
        (50e9, 200e9, "$50B-200B"),
        (200e9, 1e15, "$200B+"),
    ]:
        _stats([s for s in baseline if lo <= s.market_cap < hi], label, 3.0)

    # Combined: 21-EMA pullback + volume declining (highest-conviction)
    print(f"\n--- HIGH-CONVICTION: 21-EMA pullback + Volume Declining ---")
    for offset in STRIKE_OFFSETS:
        label = f"  offset={offset:.0f}%"
        subset = [
            s for s in simulations
            if s.strike_offset_pct == offset
            and s.pullback_type == "21ema"
            and s.volume_declining
        ]
        _stats(subset, label, offset)

    # 10 worst trades (3% offset)
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

    # Top 20 most frequent symbols (3% offset)
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
        _comparison_row(subset, label, offset)

    # High-conviction subset
    for offset in STRIKE_OFFSETS:
        subset = [
            s for s in simulations
            if s.strike_offset_pct == offset
            and s.pullback_type == "21ema"
            and s.volume_declining
        ]
        label = f"  21EMA+VolDecl @ {offset:.0f}% below"
        _comparison_row(subset, label, offset)

    print(
        f"\n  {'Uptrend CSP baseline (backtest_ema)':40s} "
        f"{'---':>7s} {'~69%':>7s} {'---':>8s} {'---':>9s} {'---':>8s} {'---':>10s}"
    )
    print("  (run backtest_ema.py for exact baseline numbers)")


# ── Reporting helpers ────────────────────────────────────────────────────

def _print_scenario_summary(
    sims: list[PullbackCSPSim], label: str, offset: float,
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

    premium_pct = PREMIUM_PCT_BY_OFFSET.get(offset, 0.015)
    total_pnl_pct = 0.0
    for s in sims:
        if s.stayed_above_strike:
            total_pnl_pct += premium_pct * 100
        else:
            loss = ((s.strike - s.min_price_during) / s.strike) * 100
            total_pnl_pct += premium_pct * 100 - loss
    avg_pnl = total_pnl_pct / n

    unique_symbols = len(set(s.symbol for s in sims))

    print(f"  {label}")
    print(f"    Trades: {n}  |  Unique symbols: {unique_symbols}")
    print(f"    Win rate (put expires OTM): {win_rate:.1f}%")
    print(f"    Assignment rate: {(n - wins) / n * 100:.1f}%")
    print(f"    Avg {sims[0].exit_date and 'forward' or ''} return: {avg_return:+.2f}%"
          f"  |  Median: {median_return:+.2f}%")
    print(f"    Avg max drawdown: {avg_dd:+.2f}%  |  Worst: {worst_dd:+.2f}%")
    print(f"    Premium assumption: {premium_pct*100:.1f}% of notional")
    print(f"    Avg trade P&L: {avg_pnl:+.2f}%  |  Cumulative: {total_pnl_pct:+.1f}%")


def _stats(
    sims: list[PullbackCSPSim], label: str, offset: float,
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

    premium_pct = PREMIUM_PCT_BY_OFFSET.get(offset, 0.015)
    pnl = 0.0
    for s in sims:
        if s.stayed_above_strike:
            pnl += premium_pct * 100
        else:
            loss = ((s.strike - s.min_price_during) / s.strike) * 100
            pnl += premium_pct * 100 - loss
    avg_pnl = pnl / n

    print(
        f"  {label:35s}: {n:5d} trades | win={wr:5.1f}% | "
        f"avg_ret={ar:+6.2f}% | avg_dd={ad:+6.2f}% | "
        f"worst_dd={wd:+7.2f}% | avg_pnl={avg_pnl:+5.2f}%"
    )


def _comparison_row(
    sims: list[PullbackCSPSim], label: str, offset: float,
) -> None:
    if not sims:
        print(f"  {label:<40s} {'0':>7s} {'---':>7s} {'---':>8s} {'---':>9s} {'---':>8s} {'---':>10s}")
        return
    n = len(sims)
    w = sum(1 for s in sims if s.stayed_above_strike)
    wr = w / n * 100
    ad = sum(s.max_drawdown_pct for s in sims) / n
    wd = min(s.max_drawdown_pct for s in sims)

    premium_pct = PREMIUM_PCT_BY_OFFSET.get(offset, 0.015)
    pnl = 0.0
    for s in sims:
        if s.stayed_above_strike:
            pnl += premium_pct * 100
        else:
            loss = ((s.strike - s.min_price_during) / s.strike) * 100
            pnl += premium_pct * 100 - loss
    avg_pnl = pnl / n

    print(
        f"  {label:<40s} {n:>7d} {wr:>6.1f}% "
        f"{avg_pnl:>+7.2f}% {wd:>+8.2f}% {ad:>+7.2f}% {pnl:>+9.1f}%"
    )


# ── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
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
    args = parser.parse_args()
    run_backtest(args.dte, args.min_prior_streak, args.dte_alt)
