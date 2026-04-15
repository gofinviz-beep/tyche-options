"""Backtest stock buying on deep EMA dips with covered call overlay.

Simulates buying stocks when they dip significantly below their 21-EMA
or 50-EMA, then selling covered calls during the recovery period.
Measures dual returns: stock appreciation + covered call premium.

Entry conditions (all must hold):
  1. Price is >= oversold_dip_pct below the 21-EMA or 50-EMA
  2. Stock had a prior uptrend (min_prior_streak days above both EMAs)
  3. Market cap >= $10B (mega/large-cap recovery candidates)

Exit conditions (first to trigger):
  - PROFIT TARGET: Price recovers above 21-EMA (mean-reversion achieved)
  - STOP LOSS: Price drops an additional stop_loss_pct from entry
  - TIME EXIT: max_hold_days reached without recovery

Covered call overlay:
  - At entry, simulate selling OTM call at cc_strike_pct above entry price
  - CC DTE = cc_dte days, rolled at expiration if still holding
  - Premium estimated via iv_proxy model

Breakdowns by: dip depth, dip type (21 vs 50 EMA), market cap tier,
prior streak length, RSI at entry, entry day of week, recovery time.

CLI flags:
  --min-dip-pct N       Min % below EMA to qualify (default: 5)
  --min-prior-streak N  Min prior uptrend days (default: 10)
  --max-hold-days N     Max days to hold (default: 60)
  --stop-loss-pct N     Stop loss % below entry (default: 15)
  --cc-strike-pct N     CC strike % above entry (default: 5)
  --cc-dte N            CC hold period (default: 14)
  --walk-forward        Enable rolling walk-forward analysis
  --train-days N        Walk-forward train window (default: 252)
  --test-days N         Walk-forward test window (default: 126)
"""

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, "src")

from tyche.backtest.walk_forward import WalkForwardRunner, WindowResult
from tyche.config import get_settings
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore

EMA_FAST = 8
EMA_SLOW = 21
EMA_LONG = 50
PROXIMITY_PCT = 2.0
MIN_BARS = 60
MIN_PRICE = 15.0
MIN_MARKET_CAP = 10_000_000_000  # $10B — larger-cap for recovery reliability
MIN_VOLUME = 500_000
VALID_EXCHANGES = {"XNYS", "XNAS", "XNMS", "XASE", "ARCX", "BATS"}

CC_PREMIUM_PCT = 0.008  # ~0.8% for a 14-day OTM call (conservative estimate)


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


def compute_rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return 50.0
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    last_loss = float(loss.iloc[-1])
    if last_loss == 0:
        return 100.0
    rs = float(gain.iloc[-1]) / last_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_prior_streak(above_both: pd.Series, idx: int) -> int:
    streak = 0
    for i in range(idx - 1, -1, -1):
        if above_both.iloc[i]:
            streak += 1
        else:
            break
    return streak


@dataclass
class DeepDipSim:
    entry_date: date
    symbol: str
    dip_type: str  # "21ema" or "50ema"
    entry_price: float
    ema_21: float
    ema_50: float
    ema_8_slope: float
    ema_21_slope: float
    dip_pct: float  # how far below the reference EMA (positive = deeper)
    prior_streak: int
    rsi_at_entry: float
    volume_ratio: float  # entry vol / 20d avg vol
    market_cap: float

    exit_date: date | None = None
    exit_price: float = 0.0
    exit_reason: str = ""  # "recovery", "stop_loss", "time_exit"
    hold_days: int = 0
    stock_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    max_gain_pct: float = 0.0
    cc_rounds: int = 0
    cc_premium_total_pct: float = 0.0
    combined_return_pct: float = 0.0


