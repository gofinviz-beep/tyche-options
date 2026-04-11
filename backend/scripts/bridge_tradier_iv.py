"""Bridge Tradier options snapshots into the IV / derived metrics pipeline.

When the Massive S3 flat file for today is not yet available, this script
reads today's snapshot from ``OptionsChainStore`` (captured via Tradier API),
extracts ATM put IV per ticker, writes it to ``HistoricalIVStore``, and
recomputes derived metrics (IV Rank, IV Percentile, RV 20d, VRP).

Tradier provides ``implied_volatility`` directly, so no Black-Scholes
inversion is needed (unlike the flat-file pipeline).

Usage:
    # After running: python scripts/ingest_options.py --from-ohlcv
    python scripts/bridge_tradier_iv.py
    python scripts/bridge_tradier_iv.py --date 2026-04-07
    python scripts/bridge_tradier_iv.py --dry-run
"""

from __future__ import annotations

import math
import sys
import time
from datetime import date, timedelta

import click
import structlog

sys.path.insert(0, "src")

from tyche.config import get_settings
from tyche.market_data.data_store import OHLCVStore, OptionsChainStore
from tyche.market_data.derived_store import DerivedMetricsStore
from tyche.market_data.historical_iv_store import HistoricalIVStore

logger = structlog.get_logger()

TARGET_DTE = 30
DTE_TOLERANCE = 15


def _extract_atm_iv_from_snapshot(
    chain_store: OptionsChainStore,
    ohlcv_store: OHLCVStore,
    iv_store: HistoricalIVStore,
    ticker: str,
    snapshot_date: date,
    target_dte: int = TARGET_DTE,
    dte_tolerance: int = DTE_TOLERANCE,
) -> int:
    """Extract ATM put IV from a Tradier options chain snapshot.

    Uses Tradier's pre-computed ``implied_volatility`` rather than
    Black-Scholes inversion.  Writes the result to ``HistoricalIVStore``.

    Returns 1 if an IV point was written, 0 otherwise.
    """
    df = chain_store.read_ticker(ticker, snapshot_date=snapshot_date, option_type="put")
    if df.empty:
        return 0

    ohlcv_df = ohlcv_store.read_ticker(ticker)
    if ohlcv_df.empty:
        return 0

    ohlcv_df["date"] = ohlcv_df["date"].apply(
        lambda d: d.date() if hasattr(d, "date") else d
    )
    close_row = ohlcv_df[ohlcv_df["date"] == snapshot_date]
    if close_row.empty:
        yesterday = snapshot_date - timedelta(days=1)
        close_row = ohlcv_df[ohlcv_df["date"] == yesterday]
    if close_row.empty:
        close_row = ohlcv_df.sort_values("date").tail(1)
    if close_row.empty:
        return 0

    underlying_close = float(close_row.iloc[-1]["close"])
    if underlying_close <= 0:
        return 0

    df = df.copy()
    df["dte"] = df["expiration"].apply(
        lambda exp: (exp - snapshot_date).days if exp > snapshot_date else 0
    )
    df = df[df["dte"] > 0]
    if df.empty:
        return 0

    dte_diff = (df["dte"] - target_dte).abs()
    within_tolerance = df[dte_diff <= dte_tolerance + 10]
    if within_tolerance.empty:
        within_tolerance = df

    best_dte_idx = (within_tolerance["dte"] - target_dte).abs().idxmin()
    best_dte = within_tolerance.loc[best_dte_idx, "dte"]
    dte_group = within_tolerance[within_tolerance["dte"] == best_dte]

    atm_idx = (dte_group["strike"] - underlying_close).abs().idxmin()
    row = dte_group.loc[atm_idx]

    iv = float(row.get("implied_volatility", 0))
    strike = float(row["strike"])
    dte = int(row["dte"])
    option_close = float(row.get("last", 0) or row.get("mid", 0))

    if iv <= 0 or math.isnan(iv) or dte <= 0:
        return 0

    iv_store.write_iv_data(ticker, [
        {
            "date": snapshot_date,
            "strike": strike,
            "expiration": row["expiration"],
            "contract_ticker": f"O:{ticker}_TRADIER_SNAPSHOT",
            "option_close": option_close,
            "underlying_close": underlying_close,
            "dte": dte,
            "implied_volatility": iv,
        }
    ])
    return 1


