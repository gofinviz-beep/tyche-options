"""Intraday time-of-day backtest for CSP entry timing.

For each CSP-eligible stock-day (identified by the daily ConvictionEngine),
samples the stock price at 30-minute intervals throughout the trading day
and simulates a CSP entry at each time slot to determine the optimal
entry window.

Metrics by time bucket:
- Win rate (stock stayed above strike through DTE)
- Average P&L per contract
- Average max drawdown during DTE
- Price position within the day's range
- Volume profile (relative to daily total)

Usage:
    python scripts/backtest_intraday.py                    # Run backtest using cached intraday data
    python scripts/backtest_intraday.py --fetch            # Fetch intraday data first, then backtest
    python scripts/backtest_intraday.py --status           # Show cached intraday data status
    python scripts/backtest_intraday.py --from 2026-01-01  # Limit backtest date range
"""

from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import click
import pandas as pd

sys.path.insert(0, "src")

from tyche.config import TycheSettings
from tyche.conviction.engine import ConvictionEngine
from tyche.market_data.data_store import IntradayStore, OHLCVStore, TickerMetaStore
from tyche.market_data.polygon import PolygonClient

DTE = 8
OTM_PCT = 0.05
PREMIUM_PCT = 0.015
MIN_PRICE = 15.0
MIN_MARKET_CAP = 5_000_000_000
MIN_VOLUME = 500_000
VALID_EXCHANGES = {"XNYS", "XNAS", "XNMS", "XASE", "ARCX", "BATS"}

TIME_SLOTS_ET = [
    time(9, 30),
    time(10, 0),
    time(10, 30),
    time(11, 0),
    time(11, 30),
    time(12, 0),
    time(12, 30),
    time(13, 0),
    time(13, 30),
    time(14, 0),
    time(14, 30),
    time(15, 0),
    time(15, 30),
]


@dataclass
class IntradayCSPSim:
    """Single intraday CSP simulation result."""

    entry_date: date
    ticker: str
    time_slot: time
    entry_price: float
    strike: float
    exit_price: float
    min_price_during: float
    max_drawdown_pct: float
    won: bool
    pnl: float
    day_high: float
    day_low: float
    price_position_in_day: float  # 0.0 = at day low, 1.0 = at day high
    volume_at_slot: int
    cumulative_volume_pct: float  # % of daily volume by this time slot


@dataclass
class TimeBucketStats:
    """Aggregated statistics for one time-of-day bucket."""

    time_slot: time
    total_trades: int = 0
    wins: int = 0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    total_pnl: float = 0.0
    avg_max_drawdown: float = 0.0
    avg_entry_price: float = 0.0
    avg_price_position: float = 0.0
    avg_cumulative_volume_pct: float = 0.0
    median_pnl: float = 0.0
    best_pnl: float = 0.0
    worst_pnl: float = 0.0


def _find_eligible_stock_days(
    ohlcv_store: OHLCVStore,
    meta_store: TickerMetaStore,
    start_date: date,
    end_date: date,
) -> list[tuple[str, date]]:
    """Run ConvictionEngine on daily data to find all CSP-eligible (ticker, date) pairs."""
    engine = ConvictionEngine()
    all_tickers = ohlcv_store.get_all_tickers()

    meta_df = meta_store.read_meta()
    if not meta_df.empty:
        large_caps = set(
            meta_df[
                (meta_df["market_cap"] >= MIN_MARKET_CAP)
                & (meta_df["exchange"].isin(VALID_EXCHANGES))
            ]["ticker"].tolist()
        )
        all_tickers = [t for t in all_tickers if t in large_caps]

    lookback_start = start_date - timedelta(days=90)
    ticker_data = ohlcv_store.read_tickers(all_tickers, start_date=lookback_start)

    eligible: list[tuple[str, date]] = []

    for ticker, df in ticker_data.items():
        if len(df) < 50:
            continue
        recent_close = df["close"].iloc[-1] if len(df) > 0 else 0
        if recent_close < MIN_PRICE:
            continue

        trading_days = sorted(
            d for d in df["date"].unique() if start_date <= d <= end_date
        )

        for td in trading_days:
            as_of_df = df[df["date"] <= td]
            if len(as_of_df) < 50:
                continue

            last_close = float(as_of_df["close"].iloc[-1])
            if last_close < MIN_PRICE:
                continue

            avg_vol = int(as_of_df["volume"].iloc[-20:].mean()) if len(as_of_df) >= 20 else 0
            if avg_vol < MIN_VOLUME:
                continue

            signal = engine.analyze(ticker, as_of_df)
            if signal.csp_eligible:
                eligible.append((ticker, td))

    return eligible


