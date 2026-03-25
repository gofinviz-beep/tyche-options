"""Earnings calendar — multi-source with graceful degradation.

Sources (tried in order):
1. Manual overrides from watchlist config
2. Alpha Vantage free API (no key required for basic use, key gets higher limits)
3. Cache from previous successful lookups
4. Empty result (system operates without earnings data)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

EarningsInfo = dict[str, Any]


class EarningsCalendarClient:
    """Fetches upcoming earnings dates from free sources.

    No paid API subscription required. Uses Alpha Vantage's free tier
    and supports manual overrides for critical stocks.
    """

    ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"

    def __init__(
        self,
        alpha_vantage_key: str = "demo",
        manual_overrides: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._av_key = alpha_vantage_key or "demo"
        self._manual: dict[str, date] = {}
        self._timeout = timeout
        self._cache: dict[str, EarningsInfo] = {}
        self._cache_date: date | None = None

        if manual_overrides:
            for symbol, date_str in manual_overrides.items():
                try:
                    self._manual[symbol.upper()] = datetime.strptime(
                        date_str, "%Y-%m-%d"
                    ).date()
                except ValueError:
                    logger.warning(
                        "invalid_manual_earnings_date",
                        symbol=symbol,
                        date=date_str,
                    )

    def set_manual_date(self, symbol: str, earnings_date: date) -> None:
        """Manually set an earnings date for a symbol."""
        self._manual[symbol.upper()] = earnings_date
        self._cache[symbol.upper()] = {
            "symbol": symbol.upper(),
            "earnings_date": earnings_date,
            "reporting_time": "unknown",
            "source": "manual",
            "confirmed": True,
        }
        logger.info(
            "manual_earnings_set",
            symbol=symbol,
            date=earnings_date.isoformat(),
        )

    async def get_upcoming_earnings(
        self, symbols: list[str]
    ) -> dict[str, EarningsInfo]:
        """Get upcoming earnings dates for a list of symbols.

        Priority: manual overrides > API fetch > cache > empty.
        """
        today = date.today()
        result: dict[str, EarningsInfo] = {}
        symbols_to_fetch: list[str] = []

        for symbol in symbols:
            sym = symbol.upper()

            # 1. Manual override
            if sym in self._manual and self._manual[sym] >= today:
                result[sym] = {
                    "symbol": sym,
                    "earnings_date": self._manual[sym],
                    "reporting_time": "unknown",
                    "source": "manual",
                    "confirmed": True,
                }
                continue

            # 2. Fresh cache
            if (
                self._cache_date == today
                and sym in self._cache
                and self._cache[sym].get("earnings_date", today) >= today
            ):
                result[sym] = self._cache[sym]
                continue

            symbols_to_fetch.append(sym)

        # 3. Fetch from API
        if symbols_to_fetch:
            try:
                fetched = await self._fetch_from_alpha_vantage(symbols_to_fetch)
                result.update(fetched)
                self._cache.update(fetched)
                self._cache_date = today
            except Exception:
                logger.warning(
                    "earnings_fetch_failed",
                    symbols=symbols_to_fetch,
                    exc_info=True,
                )
                # Fall back to stale cache
                for sym in symbols_to_fetch:
                    if sym in self._cache:
                        result[sym] = self._cache[sym]

        return result

    async def _fetch_from_alpha_vantage(
        self, symbols: list[str]
    ) -> dict[str, EarningsInfo]:
        """Fetch earnings from Alpha Vantage free API."""
        result: dict[str, EarningsInfo] = {}
        today = date.today()
        horizon = today + timedelta(days=45)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            # Alpha Vantage EARNINGS_CALENDAR returns CSV for all upcoming
            try:
                response = await client.get(
                    self.ALPHA_VANTAGE_URL,
                    params={
                        "function": "EARNINGS_CALENDAR",
                        "horizon": "3month",
                        "apikey": self._av_key,
                    },
                )
                if response.status_code != 200:
                    logger.warning(
                        "alpha_vantage_error",
                        status=response.status_code,
                    )
                    return result

                lines = response.text.strip().split("\n")
                if len(lines) < 2:
                    return result

                headers = lines[0].split(",")
                sym_idx = _col_index(headers, "symbol")
                date_idx = _col_index(headers, "reportDate")
                time_idx = _col_index(headers, "fiscalDateEnding")
                est_idx = _col_index(headers, "estimate")

                target_symbols = {s.upper() for s in symbols}

                for line in lines[1:]:
                    cols = line.split(",")
                    if len(cols) <= max(sym_idx, date_idx):
                        continue

                    sym = cols[sym_idx].strip().upper()
                    if sym not in target_symbols:
                        continue

                    try:
                        earnings_date = datetime.strptime(
                            cols[date_idx].strip(), "%Y-%m-%d"
                        ).date()
                    except ValueError:
                        continue

                    if earnings_date < today:
                        continue

                    eps_est = None
                    if est_idx >= 0 and est_idx < len(cols):
                        eps_est = _safe_float(cols[est_idx].strip())

                    result[sym] = {
                        "symbol": sym,
                        "earnings_date": earnings_date,
                        "reporting_time": "unknown",
                        "eps_estimate": eps_est,
                        "source": "alpha_vantage",
                        "confirmed": True,
                    }

            except httpx.TimeoutException:
                logger.warning("alpha_vantage_timeout")
            except Exception:
                logger.warning("alpha_vantage_parse_failed", exc_info=True)

        return result

    def get_cached_earnings_date(self, symbol: str) -> date | None:
        """Get cached earnings date for a symbol without making an API call."""
        sym = symbol.upper()
        if sym in self._manual:
            return self._manual[sym]
        info = self._cache.get(sym)
        if info:
            return info.get("earnings_date")
        return None


def _col_index(headers: list[str], name: str) -> int:
    """Find column index case-insensitively, returns -1 if not found."""
    name_lower = name.lower()
    for i, h in enumerate(headers):
        if h.strip().lower() == name_lower:
            return i
    return -1


def _safe_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
