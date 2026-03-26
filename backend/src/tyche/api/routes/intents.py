"""Order Intent routes — human-in-the-loop trade management.

Lifecycle: recommend → approve/reject → execute (manual) → record fill
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tyche.api.deps import get_settings
from tyche.config import TycheSettings
from tyche.models.order_intent import OrderIntent
from tyche.persistence.database import get_session
from tyche.schemas.conviction import (
    ApproveIntentRequest,
    CreateIntentRequest,
    OrderIntentListResponse,
    OrderIntentResponse,
    RecordExecutionRequest,
    RejectIntentRequest,
)
from tyche.workflow.intent_builder import create_manual_intent

logger = structlog.get_logger()
router = APIRouter(prefix="/intents", tags=["intents"])


@router.get("", response_model=OrderIntentListResponse)
async def list_intents(
    status: str | None = Query(default=None, description="Filter by status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> OrderIntentListResponse:
    """List order intents, optionally filtered by status."""
    async with get_session() as session:
        stmt = select(OrderIntent).order_by(OrderIntent.created_at.desc())
        if status:
            stmt = stmt.where(OrderIntent.status == status)
        stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        intents = list(result.scalars().all())

        count_stmt = select(OrderIntent)
        count_result = await session.execute(count_stmt)
        all_intents = list(count_result.scalars().all())

        return OrderIntentListResponse(
            intents=[_intent_to_response(i) for i in intents],
            total=len(all_intents),
            pending=sum(1 for i in all_intents if i.status == "pending"),
            approved=sum(1 for i in all_intents if i.status == "approved"),
            executed=sum(1 for i in all_intents if i.status == "executed"),
        )


@router.post("", response_model=OrderIntentResponse, status_code=201)
async def create_intent(req: CreateIntentRequest) -> OrderIntentResponse:
    """Manually create a trade intent (not from a scan)."""
    async with get_session() as session:
        intent = await create_manual_intent(
            session=session,
            symbol=req.symbol,
            strike=req.strike,
            expiration=req.expiration,
            quantity=req.quantity,
            limit_price=req.limit_price,
            strategy=req.strategy,
            side=req.side,
            conviction_level=req.conviction_level,
            trend_state=req.trend_state,
            thesis=req.thesis,
        )
        logger.info("intent_created_via_api", intent_id=intent.id, symbol=intent.symbol)
        return _intent_to_response(intent)


@router.get("/{intent_id}", response_model=OrderIntentResponse)
async def get_intent(intent_id: str) -> OrderIntentResponse:
    """Get a single order intent by ID."""
    async with get_session() as session:
        intent = await session.get(OrderIntent, intent_id)
        if intent is None:
            raise HTTPException(status_code=404, detail="Intent not found")
        return _intent_to_response(intent)


@router.post("/{intent_id}/approve", response_model=OrderIntentResponse)
async def approve_intent(
    intent_id: str,
    req: ApproveIntentRequest,
) -> OrderIntentResponse:
    """Approve an order intent for manual execution."""
    async with get_session() as session:
        intent = await session.get(OrderIntent, intent_id)
        if intent is None:
            raise HTTPException(status_code=404, detail="Intent not found")

        if intent.status != "pending":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot approve intent in '{intent.status}' status",
            )

        now = datetime.now(timezone.utc)
        intent.status = "approved"
        intent.approved_at = now
        intent.updated_at = now
        intent.user_note = req.user_note
        await session.commit()
        await session.refresh(intent)

        logger.info(
            "intent_approved",
            intent_id=intent_id,
            symbol=intent.symbol,
        )
        return _intent_to_response(intent)


@router.post("/{intent_id}/reject", response_model=OrderIntentResponse)
async def reject_intent(
    intent_id: str,
    req: RejectIntentRequest,
) -> OrderIntentResponse:
    """Reject an order intent."""
    async with get_session() as session:
        intent = await session.get(OrderIntent, intent_id)
        if intent is None:
            raise HTTPException(status_code=404, detail="Intent not found")

        if intent.status != "pending":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot reject intent in '{intent.status}' status",
            )

        now = datetime.now(timezone.utc)
        intent.status = "rejected"
        intent.rejected_at = now
        intent.updated_at = now
        intent.user_note = req.reason
        await session.commit()
        await session.refresh(intent)

        logger.info(
            "intent_rejected",
            intent_id=intent_id,
            symbol=intent.symbol,
            reason=req.reason,
        )
        return _intent_to_response(intent)


@router.post("/{intent_id}/execute", response_model=OrderIntentResponse)
async def record_execution(
    intent_id: str,
    req: RecordExecutionRequest,
) -> OrderIntentResponse:
    """Record that an approved intent was manually executed (e.g., in Fidelity)."""
    async with get_session() as session:
        intent = await session.get(OrderIntent, intent_id)
        if intent is None:
            raise HTTPException(status_code=404, detail="Intent not found")

        if intent.status != "approved":
            raise HTTPException(
                status_code=400,
                detail=f"Can only record execution for 'approved' intents, current: '{intent.status}'",
            )

        now = datetime.now(timezone.utc)
        intent.status = "executed"
        intent.executed_at = now
        intent.updated_at = now
        intent.actual_fill_price = req.fill_price
        intent.actual_quantity = req.quantity
        intent.actual_premium = req.premium_received
        intent.broker_confirmation = req.broker_confirmation
        if req.note:
            intent.user_note = (intent.user_note or "") + f"\n[Execution] {req.note}"
        await session.commit()
        await session.refresh(intent)

        logger.info(
            "intent_executed",
            intent_id=intent_id,
            symbol=intent.symbol,
            fill_price=req.fill_price,
            quantity=req.quantity,
        )
        return _intent_to_response(intent)


@router.post("/{intent_id}/expire", response_model=OrderIntentResponse)
async def expire_intent(intent_id: str) -> OrderIntentResponse:
    """Mark an intent as expired (e.g., EOD cleanup)."""
    async with get_session() as session:
        intent = await session.get(OrderIntent, intent_id)
        if intent is None:
            raise HTTPException(status_code=404, detail="Intent not found")

        if intent.status not in ("pending", "approved"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot expire intent in '{intent.status}' status",
            )

        now = datetime.now(timezone.utc)
        intent.status = "expired"
        intent.updated_at = now
        await session.commit()
        await session.refresh(intent)

        logger.info("intent_expired", intent_id=intent_id, symbol=intent.symbol)
        return _intent_to_response(intent)


def _intent_to_response(intent: OrderIntent) -> OrderIntentResponse:
    return OrderIntentResponse(
        id=intent.id,
        created_at=intent.created_at.isoformat(),
        updated_at=intent.updated_at.isoformat(),
        status=intent.status,
        symbol=intent.symbol,
        option_symbol=intent.option_symbol,
        side=intent.side,
        strategy=intent.strategy,
        strike=intent.strike,
        expiration=intent.expiration,
        quantity=intent.quantity,
        limit_price=intent.limit_price,
        estimated_premium=intent.estimated_premium,
        collateral_required=intent.collateral_required,
        annualized_return_pct=intent.annualized_return_pct,
        conviction_level=intent.conviction_level,
        trend_state=intent.trend_state,
        thesis=intent.thesis,
        risks=intent.risks,
        invalidation=intent.invalidation,
        risk_passed=intent.risk_passed,
        risk_summary=intent.risk_summary,
        approved_at=intent.approved_at.isoformat() if intent.approved_at else None,
        rejected_at=intent.rejected_at.isoformat() if intent.rejected_at else None,
        user_note=intent.user_note,
        executed_at=intent.executed_at.isoformat() if intent.executed_at else None,
        actual_fill_price=intent.actual_fill_price,
        actual_quantity=intent.actual_quantity,
        actual_premium=intent.actual_premium,
        broker_confirmation=intent.broker_confirmation,
        scan_id=intent.scan_id,
        wheel_cycle_id=intent.wheel_cycle_id,
    )
