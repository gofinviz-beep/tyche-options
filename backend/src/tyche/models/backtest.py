"""Backtest persistence models and stock position tracking.

Stored in backtest.db:
- PullbackEvent: each historical pullback instance with entry/peak/exit data.
- TickerPullbackProfile: aggregated per-ticker statistics for exit targets.
- StockPosition: user-tracked stock purchases with computed exit targets.
- ExitSignal: triggered sell signals (profit target or stop loss).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from tyche.persistence.database import Base


class PullbackEvent(Base):
    """One row per historical pullback event — stored in backtest.db."""

    __tablename__ = "pullback_events"
    __table_args__ = (
        Index("ix_event_ticker", "ticker"),
        Index("ix_event_type_ticker", "pullback_type", "ticker"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    ticker: Mapped[str] = mapped_column(String(20))
    pullback_type: Mapped[str] = mapped_column(String(20))

    entry_date: Mapped[date] = mapped_column(Date)
    entry_price: Mapped[float] = mapped_column(Float)

    peak_date: Mapped[date] = mapped_column(Date)
    peak_price: Mapped[float] = mapped_column(Float)
    peak_gain_pct: Mapped[float] = mapped_column(Float)

    exit_date: Mapped[date] = mapped_column(Date)
    exit_price: Mapped[float] = mapped_column(Float)
    exit_gain_pct: Mapped[float] = mapped_column(Float)

    days_to_peak: Mapped[int] = mapped_column(Integer)
    days_to_exit: Mapped[int] = mapped_column(Integer)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)
    volume_declining_at_entry: Mapped[int] = mapped_column(Integer, default=0)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ticker": self.ticker,
            "pullback_type": self.pullback_type,
            "entry_date": self.entry_date.isoformat() if self.entry_date else None,
            "entry_price": round(self.entry_price, 2),
            "peak_date": self.peak_date.isoformat() if self.peak_date else None,
            "peak_price": round(self.peak_price, 2),
            "peak_gain_pct": round(self.peak_gain_pct, 4),
            "exit_date": self.exit_date.isoformat() if self.exit_date else None,
            "exit_price": round(self.exit_price, 2),
            "exit_gain_pct": round(self.exit_gain_pct, 4),
            "days_to_peak": self.days_to_peak,
            "days_to_exit": self.days_to_exit,
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "volume_declining_at_entry": self.volume_declining_at_entry,
        }


class TickerPullbackProfile(Base):
    """Aggregated per-ticker pullback statistics — stored in backtest.db."""

    __tablename__ = "ticker_pullback_profiles"
    __table_args__ = (
        Index("ix_profile_ticker_type", "ticker", "pullback_type", unique=True),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    ticker: Mapped[str] = mapped_column(String(20))
    pullback_type: Mapped[str] = mapped_column(String(20))

    event_count: Mapped[int] = mapped_column(Integer)

    median_peak_gain_pct: Mapped[float] = mapped_column(Float, default=0.0)
    mean_peak_gain_pct: Mapped[float] = mapped_column(Float, default=0.0)
    p25_peak_gain_pct: Mapped[float] = mapped_column(Float, default=0.0)
    p75_peak_gain_pct: Mapped[float] = mapped_column(Float, default=0.0)

    median_exit_gain_pct: Mapped[float] = mapped_column(Float, default=0.0)

    win_rate_5pct: Mapped[float] = mapped_column(Float, default=0.0)
    win_rate_10pct: Mapped[float] = mapped_column(Float, default=0.0)

    median_days_to_peak: Mapped[int] = mapped_column(Integer, default=0)
    median_days_to_exit: Mapped[int] = mapped_column(Integer, default=0)
    avg_max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)

    last_computed: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ticker": self.ticker,
            "pullback_type": self.pullback_type,
            "event_count": self.event_count,
            "median_peak_gain_pct": round(self.median_peak_gain_pct, 4),
            "mean_peak_gain_pct": round(self.mean_peak_gain_pct, 4),
            "p25_peak_gain_pct": round(self.p25_peak_gain_pct, 4),
            "p75_peak_gain_pct": round(self.p75_peak_gain_pct, 4),
            "median_exit_gain_pct": round(self.median_exit_gain_pct, 4),
            "win_rate_5pct": round(self.win_rate_5pct, 4),
            "win_rate_10pct": round(self.win_rate_10pct, 4),
            "median_days_to_peak": self.median_days_to_peak,
            "median_days_to_exit": self.median_days_to_exit,
            "avg_max_drawdown_pct": round(self.avg_max_drawdown_pct, 4),
            "last_computed": (
                self.last_computed.isoformat() if self.last_computed else None
            ),
        }


class StockPosition(Base):
    """User-tracked stock purchase with data-driven exit targets."""

    __tablename__ = "stock_positions"
    __table_args__ = (
        Index("ix_position_ticker", "ticker"),
        Index("ix_position_status", "status"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    ticker: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[int] = mapped_column(Integer)
    purchase_date: Mapped[date] = mapped_column(Date)
    purchase_price: Mapped[float] = mapped_column(Float)
    pullback_type: Mapped[str] = mapped_column(String(20))

    target_exit_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_gain_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(String(30), default="active")
    exit_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ticker": self.ticker,
            "quantity": self.quantity,
            "purchase_date": (
                self.purchase_date.isoformat() if self.purchase_date else None
            ),
            "purchase_price": round(self.purchase_price, 2),
            "pullback_type": self.pullback_type,
            "target_exit_pct": (
                round(self.target_exit_pct, 2) if self.target_exit_pct else None
            ),
            "target_exit_price": (
                round(self.target_exit_price, 2) if self.target_exit_price else None
            ),
            "stop_loss_price": (
                round(self.stop_loss_price, 2) if self.stop_loss_price else None
            ),
            "current_price": (
                round(self.current_price, 2) if self.current_price else None
            ),
            "current_gain_pct": (
                round(self.current_gain_pct, 2) if self.current_gain_pct else None
            ),
            "status": self.status,
            "exit_date": (
                self.exit_date.isoformat() if self.exit_date else None
            ),
            "exit_price": (
                round(self.exit_price, 2) if self.exit_price else None
            ),
            "exit_reason": self.exit_reason,
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
            "updated_at": (
                self.updated_at.isoformat() if self.updated_at else None
            ),
        }


class ExitSignal(Base):
    """Triggered sell signal for a stock position."""

    __tablename__ = "exit_signals"
    __table_args__ = (
        Index("ix_signal_position", "position_id"),
        Index("ix_signal_ticker", "ticker"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    position_id: Mapped[str] = mapped_column(String(36))
    ticker: Mapped[str] = mapped_column(String(20))
    signal_type: Mapped[str] = mapped_column(String(20))
    trigger_price: Mapped[float] = mapped_column(Float)
    current_price: Mapped[float] = mapped_column(Float)
    gain_pct: Mapped[float] = mapped_column(Float)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "position_id": self.position_id,
            "ticker": self.ticker,
            "signal_type": self.signal_type,
            "trigger_price": round(self.trigger_price, 2),
            "current_price": round(self.current_price, 2),
            "gain_pct": round(self.gain_pct, 2),
            "triggered_at": (
                self.triggered_at.isoformat() if self.triggered_at else None
            ),
        }
