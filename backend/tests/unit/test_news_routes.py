"""Tests for the News API routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tyche.api import deps
from tyche.app import create_app
from tyche.broker.mock import MockBroker
from tyche.market_data.news_store import NewsArticleStore


@pytest.fixture(autouse=True)
def _reset_deps():
    deps.reset_all()
    yield
    deps.reset_all()


@pytest.fixture
def store(tmp_path):
    return NewsArticleStore(data_dir=str(tmp_path))


@pytest.fixture
def client(store, settings, tmp_path) -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.get_broker] = lambda: MockBroker()
    app.dependency_overrides[deps.get_analysis_agent] = lambda: None
    app.dependency_overrides[deps.get_news_article_store] = lambda: store
    # Isolate from the real .env (TYCHE_DATA_BACKEND=gcs on this laptop) and
    # from real local data dirs — without this, the published-route read in
    # list_news_signals hits real GCS or serves real leftover local data,
    # bypassing the mocked get_all_signals below.
    isolated = settings.model_copy(
        update={
            "data_backend": "local",
            "data_dir": str(tmp_path),
            "db_dir": str(tmp_path),
            "api_prefer_published_signals": False,
            "api_allow_local_db_fallback": True,
        }
    )
    app.dependency_overrides[deps.get_settings] = lambda: isolated
    return TestClient(app)


class TestNewsSignalsEndpoint:

    @patch("tyche.api.routes.news.get_all_signals", new_callable=AsyncMock)
    def test_list_signals_empty(self, mock_signals, client):
        mock_signals.return_value = []
        resp = client.get("/api/v1/news/signals")
        assert resp.status_code == 200
        assert resp.json() == []

    @patch("tyche.api.routes.news.get_all_signals", new_callable=AsyncMock)
    def test_list_signals_with_risk(self, mock_signals, client):
        mock_signals.return_value = [
            {
                "ticker": "XOM",
                "news_impact_score": -0.5,
                "negative_count_24h": 3,
                "positive_count_24h": 0,
                "total_count_24h": 3,
                "dominant_event_type": "operational",
                "last_negative_at": datetime.now(tz=timezone.utc).isoformat(),
                "last_positive_at": None,
                "updated_at": datetime.now(tz=timezone.utc).isoformat(),
            }
        ]
        resp = client.get("/api/v1/news/signals")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["ticker"] == "XOM"
        assert data[0]["has_risk"] is True

    @patch("tyche.api.routes.news.get_all_signals", new_callable=AsyncMock)
    def test_list_signals_no_risk(self, mock_signals, client):
        mock_signals.return_value = [
            {
                "ticker": "AAPL",
                "news_impact_score": 0.2,
                "negative_count_24h": 0,
                "positive_count_24h": 2,
                "total_count_24h": 2,
                "dominant_event_type": "product",
                "last_negative_at": None,
                "last_positive_at": datetime.now(tz=timezone.utc).isoformat(),
                "updated_at": datetime.now(tz=timezone.utc).isoformat(),
            }
        ]
        resp = client.get("/api/v1/news/signals")
        data = resp.json()
        assert data[0]["has_risk"] is False


class TestNewsSignalEndpoint:

    @patch("tyche.api.routes.news.get_signal", new_callable=AsyncMock)
    def test_get_signal(self, mock_signal, client):
        mock_signal.return_value = {
            "ticker": "AAPL",
            "news_impact_score": -0.4,
            "negative_count_24h": 2,
            "positive_count_24h": 0,
            "total_count_24h": 2,
            "dominant_event_type": "regulatory",
            "last_negative_at": None,
            "last_positive_at": None,
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        resp = client.get("/api/v1/news/signals/AAPL")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ticker"] == "AAPL"
        assert data["has_risk"] is True

    @patch("tyche.api.routes.news.get_signal", new_callable=AsyncMock)
    def test_get_signal_not_found(self, mock_signal, client):
        mock_signal.return_value = None
        resp = client.get("/api/v1/news/signals/UNKNOWN")
        assert resp.status_code == 200
        assert resp.json() is None


class TestNewsArticlesEndpoint:

    def test_get_articles_empty(self, client, store):
        resp = client.get("/api/v1/news/articles/AAPL")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_articles_with_data(self, client, store):
        store.write_articles("AAPL", [
            {
                "article_id": "a1",
                "source": "polygon",
                "ticker": "AAPL",
                "published_at": datetime.now(tz=timezone.utc) - timedelta(hours=1),
                "title": "AAPL beats earnings",
                "url": "https://example.com/a1",
                "author": "Author",
                "summary": "Apple did well.",
            }
        ])

        resp = client.get("/api/v1/news/articles/AAPL")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["article_id"] == "a1"
        assert data[0]["title"] == "AAPL beats earnings"

    def test_get_articles_hours_param(self, client, store):
        store.write_articles("AAPL", [
            {
                "article_id": "old",
                "source": "polygon",
                "ticker": "AAPL",
                "published_at": datetime.now(tz=timezone.utc) - timedelta(hours=72),
                "title": "Old article",
                "url": "https://example.com/old",
                "author": "Author",
                "summary": "Old.",
            },
            {
                "article_id": "new",
                "source": "polygon",
                "ticker": "AAPL",
                "published_at": datetime.now(tz=timezone.utc) - timedelta(hours=1),
                "title": "New article",
                "url": "https://example.com/new",
                "author": "Author",
                "summary": "New.",
            },
        ])

        resp = client.get("/api/v1/news/articles/AAPL?hours=24")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["article_id"] == "new"