def _get_price_at_time(
    intraday_df: pd.DataFrame,
    target_date: date,
    target_time: time,
    tolerance_minutes: int = 5,
) -> float | None:
    """Find the close price of the bar nearest to the target time on target_date.

    Uses a tolerance window to handle bars not falling exactly on round times.
    """
    day_bars = intraday_df[intraday_df["date"] == target_date]
    if day_bars.empty:
        return None

    target_dt = datetime.combine(target_date, target_time)
    day_bars = day_bars.copy()
    day_bars["time_diff"] = abs((day_bars["timestamp"] - target_dt).dt.total_seconds())

    closest = day_bars.loc[day_bars["time_diff"].idxmin()]
    if closest["time_diff"] > tolerance_minutes * 60:
        return None

    return float(closest["close"])


def _get_volume_at_time(
    intraday_df: pd.DataFrame,
    target_date: date,
    target_time: time,
    tolerance_minutes: int = 5,
) -> tuple[int, float]:
    """Get volume at a specific time slot and cumulative volume as % of daily total.

    Returns (volume_at_slot, cumulative_volume_pct).
    """
    day_bars = intraday_df[intraday_df["date"] == target_date]
    if day_bars.empty:
        return 0, 0.0

    total_daily_volume = int(day_bars["volume"].sum())
    if total_daily_volume == 0:
        return 0, 0.0

    target_dt = datetime.combine(target_date, target_time)
    cumulative = day_bars[day_bars["timestamp"] <= target_dt + timedelta(minutes=tolerance_minutes)]
    cum_vol = int(cumulative["volume"].sum())
    cum_pct = cum_vol / total_daily_volume * 100.0

    closest_bar = day_bars.copy()
    closest_bar["time_diff"] = abs((closest_bar["timestamp"] - target_dt).dt.total_seconds())
    nearest = closest_bar.loc[closest_bar["time_diff"].idxmin()]
    slot_vol = int(nearest["volume"]) if nearest["time_diff"] <= tolerance_minutes * 60 else 0

    return slot_vol, cum_pct


def _get_day_range(intraday_df: pd.DataFrame, target_date: date) -> tuple[float, float]:
    """Get the high and low for a given date from intraday data."""
    day_bars = intraday_df[intraday_df["date"] == target_date]
    if day_bars.empty:
        return 0.0, 0.0
    return float(day_bars["high"].max()), float(day_bars["low"].min())


def _simulate_csp(
    entry_price: float,
    daily_df: pd.DataFrame,
    entry_date: date,
    dte: int = DTE,
) -> tuple[float, float, float, bool]:
    """Simulate a CSP trade using daily closes for forward-looking outcome.

    Returns (exit_price, min_price_during, max_drawdown_pct, won).
    """
    strike = round(entry_price * (1 - OTM_PCT), 2)
    premium = round(entry_price * PREMIUM_PCT, 2)

    future = daily_df[daily_df["date"] > entry_date].head(dte)
    if future.empty:
        return entry_price, entry_price, 0.0, True

    exit_price = float(future["close"].iloc[-1])
    min_price = float(future["close"].min())
    max_dd = (entry_price - min_price) / entry_price * 100.0 if entry_price > 0 else 0.0

    won = min_price >= strike
    return exit_price, min_price, max_dd, won


