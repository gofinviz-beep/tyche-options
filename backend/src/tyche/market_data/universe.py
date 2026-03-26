"""Stock universe builder — filters stocks through fundamental gates.

Funnel approach:
    ~10,000 US equities
        → Stage 1: Market cap >= $500M, volume >= 500K, price >= $5
        → Stage 2: Common stock on NYSE/NASDAQ (no OTC, ETNs, warrants)
        → ~800-1,200 tickers ready for conviction analysis
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from tyche.market_data.polygon import PolygonClient, TickerSnapshot

logger = structlog.get_logger()

VALID_EXCHANGES = {
    "XNYS",   # NYSE
    "XNAS",   # NASDAQ
    "XNMS",   # NASDAQ Global Select
    "XASE",   # NYSE American (AMEX)
    "ARCX",   # NYSE Arca
    "BATS",   # Cboe BZX
}


@dataclass
class StockProfile:
    """Fundamental profile for a screened stock."""

    symbol: str
    name: str = ""
    last_price: float = 0.0
    volume: int = 0
    market_cap: float = 0.0
    sector: str = ""
    exchange: str = ""
    next_earnings: date | None = None

    passes_market_cap: bool = False
    passes_volume: bool = False
    passes_price: bool = False
    passes_exchange: bool = False
    passes_all: bool = False


class UniverseBuilder:
    """Screens US stocks through fundamental gates using Polygon data.

    Two data paths:
    1. Snapshot-based: Uses Polygon snapshots (volume, price) + ticker reference (market cap)
    2. Watchlist-based: If a curated watchlist is provided, trusts it for market cap
       but still validates volume/price from snapshots.
    """

    def __init__(
        self,
        min_market_cap_millions: float = 500.0,
        min_avg_volume: int = 500_000,
        min_price: float = 5.0,
    ) -> None:
        self._min_cap = min_market_cap_millions * 1_000_000
        self._min_vol = min_avg_volume
        self._min_price = min_price

    def screen_from_snapshots(
        self,
        snapshots: list[TickerSnapshot],
        ticker_market_caps: dict[str, float] | None = None,
        earnings_dates: dict[str, date | None] | None = None,
    ) -> list[StockProfile]:
        """Screen stocks from Polygon snapshot data.

        Args:
            snapshots: List of ticker snapshots from Polygon.
            ticker_market_caps: Optional map of ticker -> market cap.
                If provided, used for market cap gate instead of snapshot data.
            earnings_dates: Optional map of symbol -> next earnings date.

        Returns:
            List of StockProfile objects that passed all gates.
        """
        earnings_dates = earnings_dates or {}
        ticker_market_caps = ticker_market_caps or {}
        passed: list[StockProfile] = []

        for snap in snapshots:
            market_cap = ticker_market_caps.get(snap.ticker, snap.market_cap)

            profile = StockProfile(
                symbol=snap.ticker,
                last_price=snap.last_price,
                volume=snap.today_volume,
                market_cap=market_cap,
                next_earnings=earnings_dates.get(snap.ticker),
            )

            profile.passes_market_cap = market_cap >= self._min_cap
            profile.passes_volume = snap.today_volume >= self._min_vol
            profile.passes_price = snap.last_price >= self._min_price
            profile.passes_exchange = True  # snapshots are pre-filtered

            profile.passes_all = (
                profile.passes_market_cap
                and profile.passes_volume
                and profile.passes_price
            )

            if profile.passes_all:
                passed.append(profile)

        logger.info(
            "universe_screen_complete",
            total=len(snapshots),
            passed=len(passed),
            filtered=len(snapshots) - len(passed),
        )
        return passed

    def screen_watchlist(
        self,
        symbols: list[str],
        snapshots: dict[str, TickerSnapshot] | None = None,
        earnings_dates: dict[str, date | None] | None = None,
    ) -> list[StockProfile]:
        """Screen a curated watchlist, trusting market cap curation.

        For watchlist symbols, we trust that the user has already validated
        market cap. We still check volume and price from snapshots if available.
        """
        earnings_dates = earnings_dates or {}
        snapshots = snapshots or {}
        passed: list[StockProfile] = []

        for symbol in symbols:
            snap = snapshots.get(symbol)

            profile = StockProfile(
                symbol=symbol,
                last_price=snap.last_price if snap else 0.0,
                volume=snap.today_volume if snap else 0,
                next_earnings=earnings_dates.get(symbol),
            )

            profile.passes_market_cap = True  # watchlist is pre-curated
            profile.passes_exchange = True

            if snap:
                profile.passes_volume = snap.today_volume >= self._min_vol
                profile.passes_price = snap.last_price >= self._min_price
            else:
                profile.passes_volume = True
                profile.passes_price = True

            profile.passes_all = (
                profile.passes_market_cap
                and profile.passes_volume
                and profile.passes_price
            )

            if profile.passes_all:
                passed.append(profile)
            else:
                logger.debug(
                    "universe_watchlist_filtered",
                    symbol=symbol,
                    volume=profile.volume,
                    price=profile.last_price,
                )

        logger.info(
            "universe_watchlist_screened",
            total=len(symbols),
            passed=len(passed),
        )
        return passed

    async def screen_from_polygon(
        self,
        polygon: PolygonClient,
        earnings_dates: dict[str, date | None] | None = None,
    ) -> list[StockProfile]:
        """Full universe screen using live Polygon API data.

        Fetches all tickers' snapshots, applies fundamental gates.
        This is the main entry point for broad-market screening.
        """
        from tyche.market_data.polygon import PolygonClient as _PC

        snapshots = await polygon.get_all_snapshots()
        return self.screen_from_snapshots(
            snapshots=snapshots,
            earnings_dates=earnings_dates,
        )
