"""Bulk historical options ingestion via Massive / Polygon S3 flat files.

Downloads daily ``options/day-aggregates`` CSV files from the Massive
S3-compatible endpoint, filters to the ticker universe, persists full
options chain data per ticker, then extracts ATM put IV and computes
derived metrics (IV Rank, IV Percentile, RV, VRP).

Compared to the REST-based ``ingest_options_history.py`` (~50K API calls,
2+ days), this approach downloads ~500 compressed CSV files and finishes
in 30–60 minutes.

Designed for long-running execution in a ``screen`` session:

    cd backend
    screen -S flatfile-ingest
    python scripts/ingest_options_flatfiles.py --from-ohlcv --concurrency 8
    # Ctrl-A D to detach

Storage layout:
    data/options_history/{TICKER}.parquet  — full daily options chain
    data/options_iv/{TICKER}.parquet       — daily ATM put IV per ticker
    data/derived/{TICKER}.parquet          — IV Rank, RV, VRP per ticker

S3 credentials:
    Set TYCHE_MASSIVE_S3_ACCESS_KEY and TYCHE_MASSIVE_S3_SECRET_KEY in
    your .env or environment.  These are obtained from the Massive
    dashboard (separate from TYCHE_POLYGON_API_KEY).

Usage examples:
    python scripts/ingest_options_flatfiles.py --from-ohlcv
    python scripts/ingest_options_flatfiles.py --tickers AAPL,MSFT --force
    python scripts/ingest_options_flatfiles.py --from-ohlcv --dry-run
    python scripts/ingest_options_flatfiles.py --from-ohlcv --skip-iv
    python scripts/ingest_options_flatfiles.py --from-ohlcv --days-back 365
"""

from __future__ import annotations

import io
import logging
import math
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import click
import pandas as pd
import structlog
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, "src")

from tyche.config import get_settings
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.market_data.derived_store import DerivedMetricsStore
from tyche.market_data.historical_iv_store import HistoricalIVStore
from tyche.market_data.iv_calculator import compute_iv
from tyche.market_data.occ_parser import parse_occ_columns
from tyche.market_data.options_history_store import OptionsHistoryStore

logger = structlog.get_logger()

S3_PREFIX = "us_options_opra/day_aggs_v1"

MIN_MARKET_CAP = 1_000_000_000
TARGET_DTE = 30
DTE_TOLERANCE = 5
CSV_CHUNK_SIZE = 500_000


# ── logging ──────────────────────────────────────────────────────────


