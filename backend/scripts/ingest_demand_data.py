"""Ingest demand data: fundamentals, estimates/revisions, short interest.

Foundation for the Demand Conviction engine (Directional Alpha v2):
  - Fundamentals (Finnhub Fundamental-1 statements; Polygon fallback) -> FundamentalsStore
  - Estimates / revisions / surprises (Finnhub Estimates-1) -> EstimatesStore
  - Short interest (Polygon) -> ShortInterestStore
  - Corporate guidance (Benzinga via Massive/Polygon) -> CatalystSignalStore

Each source degrades gracefully when its credentials/subscription are absent.
Estimates are snapshotted (run daily to build a revision time series).

Run from ``backend/`` with the venv:
    .venv/bin/python scripts/ingest_demand_data.py
    .venv/bin/python scripts/ingest_demand_data.py --tickers MU,AMD,NVDA
    .venv/bin/python scripts/ingest_demand_data.py --no-estimates --no-short-interest
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tyche.config import get_settings  # noqa: E402
from tyche.workflow.demand_data import ingest_demand_data  # noqa: E402

logger = structlog.get_logger()


@click.command()
@click.option("--tickers", default=None, help="Comma-separated subset (default: full equity universe).")
@click.option("--fundamentals/--no-fundamentals", default=True, help="Ingest quarterly fundamentals.")
@click.option("--estimates/--no-estimates", default=True, help="Ingest analyst estimates/revisions/surprises.")
@click.option("--short-interest/--no-short-interest", default=True, help="Ingest short interest.")
@click.option("--guidance/--no-guidance", default=True, help="Ingest Benzinga corporate guidance catalysts.")
@click.option("--concurrency", default=None, type=int, help="Concurrent requests.")
@click.option("--limit-periods", default=20, help="Quarters of fundamentals to fetch per ticker.")
@click.option(
    "--finnhub-rpm",
    default=None,
    type=int,
    help="Override Finnhub calls/min for this run (set to your paid plan's limit).",
)
def main(
    tickers: str | None,
    fundamentals: bool,
    estimates: bool,
    short_interest: bool,
    guidance: bool,
    concurrency: int | None,
    limit_periods: int,
    finnhub_rpm: int | None,
) -> None:
    settings = get_settings()
    if finnhub_rpm:
        settings.finnhub_rate_limit_rpm = finnhub_rpm
    ticker_list = (
        [t.strip().upper() for t in tickers.split(",") if t.strip()] if tickers else None
    )

    fund_source = (settings.fundamentals_source or "finnhub").lower()
    if not settings.finnhub_api_key and (estimates or (fundamentals and fund_source == "finnhub")):
        click.echo(
            "Warning: TYCHE_FINNHUB_API_KEY not set — estimates/Finnhub fundamentals skipped.",
            err=True,
        )
    if not settings.polygon_api_key and (short_interest or guidance or fundamentals):
        click.echo(
            "Warning: TYCHE_POLYGON_API_KEY not set — short-interest/guidance/Polygon-fundamentals skipped.",
            err=True,
        )

    counts = asyncio.run(
        ingest_demand_data(
            settings,
            tickers=ticker_list,
            do_fundamentals=fundamentals,
            do_estimates=estimates,
            do_short_interest=short_interest,
            do_guidance=guidance,
            concurrency=concurrency,
            limit_periods=limit_periods,
        )
    )

    click.echo(
        f"Done. tickers={counts['tickers']:,} "
        f"fundamentals={counts['fundamentals']:,} "
        f"estimates={counts['estimates']:,} "
        f"short_interest={counts['short_interest']:,} "
        f"guidance_fetched={counts['guidance_tickers_fetched']:,} "
        f"guidance_written={counts['guidance_catalysts_written']:,}"
    )


if __name__ == "__main__":
    main()
