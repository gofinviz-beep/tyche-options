"""Tests for the 8-K filing classifier extension."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tyche.analysis.news_classifier import (
    ArticleClassification,
    Batch8KClassification,
    NewsClassifier,
    TickerMention,
    _8K_ITEM_MAP,
)


@pytest.fixture
def classifier():
    gemini = MagicMock()
    gemini.analyze = AsyncMock()
    gemini.analyze_batch = AsyncMock()
    return NewsClassifier(gemini=gemini, workers=1, rpm=1000)


class TestEightKClassifySingle:
    @pytest.mark.asyncio
    async def test_classify_8k_filing(self, classifier):
        expected = ArticleClassification(
            tickers_mentioned=[TickerMention(ticker="AAPL", relevance="primary")],
            event_type="financial_results",
            sentiment="positive",
            impact_score=0.6,
            reasoning="Strong earnings beat.",
        )
        classifier._gemini.analyze = AsyncMock(return_value=expected)

        result = await classifier.classify_8k_filing(
            ticker="AAPL",
            form_type="8-K",
            filed_at="2026-04-10",
            description="Results of Operations",
            items_reported="2.02",
            content="Revenue exceeded expectations...",
        )

        assert result.event_type == "financial_results"
        assert result.sentiment == "positive"
        assert result.impact_score == 0.6
        call_kwargs = classifier._gemini.analyze.call_args.kwargs
        assert call_kwargs["model_override"] == "gemini-2.5-flash-lite"

    @pytest.mark.asyncio
    async def test_clamps_impact_score(self, classifier):
        raw = ArticleClassification(
            tickers_mentioned=[],
            event_type="regulatory",
            sentiment="negative",
            impact_score=-1.0,
            reasoning="Critical regulatory issue.",
        )
        raw.impact_score = -1.5
        classifier._gemini.analyze = AsyncMock(return_value=raw)

        result = await classifier.classify_8k_filing(
            ticker="AAPL",
            form_type="8-K",
            filed_at="2026-04-10",
            description="Test",
            items_reported="",
            content="Test",
        )

        assert result.impact_score == -1.0

    @pytest.mark.asyncio
    async def test_normalizes_invalid_sentiment(self, classifier):
        raw = ArticleClassification(
            tickers_mentioned=[],
            event_type="other",
            sentiment="bullish",
            impact_score=0.3,
            reasoning="Test.",
        )
        classifier._gemini.analyze = AsyncMock(return_value=raw)

        result = await classifier.classify_8k_filing(
            ticker="AAPL",
            form_type="8-K",
            filed_at="2026-04-10",
            description="Test",
            items_reported="",
            content="Test",
        )

        assert result.sentiment == "neutral"


class TestEightKBatchClassify:
    @pytest.mark.asyncio
    async def test_single_filing_uses_single_call(self, classifier):
        """A batch of 1 filing should use classify_8k_filing (single call)."""
        expected = ArticleClassification(
            tickers_mentioned=[],
            event_type="executive",
            sentiment="negative",
            impact_score=-0.4,
            reasoning="CEO departure.",
        )
        classifier._gemini.analyze = AsyncMock(return_value=expected)

        filings = [
            {
                "accession_no": "acc1",
                "ticker": "AAPL",
                "form_type": "8-K",
                "filed_at": "2026-04-10",
                "description": "Change of officer",
                "items_reported": "5.02",
                "content_summary": "CEO resigned...",
            },
        ]

        results = await classifier.classify_8k_batch(filings)

        assert len(results) == 1
        assert "acc1" in results
        classifier._gemini.analyze.assert_called_once()
        classifier._gemini.analyze_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_uses_analyze_batch(self, classifier):
        """Multiple filings should use the batch API call."""
        classifier._gemini.analyze_batch = AsyncMock(return_value=[
            Batch8KClassification(
                accession_no="acc1",
                tickers_mentioned=[],
                event_type="executive",
                sentiment="negative",
                impact_score=-0.4,
                reasoning="CEO departure.",
            ),
            Batch8KClassification(
                accession_no="acc2",
                tickers_mentioned=[],
                event_type="material_agreement",
                sentiment="positive",
                impact_score=0.3,
                reasoning="New partnership.",
            ),
        ])

        filings = [
            {
                "accession_no": "acc1",
                "ticker": "AAPL",
                "form_type": "8-K",
                "filed_at": "2026-04-10",
                "description": "Change of officer",
                "items_reported": "5.02",
                "content_summary": "CEO resigned...",
            },
            {
                "accession_no": "acc2",
                "ticker": "MSFT",
                "form_type": "8-K",
                "filed_at": "2026-04-11",
                "description": "Material agreement",
                "items_reported": "1.01",
                "content_summary": "New partnership...",
            },
        ]

        results = await classifier.classify_8k_batch(filings)

        assert len(results) == 2
        assert "acc1" in results
        assert "acc2" in results
        classifier._gemini.analyze_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_batch_handles_errors(self, classifier):
        classifier._gemini.analyze = AsyncMock(side_effect=Exception("LLM error"))

        filings = [
            {
                "accession_no": "fail",
                "ticker": "AAPL",
                "form_type": "8-K",
                "filed_at": "2026-04-10",
                "description": "test",
                "items_reported": "",
                "content_summary": "test",
            },
        ]

        results = await classifier.classify_8k_batch(filings)

        assert "fail" not in results


class TestItemMap:
    def test_key_items_mapped(self):
        assert _8K_ITEM_MAP["2.02"] == "financial_results"
        assert _8K_ITEM_MAP["1.01"] == "material_agreement"
        assert _8K_ITEM_MAP["5.02"] == "executive"
        assert _8K_ITEM_MAP["4.02"] == "financial_results"
        assert _8K_ITEM_MAP["2.01"] == "m_and_a"
