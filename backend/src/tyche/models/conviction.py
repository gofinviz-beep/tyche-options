"""Conviction persistence models — ConvictionSnapshot and ConvictionTransition.

Stored in conviction.db. One snapshot per ticker per trading day captures the
deterministic EMA-based conviction signal. Transitions log state changes
(e.g. uptrend → pullback_to_8ema) for alerting and backtesting.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tyche.persistence.database import Base


class ConvictionSnapshot(Base):
    """One row per ticker per trading day — stored in conviction.db."""

    __tablename__ = "conviction_snapshots"
    __table_args__ = (
        Index("ix_snapshot_date", "as_of_date"),
        Index("ix_snapshot_trend_date", "trend_state", "as_of_date"),
    )

    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)

    trend_state: Mapped[str] = mapped_column(String(30))
    conviction_level: Mapped[str] = mapped_column(String(10))
    raw_conviction: Mapped[str] = mapped_column(String(10), default="none")
    csp_eligible: Mapped[bool] = mapped_column(Boolean, default=False)

    last_close: Mapped[float] = mapped_column(Float, default=0.0)
    ema_8: Mapped[float] = mapped_column(Float, default=0.0)
    ema_21: Mapped[float] = mapped_column(Float, default=0.0)
    ema_8_slope: Mapped[float] = mapped_column(Float, default=0.0)
    ema_21_slope: Mapped[float] = mapped_column(Float, default=0.0)
    price_to_8ema_pct: Mapped[float] = mapped_column(Float, default=0.0)
    price_to_21ema_pct: Mapped[float] = mapped_column(Float, default=0.0)

    volume_declining: Mapped[bool] = mapped_column(Boolean, default=False)
    days_above_both_emas: Mapped[int] = mapped_column(Integer, default=0)
    prior_streak: Mapped[int] = mapped_column(Integer, default=0)
    avg_volume_20d: Mapped[int] = mapped_column(Integer, default=0)
    latest_volume: Mapped[int] = mapped_column(Integer, default=0)

    ema_50: Mapped[float] = mapped_column(Float, default=0.0)
    ema_50_slope: Mapped[float] = mapped_column(Float, default=0.0)
    rsi_14: Mapped[float] = mapped_column(Float, default=0.0)

    iv_rank: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    iv_percentile: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    atm_iv: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    vrp: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)

    conviction_score: Mapped[float] = mapped_column(Float, default=0.0)
    csp_safety_prob: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)

    gate_results_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "as_of_date": self.as_of_date.isoformat() if self.as_of_date else None,
            "trend_state": self.trend_state,
            "conviction_level": self.conviction_level,
            "raw_conviction": self.raw_conviction,
            "csp_eligible": self.csp_eligible,
            "last_close": round(self.last_close, 2),
            "ema_8": round(self.ema_8, 4),
            "ema_21": round(self.ema_21, 4),
            "ema_8_slope": round(self.ema_8_slope, 6),
            "ema_21_slope": round(self.ema_21_slope, 6),
            "price_to_8ema_pct": round(self.price_to_8ema_pct, 2),
            "price_to_21ema_pct": round(self.price_to_21ema_pct, 2),
            "volume_declining": self.volume_declining,
            "days_above_both_emas": self.days_above_both_emas,
            "prior_streak": self.prior_streak,
            "avg_volume_20d": self.avg_volume_20d,
            "latest_volume": self.latest_volume,
            "ema_50": round(self.ema_50 or 0.0, 4),
            "ema_50_slope": round(self.ema_50_slope or 0.0, 6),
            "rsi_14": round(self.rsi_14 or 0.0, 2),
            "iv_rank": round(self.iv_rank, 1) if self.iv_rank is not None else None,
            "iv_percentile": round(self.iv_percentile, 1) if self.iv_percentile is not None else None,
            "atm_iv": round(self.atm_iv, 4) if self.atm_iv is not None else None,
            "vrp": round(self.vrp, 4) if self.vrp is not None else None,
            "conviction_score": round(self.conviction_score or 0.0, 3),
            "csp_safety_prob": round(self.csp_safety_prob, 4) if self.csp_safety_prob is not None else None,
            "gate_results_json": self.gate_results_json,
            "computed_at": self.computed_at.isoformat() if self.computed_at else None,
        }


class ConvictionTransition(Base):
    """One row per state change — stored in conviction.db."""

    __tablename__ = "conviction_transitions"
    __table_args__ = (
        Index("ix_transition_date_state", "transition_date", "to_state"),
        Index("ix_transition_ticker", "ticker"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    ticker: Mapped[str] = mapped_column(String(20))
    from_state: Mapped[str] = mapped_column(String(30))
    to_state: Mapped[str] = mapped_column(String(30))
    transition_date: Mapped[date] = mapped_column(Date)

    last_close: Mapped[float] = mapped_column(Float, default=0.0)
    ema_8: Mapped[float] = mapped_column(Float, default=0.0)
    ema_21: Mapped[float] = mapped_column(Float, default=0.0)
    ema_8_slope: Mapped[float] = mapped_column(Float, default=0.0)
    ema_21_slope: Mapped[float] = mapped_column(Float, default=0.0)
    conviction_level: Mapped[str] = mapped_column(String(10), default="none")
    raw_conviction: Mapped[str] = mapped_column(String(10), default="none")

    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ticker": self.ticker,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "transition_date": (
                self.transition_date.isoformat() if self.transition_date else None
            ),
            "last_close": round(self.last_close, 2),
            "ema_8": round(self.ema_8, 4),
            "ema_21": round(self.ema_21, 4),
            "ema_8_slope": round(self.ema_8_slope, 6),
            "ema_21_slope": round(self.ema_21_slope, 6),
            "conviction_level": self.conviction_level,
            "raw_conviction": self.raw_conviction,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
        }