def _configure_logging(log_file: str | None = None) -> None:
    """Configure structlog with JSON output to stdout and optional file."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if log_file:
        from pathlib import Path

        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        handlers.append(file_handler)

    logging.basicConfig(
        format="%(message)s",
        level=logging.INFO,
        handlers=handlers,
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )


# ── S3 download ─────────────────────────────────────────────────────


def _build_s3_client(settings: object):  # noqa: ANN202
    """Create a boto3 S3 client pointed at the Massive endpoint."""
    import boto3
    from botocore.client import Config

    access_key = settings.massive_s3_access_key
    secret_key = settings.massive_s3_secret_key

    if not access_key or not secret_key:
        raise RuntimeError(
            "Set TYCHE_MASSIVE_S3_ACCESS_KEY and TYCHE_MASSIVE_S3_SECRET_KEY "
            "in .env or environment."
        )

    session = boto3.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    return session.client(
        "s3",
        endpoint_url=settings.massive_s3_url,
        config=Config(signature_version="s3v4"),
    )


def _s3_key_for_date(d: date) -> str:
    """Return the S3 object key for a given trading date."""
    return f"{S3_PREFIX}/{d.year}/{d.month:02d}/{d.strftime('%Y-%m-%d')}.csv.gz"


def _download_date_file(  # noqa: ANN001
    s3_client, d: date, bucket: str
) -> bytes | None:
    """Download a single daily flat file from S3, returning raw bytes.

    Returns None if the file does not exist (weekends, holidays).
    """
    key = _s3_key_for_date(d)
    try:
        buf = io.BytesIO()
        s3_client.download_fileobj(bucket, key, buf)
        size_mb = buf.tell() / (1024 * 1024)
        buf.seek(0)
        raw = buf.read()
        logger.debug("s3_file_downloaded", date=str(d), size_mb=round(size_mb, 1))
        return raw
    except Exception as exc:
        err_code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        # Massive S3 returns 403 (not 404) for objects that don't exist yet.
        if err_code in ("403", "404", "NoSuchKey"):
            logger.debug("s3_file_not_found", date=str(d), key=key)
            return None
        logger.warning("s3_download_error", date=str(d), error=str(exc))
        return None


# ── date resolution ──────────────────────────────────────────────────


def _trading_dates(start: date, end: date) -> list[date]:
    """Generate weekdays between start and end (inclusive).

    Not a perfect trading calendar (holidays still included), but
    missing S3 files are handled gracefully by returning None on
    download.
    """
    result: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


# ── CSV processing ───────────────────────────────────────────────────


def _process_date_file(
    raw_bytes: bytes,
    trading_date: date,
    universe: set[str],
    ohlcv_closes: dict[str, dict[date, float]],
) -> pd.DataFrame:
    """Parse a compressed CSV, filter to universe, enrich with OCC fields.

    Returns a DataFrame matching ``OPTIONS_HISTORY_SCHEMA`` columns.
    """
    buf = io.BytesIO(raw_bytes)

    chunks: list[pd.DataFrame] = []
    for chunk in pd.read_csv(buf, compression="gzip", chunksize=CSV_CHUNK_SIZE):
        underlying = chunk["ticker"].str[2:-15]
        mask = underlying.isin(universe)
        filtered = chunk[mask].copy()
        if filtered.empty:
            continue
        chunks.append(filtered)

    if not chunks:
        return pd.DataFrame()

    df = pd.concat(chunks, ignore_index=True)

    df["option_ticker"] = df["ticker"]
    parse_occ_columns(df, ticker_col="option_ticker")

    df["date"] = trading_date
    df["dte"] = df["expiration"].apply(
        lambda exp: (exp - trading_date).days if exp > trading_date else 0
    )

    if "transactions" not in df.columns:
        df["transactions"] = 0

    result = df[
        [
            "date",
            "option_ticker",
            "underlying",
            "expiration",
            "strike",
            "option_type",
            "open",
            "close",
            "high",
            "low",
            "volume",
            "transactions",
            "dte",
        ]
    ].copy()

    result["transactions"] = result["transactions"].fillna(0).astype("int32")
    result["volume"] = result["volume"].fillna(0).astype("int64")

    return result


# ── ATM IV extraction ────────────────────────────────────────────────


def _extract_atm_iv_from_history(
    history_store: OptionsHistoryStore,
    ohlcv_store: OHLCVStore,
    iv_store: HistoricalIVStore,
    ticker: str,
    target_dte: int = TARGET_DTE,
    dte_tolerance: int = DTE_TOLERANCE,
) -> int:
    """Extract ATM put IV from stored options history for one ticker.

    For each trading day, selects the nearest-ATM put with DTE closest
    to ``target_dte``, computes IV via Black-Scholes, and writes to
    ``HistoricalIVStore``.

    Returns number of IV data points written.
    """
    opts_df = history_store.read_ticker(ticker)
    if opts_df.empty:
        return 0

    puts = opts_df[opts_df["option_type"] == "P"].copy()
    if puts.empty:
        return 0

    ohlcv_df = ohlcv_store.read_ticker(ticker)
    if ohlcv_df.empty:
        return 0

    ohlcv_df["date"] = pd.to_datetime(ohlcv_df["date"]).dt.date
    ohlcv_by_date: dict[date, float] = dict(
        zip(ohlcv_df["date"], ohlcv_df["close"].astype(float))
    )

    iv_records: list[dict] = []

    for trade_date, day_puts in puts.groupby("date"):
        underlying_close = ohlcv_by_date.get(trade_date)
        if not underlying_close or underlying_close <= 0:
            continue

        dte_diff = (day_puts["dte"] - target_dte).abs()
        within_tolerance = day_puts[dte_diff <= dte_tolerance + 10]

        if within_tolerance.empty:
            within_tolerance = day_puts

        best_dte_idx = (within_tolerance["dte"] - target_dte).abs().idxmin()
        best_dte = within_tolerance.loc[best_dte_idx, "dte"]
        dte_group = within_tolerance[within_tolerance["dte"] == best_dte]

        atm_idx = (dte_group["strike"] - underlying_close).abs().idxmin()
        row = dte_group.loc[atm_idx]

        option_close = float(row["close"])
        strike = float(row["strike"])
        dte = int(row["dte"])

        if option_close <= 0 or dte <= 0:
            continue

        iv = compute_iv(
            option_price=option_close,
            underlying_price=underlying_close,
            strike=strike,
            dte=dte,
        )

        if math.isnan(iv):
            continue

        iv_records.append(
            {
                "date": trade_date,
                "strike": strike,
                "expiration": row["expiration"],
                "contract_ticker": row["option_ticker"],
                "option_close": option_close,
                "underlying_close": underlying_close,
                "dte": dte,
                "implied_volatility": iv,
            }
        )

    if iv_records:
        iv_store.write_iv_data(ticker, iv_records)

    return len(iv_records)


# ── ticker resolution (reused from REST script) ─────────────────────


def _resolve_tickers(
    tickers_str: str | None,
    from_ohlcv: bool,
    settings: object,
    min_market_cap: float,
    min_institutional_pct: float,
) -> list[str]:
    """Resolve ticker list from CLI options."""
    if tickers_str:
        return [t.strip().upper() for t in tickers_str.split(",") if t.strip()]

    if from_ohlcv:
        data_dir = settings.data_dir
        ohlcv_store = OHLCVStore(data_dir=data_dir)
        meta_store = TickerMetaStore(data_dir=data_dir)
        all_tickers = ohlcv_store.get_all_tickers()

        if not all_tickers:
            click.echo("ERROR: OHLCV store is empty. Run ingest_data.py first.")
            sys.exit(1)

        click.echo(f"  OHLCV universe: {len(all_tickers)} tickers")

        if meta_store.exists:
            equities = meta_store.filter_equity_only(all_tickers)
            click.echo(f"  After equity filter: {len(equities)} tickers")

            if min_market_cap > 0:
                caps = meta_store.get_market_caps(equities)
                below = [
                    t for t in equities
                    if caps.get(t) is not None
                    and caps[t] > 0
                    and caps[t] < min_market_cap
                ]
                equities = [t for t in equities if t not in set(below)]
                click.echo(
                    f"  After market cap filter (>= ${min_market_cap / 1e9:.1f}B, "
                    f"dropped {len(below)} below threshold, "
                    f"passed {len(equities)} incl. no-data): "
                    f"{len(equities)} tickers"
                )

            if min_institutional_pct > 0:
                inst = meta_store.get_institutional_pcts(equities)
                equities = [
                    t
                    for t in equities
                    if (inst.get(t) or 0) >= min_institutional_pct
                ]
                click.echo(
                    f"  After institutional filter (>= {min_institutional_pct:.0f}%): "
                    f"{len(equities)} tickers"
                )

            return equities

        return all_tickers

    click.echo("ERROR: Specify --tickers or --from-ohlcv")
    sys.exit(1)


# ── main pipeline ────────────────────────────────────────────────────


def _run_download_phase(
    s3_client,  # noqa: ANN001
    bucket: str,
    dates: list[date],
    universe: set[str],
    ohlcv_closes: dict[str, dict[date, float]],
    history_store: OptionsHistoryStore,
    concurrency: int,
    flush_interval: int,
) -> dict:
    """Download flat files, filter, and persist to OptionsHistoryStore.

    Returns summary statistics.
    """
    stats = {
        "dates_processed": 0,
        "dates_skipped": 0,
        "rows_buffered": 0,
        "tickers_touched": set(),
        "bytes_downloaded": 0,
    }

    buffer: dict[str, list[pd.DataFrame]] = defaultdict(list)
    buffer_dates: list[str] = []
    start_time = time.monotonic()

    def _download_and_process(d: date) -> tuple[date, pd.DataFrame]:
        raw = _download_date_file(s3_client, d, bucket)
        if raw is None:
            return d, pd.DataFrame()
        stats["bytes_downloaded"] += len(raw)
        df = _process_date_file(raw, d, universe, ohlcv_closes)
        return d, df

    def _flush_buffer() -> None:
        if not buffer:
            return

        batch: dict[str, pd.DataFrame] = {}
        for ticker, dfs in buffer.items():
            combined = pd.concat(dfs, ignore_index=True)
            stats["rows_buffered"] += len(combined)
            batch[ticker] = combined

        history_store.write_batch(batch)
        for ticker in batch:
            stats["tickers_touched"].add(ticker)

        if buffer_dates:
            history_store.mark_dates_completed(buffer_dates)

        buffer.clear()
        buffer_dates.clear()

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        total = len(dates)
        for i, (d, df) in enumerate(
            executor.map(_download_and_process, dates), start=1
        ):
            if df.empty:
                stats["dates_skipped"] += 1
            else:
                stats["dates_processed"] += 1
                for ticker, group in df.groupby("underlying"):
                    buffer[ticker].append(group)
                buffer_dates.append(d.isoformat())

            if len(buffer_dates) >= flush_interval or i == total:
                _flush_buffer()

            if i % 10 == 0 or i == total:
                elapsed = time.monotonic() - start_time
                rate = i / elapsed * 60 if elapsed > 0 else 0
                remaining = total - i
                eta_min = remaining / rate if rate > 0 else 0
                dl_gb = stats["bytes_downloaded"] / (1024 ** 3)
                logger.info(
                    "download_progress",
                    done=i,
                    total=total,
                    dates_processed=stats["dates_processed"],
                    dates_skipped=stats["dates_skipped"],
                    rows_buffered=stats["rows_buffered"],
                    tickers=len(stats["tickers_touched"]),
                    downloaded_gb=round(dl_gb, 2),
                    elapsed_min=round(elapsed / 60, 1),
                    eta_min=round(eta_min, 1),
                )

    stats["tickers_touched_set"] = stats["tickers_touched"]
    stats["tickers_touched"] = len(stats["tickers_touched_set"])
    return stats


def _run_iv_extraction(
    history_store: OptionsHistoryStore,
    ohlcv_store: OHLCVStore,
    iv_store: HistoricalIVStore,
    derived_store: DerivedMetricsStore,
    skip_derived: bool,
    tickers_subset: set[str] | None = None,
) -> dict:
    """Extract ATM IV from stored options history and compute derived metrics.

    Args:
        tickers_subset: When provided, only process these tickers (incremental).
            When None, process all tickers in the history store (full recompute).

    Returns summary statistics.
    """
    if tickers_subset is not None:
        tickers = sorted(tickers_subset)
        logger.info("iv_extraction_incremental", tickers=len(tickers))
    else:
        tickers = history_store.get_all_tickers()
        logger.info("iv_extraction_full", tickers=len(tickers))

    stats = {
        "tickers_processed": 0,
        "iv_points": 0,
        "derived_tickers": 0,
    }

    start_time = time.monotonic()
    total = len(tickers)

    for i, ticker in enumerate(tickers, start=1):
        iv_count = _extract_atm_iv_from_history(
            history_store, ohlcv_store, iv_store, ticker
        )
        stats["iv_points"] += iv_count

        if iv_count > 0:
            stats["tickers_processed"] += 1

            if not skip_derived:
                iv_df = iv_store.read_ticker(ticker)
                ohlcv_df = ohlcv_store.read_ticker(ticker)
                metrics_df = DerivedMetricsStore.compute_metrics(iv_df, ohlcv_df)
                if not metrics_df.empty:
                    derived_store.write_metrics(ticker, metrics_df)
                    stats["derived_tickers"] += 1

        if i % 50 == 0 or i == total:
            elapsed = time.monotonic() - start_time
            logger.info(
                "iv_extraction_progress",
                done=i,
                total=total,
                iv_points=stats["iv_points"],
                elapsed_min=round(elapsed / 60, 1),
            )

    return stats


# ── pre-load OHLCV closes ───────────────────────────────────────────


def _load_ohlcv_closes(
    ohlcv_store: OHLCVStore, tickers: list[str]
) -> dict[str, dict[date, float]]:
    """Pre-load underlying close prices for all tickers.

    Used for DTE calculation and ATM selection.
    """
    result: dict[str, dict[date, float]] = {}
    for t in tickers:
        df = ohlcv_store.read_ticker(t)
        if df.empty:
            continue
        df["date"] = pd.to_datetime(df["date"]).dt.date
        result[t] = dict(zip(df["date"], df["close"].astype(float)))
    return result


# ── CLI ──────────────────────────────────────────────────────────────


@click.command()
@click.option("--tickers", type=str, default=None, help="Comma-separated ticker list")
@click.option("--from-ohlcv", is_flag=True, help="Use all tickers from OHLCVStore")
@click.option("--concurrency", type=int, default=8, help="Parallel S3 downloads")
@click.option("--days-back", type=int, default=730, help="Calendar days of history")
@click.option(
    "--flush-interval",
    type=int,
    default=10,
    help="Trading dates between Parquet flushes",
)
@click.option("--include-today", is_flag=True, help="Include today in date range (use after market close)")
@click.option("--skip-iv", is_flag=True, help="Only download raw data, skip IV")
@click.option("--skip-derived", is_flag=True, help="Compute IV but skip derived metrics")
@click.option("--force", is_flag=True, help="Reprocess already-completed dates")
@click.option("--force-iv", is_flag=True, help="Force IV extraction even when no new data downloaded")
@click.option("--dry-run", is_flag=True, help="Show plan without downloading")
@click.option("--min-market-cap", type=float, default=MIN_MARKET_CAP, help="Min market cap")
@click.option("--min-institutional-pct", type=float, default=0, help="Min institutional %")
@click.option(
    "--log-file",
    type=str,
    default=None,
    help="Path to log file (JSON lines)",
)
def main(
    tickers: str | None,
    from_ohlcv: bool,
    concurrency: int,
    days_back: int,
    flush_interval: int,
    include_today: bool,
    skip_iv: bool,
    skip_derived: bool,
    force: bool,
    force_iv: bool,
    dry_run: bool,
    min_market_cap: float,
    min_institutional_pct: float,
    log_file: str | None,
) -> None:
    """Ingest historical options data from Massive / Polygon S3 flat files."""
    _configure_logging(log_file)

    settings = get_settings()

    ticker_list = _resolve_tickers(
        tickers, from_ohlcv, settings, min_market_cap, min_institutional_pct
    )
    if not ticker_list:
        click.echo("No tickers to process.")
        return

    universe = set(ticker_list)
    data_dir = settings.data_dir

    history_store = OptionsHistoryStore(data_dir=data_dir)
    ohlcv_store = OHLCVStore(data_dir=data_dir)
    iv_store = HistoricalIVStore(data_dir=data_dir)
    derived_store = DerivedMetricsStore(data_dir=data_dir)

    end_date = date.today() if include_today else date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=days_back)
    all_dates = _trading_dates(start_date, end_date)

    if not force:
        completed = history_store.get_completed_dates()
        before = len(all_dates)
        all_dates = [d for d in all_dates if d.isoformat() not in completed]
        skipped = before - len(all_dates)
        if skipped:
            click.echo(f"  Resuming: {skipped} dates already completed, "
                        f"{len(all_dates)} remaining")

    click.echo(f"\n{'=' * 60}")
    click.echo("Flat File Options Ingestion")
    click.echo(f"{'=' * 60}")
    click.echo(f"  Tickers:              {len(ticker_list)}")
    click.echo(f"  Date range:           {start_date} → {end_date}")
    click.echo(f"  Trading dates:        {len(all_dates)}")
    click.echo(f"  Concurrency:          {concurrency}")
    click.echo(f"  Flush interval:       {flush_interval} dates")
    click.echo(f"  Est. download:        ~{len(all_dates) * 30 / 1024:.1f} GB compressed")
    click.echo(f"  Skip IV extraction:   {skip_iv}")
    click.echo(f"  Skip derived metrics: {skip_derived}")
    click.echo(f"  Data dir:             {data_dir}")
    click.echo(f"  Log file:             {log_file or '(stdout only)'}")
    click.echo(f"{'=' * 60}\n")

    if dry_run:
        click.echo("DRY RUN — no data downloaded or stored.")
        checkpoint = iv_store.get_checkpoint()
        if checkpoint:
            click.echo(
                f"\nLast IV checkpoint: {checkpoint.get('last_run_iso', 'unknown')}, "
                f"through {checkpoint.get('last_options_date', 'unknown')}, "
                f"{checkpoint.get('tickers_processed', '?')} tickers"
            )
        else:
            click.echo("\nNo IV checkpoint found (Phase 2 has never completed).")
        click.echo(f"\nFirst 10 dates: {[d.isoformat() for d in all_dates[:10]]}")
        click.echo(f"Last 10 dates:  {[d.isoformat() for d in all_dates[-10:]]}")
        click.echo(f"\nFirst 20 tickers:")
        for t in ticker_list[:20]:
            click.echo(f"  {t}")
        if len(ticker_list) > 20:
            click.echo(f"  ... and {len(ticker_list) - 20} more")
        return

    if not all_dates:
        click.echo("All dates already completed. Use --force to re-process.")
        return

    click.echo("Pre-loading OHLCV closes...")
    ohlcv_closes = _load_ohlcv_closes(ohlcv_store, ticker_list)
    click.echo(f"  Loaded closes for {len(ohlcv_closes)} tickers\n")

    s3_client = _build_s3_client(settings)

    # Phase 1: Download and persist raw options data
    click.echo("Phase 1: Downloading and persisting options data...")
    overall_start = time.monotonic()

    dl_stats = _run_download_phase(
        s3_client=s3_client,
        bucket=settings.massive_s3_bucket,
        dates=all_dates,
        universe=universe,
        ohlcv_closes=ohlcv_closes,
        history_store=history_store,
        concurrency=concurrency,
        flush_interval=flush_interval,
    )

    dl_elapsed = time.monotonic() - overall_start
    dl_gb = dl_stats["bytes_downloaded"] / (1024 ** 3)
    click.echo(f"\n  Phase 1 complete in {dl_elapsed / 60:.1f} minutes")
    click.echo(f"  Dates processed:      {dl_stats['dates_processed']}")
    click.echo(f"  Dates skipped (no file): {dl_stats['dates_skipped']}")
    click.echo(f"  Rows persisted:       {dl_stats['rows_buffered']:,}")
    click.echo(f"  Downloaded:           {dl_gb:.2f} GB")
    click.echo(f"  Tickers with data:    {dl_stats['tickers_touched']}")

    # Phase 2: ATM IV extraction
    tickers_touched_set: set[str] = dl_stats.get("tickers_touched_set", set())
    has_new_data = dl_stats["dates_processed"] > 0

    if not skip_iv:
        if not has_new_data and not force_iv:
            checkpoint = iv_store.get_checkpoint()
            click.echo("\nPhase 2: Skipped — no new options data downloaded.")
            click.echo("  Use --force-iv to recompute anyway.")
            if checkpoint:
                iv_pts = checkpoint.get("iv_points", 0)
                click.echo(
                    f"  Last IV run: {checkpoint.get('last_run_iso', 'unknown')}, "
                    f"through {checkpoint.get('last_options_date', 'unknown')}, "
                    f"{checkpoint.get('tickers_processed', '?')} tickers, "
                    f"{iv_pts:,} IV points"
                )
        else:
            tickers_for_iv = tickers_touched_set if (has_new_data and not force_iv) else None
            mode_label = (
                f"incremental ({len(tickers_for_iv)} tickers with new data)"
                if tickers_for_iv is not None
                else "full recompute"
            )
            click.echo(f"\nPhase 2: Extracting ATM IV and computing derived metrics ({mode_label})...")
            iv_start = time.monotonic()

            iv_stats = _run_iv_extraction(
                history_store=history_store,
                ohlcv_store=ohlcv_store,
                iv_store=iv_store,
                derived_store=derived_store,
                skip_derived=skip_derived,
                tickers_subset=tickers_for_iv,
            )

            iv_elapsed = time.monotonic() - iv_start
            click.echo(f"\n  Phase 2 complete in {iv_elapsed / 60:.1f} minutes")
            click.echo(f"  Tickers with IV:      {iv_stats['tickers_processed']}")
            click.echo(f"  IV data points:       {iv_stats['iv_points']:,}")
            if not skip_derived:
                click.echo(f"  Derived tickers:      {iv_stats['derived_tickers']}")

            last_date = all_dates[-1].isoformat() if all_dates else "unknown"
            iv_store.write_checkpoint(
                last_options_date=last_date,
                tickers_processed=iv_stats["tickers_processed"],
                iv_points=iv_stats["iv_points"],
            )

    total_elapsed = time.monotonic() - overall_start

    click.echo(f"\n{'=' * 60}")
    click.echo("Ingestion Complete")
    click.echo(f"{'=' * 60}")
    click.echo(f"  Total duration:       {total_elapsed / 60:.1f} minutes")

    history_stats = history_store.get_stats()
    click.echo(
        f"  Options History:      {history_stats.get('ticker_count', 0)} tickers, "
        f"{history_stats.get('total_rows', 0):,} rows"
    )

    iv_stats_store = iv_store.get_stats()
    click.echo(
        f"  IV Store:             {iv_stats_store.get('ticker_count', 0)} tickers, "
        f"{iv_stats_store.get('total_rows', 0):,} rows"
    )

    if not skip_iv and not skip_derived:
        derived_stats = derived_store.get_stats()
        click.echo(
            f"  Derived Store:        {derived_stats.get('ticker_count', 0)} tickers, "
            f"{derived_stats.get('total_rows', 0):,} rows"
        )

    click.echo(f"{'=' * 60}")

    logger.info(
        "flatfile_ingestion_complete",
        duration_min=round(total_elapsed / 60, 1),
        dates_processed=dl_stats["dates_processed"],
        dates_skipped=dl_stats["dates_skipped"],
        tickers_touched=dl_stats["tickers_touched"],
    )


if __name__ == "__main__":
    main()
