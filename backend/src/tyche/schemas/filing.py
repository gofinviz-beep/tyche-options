"""Pydantic response schemas for the Filing/EDGAR API."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class FilingSignalResponse(BaseModel):
    """Aggregate filing signal for a single ticker."""

    ticker: str
    last_8k_at: datetime | None
    last_8k_sentiment: str | None
    last_8k_impact: float | None
    eightk_count_30d: int
    insider_net_shares_30d: float
    insider_buy_count_30d: int
    insider_sell_count_30d: int
    insider_cluster_sell: bool
    last_insider_tx_at: datetime | None
    has_risk: bool
    updated_at: datetime | None


class Filing8KResponse(BaseModel):
    """Single 8-K filing for detail panels."""

    accession_no: str
    form_type: str
    filed_at: datetime
    description: str | None
    filing_url: str | None
    items_reported: str | None
    content_summary: str | None
    event_type: str | None
    sentiment: str | None
    impact_score: float | None


class InsiderTransactionResponse(BaseModel):
    """Single Form 4 insider transaction for detail panels."""

    accession_no: str
    filed_at: datetime
    period_of_report: date | None
    insider_name: str
    insider_title: str | None
    is_officer: bool
    is_director: bool
    is_ten_pct_owner: bool
    transaction_type: str
    shares: float
    price_per_share: float
    total_value: float
    shares_owned_after: float
    acquisition_or_disposition: str


class EdgarIngestResponse(BaseModel):
    """Result of an EDGAR ingestion run."""

    tickers_resolved: int
    tickers_failed_cik: int
    eightk_fetched: int
    eightk_persisted: int
    form4_fetched: int
    insider_tx_persisted: int
    errors: list[str]
    duration_ms: float
