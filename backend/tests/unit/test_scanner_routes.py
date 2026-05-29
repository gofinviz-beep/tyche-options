"""Tests for scanner API routes with mocked persistence.

Tests the HTTP layer: /scan, /latest, /history, /{scan_id} endpoints,
using mocked scan_repository functions to avoid needing real DBs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from tyche.api import deps
from tyche.app import create_app
from tyche.broker.mock import MockBroker


@pytest.fixture(autouse=True)
def _reset_deps():
    deps.reset_all()
    yield
    deps.reset_all()


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.get_broker] = lambda: MockBroker()
    app.dependency_overrides[deps.get_analysis_agent] = lambda: None
    return TestClient(app)


_MOCK_SCAN_RESULT = {
    "scan_id": "test-scan-123",
    "scanned_at": "2026-03-29T10:00:00+00:00",
    "symbols_scanned": 50,
    "pipeline_stages": [
        {"name": "Fundamental Screen", "input": 50, "output": 40, "dropped": 10, "detail": ""}
    ],
    "conviction_signals": {},
    "csp_candidates": [
        {
            "symbol": "AAPL",
            "option_symbol": "AAPL260410P00200000",
            "strike": 200.0,
            "expiration": "2026-04-10",
            "dte": 12,
            "bid": 3.50,
            "ask": 3.80,
            "premium_per_contract": 350.0,
            "collateral_required": 20000.0,
            "annualized_return_pct": 22.5,
            "score": 8.2,
            "delta": -0.28,
            "theta": -0.04,
            "implied_volatility": 0.32,
            "volume": 1200,
            "open_interest": 5000,
            "earnings_within_dte": False,
            "earnings_date": None,
        }
    ],
    "cc_candidates": [],
    "llm_analyses": [],
    "earnings_context": {},
    "institutional_ownership": {},
    "allocation": None,
    "allocated_trades": [],
    "errors": [],
}

_MOCK_HISTORY = [
    {
        "scan_id": "scan-001",
        "scanned_at": "2026-03-29T10:00:00+00:00",
        "trigger": "manual",
        "symbols_scanned": 50,
        "csp_candidate_count": 5,
        "cc_candidate_count": 0,
        "llm_analysis_count": 2,
        "errors_count": 0,
    },
    {
        "scan_id": "scan-002",
        "scanned_at": "2026-03-28T09:35:00+00:00",
        "trigger": "scheduled",
        "symbols_scanned": 2300,
        "csp_candidate_count": 36,
        "cc_candidate_count": 0,
        "llm_analysis_count": 5,
        "errors_count": 1,
    },
]


class TestLatestEndpoint:
    @patch("tyche.api.routes.scanner.load_latest", new_callable=AsyncMock)
    def test_latest_returns_null_when_empty(self, mock_load, client: TestClient) -> None:
        mock_load.return_value = None
        resp = client.get("/api/v1/scanner/latest")
        assert resp.status_code == 200
        assert resp.json() is None

    @patch("tyche.api.routes.scanner.load_latest", new_callable=AsyncMock)
    def test_latest_returns_scan(self, mock_load, client: TestClient) -> None:
        mock_load.return_value = _MOCK_SCAN_RESULT
        resp = client.get("/api/v1/scanner/latest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["scan_id"] == "test-scan-123"
        assert data["symbols_scanned"] == 50
        assert len(data["csp_candidates"]) == 1


class TestHistoryEndpoint:
    @patch("tyche.api.routes.scanner.load_history", new_callable=AsyncMock)
    def test_history_returns_list(self, mock_history, client: TestClient) -> None:
        mock_history.return_value = _MOCK_HISTORY
        resp = client.get("/api/v1/scanner/history")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["scan_id"] == "scan-001"
        assert data[1]["trigger"] == "scheduled"

    @patch("tyche.api.routes.scanner.load_history", new_callable=AsyncMock)
    def test_history_respects_limit_param(self, mock_history, client: TestClient) -> None:
        mock_history.return_value = [_MOCK_HISTORY[0]]
        resp = client.get("/api/v1/scanner/history?limit=1")
        assert resp.status_code == 200
        mock_history.assert_called_once_with(limit=1)

    @patch("tyche.api.routes.scanner.load_history", new_callable=AsyncMock)
    def test_history_empty(self, mock_history, client: TestClient) -> None:
        mock_history.return_value = []
        resp = client.get("/api/v1/scanner/history")
        assert resp.status_code == 200
        assert resp.json() == []


class TestGetScanByIdEndpoint:
    @patch("tyche.api.routes.scanner.load_scan", new_callable=AsyncMock)
    def test_get_existing_scan(self, mock_load, client: TestClient) -> None:
        mock_load.return_value = _MOCK_SCAN_RESULT
        resp = client.get("/api/v1/scanner/test-scan-123")
        assert resp.status_code == 200
        assert resp.json()["scan_id"] == "test-scan-123"

    @patch("tyche.api.routes.scanner.load_scan", new_callable=AsyncMock)
    def test_get_nonexistent_scan(self, mock_load, client: TestClient) -> None:
        mock_load.return_value = None
        resp = client.get("/api/v1/scanner/nonexistent")
        assert resp.status_code == 404


class TestScanEmptySymbols:
    def test_empty_symbols_returns_400(self, client: TestClient) -> None:
        resp = client.post("/api/v1/scanner/scan?symbols=")
        assert resp.status_code == 400
        assert "Empty symbols" in resp.json()["detail"]


class TestTargetExpiration:
    def test_past_date_returns_400(self, client: TestClient) -> None:
        resp = client.post("/api/v1/scanner/scan?target_expiration=2020-01-01")
        assert resp.status_code == 400
        assert "today or a future date" in resp.json()["detail"]

    def test_bad_format_returns_400(self, client: TestClient) -> None:
        resp = client.post("/api/v1/scanner/scan?target_expiration=not-a-date")
        assert resp.status_code == 400
        assert "YYYY-MM-DD" in resp.json()["detail"]

    def test_valid_date_accepted(self, client: TestClient) -> None:
        from datetime import date, timedelta
        future = (date.today() + timedelta(days=3)).isoformat()
        resp = client.post(f"/api/v1/scanner/scan?symbols=AAPL&target_expiration={future}")
        assert resp.status_code == 200


class TestAvailableCapital:
    def test_zero_returns_422(self, client: TestClient) -> None:
        resp = client.post("/api/v1/scanner/scan?available_capital=0")
        assert resp.status_code == 422

    def test_negative_returns_422(self, client: TestClient) -> None:
        resp = client.post("/api/v1/scanner/scan?available_capital=-50000")
        assert resp.status_code == 422

    @patch("tyche.api.routes.scanner.save_scan", new_callable=AsyncMock)
    @patch("tyche.api.routes.scanner.run_morning_scan", new_callable=AsyncMock)
    def test_passes_capital_override(
        self, mock_scan: AsyncMock, mock_save: AsyncMock, client: TestClient
    ) -> None:
        from tyche.workflow.morning_scan import MorningScanResult

        mock_scan.return_value = MorningScanResult()
        resp = client.post("/api/v1/scanner/scan?symbols=AAPL&available_capital=250000")
        assert resp.status_code == 200
        assert mock_scan.call_args.kwargs["available_capital_override"] == 250_000.0
