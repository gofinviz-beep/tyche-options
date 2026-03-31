"""Tests for POST /scanner/explore — lightweight options explorer."""

from __future__ import annotations

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
    return TestClient(app)


class TestExploreEndpoint:
    def test_returns_candidates_for_valid_symbol(self, client: TestClient) -> None:
        resp = client.post("/api/v1/scanner/explore?symbols=AAPL")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbols_requested"] == 1
        assert data["symbols_with_options"] >= 1
        assert data["total_contracts"] >= 1
        assert isinstance(data["candidates"], list)
        assert len(data["candidates"]) > 0
        c = data["candidates"][0]
        assert "symbol" in c
        assert "strike" in c
        assert "bid" in c
        assert "premium_per_contract" in c
        assert "annualized_return_pct" in c
        assert "max_contracts" in c

    def test_multiple_symbols(self, client: TestClient) -> None:
        resp = client.post("/api/v1/scanner/explore?symbols=AAPL,PL")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbols_requested"] == 2
        tickers = {c["symbol"] for c in data["candidates"]}
        assert len(tickers) >= 1

    def test_empty_symbols_returns_400(self, client: TestClient) -> None:
        resp = client.post("/api/v1/scanner/explore?symbols=")
        assert resp.status_code == 400
        assert "No valid symbols" in resp.json()["detail"]

    def test_results_sorted_by_annualized_return(self, client: TestClient) -> None:
        resp = client.post("/api/v1/scanner/explore?symbols=AAPL")
        assert resp.status_code == 200
        data = resp.json()
        returns = [c["annualized_return_pct"] for c in data["candidates"]]
        assert returns == sorted(returns, reverse=True)

    def test_custom_capital_limits_max_contracts(self, client: TestClient) -> None:
        resp_small = client.post(
            "/api/v1/scanner/explore?symbols=AAPL&available_capital=1000"
        )
        assert resp_small.status_code == 200
        data_small = resp_small.json()
        assert data_small["available_capital"] == 1000
        if data_small["candidates"]:
            for c in data_small["candidates"]:
                assert c["max_contracts"] * c["collateral"] <= 1000

    def test_response_includes_expiration(self, client: TestClient) -> None:
        resp = client.post("/api/v1/scanner/explore?symbols=AAPL")
        data = resp.json()
        assert data["expiration"] is not None
        for c in data["candidates"]:
            assert "expiration" in c
            assert "dte" in c

    def test_response_includes_duration(self, client: TestClient) -> None:
        resp = client.post("/api/v1/scanner/explore?symbols=AAPL")
        data = resp.json()
        assert "duration_ms" in data
        assert data["duration_ms"] >= 0

    def test_whitespace_handling(self, client: TestClient) -> None:
        resp = client.post("/api/v1/scanner/explore?symbols= AAPL , PL ")
        assert resp.status_code == 200
        assert resp.json()["symbols_requested"] == 2
