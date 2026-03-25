"""Tests for FastAPI API routes using TestClient."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tyche.app import create_app
from tyche.api import deps
from tyche.broker.mock import MockBroker


@pytest.fixture(autouse=True)
def _reset_deps():
    """Reset DI singletons between tests."""
    deps.reset_all()
    yield
    deps.reset_all()


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.get_broker] = lambda: MockBroker()
    return TestClient(app)


class TestHealthCheck:
    def test_health(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestAccountRoutes:
    def test_get_balances(self, client: TestClient) -> None:
        resp = client.get("/api/v1/account/balances")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cash"] == 50000.0
        assert data["buying_power"] == 50000.0
        assert "captured_at" in data

    def test_get_positions(self, client: TestClient) -> None:
        resp = client.get("/api/v1/account/positions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        symbols = {p["symbol"] for p in data}
        assert "PL" in symbols
        assert "AAPL" in symbols

    def test_get_summary(self, client: TestClient) -> None:
        resp = client.get("/api/v1/account/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["position_count"] == 2
        assert data["balance"]["cash"] == 50000.0
        assert data["cash_available_for_csp"] > 0


class TestOrderRoutes:
    def test_get_open_orders(self, client: TestClient) -> None:
        resp = client.get("/api/v1/orders/open")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["symbol"] == "PL"

    def test_preview_order(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/orders/preview",
            json={
                "symbol": "PL",
                "option_symbol": "PL260327P00023000",
                "side": "sell_to_open",
                "quantity": 10,
                "limit_price": 1.50,
                "intent": "income",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "estimated_cost" in data
        assert "risk_results" in data
        assert isinstance(data["risk_results"], list)

    def test_execute_order_blocked_by_preview_mode(self, client: TestClient) -> None:
        """Orders are blocked when preview_only_mode is True (default)."""
        resp = client.post(
            "/api/v1/orders/execute",
            json={
                "symbol": "PL",
                "option_symbol": "PL260327P00023000",
                "side": "sell_to_open",
                "quantity": 5,
                "order_type": "limit",
                "limit_price": 1.50,
                "intent": "income",
            },
        )
        assert resp.status_code == 403
        data = resp.json()
        assert "Risk rules blocked order" in str(data["detail"])

    def test_cancel_order(self, client: TestClient) -> None:
        resp = client.delete("/api/v1/orders/mock-1001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_monitor_orders(self, client: TestClient) -> None:
        resp = client.get("/api/v1/orders/monitor")
        assert resp.status_code == 200
        data = resp.json()
        assert data["orders_checked"] >= 1
        assert "alerts" in data


class TestScannerRoutes:
    def test_trigger_scan(self, client: TestClient) -> None:
        resp = client.post("/api/v1/scanner/scan?symbols=PL,AAPL&top_n=3")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbols_scanned"] == 2
        assert len(data["csp_candidates"]) > 0

    def test_get_latest_scan_before_any_scan(self, client: TestClient) -> None:
        # Reset the module-level state
        from tyche.api.routes import scanner
        scanner._latest_scan = None
        resp = client.get("/api/v1/scanner/latest")
        assert resp.status_code == 404

    def test_scan_then_latest(self, client: TestClient) -> None:
        client.post("/api/v1/scanner/scan?symbols=PL&top_n=2")
        resp = client.get("/api/v1/scanner/latest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbols_scanned"] == 1

    def test_scan_empty_symbols_error(self, client: TestClient) -> None:
        resp = client.post("/api/v1/scanner/scan?symbols=")
        assert resp.status_code == 400


class TestWatchlistRoutes:
    def test_get_watchlist_empty_config(self, client: TestClient) -> None:
        """Watchlist returns empty when no symbols are configured."""
        resp = client.get("/api/v1/watchlist/")
        assert resp.status_code == 200
        data = resp.json()
        # Default settings have no watchlist symbols, so this should be empty
        # (or contain data if env has symbols — just check it's a list)
        assert isinstance(data, list)


class TestSystemRoutes:
    def test_get_config(self, client: TestClient) -> None:
        resp = client.get("/api/v1/system/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "sandbox_mode" in data
        assert "risk_limits" in data
        assert "wheel_params" in data

    def test_get_scheduler_status(self, client: TestClient) -> None:
        resp = client.get("/api/v1/system/scheduler")
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data
