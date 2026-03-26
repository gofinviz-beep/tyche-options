"""Polygon.io broker adapter — real market data with backing broker for account ops.

Implements the BrokerClient protocol using Polygon for quotes/options and
a backing broker (MockBroker or TradierClient) for account management.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

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
from tyche.market_data.polygon import OptionsContract as PolygonOptionsContract
from tyche.market_data.polygon import PolygonClient, TickerSnapshot

logger = structlog.get_logger()

_OPTIONS_HORIZON_DAYS = 45


class PolygonBrokerAdapter:
    """Adapts PolygonClient to the BrokerClient protocol.

    Market data (quotes, option chains) comes from Polygon's real-time
    snapshots. Account operations delegate to a backing broker.

    Options are fetched with a date range filter (today → 45 days out)
    to minimize data volume and API calls.
    """

    def __init__(
        self,
        polygon: PolygonClient,
        backing_broker: Any,
        options_horizon_days: int = _OPTIONS_HORIZON_DAYS,
    ) -> None:
        self._polygon = polygon
        self._backing = backing_broker
        self._horizon_days = options_horizon_days
        self._options_cache: dict[str, list[PolygonOptionsContract]] = {}
        self._snapshot_cache: dict[str, TickerSnapshot] = {}

    def clear_cache(self) -> None:
        """Clear cached data (call between scan runs)."""
        self._options_cache.clear()
        self._snapshot_cache.clear()

    async def _get_snapshot(self, symbol: str) -> TickerSnapshot | None:
        if symbol not in self._snapshot_cache:
            snap = await self._polygon.get_ticker_snapshot(symbol)
            if snap:
                self._snapshot_cache[symbol] = snap
        return self._snapshot_cache.get(symbol)

    async def _ensure_options_cached(self, symbol: str) -> list[PolygonOptionsContract]:
        if symbol not in self._options_cache:
            today = date.today()
            max_exp = today + timedelta(days=self._horizon_days)
            contracts = await self._polygon.get_options_snapshot(
                symbol,
                expiration_date_gte=today,
                expiration_date_lte=max_exp,
                limit=250,
            )
            self._options_cache[symbol] = contracts
            logger.info(
                "polygon_options_cached",
                symbol=symbol,
                contracts=len(contracts),
                horizon=f"{today} to {max_exp}",
            )
        return self._options_cache[symbol]

    # ── Market Data (from Polygon) ─────────────────────────────────────

    async def get_quote(self, symbol: str) -> Quote:
        snap = await self._get_snapshot(symbol)
        if not snap or snap.last_price == 0:
            return await self._backing.get_quote(symbol)
        return Quote(
            symbol=snap.ticker,
            last=snap.last_price,
            bid=snap.last_price,
            ask=snap.last_price,
            high=snap.today_high,
            low=snap.today_low,
            open=snap.today_open,
            close=snap.today_close or snap.prev_close,
            volume=snap.today_volume,
            change=snap.change,
            change_pct=snap.change_pct,
        )

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        return [await self.get_quote(s) for s in symbols]

    async def get_options_expirations(self, symbol: str) -> list[str]:
        contracts = await self._ensure_options_cached(symbol)
        today = date.today()
        expirations = sorted({
            c.expiration_date
            for c in contracts
            if c.expiration_date >= today
        })
        return [exp.strftime("%Y-%m-%d") for exp in expirations]

    async def get_options_chain(
        self, symbol: str, expiration: str, greeks: bool = True
    ) -> OptionsChain:
        contracts = await self._ensure_options_cached(symbol)
        exp_date = datetime.strptime(expiration, "%Y-%m-%d").date()
        filtered = [c for c in contracts if c.expiration_date == exp_date]

        snap = await self._get_snapshot(symbol)
        underlying_price = snap.last_price if snap else 0.0
        if not underlying_price and filtered:
            underlying_price = filtered[0].underlying_price

        broker_contracts = [
            _polygon_to_broker_contract(c) for c in filtered
        ]

        return OptionsChain(
            symbol=symbol,
            expiration=exp_date,
            underlying_price=underlying_price,
            contracts=broker_contracts,
        )

    # ── Account Operations (delegate to backing broker) ────────────────

    async def get_account_balances(self) -> AccountBalance:
        return await self._backing.get_account_balances()

    async def get_positions(self) -> list[BrokerPosition]:
        return await self._backing.get_positions()

    async def get_open_orders(self) -> list[BrokerOrder]:
        return await self._backing.get_open_orders()

    async def preview_order(self, order: OrderRequest) -> OrderPreview:
        return await self._backing.preview_order(order)

    async def place_order(self, order: OrderRequest) -> OrderConfirmation:
        return await self._backing.place_order(order)

    async def cancel_order(self, order_id: str) -> CancelConfirmation:
        return await self._backing.cancel_order(order_id)

    async def replace_order(
        self, order_id: str, updates: OrderRequest
    ) -> OrderConfirmation:
        return await self._backing.replace_order(order_id, updates)


def _polygon_to_broker_contract(c: PolygonOptionsContract) -> OptionContract:
    """Map a Polygon OptionsContract to a broker OptionContract."""
    return OptionContract(
        option_symbol=c.ticker,
        option_type=c.contract_type,
        strike=c.strike_price,
        expiration=c.expiration_date,
        bid=c.bid,
        ask=c.ask,
        mid=c.mid,
        last=c.last_trade_price,
        volume=c.volume,
        open_interest=c.open_interest,
        implied_volatility=c.implied_volatility,
        delta=c.delta,
        gamma=c.gamma,
        theta=c.theta,
        vega=c.vega,
    )
