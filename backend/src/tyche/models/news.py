"""News signal persistence model — stored in news.db.

One row per ticker, updated after each classification run with
aggregate news impact metrics for the conviction/scanner pipeline.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from tyche.persistence.database import Base


class NewsSignal(Base):
    """Aggregate news signal per ticker — stored in news.db."""

    __tablename__ = "news_signals"
    __table_args__ = (
        Index("ix_news_signal_impact", "news_impact_score"),
    )

    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    news_impact_score: Mapped[float] = mapped_column(Float, default=0.0)
    last_negative_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    last_positive_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    negative_count_24h: Mapped[int] = mapped_column(Integer, default=0)
    positive_count_24h: Mapped[int] = mapped_column(Integer, default=0)
    total_count_24h: Mapped[int] = mapped_column(Integer, default=0)
    dominant_event_type: Mapped[str | None] = mapped_column(
        String(30), nullable=True, default=None
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "news_impact_score": round(self.news_impact_score, 3),
            "last_negative_at": (
                self.last_negative_at.isoformat()
                if self.last_negative_at
                else None
            ),
            "last_positive_at": (
                self.last_positive_at.isoformat()
                if self.last_positive_at
                else None
            ),
            "negative_count_24h": self.negative_count_24h,
            "positive_count_24h": self.positive_count_24h,
            "total_count_24h": self.total_count_24h,
            "dominant_event_type": self.dominant_event_type,
            "updated_at": (
                self.updated_at.isoformat() if self.updated_at else None
            ),
        }
