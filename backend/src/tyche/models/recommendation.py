"""Trade recommendation model — LLM-generated analysis tied to candidates."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tyche.persistence.database import Base


class TradeRecommendation(Base):
    """LLM-generated trade recommendation with rationale."""

    __tablename__ = "trade_recommendations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    scan_id: Mapped[str] = mapped_column(String(36), index=True)
    candidate_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )

    # Core recommendation
    symbol: Mapped[str] = mapped_column(String(10), index=True)
    strategy: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(10))  # bullish, bearish, neutral
    confidence: Mapped[str] = mapped_column(String(10))  # low, medium, high

    # LLM analysis
    thesis: Mapped[str] = mapped_column(Text)
    entry_guidance: Mapped[str] = mapped_column(Text, default="")
    exit_ladder_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    invalidation: Mapped[str] = mapped_column(Text, default="")
    risks_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    holding_period: Mapped[str] = mapped_column(String(50), default="")

    # CSP-specific fields
    assignment_comfort: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )
    assignment_comfort_reasoning: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    would_hold_if_assigned: Mapped[str | None] = mapped_column(Text, nullable=True)
    earnings_risk_assessment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Recommended order parameters
    recommended_strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommended_premium: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommended_contracts: Mapped[int | None] = mapped_column(nullable=True)
    annualized_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Capital allocation context
    allocation_mode: Mapped[str] = mapped_column(
        String(20), default="concentrated"
    )  # concentrated, diversified
    pct_of_available_cash: Mapped[float | None] = mapped_column(Float, nullable=True)

    # LLM metadata
    llm_model_used: Mapped[str] = mapped_column(String(50), default="")
    llm_prompt_tokens: Mapped[int | None] = mapped_column(nullable=True)
    llm_completion_tokens: Mapped[int | None] = mapped_column(nullable=True)
