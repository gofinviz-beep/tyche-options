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
