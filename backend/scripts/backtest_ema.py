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
    all_ohlcv_tickers = set(store.get_all_tickers())
    target_tickers = qualified_tickers & all_ohlcv_tickers
    ticker_data = store.read_tickers(list(target_tickers))

    all_dates: set[date] = set()
    for df in ticker_data.values():
        all_dates.update(df["date"].unique())
    all_dates_sorted = sorted(all_dates)

    warmup = 30
    backtest_dates = all_dates_sorted[warmup:]

    print(f"\nBacktest: {backtest_dates[0]} to {backtest_dates[-1]}")
    print(f"Trading days: {len(backtest_dates)}")
    print(f"DTE: {DTE} days | OTM: {OTM_PCT*100:.0f}%")
    print(f"Filters: mkt_cap >= ${MIN_MARKET_CAP/1e6:.0f}M, price >= ${MIN_PRICE}, "
          f"extension <= 3%, valid exchange")
    print(f"Top {TOP_N_PER_DAY} picks per day")
    print("=" * 80)

    ticker_frames: dict[str, pd.DataFrame] = {}
    for ticker, df in ticker_data.items():
        if len(df) >= warmup:
            ticker_frames[ticker] = df

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

    # By day of week
    print(f"\n--- By Entry Day of Week ---")
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    for dow in range(5):
        stats(
            [s for s in simulations if s.entry_date.weekday() == dow],
            day_names[dow],
        )

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

    # Capital-aware portfolio simulation
    run_capital_simulation(simulations, backtest_dates)


