"""Order-related models: open orders, monitoring snapshots, execution decisions."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tyche.persistence.database import Base


class OpenOrder(Base):
    """A pending order in the brokerage account."""

    __tablename__ = "open_orders"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    broker_order_id: Mapped[str] = mapped_column(String(64), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    option_symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    side: Mapped[str] = mapped_column(String(20))  # buy_to_open, sell_to_open, etc.
    order_type: Mapped[str] = mapped_column(String(16))  # limit, market, stop
    quantity: Mapped[int] = mapped_column(Integer)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20))  # pending, open, partially_filled, filled, canceled
    duration: Mapped[str] = mapped_column(String(8), default="day")  # day, gtc
    strategy: Mapped[str] = mapped_column(String(32), default="unknown")

    # Order intent drives fallback logic during monitoring
    intent: Mapped[str] = mapped_column(
        String(20), default="income"
    )  # income, exit_position, entry

    # Linkage
    recommendation_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    wheel_cycle_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )


class OrderMonitorSnapshot(Base):
    """15-minute snapshot tracking an open order against market conditions."""

    __tablename__ = "order_monitor_snapshots"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    order_id: Mapped[str] = mapped_column(String(36), index=True)
    broker_order_id: Mapped[str] = mapped_column(String(64))

    # Market state at time of snapshot
    underlying_price: Mapped[float] = mapped_column(Float)
    option_bid: Mapped[float] = mapped_column(Float, default=0.0)
    option_ask: Mapped[float] = mapped_column(Float, default=0.0)
    volume_at_strike: Mapped[int] = mapped_column(Integer, default=0)
    open_interest_at_strike: Mapped[int] = mapped_column(Integer, default=0)

    # Order state
    limit_price: Mapped[float] = mapped_column(Float)
    distance_to_fill_pct: Mapped[float] = mapped_column(Float, default=0.0)

    # LLM assessment
    fill_probability: Mapped[str] = mapped_column(
        String(16), default="unknown"
    )  # likely, possible, unlikely
    recommendation: Mapped[str] = mapped_column(
        String(20), default="hold"
    )  # hold, reprice_to_bid, reprice_custom, cancel
    reprice_suggestion: Mapped[float | None] = mapped_column(Float, nullable=True)
    alternative_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExecutionDecision(Base):
    """Audit record of user approving or rejecting a trade."""

    __tablename__ = "execution_decisions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    recommendation_id: Mapped[str] = mapped_column(String(36), index=True)
    approved: Mapped[bool] = mapped_column(default=False)
    user_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Preview result captured before execution
    preview_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Execution result if approved
    broker_order_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    execution_status: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
