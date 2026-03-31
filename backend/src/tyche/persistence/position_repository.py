"""Repository for stock position tracking and exit signal persistence.

Operates against backtest.db via the 'backtest' engine.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import structlog
from sqlalchemy import select, update

from tyche.models.backtest import ExitSignal, StockPosition, TickerPullbackProfile
from tyche.persistence.database import get_session

logger = structlog.get_logger()

DB_NAME = "backtest"


async def create_position(
    ticker: str,
    purchase_price: float,
    quantity: int,
    purchase_date: date,
    pullback_type: str = "manual",
) -> StockPosition:
    """Create a new stock position, looking up backtest profile for exit target."""
    now = datetime.now(timezone.utc)

    target_exit_pct: float | None = None
    target_exit_price: float | None = None

    try:
        async with get_session(DB_NAME) as session:
            result = await session.execute(
                select(TickerPullbackProfile).where(
                    TickerPullbackProfile.ticker == ticker.upper(),
                    TickerPullbackProfile.pullback_type == pullback_type,
                )
            )
            profile = result.scalar_one_or_none()

            if not profile and pullback_type != "manual":
                result = await session.execute(
                    select(TickerPullbackProfile).where(
                        TickerPullbackProfile.ticker == ticker.upper(),
                    )
                )
                profile = result.scalars().first()

            if profile and profile.p75_peak_gain_pct:
                target_exit_pct = profile.p75_peak_gain_pct
                target_exit_price = round(
                    purchase_price * (1 + target_exit_pct / 100), 2
                )
                logger.info(
                    "position_exit_target_set",
                    ticker=ticker,
                    p75=target_exit_pct,
                    target_price=target_exit_price,
                )
    except Exception:
        logger.warning("position_profile_lookup_failed", ticker=ticker, exc_info=True)

    position = StockPosition(
        id=str(uuid.uuid4()),
        ticker=ticker.upper(),
        quantity=quantity,
        purchase_date=purchase_date,
        purchase_price=purchase_price,
        pullback_type=pullback_type,
        target_exit_pct=target_exit_pct,
        target_exit_price=target_exit_price,
        stop_loss_price=None,
        current_price=purchase_price,
        current_gain_pct=0.0,
        status="active",
        created_at=now,
        updated_at=now,
    )

    async with get_session(DB_NAME) as session:
        session.add(position)
        await session.commit()
        await session.refresh(position)

    logger.info(
        "position_created",
        ticker=ticker,
        price=purchase_price,
        quantity=quantity,
        target=target_exit_price,
    )
    return position


async def get_active_positions() -> list[StockPosition]:
    """Return all positions with status 'active'."""
    async with get_session(DB_NAME) as session:
        result = await session.execute(
            select(StockPosition)
            .where(StockPosition.status == "active")
            .order_by(StockPosition.purchase_date.desc())
        )
        return list(result.scalars().all())


async def get_all_positions() -> list[StockPosition]:
    """Return all positions including exited, ordered by date desc."""
    async with get_session(DB_NAME) as session:
        result = await session.execute(
            select(StockPosition).order_by(StockPosition.purchase_date.desc())
        )
        return list(result.scalars().all())


async def get_position(position_id: str) -> StockPosition | None:
    """Return a single position by ID."""
    async with get_session(DB_NAME) as session:
        result = await session.execute(
            select(StockPosition).where(StockPosition.id == position_id)
        )
        return result.scalar_one_or_none()


async def update_position_prices(
    position_id: str,
    current_price: float,
    stop_loss_price: float,
    current_gain_pct: float,
) -> None:
    """Update a position's live price data (called by exit monitor)."""
    now = datetime.now(timezone.utc)
    async with get_session(DB_NAME) as session:
        await session.execute(
            update(StockPosition)
            .where(StockPosition.id == position_id)
            .values(
                current_price=round(current_price, 2),
                stop_loss_price=round(stop_loss_price, 2),
                current_gain_pct=round(current_gain_pct, 2),
                updated_at=now,
            )
        )
        await session.commit()


async def mark_exited(
    position_id: str,
    exit_price: float,
    exit_reason: str,
    exit_date_val: date | None = None,
) -> None:
    """Mark a position as exited."""
    now = datetime.now(timezone.utc)
    async with get_session(DB_NAME) as session:
        gain = 0.0
        result = await session.execute(
            select(StockPosition).where(StockPosition.id == position_id)
        )
        pos = result.scalar_one_or_none()
        if pos:
            gain = ((exit_price - pos.purchase_price) / pos.purchase_price) * 100

        await session.execute(
            update(StockPosition)
            .where(StockPosition.id == position_id)
            .values(
                status=exit_reason,
                exit_date=exit_date_val or date.today(),
                exit_price=round(exit_price, 2),
                exit_reason=exit_reason,
                current_price=round(exit_price, 2),
                current_gain_pct=round(gain, 2),
                updated_at=now,
            )
        )
        await session.commit()
        logger.info(
            "position_exited",
            position_id=position_id,
            reason=exit_reason,
            exit_price=exit_price,
            gain_pct=round(gain, 2),
        )


async def delete_position(position_id: str) -> bool:
    """Delete a position. Returns True if found and deleted."""
    from sqlalchemy import delete

    async with get_session(DB_NAME) as session:
        result = await session.execute(
            delete(StockPosition).where(StockPosition.id == position_id)
        )
        await session.commit()
        return result.rowcount > 0


async def record_exit_signal(
    position_id: str,
    ticker: str,
    signal_type: str,
    trigger_price: float,
    current_price: float,
    gain_pct: float,
) -> ExitSignal:
    """Record a triggered exit signal."""
    now = datetime.now(timezone.utc)
    signal = ExitSignal(
        id=str(uuid.uuid4()),
        position_id=position_id,
        ticker=ticker,
        signal_type=signal_type,
        trigger_price=round(trigger_price, 2),
        current_price=round(current_price, 2),
        gain_pct=round(gain_pct, 2),
        triggered_at=now,
    )
    async with get_session(DB_NAME) as session:
        session.add(signal)
        await session.commit()

    logger.info(
        "exit_signal_recorded",
        ticker=ticker,
        signal_type=signal_type,
        gain_pct=round(gain_pct, 2),
    )
    return signal


async def get_signals_for_position(position_id: str) -> list[ExitSignal]:
    """Return all exit signals for a position."""
    async with get_session(DB_NAME) as session:
        result = await session.execute(
            select(ExitSignal)
            .where(ExitSignal.position_id == position_id)
            .order_by(ExitSignal.triggered_at.desc())
        )
        return list(result.scalars().all())


async def get_recent_signals(limit: int = 20) -> list[ExitSignal]:
    """Return the most recent exit signals across all positions."""
    async with get_session(DB_NAME) as session:
        result = await session.execute(
            select(ExitSignal)
            .order_by(ExitSignal.triggered_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
