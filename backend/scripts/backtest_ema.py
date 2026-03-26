"""Backtest the 8/21 EMA conviction strategy using production-identical filters.

For each trading day in the backtest window:
1. Load ticker metadata (market cap, exchange) from ticker_meta.parquet
2. Run conviction engine (with 3% extension cap) on all qualified tickers
3. Pick top CSP-eligible stocks
4. Simulate selling a CSP at ~5% OTM strike
5. Measure forward 8-day returns and P&L

Uses the SAME filters as the production scanner:
- Market cap >= $500M (from Polygon ticker reference)
- Exchange in NYSE/NASDAQ (from Polygon ticker reference)
- Min price >= $15
- Extension <= 3% (built into conviction engine)
"""

import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

sys.path.insert(0, "src")

from tyche.config import TycheSettings
from tyche.conviction.engine import ConvictionEngine
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore

DTE = 8
OTM_PCT = 0.05
MIN_PRICE = 15.0
MIN_MARKET_CAP = 5_000_000_000  # $5B
MIN_VOLUME = 500_000
TOP_N_PER_DAY = 10
PREMIUM_PCT = 0.015  # assume 1.5% premium on notional

VALID_EXCHANGES = {"XNYS", "XNAS", "XNMS", "XASE", "ARCX", "BATS"}


@dataclass
class CSPSimulation:
    entry_date: date
    symbol: str
    conviction: str
    trend_state: str
    entry_price: float
    strike: float
    ema_8: float
    ema_21: float
    price_to_8ema_pct: float
    days_above_emas: int
    market_cap: float = 0.0

    exit_date: date | None = None
    exit_price: float = 0.0
    min_price_during: float = 0.0
    max_drawdown_pct: float = 0.0
    stayed_above_strike: bool = False
    forward_return_pct: float = 0.0


