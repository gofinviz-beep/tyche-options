"""Tests for the mock broker — verifies protocol compliance and data shape."""

from __future__ import annotations

import pytest

from tyche.broker.base import OrderRequest
from tyche.broker.mock import MockBroker


@pytest.fixture
def broker() -> MockBroker:
    return MockBroker()


@pytest.mark.asyncio
async def test_account_balances(broker: MockBroker) -> None:
    balance = await broker.get_account_balances()
    assert balance.cash > 0
    assert balance.buying_power > 0
    assert balance.net_liquidation_value > 0


@pytest.mark.asyncio
async def test_positions(broker: MockBroker) -> None:
    positions = await broker.get_positions()
    assert len(positions) >= 1
    pl_pos = next(p for p in positions if p.symbol == "PL")
    assert pl_pos.quantity == 4000.0
    assert pl_pos.cost_basis == 92000.0


@pytest.mark.asyncio
async def test_open_orders(broker: MockBroker) -> None:
    orders = await broker.get_open_orders()
    assert len(orders) >= 1
    csp_order = orders[0]
    assert csp_order.symbol == "PL"
    assert csp_order.side == "sell_to_open"
    assert csp_order.limit_price == 1.80
    assert csp_order.quantity == 40


@pytest.mark.asyncio
async def test_quote(broker: MockBroker) -> None:
    quote = await broker.get_quote("PL")
    assert quote.symbol == "PL"
    assert quote.last > 0
    assert quote.bid > 0
    assert quote.ask >= quote.bid


@pytest.mark.asyncio
async def test_options_expirations(broker: MockBroker) -> None:
    exps = await broker.get_options_expirations("PL")
    assert len(exps) >= 4
    for exp in exps:
        assert len(exp) == 10  # YYYY-MM-DD format


@pytest.mark.asyncio
async def test_options_chain(broker: MockBroker) -> None:
    exps = await broker.get_options_expirations("PL")
    chain = await broker.get_options_chain("PL", exps[1])
    assert chain.symbol == "PL"
    assert chain.underlying_price > 0
    assert len(chain.contracts) > 0
    assert len(chain.puts) > 0
    assert len(chain.calls) > 0

    for contract in chain.contracts:
        assert contract.strike > 0
        assert contract.bid >= 0
        assert contract.ask >= contract.bid
        assert contract.option_type in ("put", "call")


@pytest.mark.asyncio
async def test_preview_order(broker: MockBroker) -> None:
    order = OrderRequest(
        symbol="PL",
        side="sell_to_open",
        quantity=40,
        order_type="limit",
        limit_price=1.80,
        option_symbol="PL260327P00023000",
    )
    preview = await broker.preview_order(order)
    assert preview.status == "ok"
    assert preview.estimated_cost < 0  # Credit for selling


@pytest.mark.asyncio
async def test_place_and_cancel_order(broker: MockBroker) -> None:
    order = OrderRequest(
        symbol="PL",
        side="sell_to_open",
        quantity=10,
        order_type="limit",
        limit_price=1.50,
        option_symbol="PL260327P00023000",
    )
    confirmation = await broker.place_order(order)
    assert confirmation.broker_order_id
    assert confirmation.status == "pending"

    cancel = await broker.cancel_order(confirmation.broker_order_id)
    assert cancel.status == "ok"


@pytest.mark.asyncio
async def test_get_quotes_batch(broker: MockBroker) -> None:
    quotes = await broker.get_quotes(["PL", "AAPL"])
    assert len(quotes) == 2
    symbols = {q.symbol for q in quotes}
    assert symbols == {"PL", "AAPL"}
