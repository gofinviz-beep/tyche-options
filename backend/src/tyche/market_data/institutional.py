"""Institutional ownership checker — validates stocks have serious backing.

Uses yfinance to fetch institutional holding percentages.
Applied AFTER conviction filtering on a small set of candidates (~20-50)
to avoid excessive API calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import structlog

logger = structlog.get_logger()

_cache: dict[str, "_OwnershipData"] = {}
_CACHE_TTL_HOURS = 24


@dataclass
class _OwnershipData:
    institutional_pct: float
    insider_pct: float
    fetched_at: datetime


async def get_institutional_ownership(
    ticker: str,
) -> float | None:
    """Get institutional ownership percentage for a ticker.

    Returns a float between 0 and 1 (e.g., 0.79 = 79%), or None if unavailable.
    Results are cached for 24 hours.
    """
    cached = _cache.get(ticker)
    if cached:
        age_hours = (datetime.now(timezone.utc) - cached.fetched_at).total_seconds() / 3600
        if age_hours < _CACHE_TTL_HOURS:
            return cached.institutional_pct

    try:
        import yfinance as yf

        stock = yf.Ticker(ticker)
        info = stock.info
        inst_pct = info.get("heldPercentInstitutions")

        if inst_pct is not None:
            _cache[ticker] = _OwnershipData(
                institutional_pct=float(inst_pct),
                insider_pct=float(info.get("heldPercentInsiders", 0) or 0),
                fetched_at=datetime.now(timezone.utc),
            )
            logger.debug(
                "institutional_ownership_fetched",
                ticker=ticker,
                pct=round(inst_pct * 100, 1),
            )
            return float(inst_pct)

        logger.warning("institutional_ownership_unavailable", ticker=ticker)
        return None
    except Exception:
        logger.warning("institutional_ownership_failed", ticker=ticker, exc_info=True)
        return None


def get_cached_ownership_batch(tickers: list[str]) -> dict[str, float]:
    """Return cached institutional ownership for the given tickers.

    Zero-cost: reads in-memory cache only, never triggers yfinance API calls.
    Returns a mapping of ticker -> ownership pct (0.0–1.0) for tickers
    with cached data.
    """
    from datetime import timezone as _tz

    now = datetime.now(_tz.utc)
    result: dict[str, float] = {}
    for ticker in tickers:
        cached = _cache.get(ticker)
        if cached:
            age_hours = (now - cached.fetched_at).total_seconds() / 3600
            if age_hours < _CACHE_TTL_HOURS:
                result[ticker] = cached.institutional_pct
    return result


async def filter_by_institutional_ownership(
    tickers: list[str],
    min_pct: float = 0.40,
) -> tuple[list[str], dict[str, float]]:
    """Filter tickers by minimum institutional ownership.

    Args:
        tickers: List of ticker symbols to check.
        min_pct: Minimum institutional ownership (0.0 to 1.0). Default 40%.

    Returns:
        Tuple of (passed_tickers, ownership_map).
        ownership_map includes all tickers with their percentages.
    """
    passed: list[str] = []
    ownership_map: dict[str, float] = {}
    failed: list[str] = []

    for ticker in tickers:
        pct = await get_institutional_ownership(ticker)
        if pct is not None:
            ownership_map[ticker] = pct
            if pct >= min_pct:
                passed.append(ticker)
            else:
                failed.append(ticker)
                logger.info(
                    "institutional_ownership_below_threshold",
                    ticker=ticker,
                    pct=round(pct * 100, 1),
                    threshold=round(min_pct * 100, 1),
                )
        else:
            passed.append(ticker)
            logger.debug("institutional_ownership_unknown_pass", ticker=ticker)

    logger.info(
        "institutional_ownership_filter",
        total=len(tickers),
        passed=len(passed),
        failed_tickers=failed,
    )
    return passed, ownership_map


@dataclass
class InstitutionalFilterStats:
    """Telemetry for batched institutional filtering."""

    total_tickers: int = 0
    batches_run: int = 0
    batches_failed: int = 0
    tickers_passed: int = 0
    tickers_dropped: int = 0
    tickers_no_data: int = 0
    retries: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "total_tickers": self.total_tickers,
            "batches_run": self.batches_run,
            "batches_failed": self.batches_failed,
            "tickers_passed": self.tickers_passed,
            "tickers_dropped": self.tickers_dropped,
            "tickers_no_data": self.tickers_no_data,
            "retries": self.retries,
        }


async def filter_by_institutional_ownership_batched(
    tickers: list[str],
    min_pct: float = 0.40,
    batch_size: int = 20,
    max_retries: int = 2,
    backoff_base: float = 1.0,
) -> tuple[list[str], dict[str, float], InstitutionalFilterStats]:
    """Filter tickers by institutional ownership with async batching.

    Unlike the unbatched version, this processes tickers in bounded
    batches with retry+backoff on failures.  Large watchlists run
    without timeout explosion.

    Args:
        tickers: Ticker symbols to check.
        min_pct: Minimum institutional ownership (0.0–1.0).
        batch_size: How many tickers to fetch per batch.
        max_retries: Retries per batch on failure.
        backoff_base: Base delay (seconds) between retries (exponential).

    Returns:
        (passed_tickers, ownership_map, stats).
    """
    import asyncio

    stats = InstitutionalFilterStats(total_tickers=len(tickers))
    passed: list[str] = []
    ownership_map: dict[str, float] = {}

    batches = [
        tickers[i : i + batch_size]
        for i in range(0, len(tickers), batch_size)
    ]

    for batch in batches:
        stats.batches_run += 1
        batch_results: dict[str, float | None] = {}
        attempt = 0
        success = False

        while attempt <= max_retries:
            try:
                remaining = [t for t in batch if t not in batch_results]
                for ticker in remaining:
                    pct = await get_institutional_ownership(ticker)
                    batch_results[ticker] = pct
                success = True
                break
            except Exception:
                attempt += 1
                if attempt <= max_retries:
                    stats.retries += 1
                    delay = backoff_base * (2 ** (attempt - 1))
                    logger.warning(
                        "institutional_batch_retry",
                        batch_size=len(batch),
                        attempt=attempt,
                        delay=delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    stats.batches_failed += 1
                    logger.warning(
                        "institutional_batch_exhausted",
                        batch_tickers=[t for t in batch if t not in batch_results],
                        attempts=attempt,
                    )

        for ticker in batch:
            pct = batch_results.get(ticker)
            if pct is not None:
                ownership_map[ticker] = pct
                if pct >= min_pct:
                    passed.append(ticker)
                    stats.tickers_passed += 1
                else:
                    stats.tickers_dropped += 1
            else:
                passed.append(ticker)
                stats.tickers_no_data += 1

    logger.info(
        "institutional_batched_filter_complete",
        stats=stats.to_dict(),
        min_pct=min_pct,
        batch_size=batch_size,
    )
    return passed, ownership_map, stats
