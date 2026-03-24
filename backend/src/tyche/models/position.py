"""Position model — stocks and options held in the account."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from tyche.persistence.database import Base


class Position(Base):
    """A held position — equity shares or option contract."""

    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    cost_basis: Mapped[float] = mapped_column(Float)
    average_cost: Mapped[float] = mapped_column(Float, default=0.0)
    market_value: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pl: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pl_pct: Mapped[float] = mapped_column(Float, default=0.0)

    # Option-specific fields (null for equity positions)
    option_symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    option_type: Mapped[str | None] = mapped_column(
        String(4), nullable=True
    )  # "call" | "put"
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    expiration: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Classification
    strategy: Mapped[str] = mapped_column(
        String(32), default="unknown"
    )  # csp, covered_call, long_call, long_put, equity
    contracts: Mapped[int] = mapped_column(Integer, default=0)

    # Linkage
    wheel_cycle_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
