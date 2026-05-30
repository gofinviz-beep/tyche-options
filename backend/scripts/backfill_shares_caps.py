"""Backfill shares outstanding and recompute live market caps.

Polygon's ``market_cap`` reference field lags by months (it is not re-priced
daily). This script fetches ``weighted_shares_outstanding`` from the same free
``/v3/reference/tickers/{ticker}`` endpoint and stores it, then derives a
price-current market cap = shares x latest daily close and writes it back into
``ticker_meta.parquet``.

Run from ``backend/`` with the venv:
    .venv/bin/python scripts/backfill_shares_caps.py
    .venv/bin/python scripts/backfill_shares_caps.py --tickers MU,WMT,NVDA
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tyche.config import get_settings  # noqa: E402
from tyche.market_data.data_store import (  # noqa: E402
    OHLCVStore,
    TickerMetaStore,
    recompute_market_caps_from_shares,
)
from tyche.market_data.polygon import PolygonClient  # noqa: E402

logger = structlog.get_logger()


async def _run(tickers: list[str] | None, concurrency: int, rpm: int) -> None:
    settings = get_settings()
    if not settings.polygon_api_key:
        click.echo("Error: TYCHE_POLYGON_API_KEY not set.", err=True)
        sys.exit(1)

    ohlcv = OHLCVStore(data_dir=settings.data_dir)
    meta = TickerMetaStore(data_dir=settings.data_dir)

    if tickers:
        universe = tickers
    else:
        # Equity-only names that have OHLCV (so the derived cap has a close).
        with_ohlcv = set(ohlcv.get_all_tickers())
        eligible = meta.filter_equity_only(sorted(with_ohlcv))
        universe = sorted(t for t in eligible if t in with_ohlcv)

    click.echo(f"Fetching shares outstanding for {len(universe):,} tickers...")

    polygon = PolygonClient(
        api_key=settings.polygon_api_key,
        base_url=settings.polygon_base_url,
        rate_limit_rpm=settings.polygon_rate_limit_rpm,
    )

    details = await polygon.get_batch_ticker_details_concurrent(
        universe, concurrency=concurrency, rate_limit_rpm=rpm
    )

    shares = {
        t: info["shares_outstanding"]
        for t, info in details.items()
        if info.get("shares_outstanding", 0) > 0
    }
    click.echo(f"  Got shares for {len(shares):,} tickers.")

    stored = meta.update_shares_outstanding(shares)
    click.echo(f"  Stored shares for {stored:,} tickers.")

    updated = recompute_market_caps_from_shares(meta, ohlcv, list(shares.keys()))
    click.echo(f"  Recomputed live market caps for {updated:,} tickers.")

    # Quick sanity print for a few well-known names.
    caps = meta.get_market_caps(["MU", "WMT", "NVDA", "AAPL"])
    for t, c in caps.items():
        if c:
            click.echo(f"    {t}: ${c / 1e9:,.1f}B")


@click.command()
@click.option("--tickers", default=None, help="Comma-separated subset (default: full equity universe).")
@click.option("--concurrency", default=20, help="Concurrent Polygon requests.")
@click.option("--rpm", default=500, help="Rate limit (requests/min).")
def main(tickers: str | None, concurrency: int, rpm: int) -> None:
    ticker_list = (
        [t.strip().upper() for t in tickers.split(",") if t.strip()]
        if tickers
        else None
    )
    asyncio.run(_run(ticker_list, concurrency, rpm))


if __name__ == "__main__":
    main()
