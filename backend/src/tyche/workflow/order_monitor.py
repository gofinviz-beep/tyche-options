"""15-minute order monitor — tracks open orders against market conditions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import structlog

from tyche.analysis.agent import AnalysisAgent
from tyche.broker.base import BrokerClient, BrokerOrder, Quote
from tyche.schemas.analysis import OrderMonitorAnalysis

logger = structlog.get_logger()


class OrderMonitorResult:
    """Results of an order monitoring cycle."""

    def __init__(self) -> None:
        self.monitored_at: datetime = datetime.now(timezone.utc)
        self.orders_checked: int = 0
        self.analyses: list[OrderMonitorAnalysis] = []
        self.alerts: list[dict[str, Any]] = []
        self.errors: list[str] = []


async def run_order_monitor(
    broker: BrokerClient,
    analysis_agent: AnalysisAgent | None = None,
) -> OrderMonitorResult:
    """Monitor open orders and assess fill probability.

    Steps:
    1. Fetch open orders
    2. For each pending limit order, get current quote and chain data
    3. Calculate distance to fill, premium decay
    4. Send to LLM for assessment (if available)
    5. Generate alerts for orders needing attention
    """
    result = OrderMonitorResult()

    try:
        open_orders = await broker.get_open_orders()
    except Exception as exc:
        result.errors.append(f"Failed to fetch orders: {exc}")
        return result

    pending_orders = [
        o for o in open_orders if o.status in ("pending", "open", "partially_filled")
    ]

    if not pending_orders:
        logger.debug("order_monitor_no_pending")
        return result

    result.orders_checked = len(pending_orders)

    # Gather market data for each order
    quotes: dict[str, Quote] = {}
    chain_context: dict[str, Any] = {}

    symbols = list({o.symbol for o in pending_orders})
    for symbol in symbols:
        try:
            quote = await broker.get_quote(symbol)
            quotes[symbol] = quote
        except Exception:
            logger.warning("order_monitor_quote_failed", symbol=symbol)

        # Get chain data for option orders
        option_orders = [
            o for o in pending_orders
            if o.symbol == symbol and o.option_symbol
        ]
        if option_orders:
            try:
                exps = await broker.get_options_expirations(symbol)
                if exps:
                    chain = await broker.get_options_chain(symbol, exps[0])
                    for contract in chain.contracts:
                        key = contract.option_symbol
                        chain_context[key] = {
                            "bid": contract.bid,
                            "ask": contract.ask,
                            "volume": contract.volume,
                            "open_interest": contract.open_interest,
                            "strike": contract.strike,
                        }
            except Exception:
                logger.warning("order_monitor_chain_failed", symbol=symbol)

    # Generate alerts for orders far from fill
    for order in pending_orders:
        quote = quotes.get(order.symbol)
        if not quote or order.limit_price is None:
            continue

        alert: dict[str, Any] = {
            "order_id": order.broker_order_id,
            "symbol": order.symbol,
            "limit_price": order.limit_price,
            "underlying_price": quote.last,
        }

        if order.option_symbol and order.option_symbol in chain_context:
            ctx = chain_context[order.option_symbol]
            alert["option_bid"] = ctx["bid"]
            alert["option_ask"] = ctx["ask"]
            alert["volume_at_strike"] = ctx["volume"]
            alert["oi_at_strike"] = ctx["open_interest"]

            if "sell" in order.side:
                distance = (order.limit_price - ctx["bid"]) / order.limit_price * 100 if order.limit_price > 0 else 0
            else:
                distance = (ctx["ask"] - order.limit_price) / order.limit_price * 100 if order.limit_price > 0 else 0
            alert["distance_to_fill_pct"] = round(distance, 2)

            if abs(distance) > 10:
                alert["attention"] = "far_from_fill"
            if ctx["volume"] == 0:
                alert["attention"] = "no_volume"

        result.alerts.append(alert)

    # LLM analysis if available
    if analysis_agent and pending_orders:
        try:
            positions = await broker.get_positions()
            analyses = await analysis_agent.analyze_orders(
                orders=pending_orders,
                quotes=quotes,
                chain_context=chain_context,
                positions=positions,
            )
            result.analyses = analyses
        except Exception as exc:
            result.errors.append(f"LLM order analysis failed: {exc}")

    logger.info(
        "order_monitor_complete",
        orders_checked=result.orders_checked,
        alerts=len(result.alerts),
        analyses=len(result.analyses),
    )
    return result
