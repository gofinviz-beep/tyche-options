"""Wheel cycle model — tracks the full CSP -> assignment -> CC lifecycle."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tyche.persistence.database import Base


class WheelCycle(Base):
    """Tracks one complete wheel lifecycle with accumulated P&L.

    States: csp_pending -> csp_open -> premium_collected | assigned ->
            holding_shares -> cc_open -> cc_premium | called_away -> completed
    """

    __tablename__ = "wheel_cycles"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    symbol: Mapped[str] = mapped_column(String(10), index=True)

    # Current state in the wheel lifecycle
    state: Mapped[str] = mapped_column(
        String(32), default="csp_pending"
    )

    # CSP leg
    csp_strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    csp_premium_received: Mapped[float] = mapped_column(Float, default=0.0)
    csp_contracts: Mapped[int] = mapped_column(Integer, default=0)
    csp_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    csp_expiration: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Assignment details
    assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    assigned_shares: Mapped[int] = mapped_column(Integer, default=0)
    assignment_cost_basis: Mapped[float] = mapped_column(Float, default=0.0)

    # Covered call leg(s) — may have multiple CC rounds
    cc_rounds: Mapped[int] = mapped_column(Integer, default=0)
    cc_total_premium_received: Mapped[float] = mapped_column(Float, default=0.0)
    cc_current_strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    cc_current_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Disposal
    shares_sold_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    shares_sold_method: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # called_away, sold_at_market, sold_at_limit

    # Accumulated P&L for this entire wheel cycle
    total_premium_collected: Mapped[float] = mapped_column(Float, default=0.0)
    total_realized_pl: Mapped[float] = mapped_column(Float, default=0.0)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
