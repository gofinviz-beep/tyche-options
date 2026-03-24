"""Option candidate model — output of the strategy screening engine."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tyche.persistence.database import Base


class OptionCandidate(Base):
    """A screened option candidate that passed deterministic filters."""

    __tablename__ = "option_candidates"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    scan_id: Mapped[str] = mapped_column(String(36), index=True)
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )

    # Underlying
    symbol: Mapped[str] = mapped_column(String(10), index=True)
    underlying_price: Mapped[float] = mapped_column(Float)

    # Contract details
    option_symbol: Mapped[str] = mapped_column(String(40))
    option_type: Mapped[str] = mapped_column(String(4))  # call, put
    strike: Mapped[float] = mapped_column(Float)
    expiration: Mapped[date] = mapped_column(Date)
    dte: Mapped[int] = mapped_column(Integer)

    # Market data at scan time
    bid: Mapped[float] = mapped_column(Float)
    ask: Mapped[float] = mapped_column(Float)
    mid: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer, default=0)
    open_interest: Mapped[int] = mapped_column(Integer, default=0)
    implied_volatility: Mapped[float] = mapped_column(Float, default=0.0)

    # Greeks (from ORATS via Tradier)
    delta: Mapped[float] = mapped_column(Float, default=0.0)
    gamma: Mapped[float] = mapped_column(Float, default=0.0)
    theta: Mapped[float] = mapped_column(Float, default=0.0)
    vega: Mapped[float] = mapped_column(Float, default=0.0)

    # Strategy classification
    strategy: Mapped[str] = mapped_column(String(32))  # csp, covered_call, long_call, etc.

    # Scoring
    bid_ask_spread_pct: Mapped[float] = mapped_column(Float, default=0.0)
    premium_per_contract: Mapped[float] = mapped_column(Float, default=0.0)
    annualized_return_pct: Mapped[float] = mapped_column(Float, default=0.0)
    collateral_required: Mapped[float] = mapped_column(Float, default=0.0)
    deterministic_score: Mapped[float] = mapped_column(Float, default=0.0)

    # Earnings context
    earnings_within_dte: Mapped[bool] = mapped_column(Boolean, default=False)
    earnings_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Filter results
    passed_all_filters: Mapped[bool] = mapped_column(Boolean, default=False)
    filter_results_json: Mapped[str | None] = mapped_column(Text, nullable=True)
