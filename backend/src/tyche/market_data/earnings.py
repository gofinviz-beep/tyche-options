"""Earnings calendar client — fetches upcoming earnings dates."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import httpx
import structlog

from tyche.exceptions import EarningsDataUnavailable

logger = structlog.get_logger()

# Normalized earnings entry returned to callers
EarningsInfo = dict[str, Any]


class EarningsCalendarClient:
    """Fetches earnings calendar data from earningsapi.com (free tier).

    Falls back to returning empty data if the API is unavailable,
    allowing the system to operate without earnings data.
    """

    BASE_URL = "https://api.earningsapi.com/v1"

    def __init__(self, api_key: str = "", timeout: float = 10.0) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._cache: dict[str, EarningsInfo] = {}
        self._cache_date: date | None = None

    async def get_upcoming_earnings(
        self, symbols: list[str]
    ) -> dict[str, EarningsInfo]:
        """Get upcoming earnings dates for a list of symbols.

        Returns:
            Dict mapping symbol -> earnings info (date, time, estimates).
            Missing symbols are omitted from the result.
        """
        today = date.today()

        if self._cache_date == today and all(s in self._cache for s in symbols):
            return {s: self._cache[s] for s in symbols if s in self._cache}

        try:
            result = await self._fetch_earnings(symbols)
            self._cache.update(result)
            self._cache_date = today
            return result
        except Exception:
            logger.warning(
                "earnings_fetch_failed",
                symbols=symbols,
                exc_info=True,
            )
            return {s: self._cache[s] for s in symbols if s in self._cache}

    async def get_earnings_for_date(
        self, target_date: date
    ) -> list[EarningsInfo]:
        """Get all earnings reports for a specific date."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                params: dict[str, str] = {"date": target_date.isoformat()}
                if self._api_key:
                    params["apikey"] = self._api_key

                response = await client.get(
                    f"{self.BASE_URL}/earnings", params=params
                )
                if response.status_code != 200:
                    logger.warning(
                        "earnings_api_error",
                        status=response.status_code,
                        body=response.text[:200],
                    )
                    return []

                data = response.json()
                return self._normalize_date_response(data)
        except Exception:
            logger.warning("earnings_date_fetch_failed", exc_info=True)
            return []

    async def _fetch_earnings(
        self, symbols: list[str]
    ) -> dict[str, EarningsInfo]:
        """Fetch earnings for specific symbols via the API."""
        result: dict[str, EarningsInfo] = {}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for symbol in symbols:
                try:
                    params: dict[str, str] = {"symbol": symbol}
                    if self._api_key:
                        params["apikey"] = self._api_key

                    response = await client.get(
                        f"{self.BASE_URL}/earnings", params=params
                    )
                    if response.status_code != 200:
                        continue

                    data = response.json()
                    parsed = self._parse_symbol_earnings(symbol, data)
                    if parsed:
                        result[symbol] = parsed
                except Exception:
                    logger.debug("earnings_symbol_failed", symbol=symbol)
                    continue

        return result

    def _parse_symbol_earnings(
        self, symbol: str, data: Any
    ) -> EarningsInfo | None:
        """Parse earnings API response for a single symbol."""
        if not data:
            return None

        entries = data if isinstance(data, list) else [data]
        today = date.today()

        for entry in entries:
            try:
                date_str = entry.get("date") or entry.get("reportDate", "")
                if not date_str:
                    continue

                earnings_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                if earnings_date < today:
                    continue

                return {
                    "symbol": symbol,
                    "earnings_date": earnings_date,
                    "reporting_time": entry.get("time", "unknown"),
                    "eps_estimate": _safe_float(entry.get("epsEstimate")),
                    "eps_actual": _safe_float(entry.get("epsActual")),
                    "revenue_estimate": _safe_float(entry.get("revenueEstimate")),
                    "revenue_actual": _safe_float(entry.get("revenueActual")),
                    "confirmed": entry.get("confirmed", False),
                }
            except (ValueError, KeyError):
                continue

        return None

    def _normalize_date_response(self, data: Any) -> list[EarningsInfo]:
        if not data:
            return []
        entries = data if isinstance(data, list) else [data]
        results: list[EarningsInfo] = []
        for entry in entries:
            try:
                date_str = entry.get("date") or entry.get("reportDate", "")
                if not date_str:
                    continue
                results.append({
                    "symbol": entry.get("symbol", ""),
                    "earnings_date": datetime.strptime(date_str, "%Y-%m-%d").date(),
                    "reporting_time": entry.get("time", "unknown"),
                    "eps_estimate": _safe_float(entry.get("epsEstimate")),
                    "confirmed": entry.get("confirmed", False),
                })
            except (ValueError, KeyError):
                continue
        return results

    def get_cached_earnings_date(self, symbol: str) -> date | None:
        """Get cached earnings date for a symbol without making an API call."""
        info = self._cache.get(symbol)
        if info:
            return info.get("earnings_date")
        return None


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
