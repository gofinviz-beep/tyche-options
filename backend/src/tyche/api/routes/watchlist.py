"""Watchlist routes — manage the stock universe."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from tyche.api.deps import get_broker, get_earnings_client, get_settings
from tyche.broker.base import BrokerClient
from tyche.config import TycheSettings
from tyche.market_data.earnings import EarningsCalendarClient

logger = structlog.get_logger()
router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("/", response_model=list[dict[str, Any]])
async def get_watchlist(
    broker: BrokerClient = Depends(get_broker),
    earnings: EarningsCalendarClient | None = Depends(get_earnings_client),
    settings: TycheSettings = Depends(get_settings),
) -> list[dict[str, Any]]:
    """Get the current watchlist with live quotes and earnings dates."""
    symbols = settings.watchlist_symbols
    if not symbols:
        return []

    quotes = await broker.get_quotes(symbols)
    quote_map = {q.symbol: q for q in quotes}

    earnings_dates: dict[str, Any] = {}
    if earnings:
        try:
            earnings_dates = await earnings.get_upcoming_earnings(symbols)
        except Exception:
            logger.warning("watchlist_earnings_failed", exc_info=True)

    result = []
    for symbol in symbols:
        q = quote_map.get(symbol)
        entry: dict[str, Any] = {
            "symbol": symbol,
            "last": q.last if q else None,
            "bid": q.bid if q else None,
            "ask": q.ask if q else None,
            "volume": q.volume if q else None,
            "change_pct": q.change_pct if q else None,
        }

        einfo = earnings_dates.get(symbol)
        if einfo:
            entry["next_earnings"] = str(einfo.get("earnings_date", ""))
            entry["earnings_time"] = einfo.get("reporting_time", "unknown")
        else:
            entry["next_earnings"] = None
            entry["earnings_time"] = None

        result.append(entry)

    return result
