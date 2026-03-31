"""Backfill market cap data for all tickers in ticker_meta.parquet.

The Polygon /v3/reference/tickers list endpoint doesn't return market_cap.
This script fetches market caps from the individual ticker detail endpoint
(/v3/reference/tickers/{TICKER}) and writes them back to ticker_meta.parquet.

Usage:
    cd backend && python scripts/backfill_market_caps.py
    cd backend && python scripts/backfill_market_caps.py --only-missing   # skip tickers that already have caps
    cd backend && python scripts/backfill_market_caps.py --dry-run        # preview without writing
"""

import argparse
import asyncio
import sys
import time

sys.path.insert(0, "src")

from tyche.config import TycheSettings
from tyche.market_data.data_store import TickerMetaStore
from tyche.market_data.polygon import PolygonClient


async def run(only_missing: bool, dry_run: bool) -> None:
    settings = TycheSettings()
    meta_store = TickerMetaStore(data_dir=settings.data_dir)

    if not meta_store.exists:
        print("ERROR: ticker_meta.parquet not found. Run ingest_data.py --meta first.")
        return

    meta_df = meta_store.read_meta()
    all_tickers = meta_df["ticker"].tolist()
    print(f"Total tickers in metadata: {len(all_tickers)}")

    if only_missing:
        existing_caps = meta_store.get_market_caps()
        tickers_needing_caps = [t for t in all_tickers if existing_caps.get(t, 0) <= 0]
        print(f"Tickers already with market cap: {len(all_tickers) - len(tickers_needing_caps)}")
        print(f"Tickers needing market cap: {len(tickers_needing_caps)}")
        target_tickers = tickers_needing_caps
    else:
        target_tickers = all_tickers

    if not target_tickers:
        print("Nothing to fetch.")
        return

    rpm = settings.polygon_rate_limit_rpm
    est_minutes = len(target_tickers) / rpm
    print(f"\nRate limit: {rpm} RPM")
    print(f"Estimated time: {est_minutes:.0f} minutes ({est_minutes/60:.1f} hours)")
    print(f"Tickers to fetch: {len(target_tickers)}")

    if dry_run:
        print("\n--dry-run: would fetch market caps for the above tickers. Exiting.")
        return

    polygon = PolygonClient(
        api_key=settings.polygon_api_key,
        base_url=settings.polygon_base_url,
        rate_limit_rpm=rpm,
    )

    print(f"\nFetching market caps...")
    t0 = time.time()

    caps = await polygon.get_batch_market_caps(target_tickers)

    elapsed = time.time() - t0
    print(f"\nFetch complete in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"  Tickers with valid market cap: {len(caps)}")
    print(f"  Tickers with no/zero cap: {len(target_tickers) - len(caps)}")

    if not caps:
        print("No market caps fetched. Nothing to update.")
        return

    # Show distribution
    cap_values = sorted(caps.values(), reverse=True)
    print(f"\n  Top 10 market caps:")
    top_tickers = sorted(caps.items(), key=lambda x: -x[1])[:10]
    for ticker, cap in top_tickers:
        print(f"    {ticker:6s}: ${cap/1e9:>10.1f}B")

    above_5b = sum(1 for v in cap_values if v >= 5e9)
    above_1b = sum(1 for v in cap_values if v >= 1e9)
    print(f"\n  >= $5B:  {above_5b} tickers")
    print(f"  >= $1B:  {above_1b} tickers")
    print(f"  < $1B:   {len(caps) - above_1b} tickers")

    updated = meta_store.update_market_caps(caps)
    print(f"\nUpdated {updated} tickers in ticker_meta.parquet")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill market caps from Polygon detail API")
    parser.add_argument("--only-missing", action="store_true",
                        help="Only fetch for tickers with market_cap = 0")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview what would be fetched without making API calls")
    args = parser.parse_args()
    asyncio.run(run(args.only_missing, args.dry_run))
