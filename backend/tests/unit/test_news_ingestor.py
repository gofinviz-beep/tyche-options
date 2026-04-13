"""Tests for the NewsIngestor."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from tyche.market_data.finnhub import FinnhubArticle
from tyche.market_data.news_ingestor import NewsIngestor
from tyche.market_data.news_store import NewsArticleStore
from tyche.market_data.polygon import NewsArticle as PolygonNewsArticle


def _polygon_article(article_id="1", tickers=None):
    return PolygonNewsArticle(
        id=article_id,
        title=f"Polygon article {article_id}",
        author="Author",
        published_utc="2026-04-10T14:00:00Z",
        article_url=f"https://example.com/{article_id}",
        tickers=tickers or ["AAPL"],
        description="Test polygon article.",
    )


def _finnhub_article(article_id="100", ticker="AAPL"):
    return FinnhubArticle(
        id=article_id,
        headline=f"Finnhub article {article_id}",
        source="Reuters",
        url=f"https://example.com/fh_{article_id}",
        summary="Test finnhub article.",
        datetime_ts=1712761200,
        related=ticker,
        category="company",
    )


class TestNewsIngestor:

    @pytest.fixture
    def store(self, tmp_path):
        return NewsArticleStore(data_dir=str(tmp_path))

    @pytest.fixture
    def polygon(self):
        mock = MagicMock()
        mock.get_news = AsyncMock(return_value=[])
        return mock

    @pytest.fixture
    def finnhub(self):
        mock = MagicMock()
        mock.get_company_news = AsyncMock(return_value=[])
        return mock

    @pytest.mark.asyncio
    async def test_ingest_polygon_only(self, store, polygon):
        polygon.get_news.return_value = [
            _polygon_article("1", tickers=["AAPL"]),
            _polygon_article("2", tickers=["MSFT"]),
        ]

        ingestor = NewsIngestor(
            polygon=polygon, finnhub=None, store=store, tickers=["AAPL", "MSFT"]
        )
        result = await ingestor.ingest()

        assert result.polygon_fetched == 2
        assert result.finnhub_fetched == 0
        assert result.tickers_updated == 2
        assert store.read_articles("AAPL") is not None

    @pytest.mark.asyncio
    async def test_ingest_finnhub_only(self, store, finnhub):
        finnhub.get_company_news.return_value = [_finnhub_article("100")]

        ingestor = NewsIngestor(
            polygon=None, finnhub=finnhub, store=store, tickers=["AAPL"]
        )
        result = await ingestor.ingest()

        assert result.finnhub_fetched == 1
        assert result.tickers_updated == 1

    @pytest.mark.asyncio
    async def test_ingest_both_sources(self, store, polygon, finnhub):
        polygon.get_news.return_value = [_polygon_article("1", tickers=["AAPL"])]
        finnhub.get_company_news.return_value = [_finnhub_article("100")]

        ingestor = NewsIngestor(
            polygon=polygon, finnhub=finnhub, store=store, tickers=["AAPL"]
        )
        result = await ingestor.ingest()

        assert result.polygon_fetched == 1
        assert result.finnhub_fetched == 1

        df = store.read_articles("AAPL")
        assert len(df) == 2

    @pytest.mark.asyncio
    async def test_multi_ticker_article(self, store, polygon):
        polygon.get_news.return_value = [
            _polygon_article("1", tickers=["AAPL", "MSFT"]),
        ]

        ingestor = NewsIngestor(
            polygon=polygon, finnhub=None, store=store, tickers=["AAPL", "MSFT"]
        )
        result = await ingestor.ingest()

        aapl_df = store.read_articles("AAPL")
        msft_df = store.read_articles("MSFT")
        assert len(aapl_df) == 1
        assert len(msft_df) == 1

    @pytest.mark.asyncio
    async def test_filters_to_universe(self, store, polygon):
        polygon.get_news.return_value = [
            _polygon_article("1", tickers=["AAPL"]),
            _polygon_article("2", tickers=["UNKNOWN_TICKER"]),
        ]

        ingestor = NewsIngestor(
            polygon=polygon, finnhub=None, store=store, tickers=["AAPL"]
        )
        result = await ingestor.ingest()

        assert result.tickers_updated == 1
        df = store.read_articles("UNKNOWN_TICKER")
        assert df.empty

    @pytest.mark.asyncio
    async def test_polygon_error_handled(self, store, polygon):
        polygon.get_news = AsyncMock(side_effect=Exception("API down"))

        ingestor = NewsIngestor(
            polygon=polygon, finnhub=None, store=store, tickers=["AAPL"]
        )
        result = await ingestor.ingest()

        assert result.polygon_fetched == 0
        assert len(result.errors) == 1
        assert "API down" in result.errors[0]

    @pytest.mark.asyncio
    async def test_finnhub_per_ticker_error_handled(self, store, finnhub):
        finnhub.get_company_news = AsyncMock(side_effect=Exception("Rate limited"))

        ingestor = NewsIngestor(
            polygon=None, finnhub=finnhub, store=store, tickers=["AAPL"]
        )
        result = await ingestor.ingest()

        assert result.finnhub_fetched == 0
        assert len(result.errors) >= 1

    @pytest.mark.asyncio
    async def test_no_sources(self, store):
        ingestor = NewsIngestor(
            polygon=None, finnhub=None, store=store, tickers=["AAPL"]
        )
        result = await ingestor.ingest()

        assert result.polygon_fetched == 0
        assert result.finnhub_fetched == 0
        assert result.tickers_updated == 0

    @pytest.mark.asyncio
    async def test_dedup_within_source(self, store, polygon):
        polygon.get_news.return_value = [
            _polygon_article("1", tickers=["AAPL"]),
            _polygon_article("1", tickers=["AAPL"]),
        ]

        ingestor = NewsIngestor(
            polygon=polygon, finnhub=None, store=store, tickers=["AAPL"]
        )
        result = await ingestor.ingest()

        df = store.read_articles("AAPL")
        assert len(df) == 1
