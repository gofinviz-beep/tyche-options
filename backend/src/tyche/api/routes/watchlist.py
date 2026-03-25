"""Watchlist routes — manage the stock universe."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

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
            entry["earnings_source"] = einfo.get("source", "unknown")
        else:
            entry["next_earnings"] = None
            entry["earnings_time"] = None
            entry["earnings_source"] = None

        result.append(entry)

    return result


class ManualEarningsRequest(BaseModel):
    """Set a manual earnings date for a symbol."""

    symbol: str
    earnings_date: str  # YYYY-MM-DD


@router.post("/earnings", response_model=dict[str, str])
async def set_manual_earnings(
    req: ManualEarningsRequest,
    earnings: EarningsCalendarClient | None = Depends(get_earnings_client),
) -> dict[str, str]:
    """Manually set an upcoming earnings date for a watchlist symbol.

    Use this when you know a stock's earnings date from your own research
    and want the risk engine to account for it.
    """
    if earnings is None:
        raise HTTPException(status_code=500, detail="Earnings client not initialized")

    try:
        parsed_date = datetime.strptime(req.earnings_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid date format. Use YYYY-MM-DD."
        )

    earnings.set_manual_date(req.symbol.upper(), parsed_date)

    return {
        "status": "ok",
        "symbol": req.symbol.upper(),
        "earnings_date": parsed_date.isoformat(),
    }