def run_backtest():
    settings = TycheSettings()
    store = OHLCVStore(data_dir=settings.data_dir)
    meta_store = TickerMetaStore(data_dir=settings.data_dir)
    engine = ConvictionEngine(
        ema_fast=8, ema_slow=21, max_extension_pct=3.0,
        min_days_above_emas=5, max_days_above_emas=10,
    )

    # Load ticker metadata
    if not meta_store.exists:
        print("ERROR: ticker_meta.parquet not found. Run bootstrap first.")
        return
    all_market_caps = meta_store.get_market_caps()
    all_exchanges = meta_store.get_exchanges()
    print(f"Ticker metadata: {len(all_market_caps)} tickers with market cap")

    # Pre-filter: tickers with valid exchange AND market cap >= $500M
    qualified_tickers = set()
    for ticker, cap in all_market_caps.items():
        exchange = all_exchanges.get(ticker, "")
        if cap >= MIN_MARKET_CAP and exchange in VALID_EXCHANGES:
            qualified_tickers.add(ticker)
    print(f"Tickers passing market cap + exchange filter: {len(qualified_tickers)}")

    # Load OHLCV data
    table = pq.read_table(store.parquet_path)
    all_dates = sorted(pc.unique(table.column("date")).to_pylist())

    warmup = 30
    backtest_dates = all_dates[warmup:]

    print(f"\nBacktest: {backtest_dates[0]} to {backtest_dates[-1]}")
    print(f"Trading days: {len(backtest_dates)}")
    print(f"DTE: {DTE} days | OTM: {OTM_PCT*100:.0f}%")
    print(f"Filters: mkt_cap >= ${MIN_MARKET_CAP/1e6:.0f}M, price >= ${MIN_PRICE}, "
          f"extension <= 3%, valid exchange")
    print(f"Top {TOP_N_PER_DAY} picks per day")
    print("=" * 80)

    # Pre-build per-ticker DataFrames (only for qualified tickers)
    all_ohlcv_tickers = set(pc.unique(table.column("ticker")).to_pylist())
    target_tickers = qualified_tickers & all_ohlcv_tickers

    ticker_frames: dict[str, pd.DataFrame] = {}
    for ticker in target_tickers:
        mask = pc.equal(table.column("ticker"), ticker)
        sub = table.filter(mask).to_pandas().sort_values("date")
        if len(sub) >= warmup:
            ticker_frames[ticker] = sub

    print(f"Tickers with OHLCV + metadata: {len(ticker_frames)}")
    print()

    simulations: list[CSPSimulation] = []

    for day_idx, scan_date in enumerate(backtest_dates):
        if day_idx + DTE >= len(backtest_dates):
            break

        exit_date = backtest_dates[day_idx + DTE]
        picks_today: list[CSPSimulation] = []

        for ticker, full_df in ticker_frames.items():
            df_up_to = full_df[full_df["date"] <= scan_date]
            if len(df_up_to) < warmup:
                continue

            last_row = df_up_to.iloc[-1]
            price = float(last_row["close"])
            vol = int(last_row["volume"])

            if price < MIN_PRICE or vol < MIN_VOLUME:
                continue

            try:
                signal = engine.analyze(ticker, df_up_to)
            except Exception:
                continue

            if not signal.csp_eligible:
                continue
            if signal.conviction_level not in ("high", "medium"):
                continue

            entry_price = signal.last_close
            strike = round(entry_price * (1 - OTM_PCT), 2)

            forward = full_df[
                (full_df["date"] > scan_date) & (full_df["date"] <= exit_date)
            ]
            if len(forward) < DTE - 1:
                continue

            min_price = float(forward["low"].min())
            exit_price = float(forward.iloc[-1]["close"])
            max_dd = ((min_price - entry_price) / entry_price) * 100

            sim = CSPSimulation(
                entry_date=scan_date,
                symbol=ticker,
                conviction=signal.conviction_level,
                trend_state=str(signal.trend_state).split(".")[-1].lower(),
                entry_price=entry_price,
                strike=strike,
                ema_8=signal.ema_8,
                ema_21=signal.ema_21,
                price_to_8ema_pct=signal.price_to_8ema_pct,
                days_above_emas=signal.days_above_both_emas,
                market_cap=all_market_caps.get(ticker, 0),
                exit_date=exit_date,
                exit_price=exit_price,
                min_price_during=min_price,
                max_drawdown_pct=max_dd,
                stayed_above_strike=min_price > strike,
                forward_return_pct=((exit_price - entry_price) / entry_price) * 100,
            )
            picks_today.append(sim)

        # Sort: high conviction first, then lower extension (closer to EMA = better)
        conviction_order = {"high": 0, "medium": 1}
        picks_today.sort(
            key=lambda s: (
                conviction_order.get(s.conviction, 2),
                abs(s.price_to_8ema_pct),
            )
        )
        top_picks = picks_today[:TOP_N_PER_DAY]
        simulations.extend(top_picks)

        if (day_idx + 1) % 10 == 0:
            print(
                f"  Day {day_idx + 1}/{len(backtest_dates)}: "
                f"eligible={len(picks_today)}, picked={len(top_picks)}"
            )

    print()
    print("=" * 80)
    print("BACKTEST RESULTS — Production Filters + 3% Extension Cap")
    print("=" * 80)

    if not simulations:
        print("No simulations generated!")
        return

    total = len(simulations)
    wins = sum(1 for s in simulations if s.stayed_above_strike)
    losses = total - wins
    win_rate = wins / total * 100
    avg_return = sum(s.forward_return_pct for s in simulations) / total
    avg_drawdown = sum(s.max_drawdown_pct for s in simulations) / total
    worst_drawdown = min(s.max_drawdown_pct for s in simulations)

    # CSP P&L simulation
    total_pnl_pct = 0.0
    for s in simulations:
        if s.stayed_above_strike:
            total_pnl_pct += PREMIUM_PCT * 100
        else:
            loss = ((s.strike - s.min_price_during) / s.strike) * 100
            total_pnl_pct += PREMIUM_PCT * 100 - loss
    avg_trade_pnl = total_pnl_pct / total

    unique_symbols = set(s.symbol for s in simulations)
    print(f"\nTotal simulated CSP trades: {total}")
    print(f"Unique symbols traded: {len(unique_symbols)}")
    print(f"Win rate (stock above 5% OTM strike): {win_rate:.1f}%")
    print(f"Assignment rate: {losses/total*100:.1f}%")
    print(f"Average 8-day forward return: {avg_return:+.2f}%")
    print(f"Average max drawdown during DTE: {avg_drawdown:+.2f}%")
    print(f"Worst single drawdown: {worst_drawdown:+.2f}%")
    print(f"\n--- Simulated CSP P&L (1.5% premium assumption) ---")
    print(f"Average trade P&L: {avg_trade_pnl:+.2f}% of notional")
    print(f"Cumulative P&L over {total} trades: {total_pnl_pct:+.1f}%")

    def stats(sims: list[CSPSimulation], label: str) -> None:
        if not sims:
            print(f"  {label:30s}: no data")
            return
        n = len(sims)
        w = sum(1 for s in sims if s.stayed_above_strike)
        wr = w / n * 100
        ar = sum(s.forward_return_pct for s in sims) / n
        ad = sum(s.max_drawdown_pct for s in sims) / n
        wd = min(s.max_drawdown_pct for s in sims)
        pnl = 0.0
        for s in sims:
            if s.stayed_above_strike:
                pnl += PREMIUM_PCT * 100
            else:
                loss = ((s.strike - s.min_price_during) / s.strike) * 100
                pnl += PREMIUM_PCT * 100 - loss
        avg_pnl = pnl / n
        print(
            f"  {label:30s}: {n:4d} trades | win={wr:5.1f}% | "
            f"avg_ret={ar:+6.2f}% | avg_dd={ad:+6.2f}% | "
            f"worst_dd={wd:+7.2f}% | avg_pnl={avg_pnl:+5.2f}%"
        )

    # By conviction level
    print(f"\n--- By Conviction Level ---")
    stats([s for s in simulations if s.conviction == "high"], "HIGH conviction")
    stats([s for s in simulations if s.conviction == "medium"], "MEDIUM conviction")

    # By trend state
    print(f"\n--- By Trend State ---")
    for ts in ["strong_uptrend", "uptrend", "pullback_to_8ema", "pullback_to_21ema"]:
        stats([s for s in simulations if s.trend_state == ts], ts.upper())

    # By extension bracket
    print(f"\n--- By Extension (price vs 8-EMA) ---")
    for lo, hi, label in [(0, 1, "<1%"), (1, 2, "1-2%"), (2, 3, "2-3%")]:
        stats([s for s in simulations if lo <= abs(s.price_to_8ema_pct) < hi], label)

    # By days above both EMAs
    print(f"\n--- By Days Above Both EMAs ---")
    for lo, hi, label in [(0, 5, "0-4 days"), (5, 10, "5-9 days"), (10, 20, "10-19 days"), (20, 999, "20+ days")]:
        stats([s for s in simulations if lo <= s.days_above_emas < hi], label)

    # By market cap bracket
    print(f"\n--- By Market Cap ---")
    for lo, hi, label in [
        (500e6, 2e9, "$500M-2B"),
        (2e9, 10e9, "$2B-10B"),
        (10e9, 50e9, "$10B-50B"),
        (50e9, 200e9, "$50B-200B"),
        (200e9, 1e15, "$200B+"),
    ]:
        stats([s for s in simulations if lo <= s.market_cap < hi], label)

    # By entry price
    print(f"\n--- By Entry Price ---")
    for lo, hi, label in [(15, 30, "$15-30"), (30, 60, "$30-60"), (60, 100, "$60-100"), (100, 200, "$100-200"), (200, 99999, "$200+")]:
        stats([s for s in simulations if lo <= s.entry_price < hi], label)

    # 10 worst trades
    print(f"\n--- 10 Worst Trades ---")
    worst = sorted(simulations, key=lambda s: s.max_drawdown_pct)[:10]
    for s in worst:
        cap_b = s.market_cap / 1e9
        print(
            f"  {s.entry_date} {s.symbol:6s} | ${s.entry_price:7.2f}→${s.exit_price:7.2f} "
            f"({s.forward_return_pct:+5.1f}%) | strike=${s.strike:7.2f} min=${s.min_price_during:7.2f} "
            f"dd={s.max_drawdown_pct:+.1f}% | {s.conviction} {s.trend_state} | "
            f"ext={s.price_to_8ema_pct:.1f}% cap=${cap_b:.1f}B"
        )

    # 10 best trades
    print(f"\n--- 10 Best Trades ---")
    best = sorted(simulations, key=lambda s: s.forward_return_pct, reverse=True)[:10]
    for s in best:
        cap_b = s.market_cap / 1e9
        print(
            f"  {s.entry_date} {s.symbol:6s} | ${s.entry_price:7.2f}→${s.exit_price:7.2f} "
            f"({s.forward_return_pct:+5.1f}%) | strike=${s.strike:7.2f} "
            f"| {s.conviction} {s.trend_state} | ext={s.price_to_8ema_pct:.1f}% cap=${cap_b:.1f}B"
        )

    # Top 20 most frequently picked symbols
    print(f"\n--- Top 20 Most Frequently Picked Symbols ---")
    freq: dict[str, int] = defaultdict(int)
    sym_wins: dict[str, int] = defaultdict(int)
    for s in simulations:
        freq[s.symbol] += 1
        if s.stayed_above_strike:
            sym_wins[s.symbol] += 1

    top_syms = sorted(freq.items(), key=lambda x: -x[1])[:20]
    for sym, count in top_syms:
        wr = sym_wins[sym] / count * 100
        trades = [s for s in simulations if s.symbol == sym]
        avg_r = sum(s.forward_return_pct for s in trades) / len(trades)
        cap_b = all_market_caps.get(sym, 0) / 1e9
        print(f"  {sym:6s}: {count:3d} picks | win={wr:5.1f}% | avg_ret={avg_r:+.2f}% | cap=${cap_b:.1f}B")

    # Recent day picks
    print(f"\n--- Last 3 Complete Scan Days ---")
    all_scan_dates = sorted(set(s.entry_date for s in simulations))
    for sd in all_scan_dates[-3:]:
        recent = [s for s in simulations if s.entry_date == sd]
        if not recent:
            continue
        w = sum(1 for s in recent if s.stayed_above_strike)
        print(f"\n  {sd} ({len(recent)} picks, {w} wins):")
        for s in recent:
            outcome = "SAFE" if s.stayed_above_strike else "ASSIGNED"
            cap_b = s.market_cap / 1e9
            print(
                f"    {s.symbol:6s} | ${s.entry_price:.2f}→${s.exit_price:.2f} "
                f"({s.forward_return_pct:+.1f}%) strike=${s.strike:.2f} [{outcome}] "
                f"| {s.conviction} ext={s.price_to_8ema_pct:.1f}% cap=${cap_b:.1f}B"
            )


if __name__ == "__main__":
    run_backtest()
