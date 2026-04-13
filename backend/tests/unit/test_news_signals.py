"""Tests for news signal computation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from tyche.market_data.news_signals import compute_signal_from_articles


def _classified_df(
    articles: list[dict],
) -> pd.DataFrame:
    return pd.DataFrame(articles)


def _article(
    article_id: str,
    sentiment: str = "neutral",
    impact_score: float = 0.0,
    event_type: str = "other",
    hours_ago: int = 1,
) -> dict:
    return {
        "article_id": article_id,
        "published_at": datetime.now(tz=timezone.utc) - timedelta(hours=hours_ago),
        "event_type": event_type,
        "sentiment": sentiment,
        "impact_score": impact_score,
    }


class TestComputeSignal:

    def test_empty_articles(self):
        df = pd.DataFrame(columns=["article_id", "published_at", "event_type", "sentiment", "impact_score"])
        result = compute_signal_from_articles("AAPL", df)
        assert result["ticker"] == "AAPL"
        assert result["news_impact_score"] == 0.0
        assert result["negative_count_24h"] == 0

    def test_single_negative_article(self):
        df = _classified_df([_article("a1", sentiment="negative", impact_score=-0.7)])
        result = compute_signal_from_articles("AAPL", df)
        assert result["news_impact_score"] < 0
        assert result["negative_count_24h"] == 1
        assert result["last_negative_at"] is not None

    def test_single_positive_article(self):
        df = _classified_df([_article("a1", sentiment="positive", impact_score=0.5)])
        result = compute_signal_from_articles("AAPL", df)
        assert result["news_impact_score"] > 0
        assert result["positive_count_24h"] == 1
        assert result["last_positive_at"] is not None

    def test_mixed_articles(self):
        df = _classified_df([
            _article("a1", sentiment="negative", impact_score=-0.8, hours_ago=1),
            _article("a2", sentiment="positive", impact_score=0.3, hours_ago=2),
            _article("a3", sentiment="neutral", impact_score=0.0, hours_ago=3),
        ])
        result = compute_signal_from_articles("AAPL", df)
        assert result["negative_count_24h"] == 1
        assert result["positive_count_24h"] == 1
        assert result["total_count_24h"] == 3

    def test_recency_weighting(self):
        recent_neg = _article("a1", sentiment="negative", impact_score=-0.8, hours_ago=1)
        old_pos = _article("a2", sentiment="positive", impact_score=0.3, hours_ago=40)
        df = _classified_df([recent_neg, old_pos])
        result = compute_signal_from_articles("AAPL", df)
        assert result["news_impact_score"] < 0

    def test_dominant_event_type(self):
        df = _classified_df([
            _article("a1", event_type="earnings", hours_ago=1),
            _article("a2", event_type="earnings", hours_ago=2),
            _article("a3", event_type="regulatory", hours_ago=3),
        ])
        result = compute_signal_from_articles("AAPL", df)
        assert result["dominant_event_type"] == "earnings"

    def test_unclassified_articles_ignored(self):
        df = _classified_df([
            {"article_id": "a1", "published_at": datetime.now(tz=timezone.utc), "event_type": None, "sentiment": None, "impact_score": None},
            _article("a2", sentiment="negative", impact_score=-0.5),
        ])
        result = compute_signal_from_articles("AAPL", df)
        assert result["total_count_24h"] == 1

    def test_old_articles_outside_lookback(self):
        df = _classified_df([
            _article("a1", sentiment="negative", impact_score=-0.9, hours_ago=100),
        ])
        result = compute_signal_from_articles("AAPL", df, lookback_hours=48)
        assert result["news_impact_score"] == 0.0

    def test_updated_at_present(self):
        df = _classified_df([_article("a1")])
        result = compute_signal_from_articles("AAPL", df)
        assert result["updated_at"] is not None
        assert isinstance(result["updated_at"], datetime)
