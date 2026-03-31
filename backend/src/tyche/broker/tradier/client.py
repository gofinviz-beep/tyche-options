"""Tradier brokerage API client — async httpx implementation."""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import Any

import httpx
import structlog

from tyche.broker.base import (
    AccountBalance,
    BrokerOrder,
    BrokerPosition,
    CancelConfirmation,
    OptionContract,
    OptionsChain,
    OrderConfirmation,
    OrderPreview,
    OrderRequest,
    Quote,
)
from tyche.exceptions import (
    BrokerAuthError,
    BrokerConnectionError,
    BrokerDataError,
    BrokerOrderError,
    BrokerRateLimitError,
)

logger = structlog.get_logger()


class _TTLCache:
    """Simple in-memory TTL cache for broker API responses."""

    __slots__ = ("_data", "_ttl")

    def __init__(self, ttl_seconds: float = 300.0) -> None:
        self._data: dict[str, tuple[Any, float]] = {}
        self._ttl = ttl_seconds

    def get(self, key: str) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, ts = entry
        if (time.monotonic() - ts) > self._ttl:
            del self._data[key]
            return None
        return value

    def put(self, key: str, value: Any) -> None:
        self._data[key] = (value, time.monotonic())

    def clear(self) -> None:
        self._data.clear()

    @property
    def size(self) -> int:
        return len(self._data)


