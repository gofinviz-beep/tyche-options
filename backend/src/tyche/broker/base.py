"""Broker abstraction — Protocol-based for duck-typed replaceability."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, Protocol


@dataclass(frozen=True)
class AccountBalance:
    """Normalized broker account balance."""

    cash: float
    buying_power: float
    net_liquidation_value: float
    market_value: float = 0.0
    total_equity: float = 0.0
    open_pl: float = 0.0
    close_pl: float = 0.0
    pending_cash: float = 0.0


@dataclass(frozen=True)
class BrokerPosition:
    """Normalized position from broker."""

    symbol: str
    quantity: float
    cost_basis: float
    market_value: float = 0.0
    unrealized_pl: float = 0.0
    unrealized_pl_pct: float = 0.0
    option_symbol: str | None = None
    option_type: str | None = None
    strike: float | None = None
    expiration: date | None = None


@dataclass(frozen=True)
class BrokerOrder:
    """Normalized open order from broker."""

    broker_order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: int
    status: str
    duration: str = "day"
    limit_price: float | None = None
    stop_price: float | None = None
    option_symbol: str | None = None
    created_at: datetime | None = None
    strategy: str = "unknown"


@dataclass(frozen=True)
class Quote:
    """Normalized underlying quote."""

    symbol: str
    last: float
    bid: float
    ask: float
    high: float
    low: float
    open: float
    close: float
    volume: int
    change: float = 0.0
    change_pct: float = 0.0


@dataclass(frozen=True)
class OptionContract:
    """Single option contract within a chain."""

    option_symbol: str
    option_type: str  # call, put
    strike: float
    expiration: date
    bid: float
    ask: float
    mid: float
    last: float
    volume: int
    open_interest: int
    implied_volatility: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0


@dataclass
class OptionsChain:
    """Full options chain for an underlying at a given expiration."""

    symbol: str
    expiration: date
    underlying_price: float
    contracts: list[OptionContract] = field(default_factory=list)

    @property
    def calls(self) -> list[OptionContract]:
        return [c for c in self.contracts if c.option_type == "call"]

    @property
    def puts(self) -> list[OptionContract]:
        return [c for c in self.contracts if c.option_type == "put"]


@dataclass(frozen=True)
class OrderRequest:
    """Request to place an order."""

    symbol: str
    side: Literal[
        "buy_to_open", "buy_to_close", "sell_to_open", "sell_to_close", "buy", "sell"
    ]
    quantity: int
    order_type: Literal["market", "limit", "stop", "stop_limit"] = "limit"
    duration: Literal["day", "gtc"] = "day"
    limit_price: float | None = None
    stop_price: float | None = None
    option_symbol: str | None = None
    order_class: Literal["equity", "option", "multileg"] = "option"
    preview: bool = False


@dataclass(frozen=True)
class OrderPreview:
    """Preview of an order before submission."""

    estimated_cost: float
    estimated_commission: float
    estimated_fees: float
    margin_impact: float | None = None
    status: str = "ok"
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OrderConfirmation:
    """Result of placing an order."""

    broker_order_id: str
    status: str
    fill_price: float | None = None
    fill_quantity: int | None = None


@dataclass(frozen=True)
class CancelConfirmation:
    """Result of canceling an order."""

    broker_order_id: str
    status: str


class BrokerClient(Protocol):
    """Protocol defining the broker integration surface.

    Implementations: TradierClient (production), MockBroker (testing).
    """

    async def get_account_balances(self) -> AccountBalance: ...

    async def get_positions(self) -> list[BrokerPosition]: ...

    async def get_open_orders(self) -> list[BrokerOrder]: ...

    async def get_quote(self, symbol: str) -> Quote: ...

    async def get_quotes(self, symbols: list[str]) -> list[Quote]: ...

    async def get_options_chain(
        self, symbol: str, expiration: str, greeks: bool = True
    ) -> OptionsChain: ...

    async def get_options_expirations(self, symbol: str) -> list[str]: ...

    async def preview_order(self, order: OrderRequest) -> OrderPreview: ...

    async def place_order(self, order: OrderRequest) -> OrderConfirmation: ...

    async def cancel_order(self, order_id: str) -> CancelConfirmation: ...

    async def replace_order(
        self, order_id: str, updates: OrderRequest
    ) -> OrderConfirmation: ...
