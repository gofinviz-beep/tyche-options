"""Tests for the NewsClassifier."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tyche.analysis.news_classifier import (
    ArticleClassification,
    NewsClassifier,
    TickerMention,
)


def _mock_gemini():
    mock = MagicMock()
    mock.analyze = AsyncMock()
    return mock


def _make_classification(
    sentiment="negative",
    impact_score=-0.6,
    event_type="operational",
) -> ArticleClassification:
    return ArticleClassification(
        tickers_mentioned=[
            TickerMention(ticker="AAPL", relevance="primary"),
        ],
        event_type=event_type,
        sentiment=sentiment,
        impact_score=impact_score,
        reasoning="Test reasoning.",
    )


class TestNewsClassifier:

    @pytest.mark.asyncio
    async def test_classify_article(self):
        gemini = _mock_gemini()
        cls = _make_classification()
        gemini.analyze.return_value = cls

        classifier = NewsClassifier(gemini=gemini)
        result = await classifier.classify_article(
            title="AAPL pipeline disruption",
            summary="Apple's supply chain was disrupted.",
            published_at="2026-04-10T14:00:00Z",
        )

        assert result.sentiment == "negative"
        assert result.impact_score == pytest.approx(-0.6)
        assert result.event_type == "operational"
        gemini.analyze.assert_called_once()

    @pytest.mark.asyncio
    async def test_classify_batch(self):
        gemini = _mock_gemini()
        gemini.analyze.return_value = _make_classification()

        classifier = NewsClassifier(gemini=gemini, concurrency=2)
        articles = [
            {"article_id": "a1", "title": "Test 1", "summary": "Sum 1", "published_at": "2026-04-10"},
            {"article_id": "a2", "title": "Test 2", "summary": "Sum 2", "published_at": "2026-04-10"},
            {"article_id": "a3", "title": "Test 3", "summary": "Sum 3", "published_at": "2026-04-10"},
        ]

        results = await classifier.classify_batch(articles)

        assert len(results) == 3
        assert "a1" in results
        assert "a2" in results
        assert "a3" in results

    @pytest.mark.asyncio
    async def test_classify_batch_partial_failure(self):
        gemini = _mock_gemini()
        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ValueError("LLM error")
            return _make_classification()

        gemini.analyze = AsyncMock(side_effect=_side_effect)

        classifier = NewsClassifier(gemini=gemini, concurrency=1)
        articles = [
            {"article_id": "a1", "title": "T1", "summary": "S1", "published_at": "2026-04-10"},
            {"article_id": "a2", "title": "T2", "summary": "S2", "published_at": "2026-04-10"},
            {"article_id": "a3", "title": "T3", "summary": "S3", "published_at": "2026-04-10"},
        ]

        results = await classifier.classify_batch(articles)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_impact_score_clamping(self):
        gemini = _mock_gemini()
        cls = _make_classification(impact_score=-1.0)
        cls.impact_score = -1.5  # bypass Pydantic validation to simulate LLM edge case
        gemini.analyze.return_value = cls

        classifier = NewsClassifier(gemini=gemini)
        result = await classifier.classify_article(
            title="Test", summary="Test", published_at="2026-04-10"
        )
        assert result.impact_score == -1.0

    @pytest.mark.asyncio
    async def test_invalid_sentiment_normalized(self):
        gemini = _mock_gemini()
        cls = _make_classification(sentiment="bullish")
        gemini.analyze.return_value = cls

        classifier = NewsClassifier(gemini=gemini)
        result = await classifier.classify_article(
            title="Test", summary="Test", published_at="2026-04-10"
        )
        assert result.sentiment == "neutral"

    @pytest.mark.asyncio
    async def test_empty_batch(self):
        gemini = _mock_gemini()
        classifier = NewsClassifier(gemini=gemini)
        results = await classifier.classify_batch([])
        assert results == {}
