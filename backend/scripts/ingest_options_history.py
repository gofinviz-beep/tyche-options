"""Historical options IV ingestion from Polygon / Massive.com.

Fetches 2 years of expired put contracts for each ticker, selects the
nearest-ATM contract with ~30 DTE for each trading day, fetches daily
bars, computes implied volatility via Black-Scholes inverse, and persists
per-ticker Parquet files.  Optionally computes derived metrics (IV Rank,
IV Percentile, RV, VRP).

Designed for long-running execution in a ``screen`` session:

    cd backend
    screen -S options-ingest
    python scripts/ingest_options_history.py --from-ohlcv --concurrency 10
    # Ctrl-A D to detach

    screen -r options-ingest   # re-attach to monitor

Storage layout:
    data/options_iv/{TICKER}.parquet   — daily ATM put IV per ticker
    data/derived/{TICKER}.parquet      — IV Rank, RV, VRP per ticker

Usage examples:
    python scripts/ingest_options_history.py --from-ohlcv
    python scripts/ingest_options_history.py --tickers AAPL,MSFT --force
    python scripts/ingest_options_history.py --from-ohlcv --dry-run
    python scripts/ingest_options_history.py --from-ohlcv --days-back 365
    python scripts/ingest_options_history.py --from-ohlcv --skip-metrics

Monitoring via log file:
    python scripts/ingest_options_history.py --from-ohlcv --log-file logs/iv-ingest.log
    tail -f logs/iv-ingest.log                              # live monitoring
    grep '"event":"batch_progress"' logs/iv-ingest.log      # progress only
    grep '"event":"ticker_ingested"' logs/iv-ingest.log     # per-ticker results
"""

from __future__ import annotations

import asyncio
import logging
import math
import sys
import time
from datetime import date, datetime, timedelta

import click
import structlog

sys.path.insert(0, "src")

from tyche.config import get_settings
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.market_data.derived_store import DerivedMetricsStore
from tyche.market_data.historical_iv_store import HistoricalIVStore
from tyche.market_data.iv_calculator import compute_iv
from tyche.market_data.polygon import PolygonClient

logger = structlog.get_logger()


