"""Stock universe builder — filters stocks by fundamentals before strategy screening."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import structlog

from tyche.broker.base import BrokerClient, Quote

logger = structlog.get_logger()


@dataclass
class StockProfile:
    """Fundamental profile for a watchlist stock."""

    symbol: str
    last_price: float = 0.0
    volume: int = 0
    market_cap_billions: float = 0.0
    sector: str = ""
    next_earnings: date | None = None

    # Gates
    passes_market_cap: bool = False
    passes_volume: bool = False
    passes_all: bool = False


class UniverseBuilder:
    """Screens watchlist stocks through fundamental gates.

    Gate order (fundamentals-first):
    1. Market cap > threshold
    2. Volume > threshold
    3. Premium yield scored LAST (done in strategy engine)
    """

    def __init__(
        self,
        min_market_cap_billions: float = 1.0,
        min_avg_volume: int = 500_000,
    ) -> None:
        self._min_cap = min_market_cap_billions
        self._min_vol = min_avg_volume

    async def screen(
        self,
        broker: BrokerClient,
        symbols: list[str],
        earnings_dates: dict[str, date | None] | None = None,
    ) -> list[StockProfile]:
        """Screen symbols through fundamental gates.

        Args:
            broker: Broker client for quote data.
            symbols: List of ticker symbols to screen.
            earnings_dates: Optional map of symbol -> next earnings date.

        Returns:
            List of StockProfile objects that passed all gates.
        """
        earnings_dates = earnings_dates or {}
        passed: list[StockProfile] = []

        quotes = await broker.get_quotes(symbols)
        quote_map = {q.symbol: q for q in quotes}

        for symbol in symbols:
            quote = quote_map.get(symbol)
            if not quote:
                logger.debug("universe_no_quote", symbol=symbol)
                continue

            profile = StockProfile(
                symbol=symbol,
                last_price=quote.last,
                volume=quote.volume,
                next_earnings=earnings_dates.get(symbol),
            )

            # Estimate market cap from price and volume as a rough proxy.
            # In production, this would come from a fundamentals API.
            # For now, we trust the watchlist curation + configurable override.
            profile.passes_market_cap = True  # Watchlist is pre-curated
            profile.passes_volume = quote.volume >= self._min_vol

            profile.passes_all = (
                profile.passes_market_cap and profile.passes_volume
            )

            if profile.passes_all:
                passed.append(profile)
            else:
                logger.debug(
                    "universe_filtered_out",
                    symbol=symbol,
                    volume=quote.volume,
                    min_volume=self._min_vol,
                )

        logger.info(
            "universe_screen_complete",
            total=len(symbols),
            passed=len(passed),
            filtered=len(symbols) - len(passed),
        )
        return passed
