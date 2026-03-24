"""Trade journal model — recommendation-to-outcome chain."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tyche.persistence.database import Base


class TradeJournal(Base):
    """Full lifecycle record linking recommendation -> decision -> outcome."""

    __tablename__ = "trade_journal"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Linkage
    recommendation_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    execution_decision_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    wheel_cycle_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )

    # Trade summary
    symbol: Mapped[str] = mapped_column(String(10), index=True)
    strategy: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(10))
    entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    contracts: Mapped[int] = mapped_column(default=0)

    # Result
    realized_pl: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str] = mapped_column(
        String(20), default="open"
    )  # open, profit, loss, expired_otm, assigned, called_away

    # Context
    original_thesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    post_trade_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    lessons_learned: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_action_history_json: Mapped[str | None] = mapped_column(Text, nullable=True)