def _configure_logging(log_file: str | None = None) -> None:
    """Configure structlog to write JSON to stdout and optionally a file.

    The file receives the same structured JSON lines as stdout, making it
    easy to ``tail -f`` or ``grep`` for specific events while the script
    runs in a detached screen session.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if log_file:
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

MIN_MARKET_CAP = 4_000_000_000
TARGET_DTE = 30
DTE_TOLERANCE = 5


class _RateLimiter:
    """Token-bucket rate limiter for async API calls."""

    def __init__(self, rpm: int) -> None:
        self._interval = 60.0 / max(rpm, 1)
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()


class IngestionStats:
    """Thread-safe counters for the ingestion run."""

    def __init__(self) -> None:
        self.succeeded = 0
        self.failed = 0
        self.skipped = 0
        self.contracts_listed = 0
        self.bars_fetched = 0
        self.iv_computed = 0
        self.failed_tickers: list[str] = []
        self._lock = asyncio.Lock()

    async def record_success(
        self,
        contracts: int = 0,
        bars: int = 0,
        iv_points: int = 0,
    ) -> None:
        async with self._lock:
            self.succeeded += 1
            self.contracts_listed += contracts
            self.bars_fetched += bars
            self.iv_computed += iv_points

    async def record_failure(self, ticker: str) -> None:
        async with self._lock:
            self.failed += 1
            self.failed_tickers.append(ticker)

    async def record_skip(self) -> None:
        async with self._lock:
            self.skipped += 1


def _select_atm_contracts(
    contracts: list[dict],
    ohlcv_df: "pd.DataFrame",
    target_dte: int = TARGET_DTE,
    dte_tolerance: int = DTE_TOLERANCE,
) -> dict[str, dict]:
    """Select the nearest-ATM put for each trading day.

    For each trading day in ``ohlcv_df``, finds the contract whose
    expiration is closest to ``target_dte`` days out and whose strike
    is closest to the underlying close.

    Returns:
        Mapping of contract ticker → contract dict (deduplicated).
        Only contracts that were actually selected are returned.
    """
    import pandas as pd

    if not contracts or ohlcv_df.empty:
        return {}

    by_expiration: dict[str, list[dict]] = {}
    for c in contracts:
        exp = c["expiration_date"]
        by_expiration.setdefault(exp, []).append(c)

    exp_dates = sorted(by_expiration.keys())
    if not exp_dates:
        return {}

    selected: dict[str, dict] = {}
    closes = ohlcv_df[["date", "close"]].copy()
    closes["date"] = pd.to_datetime(closes["date"]).dt.date

    for _, row in closes.iterrows():
        trade_date = row["date"]
        close_price = float(row["close"])

        ideal_exp = trade_date + timedelta(days=target_dte)
        ideal_str = ideal_exp.strftime("%Y-%m-%d")

        best_exp: str | None = None
        best_exp_diff = float("inf")
        for exp_str in exp_dates:
            try:
                exp_dt = datetime.strptime(exp_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if exp_dt <= trade_date:
                continue
            diff = abs((exp_dt - ideal_exp).days)
            if diff < best_exp_diff and diff <= dte_tolerance + 10:
                best_exp_diff = diff
                best_exp = exp_str

        if best_exp is None:
            continue

        candidates = by_expiration[best_exp]
        best_contract = min(
            candidates,
            key=lambda c: abs(c["strike_price"] - close_price),
        )
        selected[best_contract["ticker"]] = best_contract

    return selected


async def _process_ticker(
    ticker: str,
    polygon: PolygonClient,
    ohlcv_store: OHLCVStore,
    iv_store: HistoricalIVStore,
    derived_store: DerivedMetricsStore,
    semaphore: asyncio.Semaphore,
    rate_limiter: _RateLimiter,
    stats: IngestionStats,
    *,
    days_back: int,
    compute_metrics: bool,
    force: bool,
) -> None:
    """Ingest historical IV data for a single ticker."""
    import pandas as pd

    if not force:
        latest = iv_store.get_latest_date(ticker)
        if latest and latest >= date.today() - timedelta(days=2):
            logger.debug("ticker_skipped_up_to_date", ticker=ticker)
            await stats.record_skip()
            return

    ohlcv_df = ohlcv_store.read_ticker(ticker)
    if ohlcv_df.empty or len(ohlcv_df) < 50:
        logger.debug("ticker_skipped_insufficient_ohlcv", ticker=ticker)
        await stats.record_skip()
        return

    ohlcv_df["date"] = pd.to_datetime(ohlcv_df["date"]).dt.date
    cutoff = date.today() - timedelta(days=days_back)
    ohlcv_df = ohlcv_df[ohlcv_df["date"] >= cutoff].copy()

    if ohlcv_df.empty:
        await stats.record_skip()
        return

    min_close = float(ohlcv_df["close"].min())
    max_close = float(ohlcv_df["close"].max())

    async with semaphore:
        await rate_limiter.acquire()
        try:
            contracts = await polygon.list_options_contracts(
                underlying_ticker=ticker,
                contract_type="put",
                expired=True,
                expiration_date_gte=cutoff,
                expiration_date_lte=date.today(),
                strike_price_gte=round(min_close * 0.8, 2),
                strike_price_lte=round(max_close * 1.2, 2),
            )
        except Exception as exc:
            logger.warning("contract_list_failed", ticker=ticker, error=str(exc))
            await stats.record_failure(ticker)
            return

    if not contracts:
        logger.debug("no_contracts_found", ticker=ticker)
        await stats.record_skip()
        return

    selected = _select_atm_contracts(contracts, ohlcv_df)

    if not selected:
        logger.debug("no_atm_contracts_selected", ticker=ticker)
        await stats.record_skip()
        return

    all_bars: dict[str, list[dict]] = {}
    for contract_ticker, contract in selected.items():
        exp_str = contract["expiration_date"]
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        from_date = exp_date - timedelta(days=45)
        if from_date < cutoff:
            from_date = cutoff

        async with semaphore:
            await rate_limiter.acquire()
            try:
                bars = await polygon.get_option_aggs(
                    contract_ticker,
                    from_date=from_date,
                    to_date=exp_date,
                )
                if bars:
                    all_bars[contract_ticker] = bars
            except Exception as exc:
                logger.debug(
                    "option_aggs_failed",
                    contract=contract_ticker,
                    error=str(exc),
                )

    if not all_bars:
        logger.debug("no_option_bars_fetched", ticker=ticker)
        await stats.record_skip()
        return

    ohlcv_by_date: dict[date, float] = {}
    for _, row in ohlcv_df.iterrows():
        ohlcv_by_date[row["date"]] = float(row["close"])

    iv_records: list[dict] = []
    total_bars = 0

    for contract_ticker, bars in all_bars.items():
        contract = selected[contract_ticker]
        strike = contract["strike_price"]
        exp_str = contract["expiration_date"]
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        total_bars += len(bars)

        for bar in bars:
            bar_date = bar["date"]
            option_close = bar["close"]
            underlying_close = ohlcv_by_date.get(bar_date)

            if underlying_close is None or underlying_close <= 0:
                continue
            if option_close <= 0:
                continue

            dte = (exp_date - bar_date).days
            if dte <= 0:
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
                    "date": bar_date,
                    "strike": strike,
                    "expiration": exp_date,
                    "contract_ticker": contract_ticker,
                    "option_close": option_close,
                    "underlying_close": underlying_close,
                    "dte": dte,
                    "implied_volatility": iv,
                }
            )

    if iv_records:
        iv_store.write_iv_data(ticker, iv_records)

    if compute_metrics and iv_records:
        iv_df = iv_store.read_ticker(ticker)
        ohlcv_full = ohlcv_store.read_ticker(ticker)
        metrics_df = DerivedMetricsStore.compute_metrics(iv_df, ohlcv_full)
        if not metrics_df.empty:
            derived_store.write_metrics(ticker, metrics_df)

    await stats.record_success(
        contracts=len(contracts),
        bars=total_bars,
        iv_points=len(iv_records),
    )

    logger.info(
        "ticker_ingested",
        ticker=ticker,
        contracts_listed=len(contracts),
        contracts_selected=len(selected),
        bars_fetched=total_bars,
        iv_points=len(iv_records),
    )


def _resolve_tickers(
    tickers_str: str | None,
    from_ohlcv: bool,
    settings: "TycheSettings",
    min_market_cap: float,
    min_institutional_pct: float,
) -> list[str]:
    """Resolve ticker list from CLI options."""
    from tyche.config import TycheSettings

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
                equities = [t for t in equities if caps.get(t, 0) >= min_market_cap]
                click.echo(
                    f"  After market cap filter (>= ${min_market_cap/1e9:.1f}B): "
                    f"{len(equities)} tickers"
                )

            if min_institutional_pct > 0:
                inst = meta_store.get_institutional_pcts(equities)
                equities = [
                    t for t in equities
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


@click.command()
@click.option("--tickers", type=str, default=None, help="Comma-separated ticker list")
@click.option("--from-ohlcv", is_flag=True, help="Use all tickers from OHLCVStore")
@click.option("--concurrency", type=int, default=10, help="Max concurrent API requests")
@click.option("--rpm", type=int, default=0, help="Polygon RPM limit (0 = use config)")
@click.option("--days-back", type=int, default=730, help="Calendar days of history")
@click.option("--skip-metrics", is_flag=True, help="Skip derived metrics computation")
@click.option("--force", is_flag=True, help="Re-ingest even if ticker data is current")
@click.option("--dry-run", is_flag=True, help="Show plan without fetching data")
@click.option("--min-market-cap", type=float, default=MIN_MARKET_CAP, help="Min market cap")
@click.option("--min-institutional-pct", type=float, default=0, help="Min institutional %")
@click.option(
    "--log-file", type=str, default=None,
    help="Path to log file (JSON lines). Logs always go to stdout too.",
)
def main(
    tickers: str | None,
    from_ohlcv: bool,
    concurrency: int,
    rpm: int,
    days_back: int,
    skip_metrics: bool,
    force: bool,
    dry_run: bool,
    min_market_cap: float,
    min_institutional_pct: float,
    log_file: str | None,
) -> None:
    """Ingest historical options IV data from Polygon / Massive.com."""
    _configure_logging(log_file)
    settings = get_settings()

    if not settings.polygon_api_key:
        click.echo("ERROR: TYCHE_POLYGON_API_KEY not set in .env")
        sys.exit(1)

    if not tickers and not from_ohlcv:
        click.echo("ERROR: Specify --tickers or --from-ohlcv")
        sys.exit(1)

    effective_rpm = rpm or settings.polygon_rate_limit_rpm or 500

    ticker_list = _resolve_tickers(
        tickers, from_ohlcv, settings, min_market_cap, min_institutional_pct,
    )

    if not ticker_list:
        click.echo("No tickers to process.")
        return

    est_contract_calls = len(ticker_list) * 5
    est_agg_calls = len(ticker_list) * 40
    est_total = est_contract_calls + est_agg_calls
    est_minutes = est_total / effective_rpm

    click.echo(f"\n{'=' * 60}")
    click.echo("Historical Options IV Ingestion")
    click.echo(f"{'=' * 60}")
    click.echo(f"  Tickers:              {len(ticker_list)}")
    click.echo(f"  Days back:            {days_back}")
    click.echo(f"  Concurrency:          {concurrency}")
    click.echo(f"  Rate limit:           {effective_rpm} RPM")
    click.echo(f"  Est. API calls:       ~{est_total:,}")
    click.echo(f"  Est. time:            ~{est_minutes:.0f} minutes")
    click.echo(f"  Compute metrics:      {not skip_metrics}")
    click.echo(f"  Force re-ingest:      {force}")
    click.echo(f"  Data dir:             {settings.data_dir}")
    click.echo(f"  Log file:             {log_file or '(stdout only)'}")
    click.echo(f"{'=' * 60}\n")

    if dry_run:
        click.echo("DRY RUN — no data fetched or stored.")
        click.echo("\nFirst 20 tickers:")
        for t in ticker_list[:20]:
            click.echo(f"  {t}")
        if len(ticker_list) > 20:
            click.echo(f"  ... and {len(ticker_list) - 20} more")
        return

    asyncio.run(
        _run_async(
            ticker_list=ticker_list,
            settings=settings,
            concurrency=concurrency,
            rpm=effective_rpm,
            days_back=days_back,
            compute_metrics=not skip_metrics,
            force=force,
        )
    )


async def _run_async(
    ticker_list: list[str],
    settings: "TycheSettings",
    concurrency: int,
    rpm: int,
    days_back: int,
    compute_metrics: bool,
    force: bool,
) -> None:
    """Execute the async ingestion pipeline."""
    polygon = PolygonClient(
        api_key=settings.polygon_api_key,
        base_url=settings.polygon_base_url,
        rate_limit_rpm=rpm,
    )
    ohlcv_store = OHLCVStore(data_dir=settings.data_dir)
    iv_store = HistoricalIVStore(data_dir=settings.data_dir)
    derived_store = DerivedMetricsStore(data_dir=settings.data_dir)

    semaphore = asyncio.Semaphore(concurrency)
    rate_limiter = _RateLimiter(rpm)
    stats = IngestionStats()

    start_time = time.monotonic()
    total = len(ticker_list)
    batch_size = 50

    for batch_start in range(0, total, batch_size):
        batch_end = min(batch_start + batch_size, total)
        batch = ticker_list[batch_start:batch_end]

        tasks = [
            _process_ticker(
                ticker=t,
                polygon=polygon,
                ohlcv_store=ohlcv_store,
                iv_store=iv_store,
                derived_store=derived_store,
                semaphore=semaphore,
                rate_limiter=rate_limiter,
                stats=stats,
                days_back=days_back,
                compute_metrics=compute_metrics,
                force=force,
            )
            for t in batch
        ]

        await asyncio.gather(*tasks, return_exceptions=True)

        elapsed = time.monotonic() - start_time
        done = stats.succeeded + stats.failed + stats.skipped
        rate = done / elapsed * 60 if elapsed > 0 else 0
        remaining = total - done
        eta_min = remaining / rate if rate > 0 else 0

        logger.info(
            "batch_progress",
            done=done,
            total=total,
            succeeded=stats.succeeded,
            failed=stats.failed,
            skipped=stats.skipped,
            iv_points=stats.iv_computed,
            elapsed_min=round(elapsed / 60, 1),
            eta_min=round(eta_min, 1),
        )

    elapsed_total = time.monotonic() - start_time

    click.echo(f"\n{'=' * 60}")
    click.echo("Ingestion Complete")
    click.echo(f"{'=' * 60}")
    click.echo(f"  Duration:             {elapsed_total / 60:.1f} minutes")
    click.echo(f"  Tickers processed:    {stats.succeeded}")
    click.echo(f"  Tickers skipped:      {stats.skipped}")
    click.echo(f"  Tickers failed:       {stats.failed}")
    click.echo(f"  Contracts listed:     {stats.contracts_listed:,}")
    click.echo(f"  Bars fetched:         {stats.bars_fetched:,}")
    click.echo(f"  IV data points:       {stats.iv_computed:,}")

    if stats.failed_tickers:
        click.echo(f"\n  Failed tickers: {', '.join(stats.failed_tickers[:50])}")
        if len(stats.failed_tickers) > 50:
            click.echo(f"  ... and {len(stats.failed_tickers) - 50} more")

    iv_stats = iv_store.get_stats()
    click.echo(f"\n  IV Store: {iv_stats.get('ticker_count', 0)} tickers, "
               f"{iv_stats.get('total_rows', 0):,} rows")

    if compute_metrics:
        derived_stats = derived_store.get_stats()
        click.echo(f"  Derived Store: {derived_stats.get('ticker_count', 0)} tickers, "
                    f"{derived_stats.get('total_rows', 0):,} rows")

    click.echo(f"{'=' * 60}")

    logger.info(
        "ingestion_complete",
        duration_min=round(elapsed_total / 60, 1),
        succeeded=stats.succeeded,
        failed=stats.failed,
        skipped=stats.skipped,
        contracts_listed=stats.contracts_listed,
        bars_fetched=stats.bars_fetched,
        iv_computed=stats.iv_computed,
    )


if __name__ == "__main__":
    main()
