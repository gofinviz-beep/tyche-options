"""Data ingestion CLI — fetch OHLCV bars, intraday bars, and ticker metadata from Polygon.io.

Usage:
    python scripts/ingest_data.py                          # Fetch missing days (latest+1 to yesterday)
    python scripts/ingest_data.py --from 2026-03-20        # From a specific date to yesterday
    python scripts/ingest_data.py --from 2026-03-20 --to 2026-03-25  # Specific range
    python scripts/ingest_data.py --days 120               # Full bootstrap (120 calendar days back)
    python scripts/ingest_data.py --status                 # Show store status, don't fetch
    python scripts/ingest_data.py --meta                   # Also refresh ticker metadata
    python scripts/ingest_data.py --intraday               # Also fetch 5-min intraday bars for eligible tickers
    python scripts/ingest_data.py --intraday --intraday-tickers AAPL,MSFT  # Specific tickers only
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, timedelta

import click
import structlog

sys.path.insert(0, "src")

from tyche.config import TycheSettings
from tyche.conviction.engine import ConvictionEngine
from tyche.market_data.data_store import IntradayStore, OHLCVStore, TickerMetaStore, bootstrap_ohlcv
from tyche.market_data.polygon import PolygonClient

logger = structlog.get_logger()


def _print_status(
    store: OHLCVStore,
    meta: TickerMetaStore,
    intraday: IntradayStore | None = None,
) -> None:
    """Display current data store status."""
    click.echo("\n=== OHLCV Store (per-ticker) ===")
    click.echo(f"  Dir:     {store.store_dir}")
    click.echo(f"  Exists:  {store.exists}")

    if store.has_legacy_file:
        click.echo("  WARNING: Legacy single-file ohlcv_daily.parquet detected. Run without --status to auto-migrate.")

    if store.exists:
        earliest, latest = store.get_date_range()
        click.echo(f"  Range:   {earliest} to {latest}")
        click.echo(f"  Tickers: {store.get_ticker_count():,}")
        click.echo(f"  Rows:    {store.get_row_count():,}")

        today = date.today()
        last_trading = _last_trading_day(today)
        gap = (last_trading - latest).days if latest else 0
        if gap > 0:
            click.echo(f"  Missing: {gap} calendar day(s) — latest is {latest}, last trading day is {last_trading}")
        else:
            click.echo("  Missing: up to date")
    else:
        click.echo("  (empty — run without --status to bootstrap)")

    click.echo("\n=== Ticker Meta Store ===")
    click.echo(f"  Path:    {meta.parquet_path}")
    click.echo(f"  Exists:  {meta.exists}")
    if meta.exists:
        click.echo(f"  Tickers: {meta.get_ticker_count():,}")

    if intraday is not None:
        click.echo("\n=== Intraday Store (5-min, per-ticker) ===")
        click.echo(f"  Dir:     {intraday.store_dir}")
        click.echo(f"  Exists:  {intraday.exists}")

        if intraday.has_legacy_file:
            click.echo("  WARNING: Legacy single-file intraday_5min.parquet detected. Run without --status to auto-migrate.")

        if intraday.exists:
            earliest, latest = intraday.get_date_range()
            click.echo(f"  Range:   {earliest} to {latest}")
            click.echo(f"  Tickers: {intraday.get_ticker_count():,}")
            click.echo(f"  Rows:    {intraday.get_row_count():,}")

    click.echo()


def _last_trading_day(ref: date) -> date:
    """Return the most recent weekday on or before ref (excluding today)."""
    d = ref - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _weekdays_in_range(start: date, end: date) -> list[date]:
    """Return all weekdays (Mon-Fri) in [start, end] inclusive."""
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


async def _fetch_range(
    polygon: PolygonClient,
    store: OHLCVStore,
    start: date,
    end: date,
) -> dict[str, int]:
    """Fetch grouped daily bars for each weekday in [start, end]."""
    fetch_dates = _weekdays_in_range(start, end)

    if not fetch_dates:
        click.echo("No weekdays in the specified range.")
        return {"dates_fetched": 0, "bars_stored": 0}

    click.echo(f"Fetching {len(fetch_dates)} trading day(s): {fetch_dates[0]} to {fetch_dates[-1]}")

    total_bars = 0
    dates_fetched = 0

    with click.progressbar(fetch_dates, label="Fetching", show_pos=True) as bar:
        for fetch_date in bar:
            try:
                bars = await polygon.get_grouped_daily(fetch_date)
                if bars:
                    stored = store.write_bars(bars)
                    total_bars += stored
                    dates_fetched += 1
            except Exception as exc:
                click.echo(f"\n  Warning: {fetch_date} failed — {exc}", err=True)

    return {"dates_fetched": dates_fetched, "bars_stored": total_bars}


async def _fetch_meta(polygon: PolygonClient, meta: TickerMetaStore) -> int:
    """Refresh ticker reference metadata (market cap, exchange, type)."""
    click.echo("Fetching ticker metadata...")
    try:
        ticker_infos = await polygon.get_tickers(
            market="stocks", active=True, ticker_type="CS",
        )
        if ticker_infos:
            count = meta.write_meta(ticker_infos)
            click.echo(f"  Stored metadata for {count:,} tickers")
            return count
    except Exception as exc:
        click.echo(f"  Warning: metadata fetch failed — {exc}", err=True)
    return 0


async def _fetch_intraday(
    polygon: PolygonClient,
    intraday_store: IntradayStore,
    ohlcv_store: OHLCVStore,
    meta_store: TickerMetaStore,
    from_date: date,
    to_date: date,
    explicit_tickers: list[str] | None = None,
) -> dict[str, int]:
    """Fetch 5-min intraday bars for CSP-eligible tickers.

    If explicit_tickers is provided, fetches those directly.
    Otherwise, runs ConvictionEngine on the OHLCV store to find all
    tickers that were ever CSP-eligible in the date range, then fetches
    only those tickers' intraday data.
    """
    if explicit_tickers:
        tickers = explicit_tickers
        click.echo(f"Fetching intraday data for {len(tickers)} specified ticker(s)")
    else:
        click.echo("Identifying CSP-eligible tickers from daily data...")
        engine = ConvictionEngine()
        all_tickers = ohlcv_store.get_all_tickers()

        meta_df = meta_store.read_meta()
        valid_exchanges = {"XNYS", "XNAS", "XNMS", "XASE", "ARCX", "BATS"}
        if not meta_df.empty:
            large_caps = set(
                meta_df[
                    (meta_df["market_cap"] >= 5_000_000_000)
                    & (meta_df["exchange"].isin(valid_exchanges))
                ]["ticker"].tolist()
            )
            all_tickers = [t for t in all_tickers if t in large_caps]

        ticker_data = ohlcv_store.read_tickers(all_tickers, start_date=from_date - timedelta(days=60))
        eligible_tickers: set[str] = set()

        for ticker, df in ticker_data.items():
            if len(df) < 50:
                continue
            trading_days = [d for d in df["date"].unique() if from_date <= d <= to_date]
            for td in trading_days:
                as_of_df = df[df["date"] <= td]
                if len(as_of_df) < 50:
                    continue
                signal = engine.analyze(ticker, as_of_df)
                if signal.csp_eligible:
                    eligible_tickers.add(ticker)
                    break

        tickers = sorted(eligible_tickers)
        click.echo(f"Found {len(tickers)} tickers with CSP-eligible days in range")

    if not tickers:
        click.echo("No eligible tickers for intraday data.")
        return {"tickers_fetched": 0, "bars_stored": 0}

    completed = {"fetched": 0, "bars": 0, "errors": 0}
    concurrency = 10

    async def _fetch_one(ticker: str, sem: asyncio.Semaphore) -> None:
        async with sem:
            try:
                bars = await polygon.get_aggregate_bars(
                    ticker=ticker,
                    from_date=from_date,
                    to_date=to_date,
                    multiplier=5,
                    timespan="minute",
                )
                if bars:
                    stored = intraday_store.write_bars(bars)
                    completed["bars"] += stored
                    completed["fetched"] += 1
            except Exception as exc:
                completed["errors"] += 1
                logger.warning("intraday_fetch_error", ticker=ticker, error=str(exc))

    sem = asyncio.Semaphore(concurrency)
    tasks = [_fetch_one(t, sem) for t in tickers]

    click.echo(f"Fetching {len(tickers)} tickers (concurrency={concurrency})...")
    await asyncio.gather(*tasks)

    if completed["errors"]:
        click.echo(f"  {completed['errors']} ticker(s) had fetch errors", err=True)

    return {"tickers_fetched": completed["fetched"], "bars_stored": completed["bars"]}


async def _run(
    from_date: date | None,
    to_date: date | None,
    days: int | None,
    meta: bool,
    status: bool,
    intraday: bool = False,
    intraday_tickers: str | None = None,
) -> None:
    settings = TycheSettings()
    store = OHLCVStore(data_dir=settings.data_dir)
    meta_store = TickerMetaStore(data_dir=settings.data_dir)
    intraday_store = IntradayStore(data_dir=settings.data_dir)

    if status:
        _print_status(store, meta_store, intraday_store)
        return

    if store.has_legacy_file:
        click.echo("Migrating OHLCV store from single file to per-ticker layout...")
        count = store.migrate_from_legacy()
        click.echo(f"  Migrated {count:,} tickers. Old file renamed to .parquet.bak")

    if intraday_store.has_legacy_file:
        click.echo("Migrating intraday store from single file to per-ticker layout...")
        count = intraday_store.migrate_from_legacy()
        click.echo(f"  Migrated {count:,} tickers. Old file renamed to .parquet.bak")

    if not settings.polygon_api_key:
        click.echo("Error: TYCHE_POLYGON_API_KEY not set.", err=True)
        sys.exit(1)

    polygon = PolygonClient(
        api_key=settings.polygon_api_key,
        base_url=settings.polygon_base_url,
        rate_limit_rpm=settings.polygon_rate_limit_rpm,
    )

    if days is not None:
        click.echo(f"Full bootstrap: {days} calendar days back")
        result = await bootstrap_ohlcv(polygon, store, days=days, meta_store=meta_store)
        click.echo(f"\nDone: {result['dates_fetched']} days, {result['bars_stored']:,} bars, "
                    f"{result['tickers_found']:,} tickers, {result['tickers_meta']:,} metadata")
    else:
        if from_date is None:
            latest = store.get_latest_date()
            if latest:
                from_date = latest + timedelta(days=1)
            else:
                from_date = date.today() - timedelta(days=180)
                click.echo(f"Empty store — defaulting to 180 days back ({from_date})")

        if to_date is None:
            to_date = _last_trading_day(date.today())

        if from_date > to_date:
            click.echo(f"Already up to date (latest: {from_date - timedelta(days=1)}, "
                        f"last trading day: {to_date})")
            if meta:
                await _fetch_meta(polygon, meta_store)
        else:
            result = await _fetch_range(polygon, store, from_date, to_date)
            click.echo(f"\nDone: {result['dates_fetched']} days, {result['bars_stored']:,} bars added")

            if meta:
                await _fetch_meta(polygon, meta_store)

    if intraday:
        explicit = (
            [t.strip().upper() for t in intraday_tickers.split(",") if t.strip()]
            if intraday_tickers
            else None
        )

        intraday_from = from_date
        intraday_to = to_date
        if intraday_from is None:
            intraday_latest = intraday_store.get_latest_date()
            if intraday_latest:
                intraday_from = intraday_latest + timedelta(days=1)
            else:
                intraday_from = date.today() - timedelta(days=90)

        if intraday_to is None:
            intraday_to = _last_trading_day(date.today())

        if intraday_from <= intraday_to:
            iresult = await _fetch_intraday(
                polygon, intraday_store, store, meta_store,
                intraday_from, intraday_to, explicit,
            )
            click.echo(
                f"\nIntraday: {iresult['tickers_fetched']} tickers, "
                f"{iresult['bars_stored']:,} bars stored"
            )
        else:
            click.echo("Intraday store already up to date.")

    _print_status(store, meta_store, intraday_store)


@click.command()
@click.option("--from", "from_date", type=click.DateTime(formats=["%Y-%m-%d"]),
              default=None, help="Start date (YYYY-MM-DD). Defaults to latest+1 in store.")
@click.option("--to", "to_date", type=click.DateTime(formats=["%Y-%m-%d"]),
              default=None, help="End date (YYYY-MM-DD). Defaults to last trading day.")
@click.option("--days", type=int, default=None,
              help="Full bootstrap N calendar days back (uses bootstrap_ohlcv).")
@click.option("--meta", is_flag=True, default=False,
              help="Also refresh ticker metadata (market cap, exchange).")
@click.option("--status", is_flag=True, default=False,
              help="Show store status and exit without fetching.")
@click.option("--intraday", is_flag=True, default=False,
              help="Also fetch 5-min intraday bars for CSP-eligible tickers.")
@click.option("--intraday-tickers", type=str, default=None,
              help="Comma-separated tickers for intraday fetch (overrides auto-discovery).")
def main(
    from_date: click.DateTime | None,
    to_date: click.DateTime | None,
    days: int | None,
    meta: bool,
    status: bool,
    intraday: bool,
    intraday_tickers: str | None,
) -> None:
    """Ingest OHLCV daily bars, intraday bars, and ticker metadata from Polygon.io."""
    fd = from_date.date() if from_date else None
    td = to_date.date() if to_date else None
    asyncio.run(_run(fd, td, days, meta, status, intraday, intraday_tickers))


if __name__ == "__main__":
    main()
