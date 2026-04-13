"""Tests for the NewsClassifier — single article, batch, queue workers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tyche.analysis.news_classifier import (
    ArticleClassification,
    BatchArticleClassification,
    NewsClassifier,
    TickerMention,
    _chunked,
    _sanitize,
)


def _mock_gemini():
    mock = MagicMock()
    mock.analyze = AsyncMock()
    mock.analyze_batch = AsyncMock()
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


class TestSingleArticle:

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
        call_kwargs = gemini.analyze.call_args.kwargs
        assert call_kwargs["model_override"] == "gemini-2.5-flash-lite"

    @pytest.mark.asyncio
    async def test_custom_classify_model(self):
        gemini = _mock_gemini()
        gemini.analyze.return_value = _make_classification()

        classifier = NewsClassifier(gemini=gemini, classify_model="gemini-2.0-flash")
        await classifier.classify_article(
            title="Test", summary="Test", published_at="2026-04-10"
        )

        call_kwargs = gemini.analyze.call_args.kwargs
        assert call_kwargs["model_override"] == "gemini-2.0-flash"

    @pytest.mark.asyncio
    async def test_impact_score_clamping(self):
        gemini = _mock_gemini()
        cls = _make_classification(impact_score=-1.0)
        cls.impact_score = -1.5
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


class TestBatchClassification:

    @pytest.mark.asyncio
    async def test_single_article_uses_single_call(self):
        """A batch of 1 article should use classify_article (single call)."""
        gemini = _mock_gemini()
        gemini.analyze.return_value = _make_classification()

        classifier = NewsClassifier(gemini=gemini, workers=1, rpm=1000)
        articles = [
            {"article_id": "a1", "title": "Test 1", "summary": "Sum 1", "published_at": "2026-04-10"},
        ]

        results = await classifier.classify_batch(articles)

        assert len(results) == 1
        assert "a1" in results
        gemini.analyze.assert_called_once()
        gemini.analyze_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_uses_analyze_batch(self):
        """Multiple articles should use the batch API call."""
        gemini = _mock_gemini()
        gemini.analyze_batch.return_value = [
            BatchArticleClassification(
                article_id="a1",
                tickers_mentioned=[TickerMention(ticker="AAPL", relevance="primary")],
                event_type="operational",
                sentiment="negative",
                impact_score=-0.5,
                reasoning="Test.",
            ),
            BatchArticleClassification(
                article_id="a2",
                tickers_mentioned=[],
                event_type="other",
                sentiment="neutral",
                impact_score=0.0,
                reasoning="Neutral.",
            ),
        ]

        classifier = NewsClassifier(gemini=gemini, workers=1, rpm=1000)
        articles = [
            {"article_id": "a1", "title": "Test 1", "summary": "Sum 1", "published_at": "2026-04-10"},
            {"article_id": "a2", "title": "Test 2", "summary": "Sum 2", "published_at": "2026-04-10"},
        ]

        results = await classifier.classify_batch(articles)

        assert len(results) == 2
        assert "a1" in results
        assert "a2" in results
        assert results["a1"].event_type == "operational"
        assert results["a2"].sentiment == "neutral"
        gemini.analyze_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_batch(self):
        gemini = _mock_gemini()
        classifier = NewsClassifier(gemini=gemini)
        results = await classifier.classify_batch([])
        assert results == {}

    @pytest.mark.asyncio
    async def test_batch_partial_failure(self):
        """If one chunk fails, other chunks still succeed."""
        gemini = _mock_gemini()
        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("LLM error")
            return [
                BatchArticleClassification(
                    article_id="a2",
                    tickers_mentioned=[],
                    event_type="other",
                    sentiment="neutral",
                    impact_score=0.0,
                    reasoning="OK.",
                ),
            ]

        gemini.analyze.side_effect = ValueError("LLM error")
        gemini.analyze_batch.side_effect = _side_effect

        classifier = NewsClassifier(gemini=gemini, workers=1, rpm=1000)
        articles = [
            {"article_id": "a1", "title": "T1", "summary": "S1", "published_at": "2026-04-10"},
        ]

        results = await classifier.classify_batch(articles)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_large_batch_is_chunked(self):
        """15 articles should produce 2 chunks (10 + 5)."""
        gemini = _mock_gemini()
        batch_calls = []

        async def _track_batch(*args, **kwargs):
            prompt = args[0] if args else kwargs.get("prompt", "")
            ids = []
            for line in prompt.split("\n"):
                if line.strip().startswith("article_id:"):
                    ids.append(line.strip().split(":", 1)[1].strip())
            batch_calls.append(len(ids))
            return [
                BatchArticleClassification(
                    article_id=aid,
                    tickers_mentioned=[],
                    event_type="other",
                    sentiment="neutral",
                    impact_score=0.0,
                    reasoning="OK.",
                )
                for aid in ids
            ]

        gemini.analyze_batch.side_effect = _track_batch

        classifier = NewsClassifier(gemini=gemini, workers=2, rpm=1000)
        articles = [
            {"article_id": f"a{i}", "title": f"T{i}", "summary": f"S{i}", "published_at": "2026-04-10"}
            for i in range(15)
        ]

        results = await classifier.classify_batch(articles)

        assert len(batch_calls) == 2
        assert sorted(batch_calls) == [5, 10]
        assert len(results) == 15


class TestHelpers:

    def test_chunked(self):
        assert _chunked([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
        assert _chunked([], 10) == []
        assert _chunked([1], 10) == [[1]]

    def test_sanitize_clamps(self):
        cls = _make_classification(impact_score=-1.0)
        cls.impact_score = 2.0
        _sanitize(cls)
        assert cls.impact_score == 1.0

    def test_sanitize_normalizes_sentiment(self):
        cls = _make_classification(sentiment="bullish")
        _sanitize(cls)
        assert cls.sentiment == "neutral"
