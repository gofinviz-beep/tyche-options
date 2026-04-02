"""Daily options chain snapshot workflow.

Fetches live options chains from Tradier and persists them to the
OptionsChainStore for use in backtest validation with real market data.

Used by:
  - Scheduled job (app.py → _scheduled_options_snapshot)
  - CLI script (scripts/ingest_options.py)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING

import structlog

from tyche.broker.tradier.client import TradierClient
from tyche.market_data.data_store import OptionsChainStore

if TYPE_CHECKING:
    from tyche.config import TycheSettings

logger = structlog.get_logger()


@dataclass
class SnapshotStats:
    tickers_requested: int = 0
    tickers_succeeded: int = 0
    tickers_skipped: int = 0
    tickers_failed: int = 0
    expirations_fetched: int = 0
    contracts_stored: int = 0
    rows_added: int = 0
    api_calls: int = 0
    elapsed_seconds: float = 0.0

    def print_summary(self) -> None:
        print("\n" + "=" * 60)
        print("OPTIONS CHAIN SNAPSHOT SUMMARY")
        print("=" * 60)
        print(f"  Tickers requested:    {self.tickers_requested}")
        print(f"  Tickers succeeded:    {self.tickers_succeeded}")
        print(f"  Tickers skipped:      {self.tickers_skipped}")
        print(f"  Tickers failed:       {self.tickers_failed}")
        print(f"  Expirations fetched:  {self.expirations_fetched}")
        print(f"  Contracts stored:     {self.contracts_stored}")
        print(f"  New rows added:       {self.rows_added}")
        print(f"  API calls made:       {self.api_calls}")
        print(f"  Elapsed:              {self.elapsed_seconds:.1f}s")
        if self.elapsed_seconds > 0 and self.api_calls > 0:
            rpm = self.api_calls / self.elapsed_seconds * 60
            print(f"  Effective RPM:        {rpm:.0f}")
        print("=" * 60)


class _RateLimiter:
    """Token-bucket rate limiter for async API calls."""

    def __init__(self, rpm: int) -> None:
        self._interval = 60.0 / rpm
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()


async def _fetch_ticker_chains(
    client: TradierClient,
    ticker: str,
    snapshot_date: date,
    store: OptionsChainStore,
    rate_limiter: _RateLimiter,
    semaphore: asyncio.Semaphore,
    max_expirations: int,
    min_dte: int,
    max_dte: int,
    puts_only: bool,
    stats: SnapshotStats,
) -> bool:
    """Fetch and store options chains for a single ticker."""
    async with semaphore:
        try:
            await rate_limiter.acquire()
            stats.api_calls += 1
            expirations = await client.get_options_expirations(ticker)

            if not expirations:
                logger.debug("no_expirations", ticker=ticker)
                stats.tickers_skipped += 1
                return False

            valid_exps: list[str] = []
            for exp_str in expirations:
                try:
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                    dte = (exp_date - snapshot_date).days
                    if min_dte <= dte <= max_dte:
                        valid_exps.append(exp_str)
                except ValueError:
                    continue

            valid_exps = valid_exps[:max_expirations]

            if not valid_exps:
                logger.debug("no_valid_expirations", ticker=ticker, total=len(expirations))
                stats.tickers_skipped += 1
                return False

            all_contracts: list[dict] = []
            underlying_price = 0.0

            for exp_str in valid_exps:
                await rate_limiter.acquire()
                stats.api_calls += 1
                stats.expirations_fetched += 1

                try:
                    chain = await client.get_options_chain(ticker, exp_str)
                except Exception as e:
                    logger.warning("chain_fetch_error", ticker=ticker, exp=exp_str, error=str(e))
                    continue

                if not chain or not chain.contracts:
                    continue

                if chain.underlying_price > 0:
                    underlying_price = chain.underlying_price

                for c in chain.contracts:
                    if puts_only and c.option_type != "put":
                        continue
                    all_contracts.append({
                        "expiration": c.expiration,
                        "strike": c.strike,
                        "option_type": c.option_type,
                        "bid": c.bid,
                        "ask": c.ask,
                        "mid": c.mid,
                        "last": c.last,
                        "volume": c.volume,
                        "open_interest": c.open_interest,
                        "implied_volatility": c.implied_volatility,
                        "delta": c.delta,
                        "gamma": c.gamma,
                        "theta": c.theta,
                        "vega": c.vega,
                        "rho": c.rho,
                    })

            if not all_contracts:
                logger.debug("no_contracts", ticker=ticker)
                stats.tickers_skipped += 1
                return False

            rows_added = store.write_chains(
                ticker, snapshot_date, all_contracts, underlying_price
            )

            stats.contracts_stored += len(all_contracts)
            stats.rows_added += rows_added
            stats.tickers_succeeded += 1

            logger.info(
                "ticker_ingested",
                ticker=ticker,
                expirations=len(valid_exps),
                contracts=len(all_contracts),
                rows_added=rows_added,
            )
            return True

        except Exception as e:
            stats.tickers_failed += 1
            logger.error("ticker_failed", ticker=ticker, error=str(e))
            return False


async def run_options_snapshot(
    tickers: list[str],
    settings: TycheSettings,
    snapshot_date: date | None = None,
    max_expirations: int | None = None,
    min_dte: int | None = None,
    max_dte: int | None = None,
    puts_only: bool = True,
    concurrency: int | None = None,
    rpm: int | None = None,
) -> SnapshotStats:
    """Run the options chain snapshot for the given tickers.

    Settings-driven defaults are used unless explicitly overridden.

    Args:
        tickers: List of underlying symbols to snapshot.
        settings: Application settings (provides defaults and credentials).
        snapshot_date: Date to tag the snapshot (default: today).
        max_expirations: Override settings.options_snapshot_max_expirations.
        min_dte: Override settings.options_snapshot_min_dte.
        max_dte: Override settings.options_snapshot_max_dte.
        puts_only: Only capture put contracts (default True for CSP backtests).
        concurrency: Override settings.options_snapshot_concurrency.
        rpm: Override settings.options_snapshot_rpm.

    Returns:
        SnapshotStats with summary metrics.
    """
    if snapshot_date is None:
        snapshot_date = date.today()

    max_exp = max_expirations or settings.options_snapshot_max_expirations
    dte_min = min_dte if min_dte is not None else settings.options_snapshot_min_dte
    dte_max = max_dte if max_dte is not None else settings.options_snapshot_max_dte
    conc = concurrency or settings.options_snapshot_concurrency
    rate = rpm or settings.options_snapshot_rpm

    stats = SnapshotStats(tickers_requested=len(tickers))

    client = TradierClient(
        api_token=settings.tradier_api_token,
        account_id=settings.tradier_account_id,
        base_url=settings.broker_base_url,
        timeout=30.0,
        cache_ttl=0,
    )

    store = OptionsChainStore(data_dir=settings.data_dir)
    rate_limiter = _RateLimiter(rate)
    semaphore = asyncio.Semaphore(conc)

    start = time.monotonic()

    tasks = [
        _fetch_ticker_chains(
            client, ticker, snapshot_date, store, rate_limiter, semaphore,
            max_exp, dte_min, dte_max, puts_only, stats,
        )
        for ticker in tickers
    ]

    batch_size = 50
    for i in range(0, len(tasks), batch_size):
        batch = tasks[i : i + batch_size]
        await asyncio.gather(*batch)
        done = min(i + batch_size, len(tickers))
        logger.debug("snapshot_progress", done=done, total=len(tickers))

    stats.elapsed_seconds = time.monotonic() - start

    await client.close()

    logger.info(
        "options_snapshot_complete",
        tickers_succeeded=stats.tickers_succeeded,
        tickers_failed=stats.tickers_failed,
        contracts=stats.contracts_stored,
        rows_added=stats.rows_added,
        elapsed_s=round(stats.elapsed_seconds, 1),
    )
    return stats
