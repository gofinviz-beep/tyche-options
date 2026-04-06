"""Bridge the OHLCV data gap from the infiniti store's end date to present.

Uses Polygon's grouped daily endpoint (one call per trading day, returns all
tickers) to efficiently fill the gap. Filters results to only tickers already
in the Tyche store.

Usage:
    cd backend && python scripts/bridge_ohlcv_gap.py [--max-days 200]

Requires TYCHE_POLYGON_API_KEY in .env or environment.
"""

import argparse
import asyncio
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, "src")

from tyche.config import get_settings
from tyche.market_data.data_store import OHLCVStore
from tyche.market_data.polygon import DailyBar, PolygonClient


def get_trading_days(start: date, end: date) -> list[date]:
    """Generate weekday dates between start and end (inclusive)."""
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


async def run_bridge(max_days: int, dry_run: bool = False):
    settings = get_settings()
    store = OHLCVStore(data_dir=settings.data_dir)

    if not store.exists:
        print("ERROR: Tyche OHLCV store is empty. Run ingest_infiniti.py first.")
        sys.exit(1)

    if not settings.polygon_api_key:
        print("ERROR: TYCHE_POLYGON_API_KEY not set.")
        sys.exit(1)

    store_tickers = set(store.get_all_tickers())
    latest = store.get_latest_date()
    yesterday = date.today() - timedelta(days=1)
    while yesterday.weekday() >= 5:
        yesterday -= timedelta(days=1)

    print(f"Store has {len(store_tickers)} tickers, latest date: {latest}")
    print(f"Target end date: {yesterday}")

    if latest is None:
        print("ERROR: Could not determine latest date in store.")
        sys.exit(1)

    if latest >= yesterday:
        print("Store is already up to date. Nothing to bridge.")
        return

    gap_start = latest + timedelta(days=1)
    trading_days = get_trading_days(gap_start, yesterday)

    if max_days and len(trading_days) > max_days:
        trading_days = trading_days[:max_days]
        print(f"  Capped to {max_days} days (use --max-days to adjust)")

    print(f"  Gap: {gap_start} to {trading_days[-1]} ({len(trading_days)} trading days)")

    if dry_run:
        print(f"\n[DRY RUN] Would fetch {len(trading_days)} days of grouped daily data")
        print(f"  First: {trading_days[0]}, Last: {trading_days[-1]}")
        return

    client = PolygonClient(
        api_key=settings.polygon_api_key,
        rate_limit_rpm=getattr(settings, "polygon_rate_limit_rpm", 5),
    )

    total_bars_written = 0
    days_fetched = 0
    errors = 0
    t0 = time.time()

    for i, day in enumerate(trading_days):
        elapsed = time.time() - t0
        rate = (i + 1) / elapsed if elapsed > 0 else 0
        eta = (len(trading_days) - i - 1) / rate if rate > 0 else 0

        if i % 5 == 0:
            print(
                f"  [{i + 1}/{len(trading_days)}] {day} "
                f"bars_written={total_bars_written} errors={errors} "
                f"(ETA {eta:.0f}s)"
            )

        try:
            all_bars = await client.get_grouped_daily(day)
        except Exception as exc:
            print(f"  ERROR fetching {day}: {exc}")
            errors += 1
            continue

        filtered: list[DailyBar] = [
            b for b in all_bars if b.ticker in store_tickers
        ]

        if filtered:
            written = store.write_bars(filtered)
            total_bars_written += written

        days_fetched += 1

    elapsed = time.time() - t0
    print(f"\nBridge complete in {elapsed:.1f}s")
    print(f"  Days fetched: {days_fetched}")
    print(f"  Total bars written: {total_bars_written}")
    print(f"  Errors: {errors}")

    print("Rebuilding OHLCV cache...")
    cache = store.rebuild_cache()
    print(f"  Store now has {cache.get('ticker_count', '?')} tickers, "
          f"latest: {cache.get('latest_date', '?')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bridge OHLCV gap via Polygon")
    parser.add_argument(
        "--max-days",
        type=int,
        default=0,
        help="Max trading days to fetch (0 = all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be fetched without calling Polygon",
    )
    args = parser.parse_args()
    asyncio.run(run_bridge(args.max_days, args.dry_run))
