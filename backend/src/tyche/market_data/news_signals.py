"""News signal builder — computes aggregate per-ticker signals from classified articles.

Reads classified articles from NewsArticleStore, applies recency weighting,
and writes/updates NewsSignal rows in news.db via SQLAlchemy.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import structlog
from sqlalchemy import select

from tyche.market_data.news_store import NewsArticleStore
from tyche.models.news import NewsSignal
from tyche.persistence.database import get_session

logger = structlog.get_logger()

_HALF_LIFE_HOURS = 12.0
_LOOKBACK_HOURS = 48


def compute_signal_from_articles(
    ticker: str,
    articles: pd.DataFrame,
    lookback_hours: int = _LOOKBACK_HOURS,
) -> dict:
    """Compute aggregate news signal from classified articles.

    Uses recency-weighted impact scoring with exponential decay.

    Returns:
        Dict ready to construct/update a NewsSignal row.
    """
    now = datetime.now(tz=timezone.utc)
    cutoff_24h = now - timedelta(hours=24)
    cutoff = now - timedelta(hours=lookback_hours)

    classified = articles[
        articles["event_type"].notna()
        & (articles["event_type"] != "")
        & articles["impact_score"].notna()
    ].copy()

    if classified.empty:
        return {
            "ticker": ticker,
            "news_impact_score": 0.0,
            "last_negative_at": None,
            "last_positive_at": None,
            "negative_count_24h": 0,
            "positive_count_24h": 0,
            "total_count_24h": 0,
            "dominant_event_type": None,
            "updated_at": now,
        }

    classified["published_at"] = pd.to_datetime(
        classified["published_at"], utc=True
    )

    recent = classified[classified["published_at"] >= cutoff].copy()

    if recent.empty:
        weighted_impact = 0.0
    else:
        hours_ago = (now - recent["published_at"]).dt.total_seconds() / 3600.0
        weights = np.exp(-np.log(2) * hours_ago / _HALF_LIFE_HOURS)
        weighted_impact = float(
            np.average(recent["impact_score"].values, weights=weights)
        )

    in_24h = classified[classified["published_at"] >= cutoff_24h]
    negative_24h = int((in_24h["sentiment"] == "negative").sum())
    positive_24h = int((in_24h["sentiment"] == "positive").sum())
    total_24h = len(in_24h)

    neg_articles = classified[classified["sentiment"] == "negative"]
    last_negative_at = (
        neg_articles["published_at"].max()
        if not neg_articles.empty
        else None
    )
    if pd.isna(last_negative_at):
        last_negative_at = None

    pos_articles = classified[classified["sentiment"] == "positive"]
    last_positive_at = (
        pos_articles["published_at"].max()
        if not pos_articles.empty
        else None
    )
    if pd.isna(last_positive_at):
        last_positive_at = None

    event_counts = Counter(in_24h["event_type"].dropna().tolist())
    dominant = event_counts.most_common(1)[0][0] if event_counts else None

    return {
        "ticker": ticker,
        "news_impact_score": round(weighted_impact, 4),
        "last_negative_at": (
            last_negative_at.to_pydatetime() if last_negative_at is not None else None
        ),
        "last_positive_at": (
            last_positive_at.to_pydatetime() if last_positive_at is not None else None
        ),
        "negative_count_24h": negative_24h,
        "positive_count_24h": positive_24h,
        "total_count_24h": total_24h,
        "dominant_event_type": dominant,
        "updated_at": now,
    }


async def rebuild_signals(
    store: NewsArticleStore,
    tickers: list[str] | None = None,
    lookback_hours: int = _LOOKBACK_HOURS,
) -> int:
    """Rebuild news signals for given tickers (or all tickers with articles).

    Reads classified articles from the Parquet store, computes signals,
    and upserts into news.db.

    Returns:
        Number of tickers updated.
    """
    if tickers is None:
        tickers = store.list_tickers()

    updated = 0
    async with get_session("news") as session:
        for ticker in tickers:
            articles = store.read_recent(ticker, hours=lookback_hours)
            signal_data = compute_signal_from_articles(
                ticker, articles, lookback_hours
            )

            existing = await session.get(NewsSignal, ticker)
            if existing is not None:
                for key, value in signal_data.items():
                    if key != "ticker":
                        setattr(existing, key, value)
            else:
                session.add(NewsSignal(**signal_data))

            updated += 1

        await session.commit()

    logger.info("news_signals_rebuilt", tickers_updated=updated)
    return updated


async def get_all_signals() -> list[dict]:
    """Read all news signals from the database."""
    async with get_session("news") as session:
        result = await session.execute(select(NewsSignal))
        signals = result.scalars().all()
        return [s.to_dict() for s in signals]


async def get_signal(ticker: str) -> dict | None:
    """Read a single ticker's news signal."""
    async with get_session("news") as session:
        signal = await session.get(NewsSignal, ticker.upper())
        return signal.to_dict() if signal else None
