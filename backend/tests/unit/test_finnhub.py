"""Tests for the FinnhubClient."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tyche.market_data.finnhub import FinnhubClient


@pytest.fixture
def client():
    return FinnhubClient(api_key="test_key", rate_limit_rpm=600, max_retries=1)


class TestFinnhubClient:

    @pytest.mark.asyncio
    async def test_get_company_news_success(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "id": 12345,
                "headline": "AAPL beats earnings",
                "source": "Reuters",
                "url": "https://example.com/aapl",
                "summary": "Apple reported strong Q1 results.",
                "datetime": 1712000000,
                "related": "AAPL",
                "category": "company",
            },
            {
                "id": 12346,
                "headline": "AAPL new product",
                "source": "Bloomberg",
                "url": "https://example.com/aapl2",
                "summary": "Apple launched a new product line.",
                "datetime": 1712001000,
                "related": "AAPL",
                "category": "company",
            },
        ]

        with patch("tyche.market_data.finnhub.httpx.AsyncClient") as mock_cls:
            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client_instance

            articles = await client.get_company_news(
                "AAPL", date(2026, 4, 1), date(2026, 4, 10)
            )

        assert len(articles) == 2
        assert articles[0].id == "12345"
        assert articles[0].headline == "AAPL beats earnings"
        assert articles[1].source == "Bloomberg"

    @pytest.mark.asyncio
    async def test_get_company_news_empty(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []

        with patch("tyche.market_data.finnhub.httpx.AsyncClient") as mock_cls:
            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client_instance

            articles = await client.get_company_news(
                "AAPL", date(2026, 4, 1), date(2026, 4, 10)
            )

        assert articles == []

    @pytest.mark.asyncio
    async def test_get_company_news_non_list_response(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"error": "invalid"}

        with patch("tyche.market_data.finnhub.httpx.AsyncClient") as mock_cls:
            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client_instance

            articles = await client.get_company_news(
                "AAPL", date(2026, 4, 1), date(2026, 4, 10)
            )

        assert articles == []

    @pytest.mark.asyncio
    async def test_api_error_raises(self, client):
        from tyche.exceptions import FinnhubAPIError

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        with patch("tyche.market_data.finnhub.httpx.AsyncClient") as mock_cls:
            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client_instance

            with pytest.raises(FinnhubAPIError):
                await client.get_company_news(
                    "AAPL", date(2026, 4, 1), date(2026, 4, 10)
                )
