"""Watchlist symbol model."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tyche.persistence.database import Base


class WatchlistSymbol(Base):
    """A curated stock in the watchlist with fundamental metadata."""

    __tablename__ = "watchlist_symbols"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    symbol: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    company_name: Mapped[str] = mapped_column(String(200), default="")
    sector: Mapped[str] = mapped_column(String(100), default="")
    category: Mapped[str] = mapped_column(String(100), default="")

    # Fundamental gates
    market_cap_billions: Mapped[float] = mapped_column(Float, default=0.0)
    avg_daily_volume: Mapped[float] = mapped_column(Float, default=0.0)

    # Assignment comfort
    assignment_comfort: Mapped[str] = mapped_column(
        String(10), default="medium"
    )  # high, medium, low
    assignment_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Earnings
    next_earnings_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    earnings_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)

    # Flags
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