def _scan_deep_dips(
    ticker_frames: dict[str, pd.DataFrame],
    scan_dates: list[date],
    all_market_caps: dict[str, float],
    min_dip_pct: float,
    min_prior_streak: int,
    max_hold_days: int,
    stop_loss_pct: float,
    cc_strike_pct: float,
    cc_dte: int,
    warmup: int,
) -> list[DeepDipSim]:
    simulations: list[DeepDipSim] = []

    for ticker, df in ticker_frames.items():
        close = df["close"].astype(float)
        low = df["low"].astype(float)
        high = df["high"].astype(float)
        volume = df["volume"].astype(float)
        dates = df["date"]

        ema_8 = compute_ema(close, EMA_FAST)
        ema_21 = compute_ema(close, EMA_SLOW)
        ema_50 = compute_ema(close, EMA_LONG)
        above_both = (close > ema_8) & (close > ema_21)

        avg_vol_20 = volume.rolling(20, min_periods=5).mean()

        n = len(df)
        cap = all_market_caps.get(ticker, 0)

        for i in range(warmup, n):
            price = close.iloc[i]
            vol = volume.iloc[i]

            if price < MIN_PRICE or vol < MIN_VOLUME:
                continue

            e21 = ema_21.iloc[i]
            e50 = ema_50.iloc[i]
            e8 = ema_8.iloc[i]

            pct_to_21 = ((price - e21) / e21 * 100) if e21 else 0
            pct_to_50 = ((price - e50) / e50 * 100) if e50 else 0

            above_8 = price > e8
            above_21 = price > e21
            above_50 = price > e50

            if above_8 or above_21:
                continue

            slope_8 = compute_slope(ema_8.iloc[max(0, i - 2) : i + 1])
            slope_21 = compute_slope(ema_21.iloc[max(0, i - 2) : i + 1])

            dip_type: str | None = None
            dip_pct = 0.0

            if not above_50 and pct_to_50 <= -min_dip_pct:
                dip_type = "50ema"
                dip_pct = abs(pct_to_50)
            elif pct_to_21 <= -min_dip_pct:
                dip_type = "21ema"
                dip_pct = abs(pct_to_21)

            if dip_type is None:
                continue

            prior_streak_val = compute_prior_streak(above_both, i)
            if prior_streak_val < min_prior_streak:
                continue

            scan_date = dates.iloc[i]
            if scan_dates and scan_date not in scan_dates:
                continue

            rsi = compute_rsi(close.iloc[max(0, i - 20) : i + 1])
            vol_ratio = float(volume.iloc[i] / avg_vol_20.iloc[i]) if avg_vol_20.iloc[i] > 0 else 1.0

            forward = df.iloc[i + 1 : i + 1 + max_hold_days]
            if len(forward) < 5:
                continue

            stop_price = price * (1 - stop_loss_pct / 100)
            exit_price = 0.0
            exit_reason = "time_exit"
            exit_idx = len(forward) - 1
            max_dd = 0.0
            max_gain = 0.0

            for j in range(len(forward)):
                day_low = float(forward["low"].iloc[j])
                day_high = float(forward["high"].iloc[j])
                day_close = float(forward["close"].iloc[j])

                dd = ((day_low - price) / price) * 100
                if dd < max_dd:
                    max_dd = dd

                gain = ((day_high - price) / price) * 100
                if gain > max_gain:
                    max_gain = gain

                ema_21_at_j = float(ema_21.iloc[i + 1 + j]) if (i + 1 + j) < n else e21
                if day_close > ema_21_at_j:
                    exit_reason = "recovery"
                    exit_idx = j
                    exit_price = day_close
                    break

                if day_low <= stop_price:
                    exit_reason = "stop_loss"
                    exit_idx = j
                    exit_price = stop_price
                    break

            if exit_price == 0.0:
                exit_price = float(forward["close"].iloc[exit_idx])

            hold_days_val = exit_idx + 1
            stock_return = ((exit_price - price) / price) * 100

            cc_rounds = max(1, hold_days_val // cc_dte)
            cc_premium = cc_rounds * CC_PREMIUM_PCT * 100

            simulations.append(DeepDipSim(
                entry_date=scan_date,
                symbol=ticker,
                dip_type=dip_type,
                entry_price=price,
                ema_21=round(e21, 4),
                ema_50=round(e50, 4),
                ema_8_slope=round(slope_8, 6),
                ema_21_slope=round(slope_21, 6),
                dip_pct=round(dip_pct, 2),
                prior_streak=prior_streak_val,
                rsi_at_entry=round(rsi, 2),
                volume_ratio=round(vol_ratio, 2),
                market_cap=cap,
                exit_date=forward["date"].iloc[exit_idx],
                exit_price=round(exit_price, 2),
                exit_reason=exit_reason,
                hold_days=hold_days_val,
                stock_return_pct=round(stock_return, 2),
                max_drawdown_pct=round(max_dd, 2),
                max_gain_pct=round(max_gain, 2),
                cc_rounds=cc_rounds,
                cc_premium_total_pct=round(cc_premium, 2),
                combined_return_pct=round(stock_return + cc_premium, 2),
            ))

    return simulations


def _print_summary(sims: list[DeepDipSim], label: str) -> None:
    if not sims:
        print(f"\n  {label}: no data")
        return

    n = len(sims)
    recoveries = sum(1 for s in sims if s.exit_reason == "recovery")
    stops = sum(1 for s in sims if s.exit_reason == "stop_loss")
    time_exits = sum(1 for s in sims if s.exit_reason == "time_exit")
    recovery_rate = recoveries / n * 100

    avg_stock_return = sum(s.stock_return_pct for s in sims) / n
    avg_combined = sum(s.combined_return_pct for s in sims) / n
    avg_cc_prem = sum(s.cc_premium_total_pct for s in sims) / n
    avg_dd = sum(s.max_drawdown_pct for s in sims) / n
    worst_dd = min(s.max_drawdown_pct for s in sims)
    avg_hold = sum(s.hold_days for s in sims) / n
    median_hold = float(np.median([s.hold_days for s in sims]))
    cum_return = sum(s.combined_return_pct for s in sims)
    unique_symbols = len(set(s.symbol for s in sims))

    winners = [s for s in sims if s.combined_return_pct > 0]
    win_rate = len(winners) / n * 100

    print(f"\n  {label}")
    print(f"    Trades: {n}  |  Unique symbols: {unique_symbols}")
    print(f"    Recovery rate: {recovery_rate:.1f}% "
          f"({recoveries} recovered, {stops} stopped, {time_exits} timed out)")
    print(f"    Win rate (combined > 0): {win_rate:.1f}%")
    print(f"    Avg stock return: {avg_stock_return:+.2f}%  |  "
          f"Avg CC premium: +{avg_cc_prem:.2f}%")
    print(f"    Avg combined return: {avg_combined:+.2f}%  |  "
          f"Cumulative: {cum_return:+.1f}%")
    print(f"    Avg drawdown: {avg_dd:+.2f}%  |  Worst: {worst_dd:+.2f}%")
    print(f"    Avg hold: {avg_hold:.1f} days  |  Median: {median_hold:.0f} days")


def _stats(sims: list[DeepDipSim], label: str) -> None:
    if not sims:
        print(f"  {label:35s}: no data")
        return
    n = len(sims)
    rec = sum(1 for s in sims if s.exit_reason == "recovery")
    rr = rec / n * 100
    ar = sum(s.combined_return_pct for s in sims) / n
    ad = sum(s.max_drawdown_pct for s in sims) / n
    wd = min(s.max_drawdown_pct for s in sims)
    ah = sum(s.hold_days for s in sims) / n
    print(
        f"  {label:35s}: {n:5d} trades | rec={rr:5.1f}% | "
        f"avg_ret={ar:+6.2f}% | avg_dd={ad:+6.2f}% | "
        f"worst_dd={wd:+7.2f}% | avg_hold={ah:4.1f}d"
    )


def run_backtest(
    min_dip_pct: float,
    min_prior_streak: int,
    max_hold_days: int,
    stop_loss_pct: float,
    cc_strike_pct: float,
    cc_dte: int,
    walk_forward: bool = False,
    train_days: int = 252,
    test_days: int = 126,
) -> None:
    settings = get_settings()
    store = OHLCVStore(data_dir=settings.data_dir)
    meta_store = TickerMetaStore(data_dir=settings.data_dir)

    if not store.exists:
        print("ERROR: OHLCV store is empty. Run bootstrap first.")
        return

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
        cap_msg = f", mkt_cap >= ${MIN_MARKET_CAP / 1e9:.0f}B" if has_cap_data else ""
        print(f"Tickers passing exchange{cap_msg} filter: {len(qualified_tickers)}")
    else:
        qualified_tickers = set(store.get_all_tickers())
        print(f"No metadata filter — using all {len(qualified_tickers)} tickers")

    all_ohlcv_tickers = set(store.get_all_tickers())
    target_tickers = qualified_tickers & all_ohlcv_tickers
    ticker_data = store.read_tickers(list(target_tickers))

    ticker_frames: dict[str, pd.DataFrame] = {}
    for ticker, df in ticker_data.items():
        if len(df) >= MIN_BARS:
            ticker_frames[ticker] = df.sort_values("date").reset_index(drop=True)

    all_dates: set[date] = set()
    for df in ticker_frames.values():
        all_dates.update(df["date"].unique())
    all_dates_sorted = sorted(all_dates)

    if len(all_dates_sorted) < MIN_BARS + max_hold_days:
        print("ERROR: Not enough trading days for backtest.")
        return

    print(f"\nTickers with sufficient data: {len(ticker_frames)}")
    print(f"Date range: {all_dates_sorted[0]} to {all_dates_sorted[-1]}")
    print(f"Total trading days: {len(all_dates_sorted)}")
    print(f"\nParams: min_dip={min_dip_pct}%, prior_streak>={min_prior_streak}, "
          f"max_hold={max_hold_days}d, stop={stop_loss_pct}%, "
          f"cc_strike=+{cc_strike_pct}%, cc_dte={cc_dte}d")

    print(f"\n{'#' * 100}")
    print(f"{'DEEP DIP RECOVERY + COVERED CALL BACKTEST':^100}")
    print(f"{'#' * 100}")

    if walk_forward:
        _run_walk_forward(
            ticker_frames, all_dates_sorted, all_market_caps,
            min_dip_pct, min_prior_streak, max_hold_days,
            stop_loss_pct, cc_strike_pct, cc_dte,
            MIN_BARS, train_days, test_days,
        )
        return

    simulations = _scan_deep_dips(
        ticker_frames, [], all_market_caps,
        min_dip_pct, min_prior_streak, max_hold_days,
        stop_loss_pct, cc_strike_pct, cc_dte, MIN_BARS,
    )

    if not simulations:
        print("\nNo deep dip entries found!")
        return

    print(f"\nTotal simulations: {len(simulations)}")

    _print_summary(simulations, "ALL DEEP DIP ENTRIES")

    # Stock-only vs stock+CC comparison
    print(f"\n{'=' * 100}")
    print("STOCK-ONLY vs STOCK + COVERED CALL")
    print(f"{'=' * 100}")
    n = len(simulations)
    stock_avg = sum(s.stock_return_pct for s in simulations) / n
    combined_avg = sum(s.combined_return_pct for s in simulations) / n
    cc_contrib = combined_avg - stock_avg
    print(f"  Stock-only avg return: {stock_avg:+.2f}%")
    print(f"  Stock + CC avg return: {combined_avg:+.2f}%")
    print(f"  CC premium contribution: +{cc_contrib:.2f}%")

    # Breakdowns
    print(f"\n{'=' * 100}")
    print("BREAKDOWNS")
    print(f"{'=' * 100}")

    print(f"\n--- By Dip Type ---")
    _stats([s for s in simulations if s.dip_type == "21ema"], "DIP BELOW 21-EMA")
    _stats([s for s in simulations if s.dip_type == "50ema"], "DIP BELOW 50-EMA")

    print(f"\n--- By Dip Depth ---")
    for lo, hi, label in [
        (5, 8, "5-8% below EMA"),
        (8, 12, "8-12% below EMA"),
        (12, 18, "12-18% below EMA"),
        (18, 100, "18%+ below EMA"),
    ]:
        _stats([s for s in simulations if lo <= s.dip_pct < hi], label)

    print(f"\n--- By Prior Streak ---")
    for lo, hi, label in [
        (5, 10, "5-9 days"),
        (10, 20, "10-19 days"),
        (20, 40, "20-39 days"),
        (40, 999, "40+ days"),
    ]:
        _stats([s for s in simulations if lo <= s.prior_streak < hi], label)

    print(f"\n--- By RSI at Entry ---")
    for lo, hi, label in [
        (0, 20, "RSI < 20 (deeply oversold)"),
        (20, 30, "RSI 20-30 (oversold)"),
        (30, 40, "RSI 30-40"),
        (40, 50, "RSI 40-50"),
        (50, 100, "RSI 50+"),
    ]:
        _stats([s for s in simulations if lo <= s.rsi_at_entry < hi], label)

    print(f"\n--- By Volume Spike at Entry ---")
    _stats([s for s in simulations if s.volume_ratio > 2.0], "Volume spike (>2x avg)")
    _stats([s for s in simulations if 1.0 < s.volume_ratio <= 2.0], "Normal volume (1-2x)")
    _stats([s for s in simulations if s.volume_ratio <= 1.0], "Low volume (<1x avg)")

    print(f"\n--- By Entry Day of Week ---")
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    for dow in range(5):
        _stats(
            [s for s in simulations if s.entry_date.weekday() == dow],
            day_names[dow],
        )

    print(f"\n--- By Market Cap ---")
    for lo, hi, label in [
        (10e9, 30e9, "$10B-30B"),
        (30e9, 100e9, "$30B-100B"),
        (100e9, 500e9, "$100B-500B"),
        (500e9, 1e15, "$500B+ (mega-cap)"),
    ]:
        _stats([s for s in simulations if lo <= s.market_cap < hi], label)

    print(f"\n--- By Exit Reason ---")
    for reason in ["recovery", "stop_loss", "time_exit"]:
        subset = [s for s in simulations if s.exit_reason == reason]
        if subset:
            avg_ret = sum(s.combined_return_pct for s in subset) / len(subset)
            print(f"  {reason:20s}: {len(subset):5d} trades | "
                  f"avg_combined_return={avg_ret:+.2f}%")

    print(f"\n--- HIGH-CONVICTION: 50-EMA dip + RSI < 30 + prior streak >= 15 ---")
    high_conv = [
        s for s in simulations
        if s.dip_type == "50ema" and s.rsi_at_entry < 30 and s.prior_streak >= 15
    ]
    _stats(high_conv, "High-conviction subset")

    print(f"\n--- 10 Worst Trades ---")
    worst = sorted(simulations, key=lambda s: s.combined_return_pct)[:10]
    for s in worst:
        cap_b = s.market_cap / 1e9
        print(
            f"  {s.entry_date} {s.symbol:6s} | ${s.entry_price:7.2f}→${s.exit_price:7.2f} "
            f"(stock={s.stock_return_pct:+5.1f}% cc=+{s.cc_premium_total_pct:.1f}% "
            f"comb={s.combined_return_pct:+5.1f}%) | "
            f"dip={s.dip_pct:.1f}% {s.dip_type} rsi={s.rsi_at_entry:.0f} "
            f"streak={s.prior_streak} hold={s.hold_days}d [{s.exit_reason}] "
            f"cap=${cap_b:.1f}B"
        )

    print(f"\n--- 10 Best Trades ---")
    best = sorted(simulations, key=lambda s: -s.combined_return_pct)[:10]
    for s in best:
        cap_b = s.market_cap / 1e9
        print(
            f"  {s.entry_date} {s.symbol:6s} | ${s.entry_price:7.2f}→${s.exit_price:7.2f} "
            f"(stock={s.stock_return_pct:+5.1f}% cc=+{s.cc_premium_total_pct:.1f}% "
            f"comb={s.combined_return_pct:+5.1f}%) | "
            f"dip={s.dip_pct:.1f}% {s.dip_type} rsi={s.rsi_at_entry:.0f} "
            f"streak={s.prior_streak} hold={s.hold_days}d [{s.exit_reason}] "
            f"cap=${cap_b:.1f}B"
        )

    print(f"\n--- Top 20 Most Frequent Symbols ---")
    freq: dict[str, int] = defaultdict(int)
    sym_ret: dict[str, list[float]] = defaultdict(list)
    for s in simulations:
        freq[s.symbol] += 1
        sym_ret[s.symbol].append(s.combined_return_pct)

    top_syms = sorted(freq.items(), key=lambda x: -x[1])[:20]
    for sym, count in top_syms:
        avg_r = sum(sym_ret[sym]) / count
        rec = sum(1 for s in simulations if s.symbol == sym and s.exit_reason == "recovery")
        rr = rec / count * 100
        cap_b = all_market_caps.get(sym, 0) / 1e9
        print(f"  {sym:6s}: {count:3d} entries | rec={rr:5.1f}% | "
              f"avg_ret={avg_r:+5.2f}% | cap=${cap_b:.1f}B")

    # Comparison table
    print(f"\n{'=' * 100}")
    print("COMPARISON TABLE")
    print(f"{'=' * 100}")
    print(
        f"\n  {'Scenario':<40s} {'Trades':>7s} {'Rec%':>7s} "
        f"{'AvgRet':>8s} {'WorstDD':>9s} {'AvgDD':>8s} {'CumRet':>10s}"
    )
    print("  " + "-" * 90)

    for dip_type in ["21ema", "50ema"]:
        subset = [s for s in simulations if s.dip_type == dip_type]
        _comparison_row(subset, f"Deep dip below {dip_type.upper()}")

    _comparison_row(high_conv, "High-conviction (50EMA+RSI<30)")
    _comparison_row(simulations, "ALL entries combined")


def _comparison_row(sims: list[DeepDipSim], label: str) -> None:
    if not sims:
        print(f"  {label:<40s} {'0':>7s} {'---':>7s} {'---':>8s} "
              f"{'---':>9s} {'---':>8s} {'---':>10s}")
        return
    n = len(sims)
    rec = sum(1 for s in sims if s.exit_reason == "recovery")
    rr = rec / n * 100
    ar = sum(s.combined_return_pct for s in sims) / n
    ad = sum(s.max_drawdown_pct for s in sims) / n
    wd = min(s.max_drawdown_pct for s in sims)
    cum = sum(s.combined_return_pct for s in sims)
    print(
        f"  {label:<40s} {n:>7d} {rr:>6.1f}% "
        f"{ar:>+7.2f}% {wd:>+8.2f}% {ad:>+7.2f}% {cum:>+9.1f}%"
    )


def _run_walk_forward(
    ticker_frames: dict[str, pd.DataFrame],
    all_dates_sorted: list[date],
    all_market_caps: dict[str, float],
    min_dip_pct: float,
    min_prior_streak: int,
    max_hold_days: int,
    stop_loss_pct: float,
    cc_strike_pct: float,
    cc_dte: int,
    warmup: int,
    train_days: int,
    test_days: int,
) -> None:
    def run_window(
        train_dates: list[date],
        test_dates: list[date],
        **ctx: object,
    ) -> WindowResult:
        test_set = set(test_dates)
        sims = _scan_deep_dips(
            ticker_frames, list(test_set), all_market_caps,
            min_dip_pct, min_prior_streak, max_hold_days,
            stop_loss_pct, cc_strike_pct, cc_dte, warmup,
        )
        total = len(sims)
        wins = sum(1 for s in sims if s.combined_return_pct > 0)
        pnl = sum(s.combined_return_pct for s in sims)
        avg_pnl = pnl / total if total else 0.0
        worst_dd = min((s.max_drawdown_pct for s in sims), default=0.0)

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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest deep dip stock buying with covered call overlay"
    )
    parser.add_argument(
        "--min-dip-pct", type=float, default=5.0,
        help="Minimum %% below EMA to qualify as deep dip (default: 5)",
    )
    parser.add_argument(
        "--min-prior-streak", type=int, default=10,
        help="Minimum prior uptrend days above both EMAs (default: 10)",
    )
    parser.add_argument(
        "--max-hold-days", type=int, default=60,
        help="Maximum days to hold before forced exit (default: 60)",
    )
    parser.add_argument(
        "--stop-loss-pct", type=float, default=15.0,
        help="Stop loss %% below entry price (default: 15)",
    )
    parser.add_argument(
        "--cc-strike-pct", type=float, default=5.0,
        help="Covered call strike %% above entry price (default: 5)",
    )
    parser.add_argument(
        "--cc-dte", type=int, default=14,
        help="Covered call DTE for rolling (default: 14)",
    )
    parser.add_argument(
        "--walk-forward", action="store_true",
        help="Enable rolling walk-forward analysis",
    )
    parser.add_argument(
        "--train-days", type=int, default=252,
        help="Walk-forward train window (default: 252 ≈ 1 year)",
    )
    parser.add_argument(
        "--test-days", type=int, default=126,
        help="Walk-forward test window (default: 126 ≈ 6 months)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_backtest(
        min_dip_pct=args.min_dip_pct,
        min_prior_streak=args.min_prior_streak,
        max_hold_days=args.max_hold_days,
        stop_loss_pct=args.stop_loss_pct,
        cc_strike_pct=args.cc_strike_pct,
        cc_dte=args.cc_dte,
        walk_forward=args.walk_forward,
        train_days=args.train_days,
        test_days=args.test_days,
    )
