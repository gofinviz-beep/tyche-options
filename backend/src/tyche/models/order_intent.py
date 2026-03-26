"""Order Intent model — represents a trade recommendation awaiting human action.

Lifecycle:
    pending → approved → executed → (optionally) closed
    pending → rejected
    pending → expired (auto-expire at EOD if not acted on)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tyche.persistence.database import Base


class OrderIntent(Base):
    """A recommended trade that requires human approval and manual execution."""

    __tablename__ = "order_intents"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )

    # Intent state: pending, approved, rejected, executed, expired, closed
    status: Mapped[str] = mapped_column(String(20), index=True, default="pending")

    # Trade details
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    option_symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    side: Mapped[str] = mapped_column(String(20))  # sell_to_open, buy_to_close, etc.
    strategy: Mapped[str] = mapped_column(String(32))  # csp, covered_call, etc.
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    expiration: Mapped[str | None] = mapped_column(String(10), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Economics
    estimated_premium: Mapped[float] = mapped_column(Float, default=0.0)
    collateral_required: Mapped[float] = mapped_column(Float, default=0.0)
    annualized_return_pct: Mapped[float] = mapped_column(Float, default=0.0)

    # Conviction context
    conviction_level: Mapped[str] = mapped_column(String(10), default="none")
    trend_state: Mapped[str] = mapped_column(String(30), default="unknown")

    # LLM reasoning
    thesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    risks: Mapped[str | None] = mapped_column(Text, nullable=True)
    invalidation: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Risk check summary
    risk_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Human action tracking
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    user_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Manual execution tracking (user executes in Fidelity, then records here)
    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actual_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_premium: Mapped[float | None] = mapped_column(Float, nullable=True)
    broker_confirmation: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )

    # Linkage
    scan_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    wheel_cycle_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
