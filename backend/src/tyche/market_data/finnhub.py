"""Finnhub API client for company news.

Lightweight async client using httpx. Free tier allows 60 calls/min.
Only fetches company news — no other Finnhub endpoints are used.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import date, datetime

import httpx
import structlog

from tyche.exceptions import FinnhubAPIError

logger = structlog.get_logger()

_BASE_URL = "https://finnhub.io/api/v1"
_FREE_TIER_RPM = 60


@dataclass(frozen=True)
class FinnhubArticle:
    """Single news article from Finnhub /company-news."""

    id: str
    headline: str
    source: str
    url: str
    summary: str
    datetime_ts: int
    related: str
    category: str


class FinnhubClient:
    """Async HTTP client for Finnhub REST API.

    Implements rate limiting for the free tier (60 RPM).
    """

    def __init__(
        self,
        api_key: str,
        rate_limit_rpm: int = _FREE_TIER_RPM,
        timeout: float = 15.0,
        max_retries: int = 2,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._max_retries = max_retries
        self._min_interval = 60.0 / max(rate_limit_rpm, 1)
        self._last_request_time: float = 0.0
        self._request_lock = asyncio.Lock()

    async def _throttle(self) -> None:
        async with self._request_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_request_time = time.monotonic()

    async def _request(
        self, path: str, params: dict | None = None
    ) -> list | dict:
        params = params or {}
        params["token"] = self._api_key
        url = f"{_BASE_URL}{path}"

        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(url, params=params)

                if resp.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "finnhub_rate_limited", attempt=attempt + 1, wait_seconds=wait
                    )
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code >= 400:
                    raise FinnhubAPIError(resp.status_code, resp.text[:200])

                return resp.json()

            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning(
                    "finnhub_timeout", path=path, attempt=attempt + 1
                )
                await asyncio.sleep(2 ** attempt)

            except FinnhubAPIError:
                raise

            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "finnhub_request_error",
                    path=path,
                    error=str(exc),
                    attempt=attempt + 1,
                )
                await asyncio.sleep(2 ** attempt)

        raise FinnhubAPIError(
            0, f"Request failed after {self._max_retries} retries: {last_exc}"
        )

    async def get_company_news(
        self,
        ticker: str,
        from_date: date,
        to_date: date,
    ) -> list[FinnhubArticle]:
        """Fetch company news for a ticker within a date range.

        Args:
            ticker: Stock symbol (e.g. "AAPL").
            from_date: Start date (inclusive).
            to_date: End date (inclusive).

        Returns:
            List of FinnhubArticle dataclasses.
        """
        params = {
            "symbol": ticker.upper(),
            "from": from_date.strftime("%Y-%m-%d"),
            "to": to_date.strftime("%Y-%m-%d"),
        }

        data = await self._request("/company-news", params=params)
        if not isinstance(data, list):
            return []

        articles: list[FinnhubArticle] = []
        for item in data:
            articles.append(
                FinnhubArticle(
                    id=str(item.get("id", "")),
                    headline=item.get("headline", ""),
                    source=item.get("source", ""),
                    url=item.get("url", ""),
                    summary=item.get("summary", "")[:500],
                    datetime_ts=item.get("datetime", 0),
                    related=item.get("related", ""),
                    category=item.get("category", ""),
                )
            )

        logger.debug(
            "finnhub_news_fetched",
            ticker=ticker,
            count=len(articles),
            from_date=str(from_date),
            to_date=str(to_date),
        )
        return articles