def run_capital_simulation(
    simulations: list[CSPSimulation],
    backtest_dates: list[date],
    starting_capital: float = 100_000.0,
    max_positions: int = 8,
    max_concentration_pct: float = 25.0,
) -> None:
    """Run a capital-aware portfolio simulation using the MILP allocator.

    Tracks a real capital pool across overlapping 8-day CSP positions,
    compounds returns, and reports equity curve + risk metrics.
    """
    from tyche.strategy.allocator import PortfolioAllocator
    from tyche.strategy.strategies.base import ScoredCandidate

    print("\n" + "=" * 80)
    print(f"CAPITAL-AWARE PORTFOLIO SIMULATION (${starting_capital:,.0f} starting)")
    print("=" * 80)

    allocator = PortfolioAllocator(
        max_positions=max_positions,
        max_contracts_per_position=40,
        max_concentration_pct=max_concentration_pct,
    )

    sims_by_date: dict[date, list[CSPSimulation]] = {}
    for s in simulations:
        sims_by_date.setdefault(s.entry_date, []).append(s)

    @dataclass
    class OpenPosition:
        symbol: str
        strike: float
        contracts: int
        collateral: float
        premium: float
        entry_date: date
        exit_date: date
        sim: CSPSimulation

    capital = starting_capital
    open_positions: list[OpenPosition] = []
    equity_curve: list[tuple[date, float]] = []
    daily_returns: list[float] = []
    total_premium_collected = 0.0
    total_losses = 0.0
    trades_executed = 0
    peak_equity = starting_capital
    max_drawdown_pct = 0.0
    utilization_samples: list[float] = []

    prev_equity = starting_capital

    for scan_date in backtest_dates:
        # Close expired positions
        newly_closed: list[OpenPosition] = []
        still_open: list[OpenPosition] = []
        for pos in open_positions:
            if scan_date >= pos.exit_date:
                newly_closed.append(pos)
            else:
                still_open.append(pos)

        for pos in newly_closed:
            if pos.sim.stayed_above_strike:
                capital += pos.collateral + pos.premium
                total_premium_collected += pos.premium
            else:
                loss_per_share = max(0, pos.strike - pos.sim.min_price_during)
                total_loss = loss_per_share * 100 * pos.contracts
                net = pos.collateral + pos.premium - total_loss
                capital += net
                total_premium_collected += pos.premium
                total_losses += total_loss

        open_positions = still_open

        locked_collateral = sum(p.collateral for p in open_positions)
        available = capital - locked_collateral

        # Build ScoredCandidate objects from today's simulations for the allocator
        today_sims = sims_by_date.get(scan_date, [])
        candidates: list[ScoredCandidate] = []
        sim_map: dict[str, CSPSimulation] = {}

        for sim in today_sims:
            already_holding = any(p.symbol == sim.symbol for p in open_positions)
            if already_holding:
                continue

            collateral_per = sim.strike * 100
            if collateral_per > available:
                continue

            premium_per = sim.strike * PREMIUM_PCT * 100
            ann_return = (PREMIUM_PCT / 1.0) * (365 / DTE) * 100
            oi_approx = 500

            key = f"{sim.symbol}_{scan_date}"
            sim_map[key] = sim

            sc = ScoredCandidate(
                symbol=sim.symbol,
                option_symbol=key,
                option_type="put",
                strike=sim.strike,
                expiration=sim.exit_date or scan_date,
                dte=DTE,
                bid=sim.strike * PREMIUM_PCT,
                ask=sim.strike * PREMIUM_PCT * 1.1,
                mid=sim.strike * PREMIUM_PCT * 1.05,
                volume=100,
                open_interest=oi_approx,
                implied_volatility=0.3,
                underlying_price=sim.entry_price,
                strategy="csp",
                premium_per_contract=round(premium_per, 2),
                total_premium=round(premium_per, 2),
                collateral_required=round(collateral_per, 2),
                annualized_return_pct=round(ann_return, 2),
                score=round(ann_return, 4),
            )
            candidates.append(sc)

        if candidates and available > 0:
            max_new = max_positions - len(open_positions)
            if max_new > 0:
                sub_allocator = PortfolioAllocator(
                    max_positions=max_new,
                    max_contracts_per_position=40,
                    max_concentration_pct=max_concentration_pct,
                )
                result = sub_allocator.optimize(
                    csp_candidates=candidates,
                    available_capital=available,
                )
                for trade in result.trades:
                    sim = sim_map.get(trade.option_symbol)
                    if not sim:
                        continue
                    premium = trade.contracts * trade.premium_per_contract
                    collateral = trade.contracts * trade.strike * 100
                    capital -= collateral
                    capital -= 0  # premium is received, added at exit

                    open_positions.append(OpenPosition(
                        symbol=sim.symbol,
                        strike=sim.strike,
                        contracts=trade.contracts,
                        collateral=collateral,
                        premium=premium,
                        entry_date=scan_date,
                        exit_date=sim.exit_date or scan_date,
                        sim=sim,
                    ))
                    trades_executed += 1

        total_equity = capital + sum(p.collateral for p in open_positions)
        equity_curve.append((scan_date, total_equity))

        if total_equity > peak_equity:
            peak_equity = total_equity
        dd = (peak_equity - total_equity) / peak_equity * 100
        if dd > max_drawdown_pct:
            max_drawdown_pct = dd

        if prev_equity > 0:
            daily_returns.append((total_equity - prev_equity) / prev_equity)
        prev_equity = total_equity

        locked = sum(p.collateral for p in open_positions)
        util = (locked / total_equity * 100) if total_equity > 0 else 0
        utilization_samples.append(util)

    # Close any remaining positions at end
    for pos in open_positions:
        if pos.sim.stayed_above_strike:
            capital += pos.collateral + pos.premium
            total_premium_collected += pos.premium
        else:
            loss_per_share = max(0, pos.strike - pos.sim.min_price_during)
            total_loss = loss_per_share * 100 * pos.contracts
            net = pos.collateral + pos.premium - total_loss
            capital += net
            total_premium_collected += pos.premium
            total_losses += total_loss

    final_equity = capital
    total_return = (final_equity - starting_capital) / starting_capital * 100

    import math
    if daily_returns:
        avg_daily = sum(daily_returns) / len(daily_returns)
        std_daily = (sum((r - avg_daily) ** 2 for r in daily_returns) / len(daily_returns)) ** 0.5
        sharpe = (avg_daily / std_daily * math.sqrt(252)) if std_daily > 0 else 0.0
    else:
        sharpe = 0.0

    avg_util = sum(utilization_samples) / len(utilization_samples) if utilization_samples else 0

    trading_days = len(backtest_dates)
    annualized_return = total_return * (252 / trading_days) if trading_days > 0 else 0

    print(f"\nStarting capital:     ${starting_capital:>12,.0f}")
    print(f"Final equity:         ${final_equity:>12,.2f}")
    print(f"Total return:         {total_return:>+11.2f}%")
    print(f"Annualized return:    {annualized_return:>+11.2f}%")
    print(f"Sharpe ratio:         {sharpe:>11.2f}")
    print(f"Max drawdown:         {max_drawdown_pct:>11.2f}%")
    print(f"Trades executed:      {trades_executed:>11}")
    print(f"Premium collected:    ${total_premium_collected:>12,.2f}")
    print(f"Assignment losses:    ${total_losses:>12,.2f}")
    print(f"Net P&L:              ${final_equity - starting_capital:>12,.2f}")
    print(f"Avg capital util:     {avg_util:>11.1f}%")
    print(f"Trading days:         {trading_days:>11}")

    # Equity curve milestones
    if equity_curve:
        print(f"\n--- Equity Curve (monthly checkpoints) ---")
        prev_month = None
        for dt, eq in equity_curve:
            month_key = (dt.year, dt.month)
            if prev_month != month_key:
                ret = (eq - starting_capital) / starting_capital * 100
                print(f"  {dt.isoformat()}: ${eq:>12,.2f}  ({ret:+.2f}%)")
                prev_month = month_key
        last_dt, last_eq = equity_curve[-1]
        ret = (last_eq - starting_capital) / starting_capital * 100
        print(f"  {last_dt.isoformat()}: ${last_eq:>12,.2f}  ({ret:+.2f}%)  [final]")


if __name__ == "__main__":
    run_backtest()
