"""Order routes — preview, execute, cancel, monitor open orders."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException

from tyche.api.deps import get_analysis_agent, get_broker, get_risk_engine
from tyche.analysis.agent import AnalysisAgent
from tyche.broker.base import BrokerClient, OrderRequest
from tyche.risk.engine import OrderCandidate, PortfolioContext, RiskEngine
from tyche.schemas.trading import (
    OpenOrderResponse,
    OrderExecuteRequest,
    OrderExecuteResponse,
    OrderPreviewRequest,
    OrderPreviewResponse,
    RiskRuleResultResponse,
)
from tyche.workflow.order_monitor import OrderMonitorResult, run_order_monitor

logger = structlog.get_logger()
router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("/preview", response_model=OrderPreviewResponse)
async def preview_order(
    req: OrderPreviewRequest,
    broker: BrokerClient = Depends(get_broker),
    risk: RiskEngine = Depends(get_risk_engine),
) -> OrderPreviewResponse:
    """Preview an order through risk rules and broker preview."""
    balance = await broker.get_account_balances()
    positions = await broker.get_positions()
    open_orders = await broker.get_open_orders()

    strike = req.limit_price or 0.0
    candidate = OrderCandidate(
        symbol=req.symbol,
        side=req.side,
        quantity=req.quantity,
        limit_price=strike,
        strategy=req.intent,
        strike=strike,
        option_type="put" if "sell" in req.side else "call",
        intent=req.intent,
    )

    context = PortfolioContext(
        balance=balance,
        positions=positions,
        open_orders=open_orders,
        trades_today=0,
    )

    risk_result = risk.validate(candidate, context)

    broker_order = OrderRequest(
        symbol=req.symbol,
        side=req.side,
        quantity=req.quantity,
        order_type=req.order_type,
        limit_price=req.limit_price,
        option_symbol=req.option_symbol,
        duration=req.duration,
        preview=True,
    )

    try:
        preview = await broker.preview_order(broker_order)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Broker preview failed: {exc}")

    collateral_required = strike * 100 * req.quantity

    return OrderPreviewResponse(
        estimated_cost=preview.estimated_cost,
        estimated_commission=preview.estimated_commission,
        estimated_fees=preview.estimated_fees,
        estimated_premium=abs(preview.estimated_cost) if "sell" in req.side else 0,
        collateral_required=collateral_required,
        risk_results=[
            RiskRuleResultResponse(
                rule_name=r.rule_name,
                passed=r.passed,
                reason=r.reason,
                details=r.details,
            )
            for r in risk_result.results
        ],
        all_rules_passed=risk_result.all_passed,
        warnings=preview.warnings,
    )


@router.post("/execute", response_model=OrderExecuteResponse)
async def execute_order(
    req: OrderExecuteRequest,
    broker: BrokerClient = Depends(get_broker),
    risk: RiskEngine = Depends(get_risk_engine),
) -> OrderExecuteResponse:
    """Execute an approved order (runs risk checks again for safety)."""
    balance = await broker.get_account_balances()
    positions = await broker.get_positions()
    open_orders = await broker.get_open_orders()

    strike = req.limit_price or 0.0
    candidate = OrderCandidate(
        symbol=req.symbol,
        side=req.side,
        quantity=req.quantity,
        limit_price=strike,
        strategy=req.intent,
        strike=strike,
        option_type="put" if "sell" in req.side else "call",
        intent=req.intent,
    )

    context = PortfolioContext(
        balance=balance,
        positions=positions,
        open_orders=open_orders,
        trades_today=0,
    )

    risk_result = risk.validate(candidate, context)
    if not risk_result.all_passed:
        failed = [r for r in risk_result.results if not r.passed]
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Risk rules blocked order",
                "violations": [
                    {"rule": r.rule_name, "reason": r.reason} for r in failed
                ],
            },
        )

    broker_order = OrderRequest(
        symbol=req.symbol,
        side=req.side,
        quantity=req.quantity,
        order_type=req.order_type,
        limit_price=req.limit_price,
        option_symbol=req.option_symbol,
        duration=req.duration,
    )

    try:
        confirmation = await broker.place_order(broker_order)
    except Exception as exc:
        logger.error("order_execute_failed", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Order execution failed: {exc}")

    decision_id = str(uuid.uuid4())

    logger.info(
        "order_executed",
        decision_id=decision_id,
        broker_order_id=confirmation.broker_order_id,
        symbol=req.symbol,
        side=req.side,
        quantity=req.quantity,
        limit_price=req.limit_price,
    )

    return OrderExecuteResponse(
        broker_order_id=confirmation.broker_order_id,
        status=confirmation.status,
        execution_decision_id=decision_id,
        wheel_cycle_id=req.wheel_cycle_id,
        message=f"Order {confirmation.broker_order_id} placed successfully",
    )


@router.get("/open", response_model=list[OpenOrderResponse])
async def get_open_orders(
    broker: BrokerClient = Depends(get_broker),
) -> list[OpenOrderResponse]:
    """List all open orders."""
    try:
        orders = await broker.get_open_orders()
        return [
            OpenOrderResponse(
                id=f"order:{o.broker_order_id}",
                broker_order_id=o.broker_order_id,
                symbol=o.symbol,
                option_symbol=o.option_symbol,
                side=o.side,
                order_type=o.order_type,
                quantity=o.quantity,
                limit_price=o.limit_price,
                status=o.status,
                intent="income",
                strategy=o.strategy,
                duration=o.duration,
                captured_at=datetime.now(timezone.utc),
            )
            for o in orders
        ]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Broker error: {exc}")


@router.delete("/{order_id}")
async def cancel_order(
    order_id: str,
    broker: BrokerClient = Depends(get_broker),
) -> dict[str, str]:
    """Cancel an open order."""
    try:
        result = await broker.cancel_order(order_id)
        return {"status": result.status, "order_id": result.broker_order_id}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Cancel failed: {exc}")


@router.get("/monitor", response_model=dict[str, Any])
async def monitor_orders(
    broker: BrokerClient = Depends(get_broker),
    analysis: AnalysisAgent | None = Depends(get_analysis_agent),
) -> dict[str, Any]:
    """Run order monitor and return results."""
    result = await run_order_monitor(broker=broker, analysis_agent=analysis)
    return {
        "monitored_at": result.monitored_at.isoformat(),
        "orders_checked": result.orders_checked,
        "alerts": result.alerts,
        "analyses": [a.model_dump() for a in result.analyses],
        "errors": result.errors,
    }