def run_backtest(
    ohlcv_store: OHLCVStore,
    meta_store: TickerMetaStore,
    intraday_store: IntradayStore,
    start_date: date,
    end_date: date,
) -> list[TimeBucketStats]:
    """Execute the intraday timing backtest and return per-bucket stats."""
    click.echo("\n=== Step 1: Finding CSP-eligible stock-days ===")
    eligible = _find_eligible_stock_days(ohlcv_store, meta_store, start_date, end_date)
    click.echo(f"Found {len(eligible)} eligible (ticker, date) pairs")

    if not eligible:
        click.echo("No eligible trades found. Ensure OHLCV and metadata are populated.")
        return []

    unique_tickers = sorted(set(t for t, _ in eligible))
    click.echo(f"Unique tickers: {len(unique_tickers)}")

    click.echo("\n=== Step 2: Loading intraday data ===")
    available_tickers = set(intraday_store.get_tickers())
    missing = [t for t in unique_tickers if t not in available_tickers]
    if missing:
        click.echo(
            f"Warning: {len(missing)} tickers missing intraday data: "
            f"{', '.join(missing[:10])}{'...' if len(missing) > 10 else ''}"
        )
        click.echo("Run with --fetch or use ingest_data.py --intraday to fetch them.")

    tickers_with_data = [t for t in unique_tickers if t in available_tickers]
    if not tickers_with_data:
        click.echo("No intraday data available for any eligible ticker.")
        return []

    intraday_data = intraday_store.read_tickers(tickers_with_data, start_date, end_date)
    daily_data = ohlcv_store.read_tickers(tickers_with_data)

    click.echo(f"Loaded intraday data for {len(intraday_data)} tickers")

    click.echo("\n=== Step 3: Simulating CSP entries by time slot ===")
    simulations: list[IntradayCSPSim] = []
    skipped = 0

    eligible_with_data = [
        (t, d) for t, d in eligible if t in intraday_data
    ]

    with click.progressbar(eligible_with_data, label="Simulating", show_pos=True) as bar:
        for ticker, entry_date in bar:
            idf = intraday_data.get(ticker)
            ddf = daily_data.get(ticker)
            if idf is None or ddf is None or idf.empty or ddf.empty:
                skipped += 1
                continue

            day_high, day_low = _get_day_range(idf, entry_date)
            if day_high == 0 and day_low == 0:
                skipped += 1
                continue

            day_range = day_high - day_low if day_high > day_low else 1.0

            for slot in TIME_SLOTS_ET:
                price = _get_price_at_time(idf, entry_date, slot)
                if price is None or price < MIN_PRICE:
                    continue

                exit_price, min_price, max_dd, won = _simulate_csp(
                    price, ddf, entry_date, DTE,
                )
                strike = round(price * (1 - OTM_PCT), 2)
                premium = round(price * PREMIUM_PCT * 100, 2)

                if won:
                    pnl = premium
                else:
                    intrinsic_loss = (strike - min_price) * 100 if min_price < strike else 0
                    pnl = premium - intrinsic_loss

                slot_vol, cum_vol_pct = _get_volume_at_time(idf, entry_date, slot)
                price_pos = (price - day_low) / day_range if day_range > 0 else 0.5

                simulations.append(
                    IntradayCSPSim(
                        entry_date=entry_date,
                        ticker=ticker,
                        time_slot=slot,
                        entry_price=price,
                        strike=strike,
                        exit_price=exit_price,
                        min_price_during=min_price,
                        max_drawdown_pct=max_dd,
                        won=won,
                        pnl=pnl,
                        day_high=day_high,
                        day_low=day_low,
                        price_position_in_day=price_pos,
                        volume_at_slot=slot_vol,
                        cumulative_volume_pct=cum_vol_pct,
                    )
                )

    click.echo(f"\nTotal simulations: {len(simulations)} ({skipped} stock-days skipped)")

    if not simulations:
        return []

    click.echo("\n=== Step 4: Aggregating by time bucket ===")
    bucket_sims: dict[time, list[IntradayCSPSim]] = defaultdict(list)
    for sim in simulations:
        bucket_sims[sim.time_slot].append(sim)

    stats: list[TimeBucketStats] = []
    for slot in TIME_SLOTS_ET:
        sims = bucket_sims.get(slot, [])
        if not sims:
            stats.append(TimeBucketStats(time_slot=slot))
            continue

        pnls = [s.pnl for s in sims]
        wins = sum(1 for s in sims if s.won)
        sorted_pnls = sorted(pnls)
        median_idx = len(sorted_pnls) // 2

        stats.append(
            TimeBucketStats(
                time_slot=slot,
                total_trades=len(sims),
                wins=wins,
                win_rate=wins / len(sims) * 100 if sims else 0,
                avg_pnl=sum(pnls) / len(pnls),
                total_pnl=sum(pnls),
                avg_max_drawdown=sum(s.max_drawdown_pct for s in sims) / len(sims),
                avg_entry_price=sum(s.entry_price for s in sims) / len(sims),
                avg_price_position=sum(s.price_position_in_day for s in sims) / len(sims),
                avg_cumulative_volume_pct=sum(s.cumulative_volume_pct for s in sims) / len(sims),
                median_pnl=sorted_pnls[median_idx],
                best_pnl=max(pnls),
                worst_pnl=min(pnls),
            )
        )

    return stats


