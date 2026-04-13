"""Filing signal persistence model — stored in news.db.

One row per ticker, updated after each EDGAR ingestion/classification run
with aggregate 8-K and insider transaction metrics.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from tyche.persistence.database import Base


class FilingSignal(Base):
    """Aggregate filing signal per ticker — stored in news.db."""

    __tablename__ = "filing_signals"
    __table_args__ = (Index("ix_filing_signal_cluster", "insider_cluster_sell"),)

    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)

    last_8k_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    last_8k_sentiment: Mapped[str | None] = mapped_column(
        String(20), nullable=True, default=None
    )
    last_8k_impact: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )
    eightk_count_30d: Mapped[int] = mapped_column(Integer, default=0)

    insider_net_shares_30d: Mapped[float] = mapped_column(Float, default=0.0)
    insider_buy_count_30d: Mapped[int] = mapped_column(Integer, default=0)
    insider_sell_count_30d: Mapped[int] = mapped_column(Integer, default=0)
    insider_cluster_sell: Mapped[bool] = mapped_column(Boolean, default=False)
    last_insider_tx_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "last_8k_at": (
                self.last_8k_at.isoformat() if self.last_8k_at else None
            ),
            "last_8k_sentiment": self.last_8k_sentiment,
            "last_8k_impact": (
                round(self.last_8k_impact, 3) if self.last_8k_impact is not None else None
            ),
            "eightk_count_30d": self.eightk_count_30d,
            "insider_net_shares_30d": round(self.insider_net_shares_30d, 2),
            "insider_buy_count_30d": self.insider_buy_count_30d,
            "insider_sell_count_30d": self.insider_sell_count_30d,
            "insider_cluster_sell": self.insider_cluster_sell,
            "last_insider_tx_at": (
                self.last_insider_tx_at.isoformat()
                if self.last_insider_tx_at
                else None
            ),
            "updated_at": (
                self.updated_at.isoformat() if self.updated_at else None
            ),
        }
