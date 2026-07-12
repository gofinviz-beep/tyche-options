"""Tests for the EDGAR Filing API routes."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from tyche.api import deps
from tyche.app import create_app
from tyche.broker.mock import MockBroker
from tyche.market_data.filing_store import Filing8KStore, InsiderTxStore


@pytest.fixture(autouse=True)
def _reset_deps():
    deps.reset_all()
    yield
    deps.reset_all()


@pytest.fixture
def filing_store(tmp_path):
    return Filing8KStore(data_dir=str(tmp_path))


@pytest.fixture
def insider_store(tmp_path):
    return InsiderTxStore(data_dir=str(tmp_path))


@pytest.fixture
def client(filing_store, insider_store, settings, tmp_path) -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.get_broker] = lambda: MockBroker()
    app.dependency_overrides[deps.get_analysis_agent] = lambda: None
    app.dependency_overrides[deps.get_filing_8k_store] = lambda: filing_store
    app.dependency_overrides[deps.get_insider_tx_store] = lambda: insider_store
    # Isolate from the real .env (TYCHE_DATA_BACKEND=gcs on this laptop) and
    # from real local data dirs — without this, the published-route read in
    # list_filing_signals hits real GCS or serves real leftover local data,
    # bypassing the mocked get_all_filing_signals below.
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


class TestFilingSignalsEndpoint:
    @patch("tyche.api.routes.filings.get_all_filing_signals", new_callable=AsyncMock)
    def test_list_signals_empty(self, mock_signals, client):
        mock_signals.return_value = []
        resp = client.get("/api/v1/filings/signals")
        assert resp.status_code == 200
        assert resp.json() == []

    @patch("tyche.api.routes.filings.get_all_filing_signals", new_callable=AsyncMock)
    def test_list_signals_with_cluster_sell(self, mock_signals, client):
        mock_signals.return_value = [
            {
                "ticker": "AAPL",
                "last_8k_at": None,
                "last_8k_sentiment": None,
                "last_8k_impact": None,
                "eightk_count_30d": 0,
                "insider_net_shares_30d": -15000.0,
                "insider_buy_count_30d": 0,
                "insider_sell_count_30d": 4,
                "insider_cluster_sell": True,
                "last_insider_tx_at": datetime.now(tz=timezone.utc).isoformat(),
                "updated_at": datetime.now(tz=timezone.utc).isoformat(),
            }
        ]

        resp = client.get("/api/v1/filings/signals")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["ticker"] == "AAPL"
        assert data[0]["insider_cluster_sell"] is True
        assert data[0]["has_risk"] is True

    @patch("tyche.api.routes.filings.get_all_filing_signals", new_callable=AsyncMock)
    def test_list_signals_with_negative_8k(self, mock_signals, client):
        mock_signals.return_value = [
            {
                "ticker": "MSFT",
                "last_8k_at": datetime.now(tz=timezone.utc).isoformat(),
                "last_8k_sentiment": "negative",
                "last_8k_impact": -0.7,
                "eightk_count_30d": 1,
                "insider_net_shares_30d": 0.0,
                "insider_buy_count_30d": 0,
                "insider_sell_count_30d": 0,
                "insider_cluster_sell": False,
                "last_insider_tx_at": None,
                "updated_at": datetime.now(tz=timezone.utc).isoformat(),
            }
        ]

        resp = client.get("/api/v1/filings/signals")
        data = resp.json()
        assert data[0]["has_risk"] is True

    @patch("tyche.api.routes.filings.get_all_filing_signals", new_callable=AsyncMock)
    def test_no_risk_when_neutral(self, mock_signals, client):
        mock_signals.return_value = [
            {
                "ticker": "GOOG",
                "last_8k_at": None,
                "last_8k_sentiment": None,
                "last_8k_impact": None,
                "eightk_count_30d": 0,
                "insider_net_shares_30d": 1000.0,
                "insider_buy_count_30d": 2,
                "insider_sell_count_30d": 0,
                "insider_cluster_sell": False,
                "last_insider_tx_at": None,
                "updated_at": datetime.now(tz=timezone.utc).isoformat(),
            }
        ]

        resp = client.get("/api/v1/filings/signals")
        data = resp.json()
        assert data[0]["has_risk"] is False


class TestSingleSignalEndpoint:
    @patch("tyche.api.routes.filings.get_filing_signal", new_callable=AsyncMock)
    def test_signal_not_found(self, mock_signal, client):
        mock_signal.return_value = None
        resp = client.get("/api/v1/filings/signals/ZZZZ")
        assert resp.status_code == 200
        assert resp.json() is None

    @patch("tyche.api.routes.filings.get_filing_signal", new_callable=AsyncMock)
    def test_signal_found(self, mock_signal, client):
        mock_signal.return_value = {
            "ticker": "AAPL",
            "last_8k_at": None,
            "last_8k_sentiment": None,
            "last_8k_impact": None,
            "eightk_count_30d": 2,
            "insider_net_shares_30d": 0.0,
            "insider_buy_count_30d": 0,
            "insider_sell_count_30d": 0,
            "insider_cluster_sell": False,
            "last_insider_tx_at": None,
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        resp = client.get("/api/v1/filings/signals/AAPL")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ticker"] == "AAPL"
        assert data["eightk_count_30d"] == 2


class TestFiling8KEndpoint:
    def test_empty_filings(self, client):
        resp = client.get("/api/v1/filings/8k/AAPL")
        assert resp.status_code == 200
        assert resp.json() == []


class TestInsiderTxEndpoint:
    def test_empty_transactions(self, client):
        resp = client.get("/api/v1/filings/insider/AAPL")
        assert resp.status_code == 200
        assert resp.json() == []
