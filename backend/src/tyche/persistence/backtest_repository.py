"""Repository for querying backtest pullback profiles and events.

Operates against backtest.db via the 'backtest' engine. Read-only from the
application's perspective — the CLI script populates the data.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select

from tyche.models.backtest import PullbackEvent, TickerPullbackProfile
from tyche.persistence.database import get_session

logger = structlog.get_logger()

DB_NAME = "backtest"


async def get_all_profiles() -> list[TickerPullbackProfile]:
    """Return all ticker pullback profiles."""
    try:
        async with get_session(DB_NAME) as session:
            result = await session.execute(
                select(TickerPullbackProfile).order_by(
                    TickerPullbackProfile.ticker,
                    TickerPullbackProfile.pullback_type,
                )
            )
            return list(result.scalars().all())
    except Exception:
        logger.warning("backtest_profiles_query_failed", exc_info=True)
        return []


async def get_profile_for_ticker(
    ticker: str,
) -> list[TickerPullbackProfile]:
    """Return pullback profiles for a single ticker (both 8ema and 21ema)."""
    try:
        async with get_session(DB_NAME) as session:
            result = await session.execute(
                select(TickerPullbackProfile).where(
                    TickerPullbackProfile.ticker == ticker.upper()
                )
            )
            return list(result.scalars().all())
    except Exception:
        logger.warning("backtest_profile_query_failed", ticker=ticker, exc_info=True)
        return []


async def get_profiles_map() -> dict[str, dict[str, TickerPullbackProfile]]:
    """Return a nested map: ticker -> pullback_type -> profile."""
    profiles = await get_all_profiles()
    result: dict[str, dict[str, TickerPullbackProfile]] = {}
    for p in profiles:
        result.setdefault(p.ticker, {})[p.pullback_type] = p
    return result


async def get_events_for_ticker(
    ticker: str,
    pullback_type: str | None = None,
    limit: int = 100,
) -> list[PullbackEvent]:
    """Return historical pullback events for a ticker."""
    try:
        async with get_session(DB_NAME) as session:
            stmt = select(PullbackEvent).where(
                PullbackEvent.ticker == ticker.upper()
            )
            if pullback_type:
                stmt = stmt.where(PullbackEvent.pullback_type == pullback_type)
            stmt = stmt.order_by(PullbackEvent.entry_date.desc()).limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())
    except Exception:
        logger.warning("backtest_events_query_failed", ticker=ticker, exc_info=True)
        return []
