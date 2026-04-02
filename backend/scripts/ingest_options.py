"""Ingest options chain snapshots from Tradier into the OptionsChainStore.

Fetches live options chains and persists them as Parquet files for use in
backtest validation.  Can be run manually or is automatically scheduled
daily at 4:10 PM ET (configurable via TYCHE_OPTIONS_SNAPSHOT_TIME).

Storage layout:  data/options_chains/{TICKER}.parquet
Each file accumulates timestamped snapshots; deduplication prevents
re-ingesting the same (date, expiration, strike, type) combination.

Usage examples:

  # Ingest chains for specific tickers (nearest 2 expirations, puts only)
  python scripts/ingest_options.py --tickers AAPL,MSFT,GOOGL

  # Ingest for all tickers in the OHLCV store (large-cap only)
  python scripts/ingest_options.py --from-ohlcv --min-market-cap 5e9

  # Ingest all expirations within 45 DTE, both puts and calls
  python scripts/ingest_options.py --tickers AAPL --max-dte 45 --include-calls

  # Dry run to see what would be fetched
  python scripts/ingest_options.py --from-ohlcv --dry-run

  # Custom rate limit and concurrency
  python scripts/ingest_options.py --from-ohlcv --rpm 60 --concurrency 5
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime

sys.path.insert(0, "src")

from tyche.config import TycheSettings
from tyche.market_data.data_store import OHLCVStore, OptionsChainStore, TickerMetaStore
from tyche.workflow.options_snapshot import run_options_snapshot


def _resolve_tickers(args: argparse.Namespace, settings: TycheSettings) -> list[str]:
    """Resolve the ticker list from CLI args."""
    if args.tickers:
        return [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    data_dir = settings.data_dir

    if args.from_ohlcv:
        ohlcv_store = OHLCVStore(data_dir=data_dir)
        meta_store = TickerMetaStore(data_dir=data_dir)
        all_tickers = ohlcv_store.get_all_tickers()

        if not all_tickers:
            print("ERROR: OHLCV store is empty. Run bootstrap first.")
            sys.exit(1)

        if meta_store.exists:
            equities = meta_store.filter_equity_only(all_tickers)

            if args.min_market_cap > 0:
                caps = meta_store.get_market_caps(equities)
                equities = [t for t in equities if caps.get(t, 0) >= args.min_market_cap]
                print(f"  After market cap filter (>= ${args.min_market_cap/1e9:.0f}B): "
                      f"{len(equities)} tickers")

            return equities

        return all_tickers

    if args.from_watchlist:
        if settings.watchlist_symbols:
            return list(settings.watchlist_symbols)
        print("ERROR: No watchlist configured in settings.")
        sys.exit(1)

    print("ERROR: Specify --tickers, --from-ohlcv, or --from-watchlist")
    sys.exit(1)


async def _run(args: argparse.Namespace, settings: TycheSettings) -> None:
    tickers = _resolve_tickers(args, settings)
    print(f"Resolved {len(tickers)} tickers")

    if not tickers:
        print("No tickers to process.")
        return

    snap_date = None
    if args.snapshot_date:
        snap_date = datetime.strptime(args.snapshot_date, "%Y-%m-%d").date()

    if args.dry_run:
        est_calls = len(tickers) * (1 + (args.max_expirations or settings.options_snapshot_max_expirations))
        rpm = args.rpm or settings.options_snapshot_rpm
        est_minutes = est_calls / rpm
        print(f"\n  DRY RUN — no data will be fetched or stored")
        print(f"  Tickers:             {len(tickers)}")
        print(f"  Expirations/ticker:  {args.max_expirations or settings.options_snapshot_max_expirations}")
        print(f"  Estimated API calls: ~{est_calls}")
        print(f"  Rate limit:          {rpm} RPM")
        print(f"  Estimated time:      ~{est_minutes:.1f} minutes")
        return

    stats = await run_options_snapshot(
        tickers=tickers,
        settings=settings,
        snapshot_date=snap_date,
        max_expirations=args.max_expirations,
        min_dte=args.min_dte,
        max_dte=args.max_dte,
        puts_only=not args.include_calls,
        concurrency=args.concurrency,
        rpm=args.rpm,
    )

    stats.print_summary()

    store = OptionsChainStore(data_dir=settings.data_dir)
    print(f"\nStore totals: {store.get_ticker_count()} tickers, "
          f"{len(store.list_snapshot_dates())} snapshot dates")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest options chain snapshots from Tradier",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --tickers AAPL,MSFT,GOOGL
  %(prog)s --from-ohlcv --min-market-cap 5e9
  %(prog)s --from-watchlist --max-dte 30
  %(prog)s --from-ohlcv --dry-run
        """,
    )

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--tickers", type=str,
        help="Comma-separated list of ticker symbols",
    )
    source.add_argument(
        "--from-ohlcv", action="store_true",
        help="Use all tickers from the OHLCV store (filtered by metadata)",
    )
    source.add_argument(
        "--from-watchlist", action="store_true",
        help="Use tickers from the configured watchlist",
    )

    parser.add_argument(
        "--max-expirations", type=int, default=None,
        help="Max expirations to fetch per ticker (default: from config, typically 2)",
    )
    parser.add_argument(
        "--min-dte", type=int, default=None,
        help="Minimum days to expiration (default: from config, typically 1)",
    )
    parser.add_argument(
        "--max-dte", type=int, default=None,
        help="Maximum days to expiration (default: from config, typically 45)",
    )
    parser.add_argument(
        "--include-calls", action="store_true",
        help="Include call contracts (default: puts only)",
    )
    parser.add_argument(
        "--concurrency", type=int, default=None,
        help="Max concurrent API requests (default: from config, typically 10)",
    )
    parser.add_argument(
        "--rpm", type=int, default=None,
        help="Tradier API rate limit in requests/minute (default: from config, typically 120)",
    )
    parser.add_argument(
        "--min-market-cap", type=float, default=5e9,
        help="Min market cap filter when using --from-ohlcv (default: $5B)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be fetched without making API calls",
    )
    parser.add_argument(
        "--snapshot-date", type=str, default=None,
        help="Override snapshot date (YYYY-MM-DD, default: today)",
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    settings = TycheSettings()
    asyncio.run(_run(args, settings))
