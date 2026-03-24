"""Bot memory model — running context notes for LLM continuity."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tyche.persistence.database import Base


class BotMemory(Base):
    """Running notes the LLM can reference for cross-session continuity."""

    __tablename__ = "bot_memory"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )

    # Memory categories
    category: Mapped[str] = mapped_column(
        String(32), index=True
    )  # position_context, rejection_reason, portfolio_note, market_observation

    symbol: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    content: Mapped[str] = mapped_column(Text)

    # Auto-expire old memories
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    superseded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
