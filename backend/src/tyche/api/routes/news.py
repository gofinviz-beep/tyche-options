"""API routes for the News Intelligence module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncio

import structlog
from fastapi import APIRouter, Depends, Query

from tyche.api.deps import (
    get_finnhub,
    get_news_article_store,
    get_news_classifier,
    get_polygon,
    get_settings as dep_settings,
)
from tyche.config import TycheSettings, get_settings
from tyche.market_data.news_signals import get_all_signals, get_signal, rebuild_signals
from tyche.persistence.published_routes import get_intelligence_news_rows
from tyche.market_data.news_store import NewsArticleStore
from tyche.schemas.news import (
    NewsArticleResponse,
    NewsIngestResponse,
    NewsSignalResponse,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/news", tags=["news"])


@router.get("/signals", response_model=list[NewsSignalResponse])
async def list_news_signals(
    settings: TycheSettings = Depends(get_settings),
) -> list[NewsSignalResponse]:
    """Get all tickers with active news signals."""
    published = await asyncio.to_thread(get_intelligence_news_rows, settings=settings)
    if published is not None:
        rows, _layer = published
        threshold = settings.news_risk_threshold
        return [
            row.model_copy(
                update={"has_risk": row.news_impact_score < threshold},
            )
            for row in rows
        ]

    signals = await get_all_signals()
    threshold = settings.news_risk_threshold
    return [
        NewsSignalResponse(
            ticker=s["ticker"],
            news_impact_score=s["news_impact_score"],
            negative_count_24h=s["negative_count_24h"],
            positive_count_24h=s["positive_count_24h"],
            total_count_24h=s["total_count_24h"],
            dominant_event_type=s["dominant_event_type"],
            last_negative_at=s["last_negative_at"],
            last_positive_at=s["last_positive_at"],
            has_risk=s["news_impact_score"] < threshold,
            updated_at=s["updated_at"],
        )
        for s in signals
    ]


@router.get("/signals/{ticker}", response_model=NewsSignalResponse | None)
async def get_ticker_signal(
    ticker: str,
    settings: TycheSettings = Depends(get_settings),
) -> NewsSignalResponse | None:
    """Get the news signal for a single ticker."""
    s = await get_signal(ticker)
    if s is None:
        return None
    threshold = settings.news_risk_threshold
    return NewsSignalResponse(
        ticker=s["ticker"],
        news_impact_score=s["news_impact_score"],
        negative_count_24h=s["negative_count_24h"],
        positive_count_24h=s["positive_count_24h"],
        total_count_24h=s["total_count_24h"],
        dominant_event_type=s["dominant_event_type"],
        last_negative_at=s["last_negative_at"],
        last_positive_at=s["last_positive_at"],
        has_risk=s["news_impact_score"] < threshold,
        updated_at=s["updated_at"],
    )


@router.get("/articles/{ticker}", response_model=list[NewsArticleResponse])
async def get_ticker_articles(
    ticker: str,
    hours: int = Query(default=48, ge=1, le=168),
    store: NewsArticleStore = Depends(get_news_article_store),
) -> list[NewsArticleResponse]:
    """Get recent news articles for a ticker."""
    df = store.read_recent(ticker, hours=hours)
    if df.empty:
        return []

    articles: list[NewsArticleResponse] = []
    for _, row in df.iterrows():
        articles.append(
            NewsArticleResponse(
                article_id=row.get("article_id", ""),
                source=row.get("source", ""),
                title=row.get("title", ""),
                published_at=row.get("published_at"),
                url=row.get("url", ""),
                author=row.get("author"),
                summary=row.get("summary"),
                event_type=row.get("event_type") if _notna(row.get("event_type")) else None,
                sentiment=row.get("sentiment") if _notna(row.get("sentiment")) else None,
                impact_score=(
                    float(row["impact_score"])
                    if _notna(row.get("impact_score"))
                    else None
                ),
                relevance=row.get("relevance") if _notna(row.get("relevance")) else None,
            )
        )
    return articles


@router.post("/ingest")
async def trigger_ingest(
    settings: TycheSettings = Depends(get_settings),
) -> dict[str, str]:
    """Trigger news ingestion in the background.

    Returns immediately; the pipeline runs asynchronously.
    Check /news/signals or backend logs for results.
    """
    from tyche.workflow.news_pipeline import run_news_pipeline

    async def _run() -> None:
        result = await run_news_pipeline(settings)
        logger.info(
            "manual_news_ingest_done",
            polygon=result.polygon_fetched,
            finnhub=result.finnhub_fetched,
            persisted=result.total_persisted,
            classified=result.articles_classified,
            errors=len(result.errors),
        )

    asyncio.create_task(_run())
    return {"status": "started", "message": "News ingestion running in background"}


def _notna(val: object) -> bool:
    """Check if a value is not None/NaN/empty."""
    if val is None:
        return False
    if isinstance(val, float):
        import math
        return not math.isnan(val)
    if isinstance(val, str):
        return val != ""
    return True