@click.command()
@click.option("--date", "snapshot_date_str", type=str, default=None,
              help="Snapshot date (YYYY-MM-DD, default: today)")
@click.option("--skip-derived", is_flag=True, help="Skip derived metrics recomputation")
@click.option("--dry-run", is_flag=True, help="Show what would be processed")
def main(
    snapshot_date_str: str | None,
    skip_derived: bool,
    dry_run: bool,
) -> None:
    """Bridge Tradier snapshots into the IV/derived metrics pipeline."""
    settings = get_settings()
    data_dir = settings.data_dir

    snapshot_date = (
        date.fromisoformat(snapshot_date_str)
        if snapshot_date_str
        else date.today()
    )

    chain_store = OptionsChainStore(data_dir=data_dir)
    ohlcv_store = OHLCVStore(data_dir=data_dir)
    iv_store = HistoricalIVStore(data_dir=data_dir)
    derived_store = DerivedMetricsStore(data_dir=data_dir)

    available_dates = chain_store.list_snapshot_dates()
    if snapshot_date not in available_dates:
        click.echo(
            f"No snapshot for {snapshot_date} in OptionsChainStore. "
            f"Run ingest_options.py first."
        )
        if available_dates:
            click.echo(f"  Available dates: {available_dates[-5:]}")
        return

    all_tickers = sorted(
        p.stem.upper()
        for p in chain_store.store_dir.glob("*.parquet")
        if not p.name.startswith("_")
    )

    tickers_with_snapshot: list[str] = []
    for t in all_tickers:
        df = chain_store.read_ticker(t, snapshot_date=snapshot_date)
        if not df.empty:
            tickers_with_snapshot.append(t)

    click.echo(f"\n{'=' * 60}")
    click.echo("Tradier → IV/Derived Bridge")
    click.echo(f"{'=' * 60}")
    click.echo(f"  Snapshot date:        {snapshot_date}")
    click.echo(f"  Tickers in store:     {len(all_tickers)}")
    click.echo(f"  Tickers with data:    {len(tickers_with_snapshot)}")
    click.echo(f"  Skip derived:         {skip_derived}")
    click.echo(f"{'=' * 60}\n")

    if dry_run:
        click.echo("DRY RUN — no data written.")
        return

    if not tickers_with_snapshot:
        click.echo("No tickers with snapshot data for this date.")
        return

    start = time.monotonic()
    iv_written = 0
    derived_written = 0
    total = len(tickers_with_snapshot)

    for i, ticker in enumerate(tickers_with_snapshot, start=1):
        count = _extract_atm_iv_from_snapshot(
            chain_store, ohlcv_store, iv_store, ticker, snapshot_date,
        )
        iv_written += count

        if count > 0 and not skip_derived:
            iv_df = iv_store.read_ticker(ticker)
            ohlcv_df = ohlcv_store.read_ticker(ticker)
            metrics_df = DerivedMetricsStore.compute_metrics(iv_df, ohlcv_df)
            if not metrics_df.empty:
                derived_store.write_metrics(ticker, metrics_df)
                derived_written += 1

        if i % 100 == 0 or i == total:
            elapsed = time.monotonic() - start
            click.echo(
                f"  Progress: {i}/{total} tickers, "
                f"{iv_written} IV points, "
                f"{elapsed:.1f}s"
            )

    elapsed = time.monotonic() - start

    iv_store.write_checkpoint(
        last_options_date=snapshot_date.isoformat(),
        tickers_processed=iv_written,
        iv_points=iv_written,
    )

    click.echo(f"\n{'=' * 60}")
    click.echo("Bridge Complete")
    click.echo(f"{'=' * 60}")
    click.echo(f"  Duration:             {elapsed:.1f}s")
    click.echo(f"  IV points written:    {iv_written}")
    click.echo(f"  Derived updated:      {derived_written}")
    click.echo(f"{'=' * 60}")


if __name__ == "__main__":
    main()
