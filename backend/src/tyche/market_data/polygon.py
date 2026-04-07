"""Polygon.io / Massive.com API client for market data.

Handles:
- Grouped daily bars (all tickers, one date) for efficient bootstrap + daily updates
- Ticker reference for universe building (market cap, type, exchange)
- Ticker snapshots for real-time quotes
- Options snapshots for chain data
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import httpx
import structlog

from tyche.exceptions import PolygonAPIError, PolygonRateLimitError

logger = structlog.get_logger()


@dataclass(frozen=True)
class DailyBar:
    """Single OHLCV bar from Polygon."""

    ticker: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float = 0.0
    num_transactions: int = 0


@dataclass(frozen=True)
class IntradayBar:
    """Single intraday OHLCV bar from Polygon aggregate endpoint."""

    ticker: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float = 0.0
    num_transactions: int = 0


@dataclass(frozen=True)
class TickerInfo:
    """Ticker reference data from Polygon."""

    ticker: str
    name: str
    market: str
    locale: str
    type: str
    active: bool
    currency_name: str = "usd"
    primary_exchange: str = ""
    market_cap: float = 0.0


@dataclass(frozen=True)
class TickerSnapshot:
    """Real-time snapshot of a ticker from Polygon."""

    ticker: str
    last_price: float
    today_open: float
    today_high: float
    today_low: float
    today_close: float
    today_volume: int
    prev_close: float
    change: float
    change_pct: float
    market_cap: float = 0.0


@dataclass(frozen=True)
class OptionsContract:
    """Single options contract from Polygon snapshot."""

    ticker: str
    underlying_ticker: str
    contract_type: str  # call, put
    strike_price: float
    expiration_date: date
    bid: float
    ask: float
    mid: float
    last_trade_price: float
    volume: int
    open_interest: int
    implied_volatility: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    underlying_price: float = 0.0


class PolygonClient:
    """Async HTTP client for Polygon.io / Massive.com REST API.

    Implements rate limiting, retries with backoff, and response normalization.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.polygon.io",
        rate_limit_rpm: int = 5,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries

        self._min_interval = 60.0 / max(rate_limit_rpm, 1)
        self._last_request_time: float = 0.0
        self._request_lock = asyncio.Lock()

    async def _throttle(self) -> None:
        """Enforce rate limiting between requests."""
        async with self._request_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_request_time = time.monotonic()

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute an API request with rate limiting and retries."""
        url = f"{self._base_url}{path}"
        params = params or {}
        params["apiKey"] = self._api_key

        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.request(method, url, params=params)

                if response.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "polygon_rate_limited",
                        attempt=attempt + 1,
                        wait_seconds=wait,
                    )
                    await asyncio.sleep(wait)
                    continue

                if response.status_code >= 400:
                    raise PolygonAPIError(
                        response.status_code,
                        response.text[:200],
                    )

                return response.json()

            except httpx.TimeoutException as exc:
                last_exc = exc
                wait = 2 ** attempt
                logger.warning(
                    "polygon_timeout",
                    path=path,
                    attempt=attempt + 1,
                    wait_seconds=wait,
                )
                await asyncio.sleep(wait)

            except PolygonAPIError:
                raise

            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "polygon_request_error",
                    path=path,
                    error=str(exc),
                    attempt=attempt + 1,
                )
                await asyncio.sleep(2 ** attempt)

        raise PolygonRateLimitError() if last_exc is None else PolygonAPIError(
            0, f"Request failed after {self._max_retries} retries: {last_exc}"
        )

    # ── Grouped Daily Bars ──────────────────────────────────────────────

    async def get_grouped_daily(self, bar_date: date) -> list[DailyBar]:
        """Fetch ALL tickers' OHLCV for a single date.

        Uses /v2/aggs/grouped/locale/us/market/stocks/{date}
        One call returns every US stock's bar for that day.
        """
        date_str = bar_date.strftime("%Y-%m-%d")
        data = await self._request(
            "GET",
            f"/v2/aggs/grouped/locale/us/market/stocks/{date_str}",
            params={"adjusted": "true"},
        )
        results = data.get("results", [])
        bars: list[DailyBar] = []
        for r in results:
            try:
                bars.append(
                    DailyBar(
                        ticker=r["T"],
                        date=bar_date,
                        open=float(r.get("o", 0)),
                        high=float(r.get("h", 0)),
                        low=float(r.get("l", 0)),
                        close=float(r.get("c", 0)),
                        volume=int(r.get("v", 0)),
                        vwap=float(r.get("vw", 0)),
                        num_transactions=int(r.get("n", 0)),
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue

        logger.info(
            "polygon_grouped_daily",
            date=date_str,
            tickers=len(bars),
        )
        return bars

    # ── Aggregate Bars (intraday / custom timespan) ────────────────────

    async def get_aggregate_bars(
        self,
        ticker: str,
        from_date: date,
        to_date: date,
        multiplier: int = 5,
        timespan: str = "minute",
        limit: int = 50000,
    ) -> list[IntradayBar]:
        """Fetch aggregate bars for a single ticker over a date range.

        Uses /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}

        Args:
            ticker: Stock symbol.
            from_date: Start date (inclusive).
            to_date: End date (inclusive).
            multiplier: Bar size multiplier (e.g., 5 for 5-minute bars).
            timespan: Bar timespan (minute, hour, day, week, month).
            limit: Max results per page (Polygon max is 50000).

        Returns:
            List of IntradayBar sorted by timestamp ascending.
        """
        from_str = from_date.strftime("%Y-%m-%d")
        to_str = to_date.strftime("%Y-%m-%d")
        path = f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from_str}/{to_str}"

        all_bars: list[IntradayBar] = []
        next_url: str | None = None

        while True:
            if next_url:
                await self._throttle()
                separator = "&" if "?" in next_url else "?"
                url_with_key = f"{next_url}{separator}apiKey={self._api_key}"
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(url_with_key)
                    data = resp.json()
            else:
                data = await self._request(
                    "GET",
                    path,
                    params={"adjusted": "true", "sort": "asc", "limit": limit},
                )

            for r in data.get("results", []):
                try:
                    ts_ms = int(r.get("t", 0))
                    ts = datetime.fromtimestamp(ts_ms / 1000)
                    all_bars.append(
                        IntradayBar(
                            ticker=ticker,
                            timestamp=ts,
                            open=float(r.get("o", 0)),
                            high=float(r.get("h", 0)),
                            low=float(r.get("l", 0)),
                            close=float(r.get("c", 0)),
                            volume=int(r.get("v", 0)),
                            vwap=float(r.get("vw", 0)),
                            num_transactions=int(r.get("n", 0)),
                        )
                    )
                except (KeyError, ValueError, TypeError):
                    continue

            next_url = data.get("next_url")
            if not next_url:
                break

        logger.info(
            "polygon_aggregate_bars",
            ticker=ticker,
            timespan=f"{multiplier}{timespan}",
            bars=len(all_bars),
            from_date=from_str,
            to_date=to_str,
        )
        return all_bars

    # ── Ticker Reference ────────────────────────────────────────────────

    async def get_tickers(
        self,
        market: str = "stocks",
        active: bool = True,
        limit: int = 1000,
        ticker_type: str = "CS",
    ) -> list[TickerInfo]:
        """Fetch ticker reference data with pagination.

        Uses /v3/reference/tickers. ticker_type 'CS' = common stock.
        """
        all_tickers: list[TickerInfo] = []
        next_url: str | None = None

        params: dict[str, Any] = {
            "market": market,
            "active": str(active).lower(),
            "limit": limit,
            "type": ticker_type,
        }

        while True:
            if next_url:
                await self._throttle()
                separator = "&" if "?" in next_url else "?"
                url_with_key = f"{next_url}{separator}apiKey={self._api_key}"
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(url_with_key)
                    data = resp.json()
            else:
                data = await self._request(
                    "GET", "/v3/reference/tickers", params=params
                )

            for r in data.get("results", []):
                all_tickers.append(
                    TickerInfo(
                        ticker=r.get("ticker", ""),
                        name=r.get("name", ""),
                        market=r.get("market", ""),
                        locale=r.get("locale", ""),
                        type=r.get("type", ""),
                        active=r.get("active", False),
                        currency_name=r.get("currency_name", "usd"),
                        primary_exchange=r.get("primary_exchange", ""),
                        market_cap=float(r.get("market_cap", 0) or 0),
                    )
                )

            next_url = data.get("next_url")
            if not next_url:
                break

        logger.info("polygon_tickers_loaded", count=len(all_tickers))
        return all_tickers

    # ── Ticker Snapshots ────────────────────────────────────────────────

    async def get_all_snapshots(self) -> list[TickerSnapshot]:
        """Fetch real-time snapshots for all US stock tickers.

        Uses /v2/snapshot/locale/us/markets/stocks/tickers
        """
        data = await self._request(
            "GET",
            "/v2/snapshot/locale/us/markets/stocks/tickers",
        )
        snapshots: list[TickerSnapshot] = []
        for t in data.get("tickers", []):
            try:
                day = t.get("day", {})
                prev = t.get("prevDay", {})
                tod = t.get("todaysChangePerc", 0)
                snapshots.append(
                    TickerSnapshot(
                        ticker=t.get("ticker", ""),
                        last_price=float(t.get("lastTrade", {}).get("p", 0) or day.get("c", 0)),
                        today_open=float(day.get("o", 0)),
                        today_high=float(day.get("h", 0)),
                        today_low=float(day.get("l", 0)),
                        today_close=float(day.get("c", 0)),
                        today_volume=int(day.get("v", 0)),
                        prev_close=float(prev.get("c", 0)),
                        change=float(t.get("todaysChange", 0)),
                        change_pct=float(tod),
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue

        logger.info("polygon_snapshots_loaded", count=len(snapshots))
        return snapshots

    async def get_ticker_snapshot(self, ticker: str) -> TickerSnapshot | None:
        """Fetch snapshot for a single ticker."""
        try:
            data = await self._request(
                "GET",
                f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}",
            )
            t = data.get("ticker", {})
            day = t.get("day", {})
            prev = t.get("prevDay", {})
            return TickerSnapshot(
                ticker=t.get("ticker", ticker),
                last_price=float(t.get("lastTrade", {}).get("p", 0) or day.get("c", 0)),
                today_open=float(day.get("o", 0)),
                today_high=float(day.get("h", 0)),
                today_low=float(day.get("l", 0)),
                today_close=float(day.get("c", 0)),
                today_volume=int(day.get("v", 0)),
                prev_close=float(prev.get("c", 0)),
                change=float(t.get("todaysChange", 0)),
                change_pct=float(t.get("todaysChangePerc", 0)),
            )
        except PolygonAPIError:
            return None

    # ── Options Data ────────────────────────────────────────────────────

    async def get_options_snapshot(
        self,
        underlying: str,
        expiration_date: date | None = None,
        expiration_date_gte: date | None = None,
        expiration_date_lte: date | None = None,
        strike_price_gte: float | None = None,
        strike_price_lte: float | None = None,
        contract_type: str | None = None,
        limit: int = 250,
    ) -> list[OptionsContract]:
        """Fetch options chain snapshot for an underlying.

        Uses /v3/snapshot/options/{underlyingAsset}
        Supports date range and strike range filtering to reduce data volume.
        """
        params: dict[str, Any] = {"limit": limit}
        if expiration_date:
            params["expiration_date"] = expiration_date.strftime("%Y-%m-%d")
        if expiration_date_gte:
            params["expiration_date.gte"] = expiration_date_gte.strftime("%Y-%m-%d")
        if expiration_date_lte:
            params["expiration_date.lte"] = expiration_date_lte.strftime("%Y-%m-%d")
        if strike_price_gte is not None:
            params["strike_price.gte"] = strike_price_gte
        if strike_price_lte is not None:
            params["strike_price.lte"] = strike_price_lte
        if contract_type:
            params["contract_type"] = contract_type

        all_contracts: list[OptionsContract] = []
        next_url: str | None = None
        max_pages = 10
        page = 0

        while page < max_pages:
            page += 1
            if next_url:
                await self._throttle()
                separator = "&" if "?" in next_url else "?"
                url_with_key = f"{next_url}{separator}apiKey={self._api_key}"
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(url_with_key)
                    data = resp.json()
            else:
                data = await self._request(
                    "GET",
                    f"/v3/snapshot/options/{underlying}",
                    params=params,
                )

            for r in data.get("results", []):
                try:
                    details = r.get("details", {})
                    greeks = r.get("greeks", {})
                    day = r.get("day", {})
                    last_quote = r.get("last_quote", {})
                    last_trade = r.get("last_trade", {})
                    oi = r.get("open_interest", 0)
                    underlying_asset = r.get("underlying_asset", {})

                    exp_str = details.get("expiration_date", "")
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date() if exp_str else date.today()

                    bid = float(last_quote.get("bid", 0) or 0)
                    ask = float(last_quote.get("ask", 0) or 0)
                    mid = float(last_quote.get("midpoint", 0) or 0)
                    last_price = float(last_trade.get("price", 0) or 0)
                    day_close = float(day.get("close", 0) or 0)

                    if bid == 0.0 and day_close > 0:
                        bid = day_close
                    if ask == 0.0 and day_close > 0:
                        ask = day_close
                    if last_price == 0.0:
                        last_price = day_close
                    if mid == 0.0:
                        mid = round((bid + ask) / 2, 4) if (bid + ask) > 0 else day_close

                    all_contracts.append(
                        OptionsContract(
                            ticker=details.get("ticker", ""),
                            underlying_ticker=underlying,
                            contract_type=details.get("contract_type", "").lower(),
                            strike_price=float(details.get("strike_price", 0)),
                            expiration_date=exp_date,
                            bid=bid,
                            ask=ask,
                            mid=mid,
                            last_trade_price=last_price,
                            volume=int(day.get("volume", 0)),
                            open_interest=int(oi),
                            implied_volatility=float(r.get("implied_volatility", 0) or 0),
                            delta=float(greeks.get("delta", 0) or 0),
                            gamma=float(greeks.get("gamma", 0) or 0),
                            theta=float(greeks.get("theta", 0) or 0),
                            vega=float(greeks.get("vega", 0) or 0),
                            underlying_price=float(underlying_asset.get("price", 0) or 0),
                        )
                    )
                except (KeyError, ValueError, TypeError):
                    continue

            next_url = data.get("next_url")
            if not next_url:
                break

        logger.info(
            "polygon_options_loaded",
            underlying=underlying,
            contracts=len(all_contracts),
        )
        return all_contracts

    # ── Options Contracts Reference ─────────────────────────────────────

    async def list_options_contracts(
        self,
        underlying_ticker: str,
        contract_type: str = "put",
        expired: bool = True,
        expiration_date_gte: date | None = None,
        expiration_date_lte: date | None = None,
        strike_price_gte: float | None = None,
        strike_price_lte: float | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """List options contracts from the reference endpoint.

        Uses /v3/reference/options/contracts with pagination.
        Returns raw contract dicts with keys: ticker, underlying_ticker,
        contract_type, expiration_date, strike_price, exercise_style.
        """
        params: dict[str, Any] = {
            "underlying_ticker": underlying_ticker,
            "contract_type": contract_type,
            "expired": str(expired).lower(),
            "limit": limit,
        }
        if expiration_date_gte:
            params["expiration_date.gte"] = expiration_date_gte.strftime("%Y-%m-%d")
        if expiration_date_lte:
            params["expiration_date.lte"] = expiration_date_lte.strftime("%Y-%m-%d")
        if strike_price_gte is not None:
            params["strike_price.gte"] = strike_price_gte
        if strike_price_lte is not None:
            params["strike_price.lte"] = strike_price_lte

        all_contracts: list[dict] = []
        next_url: str | None = None

        while True:
            if next_url:
                await self._throttle()
                separator = "&" if "?" in next_url else "?"
                url_with_key = f"{next_url}{separator}apiKey={self._api_key}"
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(url_with_key)
                    data = resp.json()
            else:
                data = await self._request(
                    "GET",
                    "/v3/reference/options/contracts",
                    params=params,
                )

            for r in data.get("results", []):
                exp_str = r.get("expiration_date", "")
                all_contracts.append(
                    {
                        "ticker": r.get("ticker", ""),
                        "underlying_ticker": r.get("underlying_ticker", ""),
                        "contract_type": r.get("contract_type", ""),
                        "expiration_date": exp_str,
                        "strike_price": float(r.get("strike_price", 0)),
                        "exercise_style": r.get("exercise_style", ""),
                    }
                )

            next_url = data.get("next_url")
            if not next_url:
                break

        logger.info(
            "polygon_options_contracts_listed",
            underlying=underlying_ticker,
            contract_type=contract_type,
            contracts=len(all_contracts),
        )
        return all_contracts

    async def get_option_aggs(
        self,
        option_ticker: str,
        from_date: date,
        to_date: date,
        limit: int = 50000,
    ) -> list[dict]:
        """Fetch daily OHLCV bars for a single options contract.

        Uses /v2/aggs/ticker/{optionsTicker}/range/1/day/{from}/{to}.

        Returns:
            List of bar dicts with keys: date, open, high, low, close,
            volume, vwap, num_transactions.
        """
        from_str = from_date.strftime("%Y-%m-%d")
        to_str = to_date.strftime("%Y-%m-%d")
        path = (
            f"/v2/aggs/ticker/{option_ticker}"
            f"/range/1/day/{from_str}/{to_str}"
        )

        all_bars: list[dict] = []
        next_url: str | None = None

        while True:
            if next_url:
                await self._throttle()
                separator = "&" if "?" in next_url else "?"
                url_with_key = f"{next_url}{separator}apiKey={self._api_key}"
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(url_with_key)
                    data = resp.json()
            else:
                data = await self._request(
                    "GET",
                    path,
                    params={"adjusted": "true", "sort": "asc", "limit": limit},
                )

            for r in data.get("results", []):
                try:
                    ts_ms = int(r.get("t", 0))
                    bar_date = datetime.fromtimestamp(ts_ms / 1000).date()
                    all_bars.append(
                        {
                            "date": bar_date,
                            "open": float(r.get("o", 0)),
                            "high": float(r.get("h", 0)),
                            "low": float(r.get("l", 0)),
                            "close": float(r.get("c", 0)),
                            "volume": int(r.get("v", 0)),
                            "vwap": float(r.get("vw", 0)),
                            "num_transactions": int(r.get("n", 0)),
                        }
                    )
                except (KeyError, ValueError, TypeError):
                    continue

            next_url = data.get("next_url")
            if not next_url:
                break

        logger.debug(
            "polygon_option_aggs",
            option_ticker=option_ticker,
            bars=len(all_bars),
        )
        return all_bars

    # ── Ticker Details (for market cap) ─────────────────────────────────

    async def get_ticker_details(self, ticker: str) -> dict[str, Any]:
        """Fetch detailed info for a single ticker including market cap.

        Uses /v3/reference/tickers/{ticker}
        """
        data = await self._request(
            "GET",
            f"/v3/reference/tickers/{ticker}",
        )
        return data.get("results", {})

    async def get_batch_market_caps(
        self,
        tickers: list[str],
    ) -> dict[str, float]:
        """Fetch market caps sequentially. Deprecated: use get_batch_market_caps_concurrent."""
        result: dict[str, float] = {}
        total = len(tickers)

        for i, ticker in enumerate(tickers):
            try:
                details = await self.get_ticker_details(ticker)
                market_cap = float(details.get("market_cap", 0) or 0)
                if market_cap > 0:
                    result[ticker] = market_cap
            except Exception:
                logger.debug("market_cap_fetch_failed", ticker=ticker)

            if (i + 1) % 100 == 0:
                logger.info(
                    "batch_market_caps_progress",
                    done=i + 1,
                    total=total,
                    found=len(result),
                )

        logger.info(
            "batch_market_caps_complete",
            total=total,
            found=len(result),
        )
        return result

    async def get_batch_market_caps_concurrent(
        self,
        tickers: list[str],
        concurrency: int = 20,
        rate_limit_rpm: int = 500,
    ) -> dict[str, float]:
        """Fetch market caps concurrently with semaphore-bounded parallelism.

        Uses a token-bucket style rate limiter that allows parallel requests
        while staying within the RPM ceiling, unlike the serial _throttle() lock.

        Args:
            tickers: Ticker symbols to fetch market caps for.
            concurrency: Max simultaneous in-flight requests.
            rate_limit_rpm: Requests per minute ceiling.

        Returns:
            Mapping of ticker -> market_cap (only tickers with cap > 0).
        """
        if not tickers:
            return {}

        result: dict[str, float] = {}
        failed: int = 0
        total = len(tickers)
        semaphore = asyncio.Semaphore(concurrency)

        min_interval = 60.0 / max(rate_limit_rpm, 1)
        token_lock = asyncio.Lock()
        last_request_time = 0.0

        async def _acquire_rate_slot() -> None:
            nonlocal last_request_time
            async with token_lock:
                now = time.monotonic()
                elapsed = now - last_request_time
                if elapsed < min_interval:
                    await asyncio.sleep(min_interval - elapsed)
                last_request_time = time.monotonic()

        async def _fetch_one(ticker: str) -> None:
            nonlocal failed
            async with semaphore:
                await _acquire_rate_slot()
                try:
                    url = f"{self._base_url}/v3/reference/tickers/{ticker}"
                    params = {"apiKey": self._api_key}
                    async with httpx.AsyncClient(timeout=self._timeout) as client:
                        resp = await client.get(url, params=params)

                    if resp.status_code == 429:
                        await asyncio.sleep(2.0)
                        await _acquire_rate_slot()
                        async with httpx.AsyncClient(timeout=self._timeout) as client:
                            resp = await client.get(url, params=params)

                    if resp.status_code >= 400:
                        failed += 1
                        return

                    data = resp.json().get("results", {})
                    cap = float(data.get("market_cap", 0) or 0)
                    if cap > 0:
                        result[ticker] = cap
                except Exception:
                    failed += 1
                    logger.debug("market_cap_fetch_failed", ticker=ticker)

        logger.info(
            "batch_market_caps_concurrent_start",
            total=total,
            concurrency=concurrency,
            rate_limit_rpm=rate_limit_rpm,
        )

        tasks = [asyncio.create_task(_fetch_one(t)) for t in tickers]

        done_count = 0
        for coro in asyncio.as_completed(tasks):
            await coro
            done_count += 1
            if done_count % 200 == 0:
                logger.info(
                    "batch_market_caps_progress",
                    done=done_count,
                    total=total,
                    found=len(result),
                    failed=failed,
                )

        logger.info(
            "batch_market_caps_concurrent_complete",
            total=total,
            found=len(result),
            failed=failed,
        )
        return result
