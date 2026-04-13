"""Pydantic response schemas for the News API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class NewsSignalResponse(BaseModel):
    """Aggregate news signal for a single ticker."""

    ticker: str
    news_impact_score: float
    negative_count_24h: int
    positive_count_24h: int
    total_count_24h: int
    dominant_event_type: str | None
    last_negative_at: datetime | None
    last_positive_at: datetime | None
    has_risk: bool
    updated_at: datetime | None


class NewsArticleResponse(BaseModel):
    """Single news article for detail panels."""

    article_id: str
    source: str
    title: str
    published_at: datetime
    url: str
    author: str | None
    summary: str | None
    event_type: str | None
    sentiment: str | None
    impact_score: float | None
    relevance: str | None


class NewsIngestResponse(BaseModel):
    """Result of a news ingestion + classification run."""

    polygon_fetched: int
    finnhub_fetched: int
    total_persisted: int
    tickers_updated: int
    articles_classified: int
    signals_rebuilt: int
    errors: list[str]