def _format_time_et(t: time) -> str:
    """Format time in ET and PST for display."""
    et_str = t.strftime("%I:%M %p ET")
    pst_hour = (t.hour - 3) % 24
    pst_time = time(pst_hour, t.minute)
    pst_str = pst_time.strftime("%I:%M %p PT")
    return f"{et_str} ({pst_str})"


def print_results(stats: list[TimeBucketStats]) -> None:
    """Print the backtest results in a readable table."""
    if not stats:
        click.echo("No results to display.")
        return

    click.echo("\n" + "=" * 120)
    click.echo("INTRADAY TIME-OF-DAY BACKTEST RESULTS")
    click.echo("=" * 120)

    click.echo(
        f"\n{'Time Slot':<28} {'Trades':>7} {'Win%':>7} {'Avg P&L':>10} "
        f"{'Med P&L':>10} {'Total P&L':>12} {'Avg DD%':>8} {'Price Pos':>10} {'Vol%':>7}"
    )
    click.echo("-" * 120)

    best_slot = max(stats, key=lambda s: s.avg_pnl if s.total_trades > 0 else float("-inf"))

    for s in stats:
        if s.total_trades == 0:
            continue

        marker = " ***" if s == best_slot else ""
        click.echo(
            f"{_format_time_et(s.time_slot):<28} {s.total_trades:>7} {s.win_rate:>6.1f}% "
            f"${s.avg_pnl:>9.2f} ${s.median_pnl:>9.2f} ${s.total_pnl:>11,.2f} "
            f"{s.avg_max_drawdown:>7.2f}% {s.avg_price_position:>9.1%} {s.avg_cumulative_volume_pct:>6.1f}%"
            f"{marker}"
        )

    click.echo("-" * 120)
    click.echo("  *** = Best time slot by average P&L")

    click.echo(f"\n{'Time Slot':<28} {'Best P&L':>10} {'Worst P&L':>10}")
    click.echo("-" * 55)
    for s in stats:
        if s.total_trades == 0:
            continue
        click.echo(
            f"{_format_time_et(s.time_slot):<28} ${s.best_pnl:>9.2f} ${s.worst_pnl:>9.2f}"
        )

    click.echo("\n=== Interpretation Guide ===")
    click.echo("  Price Pos: Where in the day's range the entry was (0% = day low, 100% = day high)")
    click.echo("  Vol%:      Cumulative volume by that time slot as % of total daily volume")
    click.echo("  Avg DD%:   Average max intraday-to-forward drawdown during the DTE period")

    if best_slot.total_trades > 0:
        click.echo(f"\n=== Recommendation ===")
        click.echo(
            f"  Best entry window: {_format_time_et(best_slot.time_slot)}"
        )
        click.echo(
            f"  Win rate: {best_slot.win_rate:.1f}%, Avg P&L: ${best_slot.avg_pnl:.2f}/contract, "
            f"Avg drawdown: {best_slot.avg_max_drawdown:.2f}%"
        )


