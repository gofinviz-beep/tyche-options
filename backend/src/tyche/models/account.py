"""Account snapshot model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from tyche.persistence.database import Base


class AccountSnapshot(Base):
    """Time-stamped snapshot of brokerage account state."""

    __tablename__ = "account_snapshots"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    cash: Mapped[float] = mapped_column(Float)
    buying_power: Mapped[float] = mapped_column(Float)
    net_liquidation_value: Mapped[float] = mapped_column(Float)
    market_value: Mapped[float] = mapped_column(Float, default=0.0)
    total_equity: Mapped[float] = mapped_column(Float, default=0.0)
    open_pl: Mapped[float] = mapped_column(Float, default=0.0)
    close_pl: Mapped[float] = mapped_column(Float, default=0.0)
    pending_cash: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(
        String(32), default="tradier"
    )
