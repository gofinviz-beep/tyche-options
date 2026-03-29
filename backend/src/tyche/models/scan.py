"""Scan persistence models — ScanRun, ScanCandidate, LLMAnalysisRecord.

These models persist across three separate SQLite databases:
- scans.db     → ScanRun
- candidates.db → ScanCandidate
- analyses.db  → LLMAnalysisRecord

Each model uses scan_id as a logical foreign key (enforced at the application
layer, not via DB-level FK constraints, since they live in different files).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tyche.persistence.database import Base


class ScanRun(Base):
    """One row per scan execution — stored in scans.db."""

    __tablename__ = "scan_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    trigger: Mapped[str] = mapped_column(String(20), default="manual")
    symbols_input: Mapped[str] = mapped_column(Text, default="[]")
    symbols_scanned: Mapped[int] = mapped_column(Integer, default=0)
    pipeline_stages: Mapped[str] = mapped_column(Text, default="[]")
    errors: Mapped[str] = mapped_column(Text, default="[]")
    config_snapshot: Mapped[str] = mapped_column(Text, default="{}")

    csp_candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    cc_candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    llm_analysis_count: Mapped[int] = mapped_column(Integer, default=0)
    intents_created: Mapped[int] = mapped_column(Integer, default=0)

    conviction_signals: Mapped[str] = mapped_column(Text, default="{}")
    earnings_context: Mapped[str] = mapped_column(Text, default="{}")
    institutional_ownership: Mapped[str] = mapped_column(Text, default="{}")

    allocation_total_premium: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    allocation_utilization_pct: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    allocation_solver_status: Mapped[str | None] = mapped_column(
        String(30), nullable=True
    )
    allocation_trades: Mapped[str | None] = mapped_column(Text, nullable=True)


class ScanCandidate(Base):
    """One row per scored option contract — stored in candidates.db."""

    __tablename__ = "scan_candidates"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    scan_id: Mapped[str] = mapped_column(String(36), index=True)
    strategy: Mapped[str] = mapped_column(String(10))

    symbol: Mapped[str] = mapped_column(String(20), index=True)
    option_symbol: Mapped[str] = mapped_column(String(40))
    strike: Mapped[float] = mapped_column(Float)
    expiration: Mapped[str] = mapped_column(String(10))
    dte: Mapped[int] = mapped_column(Integer)

    bid: Mapped[float] = mapped_column(Float, default=0.0)
    ask: Mapped[float] = mapped_column(Float, default=0.0)
    premium_per_contract: Mapped[float] = mapped_column(Float, default=0.0)
    collateral_required: Mapped[float] = mapped_column(Float, default=0.0)
    annualized_return_pct: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.0)

    delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    theta: Mapped[float | None] = mapped_column(Float, nullable=True)
    implied_volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    open_interest: Mapped[int | None] = mapped_column(Integer, nullable=True)

    earnings_within_dte: Mapped[bool] = mapped_column(Integer, default=0)
    earnings_date: Mapped[str | None] = mapped_column(String(10), nullable=True)


class LLMAnalysisRecord(Base):
    """One row per ticker per scan — stored in analyses.db."""

    __tablename__ = "llm_analyses"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    scan_id: Mapped[str] = mapped_column(String(36), index=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)

    thesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    risks: Mapped[str | None] = mapped_column(Text, nullable=True)
    invalidation: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(10), nullable=True)
    assignment_comfort: Mapped[str | None] = mapped_column(String(10), nullable=True)
    assignment_comfort_reasoning: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    recommended_strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommended_expiration: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )
    target_premium: Mapped[float | None] = mapped_column(Float, nullable=True)
    annualized_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    suggested_contracts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    collateral_required: Mapped[float | None] = mapped_column(Float, nullable=True)
    allocation_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)

    conviction_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    trend_state: Mapped[str | None] = mapped_column(String(30), nullable=True)

    would_you_hold_if_assigned: Mapped[str | None] = mapped_column(Text, nullable=True)
    earnings_proximity: Mapped[str | None] = mapped_column(Text, nullable=True)
    earnings_risk_assessment: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(10), default="success")
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