async def _fetch_missing_intraday(
    settings: TycheSettings,
    ohlcv_store: OHLCVStore,
    meta_store: TickerMetaStore,
    intraday_store: IntradayStore,
    start_date: date,
    end_date: date,
) -> None:
    """Fetch intraday data for eligible tickers that are missing from the store."""
    polygon = PolygonClient(
        api_key=settings.polygon_api_key,
        base_url=settings.polygon_base_url,
        rate_limit_rpm=settings.polygon_rate_limit_rpm,
    )

    eligible = _find_eligible_stock_days(ohlcv_store, meta_store, start_date, end_date)
    unique_tickers = sorted(set(t for t, _ in eligible))

    already_cached = set(intraday_store.get_tickers())
    to_fetch = [t for t in unique_tickers if t not in already_cached]

    if not to_fetch:
        click.echo("All eligible tickers already have intraday data cached.")
        return

    click.echo(f"Fetching intraday data for {len(to_fetch)} tickers (skipping {len(already_cached)} cached)...")

    completed = {"fetched": 0, "bars": 0}
    concurrency = 10

    async def _fetch_one(ticker: str, sem: asyncio.Semaphore) -> None:
        async with sem:
            try:
                bars = await polygon.get_aggregate_bars(
                    ticker=ticker,
                    from_date=start_date,
                    to_date=end_date,
                    multiplier=5,
                    timespan="minute",
                )
                if bars:
                    stored = intraday_store.write_bars(bars)
                    completed["bars"] += stored
                    completed["fetched"] += 1
            except Exception as exc:
                click.echo(f"\n  Warning: {ticker} failed — {exc}", err=True)

    sem = asyncio.Semaphore(concurrency)
    await asyncio.gather(*[_fetch_one(t, sem) for t in to_fetch])

    click.echo(
        f"Fetched intraday data for {completed['fetched']} tickers, "
        f"{completed['bars']:,} bars stored"
    )


@click.command()
@click.option("--from", "from_date", type=click.DateTime(formats=["%Y-%m-%d"]),
              default=None, help="Backtest start date (YYYY-MM-DD). Default: 90 days back.")
@click.option("--to", "to_date", type=click.DateTime(formats=["%Y-%m-%d"]),
              default=None, help="Backtest end date (YYYY-MM-DD). Default: last trading day.")
@click.option("--fetch", is_flag=True, default=False,
              help="Fetch missing intraday data before running the backtest.")
@click.option("--status", is_flag=True, default=False,
              help="Show intraday data status and exit.")
def main(
    from_date: click.DateTime | None,
    to_date: click.DateTime | None,
    fetch: bool,
    status: bool,
) -> None:
    """Run intraday time-of-day backtest for CSP entry timing."""
    settings = TycheSettings()
    ohlcv_store = OHLCVStore(data_dir=settings.data_dir)
    meta_store = TickerMetaStore(data_dir=settings.data_dir)
    intraday_store = IntradayStore(data_dir=settings.data_dir)

    if status:
        click.echo("\n=== Intraday Store (5-min bars) ===")
        click.echo(f"  Dir:     {intraday_store.store_dir}")
        click.echo(f"  Exists:  {intraday_store.exists}")
        if intraday_store.exists:
            earliest, latest = intraday_store.get_date_range()
            click.echo(f"  Range:   {earliest} to {latest}")
            click.echo(f"  Tickers: {intraday_store.get_ticker_count():,}")
            click.echo(f"  Rows:    {intraday_store.get_row_count():,}")
        click.echo()
        return

    fd = from_date.date() if from_date else date.today() - timedelta(days=90)
    td = to_date.date() if to_date else date.today() - timedelta(days=1)

    while td.weekday() >= 5:
        td -= timedelta(days=1)

    click.echo(f"Backtest period: {fd} to {td}")

    if fetch:
        if not settings.polygon_api_key:
            click.echo("Error: TYCHE_POLYGON_API_KEY not set.", err=True)
            sys.exit(1)
        asyncio.run(
            _fetch_missing_intraday(settings, ohlcv_store, meta_store, intraday_store, fd, td)
        )

    stats = run_backtest(ohlcv_store, meta_store, intraday_store, fd, td)
    print_results(stats)


if __name__ == "__main__":
    main()
