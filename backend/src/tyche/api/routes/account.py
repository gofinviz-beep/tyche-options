"""Account routes — balances, positions, summary."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException

from tyche.api.deps import get_broker
from tyche.broker.base import BrokerClient
from tyche.schemas.account import (
    AccountBalanceResponse,
    AccountSummaryResponse,
    PositionResponse,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/account", tags=["account"])


@router.get("/balances", response_model=AccountBalanceResponse)
async def get_balances(
    broker: BrokerClient = Depends(get_broker),
) -> AccountBalanceResponse:
    """Fetch current account balances from the broker."""
    try:
        balance = await broker.get_account_balances()
        return AccountBalanceResponse(
            cash=balance.cash,
            buying_power=balance.buying_power,
            net_liquidation_value=balance.net_liquidation_value,
            market_value=balance.market_value,
            total_equity=balance.total_equity,
            open_pl=balance.open_pl,
            close_pl=balance.close_pl,
            pending_cash=balance.pending_cash,
            captured_at=datetime.now(timezone.utc),
        )
    except Exception as exc:
        logger.error("api_balances_failed", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Broker error: {exc}")


@router.get("/positions", response_model=list[PositionResponse])
async def get_positions(
    broker: BrokerClient = Depends(get_broker),
) -> list[PositionResponse]:
    """Fetch current positions from the broker."""
    try:
        positions = await broker.get_positions()
        return [
            PositionResponse(
                id=f"{p.symbol}:{i}",
                symbol=p.symbol,
                quantity=p.quantity,
                cost_basis=p.cost_basis,
                average_cost=p.cost_basis / p.quantity if p.quantity else 0,
                market_value=p.market_value,
                unrealized_pl=p.unrealized_pl,
                unrealized_pl_pct=p.unrealized_pl_pct,
                option_symbol=p.option_symbol,
                option_type=p.option_type,
                strike=p.strike,
                expiration=p.expiration.isoformat() if p.expiration else None,
                strategy="stock" if not p.option_symbol else "option",
                contracts=int(abs(p.quantity)) if p.option_symbol else 0,
            )
            for i, p in enumerate(positions)
        ]
    except Exception as exc:
        logger.error("api_positions_failed", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Broker error: {exc}")


@router.get("/summary", response_model=AccountSummaryResponse)
async def get_summary(
    broker: BrokerClient = Depends(get_broker),
) -> AccountSummaryResponse:
    """Combined account summary for the dashboard."""
    try:
        balance = await broker.get_account_balances()
        positions = await broker.get_positions()

        balance_resp = AccountBalanceResponse(
            cash=balance.cash,
            buying_power=balance.buying_power,
            net_liquidation_value=balance.net_liquidation_value,
            market_value=balance.market_value,
            total_equity=balance.total_equity,
            open_pl=balance.open_pl,
            close_pl=balance.close_pl,
            pending_cash=balance.pending_cash,
            captured_at=datetime.now(timezone.utc),
        )

        position_responses = [
            PositionResponse(
                id=f"{p.symbol}:{i}",
                symbol=p.symbol,
                quantity=p.quantity,
                cost_basis=p.cost_basis,
                average_cost=p.cost_basis / p.quantity if p.quantity else 0,
                market_value=p.market_value,
                unrealized_pl=p.unrealized_pl,
                unrealized_pl_pct=p.unrealized_pl_pct,
                option_symbol=p.option_symbol,
                strategy="stock" if not p.option_symbol else "option",
                contracts=int(abs(p.quantity)) if p.option_symbol else 0,
            )
            for i, p in enumerate(positions)
        ]

        total_unrealized = sum(p.unrealized_pl for p in positions)
        open_orders = await broker.get_open_orders()
        reserved_collateral = sum(
            (o.limit_price or 0) * o.quantity * 100
            for o in open_orders
            if "sell" in o.side and o.option_symbol
        )

        return AccountSummaryResponse(
            balance=balance_resp,
            positions=position_responses,
            position_count=len(positions),
            total_unrealized_pl=total_unrealized,
            cash_available_for_csp=max(0, balance.buying_power - reserved_collateral),
        )
    except Exception as exc:
        logger.error("api_summary_failed", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Broker error: {exc}")
