"""Earnings calendar cache model."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from tyche.persistence.database import Base


class EarningsEntry(Base):
    """Cached earnings date for a symbol, refreshed daily."""

    __tablename__ = "earnings_calendar"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    symbol: Mapped[str] = mapped_column(String(10), index=True)
    earnings_date: Mapped[date] = mapped_column(Date, index=True)
    reporting_time: Mapped[str] = mapped_column(
        String(20), default="unknown"
    )  # pre_market, after_hours, unknown
    eps_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    eps_actual: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_actual: Mapped[float | None] = mapped_column(Float, nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