class TradierClient:
    """Async Tradier API client implementing the BrokerClient protocol.

    Includes an in-memory TTL cache for market data calls (quotes,
    expirations, option chains).  Re-running a scan within the TTL
    window reuses cached responses instead of re-hitting the API.

    Call ``clear_cache()`` to force a full refresh on next access.
    """

    def __init__(
        self,
        api_token: str,
        account_id: str,
        base_url: str = "https://sandbox.tradier.com/v1",
        timeout: float = 15.0,
        cache_ttl: float = 300.0,
    ) -> None:
        self._account_id = account_id
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json",
        }
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

        self._quote_cache = _TTLCache(cache_ttl)
        self._exp_cache = _TTLCache(cache_ttl)
        self._chain_cache = _TTLCache(cache_ttl)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=self._headers,
                timeout=self._timeout,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def clear_cache(self) -> dict[str, int]:
        """Clear all cached market data. Returns counts of cleared entries."""
        stats = {
            "quotes": self._quote_cache.size,
            "expirations": self._exp_cache.size,
            "chains": self._chain_cache.size,
        }
        self._quote_cache.clear()
        self._exp_cache.clear()
        self._chain_cache.clear()
        logger.info("broker_cache_cleared", **stats)
        return stats

    @property
    def cache_stats(self) -> dict[str, int]:
        return {
            "quotes": self._quote_cache.size,
            "expirations": self._exp_cache.size,
            "chains": self._chain_cache.size,
        }

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated request to the Tradier API."""
        client = await self._get_client()
        try:
            match method.upper():
                case "GET":
                    response = await client.get(path, params=params)
                case "POST":
                    response = await client.post(path, data=data)
                case "PUT":
                    response = await client.put(path, data=data)
                case "DELETE":
                    response = await client.delete(path, params=params)
                case _:
                    raise ValueError(f"Unsupported HTTP method: {method}")
        except httpx.ConnectError as exc:
            raise BrokerConnectionError(
                f"Failed to connect to Tradier: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise BrokerConnectionError(
                f"Tradier request timed out: {exc}"
            ) from exc

        if response.status_code == 401:
            raise BrokerAuthError("Invalid Tradier API token")
        if response.status_code == 429:
            raise BrokerRateLimitError("Tradier API rate limit exceeded")
        if response.status_code >= 400:
            raise BrokerDataError(
                f"Tradier API error {response.status_code}: {response.text}"
            )

        return response.json()  # type: ignore[no-any-return]

    # --- Account ---

    async def get_account_balances(self) -> AccountBalance:
        path = f"/accounts/{self._account_id}/balances"
        data = await self._request("GET", path)
        b = data.get("balances", data)
        return AccountBalance(
            cash=float(b.get("total_cash", 0)),
            buying_power=float(b.get("option_buying_power", b.get("buying_power", 0))),
            net_liquidation_value=float(b.get("net_value", b.get("total_equity", 0))),
            market_value=float(b.get("market_value", 0)),
            total_equity=float(b.get("total_equity", 0)),
            open_pl=float(b.get("open_pl", 0)),
            close_pl=float(b.get("close_pl", 0)),
            pending_cash=float(b.get("pending_cash", 0)),
        )

    async def get_positions(self) -> list[BrokerPosition]:
        path = f"/accounts/{self._account_id}/positions"
        data = await self._request("GET", path)
        positions_data = data.get("positions", {})

        if positions_data == "null" or not positions_data:
            return []

        position_list = positions_data.get("position", [])
        if isinstance(position_list, dict):
            position_list = [position_list]

        results: list[BrokerPosition] = []
        for p in position_list:
            exp_str = p.get("date_acquired")
            exp_date = None
            if exp_str:
                try:
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                except ValueError:
                    pass

            results.append(
                BrokerPosition(
                    symbol=p["symbol"],
                    quantity=float(p.get("quantity", 0)),
                    cost_basis=float(p.get("cost_basis", 0)),
                    market_value=float(p.get("market_value", 0)),
                    unrealized_pl=float(p.get("unrealized_pl", 0)),
                    unrealized_pl_pct=float(p.get("unrealized_pl_pct", 0)),
                )
            )
        return results

    async def get_open_orders(self) -> list[BrokerOrder]:
        path = f"/accounts/{self._account_id}/orders"
        data = await self._request("GET", path)
        orders_data = data.get("orders", {})

        if orders_data == "null" or not orders_data:
            return []

        order_list = orders_data.get("order", [])
        if isinstance(order_list, dict):
            order_list = [order_list]

        results: list[BrokerOrder] = []
        for o in order_list:
            status = o.get("status", "")
            if status in ("filled", "expired", "canceled"):
                continue

            leg = o.get("leg", [])
            if isinstance(leg, dict):
                leg = [leg]

            option_symbol = None
            side = o.get("side", "")
            if leg:
                first_leg = leg[0]
                option_symbol = first_leg.get("option_symbol")
                side = first_leg.get("side", side)

            created = None
            if o.get("create_date"):
                try:
                    created = datetime.fromisoformat(o["create_date"])
                except ValueError:
                    pass

            results.append(
                BrokerOrder(
                    broker_order_id=str(o["id"]),
                    symbol=o.get("symbol", o.get("underlying", "")),
                    side=side,
                    order_type=o.get("type", "limit"),
                    quantity=int(o.get("quantity", 0)),
                    status=status,
                    duration=o.get("duration", "day"),
                    limit_price=_float_or_none(o.get("price")),
                    stop_price=_float_or_none(o.get("stop_price")),
                    option_symbol=option_symbol,
                    created_at=created,
                    strategy=o.get("strategy", "unknown"),
                )
            )
        return results

    # --- Market Data ---

    async def get_quote(self, symbol: str) -> Quote:
        cached = self._quote_cache.get(symbol)
        if cached is not None:
            return cached

        data = await self._request(
            "GET", "/markets/quotes", params={"symbols": symbol, "greeks": "false"}
        )
        quotes = data.get("quotes", {})
        q = quotes.get("quote", {})

        if isinstance(q, list):
            q = q[0]

        result = Quote(
            symbol=q.get("symbol", symbol),
            last=float(q.get("last", 0)),
            bid=float(q.get("bid", 0)),
            ask=float(q.get("ask", 0)),
            high=float(q.get("high", 0)),
            low=float(q.get("low", 0)),
            open=float(q.get("open", 0)),
            close=float(q.get("prevclose", q.get("close", 0))),
            volume=int(q.get("volume", 0)),
            change=float(q.get("change", 0)),
            change_pct=float(q.get("change_percentage", 0)),
        )
        self._quote_cache.put(symbol, result)
        return result

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        if not symbols:
            return []
        data = await self._request(
            "GET",
            "/markets/quotes",
            params={"symbols": ",".join(symbols), "greeks": "false"},
        )
        quotes = data.get("quotes", {})
        q_list = quotes.get("quote", [])
        if isinstance(q_list, dict):
            q_list = [q_list]

        return [
            Quote(
                symbol=q.get("symbol", ""),
                last=float(q.get("last", 0)),
                bid=float(q.get("bid", 0)),
                ask=float(q.get("ask", 0)),
                high=float(q.get("high", 0)),
                low=float(q.get("low", 0)),
                open=float(q.get("open", 0)),
                close=float(q.get("prevclose", q.get("close", 0))),
                volume=int(q.get("volume", 0)),
                change=float(q.get("change", 0)),
                change_pct=float(q.get("change_percentage", 0)),
            )
            for q in q_list
        ]

    async def get_options_expirations(self, symbol: str) -> list[str]:
        cached = self._exp_cache.get(symbol)
        if cached is not None:
            return cached

        data = await self._request(
            "GET",
            "/markets/options/expirations",
            params={"symbol": symbol, "includeAllRoots": "true"},
        )
        expirations = data.get("expirations", {})
        date_list = expirations.get("date", [])
        if isinstance(date_list, str):
            date_list = [date_list]
        self._exp_cache.put(symbol, date_list)
        return date_list

    async def get_options_chain(
        self, symbol: str, expiration: str, greeks: bool = True
    ) -> OptionsChain:
        cache_key = f"{symbol}:{expiration}"
        cached = self._chain_cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._request(
            "GET",
            "/markets/options/chains",
            params={
                "symbol": symbol,
                "expiration": expiration,
                "greeks": str(greeks).lower(),
            },
        )
        options_data = data.get("options") or {}
        option_list = options_data.get("option") or []
        if isinstance(option_list, dict):
            option_list = [option_list]

        exp_date = datetime.strptime(expiration, "%Y-%m-%d").date()

        underlying_price = 0.0
        if option_list:
            underlying_price = _safe_float(option_list[0].get("underlying_last_price"))

        contracts: list[OptionContract] = []
        for opt in option_list:
            greeks_data = opt.get("greeks", {}) or {}
            bid = _safe_float(opt.get("bid"))
            ask = _safe_float(opt.get("ask"))
            contracts.append(
                OptionContract(
                    option_symbol=opt.get("symbol", ""),
                    option_type=opt.get("option_type", "").lower(),
                    strike=_safe_float(opt.get("strike")),
                    expiration=exp_date,
                    bid=bid,
                    ask=ask,
                    mid=round((bid + ask) / 2, 4) if (bid + ask) > 0 else 0.0,
                    last=_safe_float(opt.get("last")),
                    volume=int(opt.get("volume") or 0),
                    open_interest=int(opt.get("open_interest") or 0),
                    implied_volatility=_safe_float(
                        greeks_data.get("smv_vol") or opt.get("implied_volatility")
                    ),
                    delta=_safe_float(greeks_data.get("delta")),
                    gamma=_safe_float(greeks_data.get("gamma")),
                    theta=_safe_float(greeks_data.get("theta")),
                    vega=_safe_float(greeks_data.get("vega")),
                    rho=_safe_float(greeks_data.get("rho")),
                )
            )

        chain = OptionsChain(
            symbol=symbol,
            expiration=exp_date,
            underlying_price=underlying_price,
            contracts=contracts,
        )
        self._chain_cache.put(cache_key, chain)
        return chain

    # --- Trading ---

    async def preview_order(self, order: OrderRequest) -> OrderPreview:
        return await self._submit_order(order, preview=True)

    async def place_order(self, order: OrderRequest) -> OrderConfirmation:
        result = await self._submit_order(order, preview=False)
        if isinstance(result, OrderPreview):
            raise BrokerOrderError("Expected confirmation but got preview")
        return result

    async def _submit_order(
        self, order: OrderRequest, preview: bool = False
    ) -> OrderPreview | OrderConfirmation:
        path = f"/accounts/{self._account_id}/orders"
        form_data: dict[str, Any] = {
            "symbol": order.symbol,
            "side": order.side,
            "quantity": str(order.quantity),
            "type": order.order_type,
            "duration": order.duration,
        }

        if order.option_symbol:
            form_data["class"] = "option"
            form_data["option_symbol"] = order.option_symbol
        else:
            form_data["class"] = "equity"

        if order.limit_price is not None:
            form_data["price"] = str(order.limit_price)
        if order.stop_price is not None:
            form_data["stop"] = str(order.stop_price)
        if preview:
            form_data["preview"] = "true"

        data = await self._request("POST", path, data=form_data)

        if preview:
            order_data = data.get("order", {})
            return OrderPreview(
                estimated_cost=float(order_data.get("cost", 0)),
                estimated_commission=float(order_data.get("commission", 0)),
                estimated_fees=float(order_data.get("fees", 0)),
                margin_impact=_float_or_none(order_data.get("margin_impact")),
                status=order_data.get("status", "ok"),
                warnings=order_data.get("warnings", []),
            )

        order_data = data.get("order", {})
        return OrderConfirmation(
            broker_order_id=str(order_data.get("id", "")),
            status=order_data.get("status", "ok"),
        )

    async def cancel_order(self, order_id: str) -> CancelConfirmation:
        path = f"/accounts/{self._account_id}/orders/{order_id}"
        client = await self._get_client()
        try:
            response = await client.delete(path)
        except httpx.ConnectError as exc:
            raise BrokerConnectionError(f"Failed to connect: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise BrokerConnectionError(f"Request timed out: {exc}") from exc

        if response.status_code == 401:
            raise BrokerAuthError("Invalid Tradier API token")
        if response.status_code >= 400:
            raise BrokerOrderError(
                f"Cancel failed {response.status_code}: {response.text}"
            )
        data = response.json()
        return CancelConfirmation(
            broker_order_id=order_id,
            status=data.get("order", {}).get("status", "ok"),
        )

    async def replace_order(
        self, order_id: str, updates: OrderRequest
    ) -> OrderConfirmation:
        path = f"/accounts/{self._account_id}/orders/{order_id}"
        form_data: dict[str, Any] = {
            "type": updates.order_type,
            "duration": updates.duration,
        }
        if updates.limit_price is not None:
            form_data["price"] = str(updates.limit_price)
        if updates.stop_price is not None:
            form_data["stop"] = str(updates.stop_price)

        data = await self._request("PUT", path, data=form_data)
        order_data = data.get("order", {})
        return OrderConfirmation(
            broker_order_id=str(order_data.get("id", order_id)),
            status=order_data.get("status", "ok"),
        )


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Convert to float, returning default for None/invalid values."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _float_or_none(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
