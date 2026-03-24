"""Mock broker client for testing and development without API access."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

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


class MockBroker:
    """Deterministic mock implementing the BrokerClient protocol.

    Provides realistic test data modeled after the user's actual
    PL (Planet Labs) trades for consistent testing.
    """

    def __init__(self) -> None:
        self._orders: dict[str, dict] = {}
        self._next_order_id = 1000

    async def get_account_balances(self) -> AccountBalance:
        return AccountBalance(
            cash=50000.0,
            buying_power=50000.0,
            net_liquidation_value=112000.0,
            market_value=62000.0,
            total_equity=112000.0,
            open_pl=1500.0,
            close_pl=8500.0,
            pending_cash=0.0,
        )

    async def get_positions(self) -> list[BrokerPosition]:
        return [
            BrokerPosition(
                symbol="PL",
                quantity=4000.0,
                cost_basis=92000.0,
                market_value=96000.0,
                unrealized_pl=4000.0,
                unrealized_pl_pct=4.35,
            ),
            BrokerPosition(
                symbol="AAPL",
                quantity=100.0,
                cost_basis=18500.0,
                market_value=19200.0,
                unrealized_pl=700.0,
                unrealized_pl_pct=3.78,
            ),
        ]

    async def get_open_orders(self) -> list[BrokerOrder]:
        return [
            BrokerOrder(
                broker_order_id="mock-1001",
                symbol="PL",
                side="sell_to_open",
                order_type="limit",
                quantity=40,
                status="pending",
                duration="day",
                limit_price=1.80,
                option_symbol="PL260327P00023000",
                created_at=datetime.now(timezone.utc) - timedelta(hours=2),
                strategy="csp",
            ),
        ]

    async def get_quote(self, symbol: str) -> Quote:
        quotes = {
            "PL": Quote(
                symbol="PL", last=24.50, bid=24.48, ask=24.52,
                high=25.10, low=24.20, open=24.80, close=24.30,
                volume=12500000, change=0.20, change_pct=0.82,
            ),
            "AAPL": Quote(
                symbol="AAPL", last=192.00, bid=191.98, ask=192.02,
                high=193.50, low=191.00, open=192.50, close=191.80,
                volume=45000000, change=0.20, change_pct=0.10,
            ),
        }
        return quotes.get(
            symbol,
            Quote(
                symbol=symbol, last=100.0, bid=99.95, ask=100.05,
                high=101.0, low=99.0, open=100.5, close=99.5,
                volume=5000000, change=0.50, change_pct=0.50,
            ),
        )

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        return [await self.get_quote(s) for s in symbols]

    async def get_options_expirations(self, symbol: str) -> list[str]:
        today = date.today()
        return [
            (today + timedelta(days=d)).isoformat()
            for d in [3, 7, 10, 14, 21, 28, 35, 42]
        ]

    async def get_options_chain(
        self, symbol: str, expiration: str, greeks: bool = True
    ) -> OptionsChain:
        exp_date = datetime.strptime(expiration, "%Y-%m-%d").date()
        quote = await self.get_quote(symbol)
        price = quote.last

        contracts: list[OptionContract] = []
        for offset in [-5, -3, -2, -1, 0, 1, 2, 3, 5]:
            strike = round(price + offset, 2)
            if strike <= 0:
                continue

            dte = (exp_date - date.today()).days
            for opt_type in ["put", "call"]:
                type_char = "P" if opt_type == "put" else "C"
                strike_str = f"{int(strike * 1000):08d}"
                occ = f"{symbol.ljust(6)[:6]}{exp_date.strftime('%y%m%d')}{type_char}{strike_str}"

                itm = (opt_type == "put" and strike > price) or (
                    opt_type == "call" and strike < price
                )
                base_premium = max(0.05, abs(strike - price) * 0.15 if itm else 0.02 * max(1, dte / 7))
                bid = round(base_premium * 0.95, 2)
                ask = round(base_premium * 1.05, 2)

                contracts.append(
                    OptionContract(
                        option_symbol=occ.replace(" ", ""),
                        option_type=opt_type,
                        strike=strike,
                        expiration=exp_date,
                        bid=bid,
                        ask=ask,
                        mid=round((bid + ask) / 2, 4),
                        last=round((bid + ask) / 2, 2),
                        volume=max(100, 5000 - abs(offset) * 800),
                        open_interest=max(500, 10000 - abs(offset) * 1500),
                        implied_volatility=0.45 + abs(offset) * 0.02,
                        delta=-0.3 if opt_type == "put" else 0.7,
                        gamma=0.05,
                        theta=-0.03,
                        vega=0.08,
                    )
                )

        return OptionsChain(
            symbol=symbol,
            expiration=exp_date,
            underlying_price=price,
            contracts=contracts,
        )

    async def preview_order(self, order: OrderRequest) -> OrderPreview:
        premium = (order.limit_price or 1.0) * order.quantity * 100
        return OrderPreview(
            estimated_cost=-premium if "sell" in order.side else premium,
            estimated_commission=0.65 * order.quantity,
            estimated_fees=0.02 * order.quantity,
            status="ok",
        )

    async def place_order(self, order: OrderRequest) -> OrderConfirmation:
        oid = str(self._next_order_id)
        self._next_order_id += 1
        self._orders[oid] = {
            "order": order,
            "status": "pending",
        }
        return OrderConfirmation(
            broker_order_id=oid,
            status="pending",
        )

    async def cancel_order(self, order_id: str) -> CancelConfirmation:
        if order_id in self._orders:
            self._orders[order_id]["status"] = "canceled"
        return CancelConfirmation(
            broker_order_id=order_id,
            status="ok",
        )

    async def replace_order(
        self, order_id: str, updates: OrderRequest
    ) -> OrderConfirmation:
        if order_id in self._orders:
            self._orders[order_id]["order"] = updates
        return OrderConfirmation(
            broker_order_id=order_id,
            status="ok",
        )
