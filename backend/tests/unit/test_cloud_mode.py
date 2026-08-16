"""Tests for the cloud-mode inline compute guard.

The guard has two tiers: universe-wide scans stay blocked in GCS mode because
they read thousands of Parquet objects, while bounded work scoped to
caller-named tickers is allowed because it costs one broker call per ticker.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tyche.api import deps
from tyche.api.cloud_mode import (
    bounded_inline_compute_blocked,
    cloud_inline_compute_blocked,
    require_inline_compute_allowed,
)
from tyche.app import create_app
from tyche.broker.mock import MockBroker
from tyche.config import TycheSettings


def _settings(**overrides: object) -> TycheSettings:
    base: dict[str, object] = {
        "tradier_api_token": "t",
        "tradier_account_id": "a",
        "gemini_api_key": "g",
    }
    base.update(overrides)
    return TycheSettings(**base)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _reset_deps():
    deps.reset_all()
    yield
    deps.reset_all()


class TestBlockedPredicates:
    def test_local_backend_blocks_nothing(self) -> None:
        settings = _settings(data_backend="local")
        assert not cloud_inline_compute_blocked(settings)
        assert not bounded_inline_compute_blocked(settings)

    def test_gcs_blocks_universe_scans_but_not_bounded_work(self) -> None:
        settings = _settings(data_backend="gcs")
        assert cloud_inline_compute_blocked(settings)
        assert not bounded_inline_compute_blocked(settings)

    def test_bounded_work_can_be_turned_off(self) -> None:
        settings = _settings(
            data_backend="gcs", allow_bounded_inline_compute=False
        )
        assert bounded_inline_compute_blocked(settings)

    def test_inline_scan_override_unblocks_both_tiers(self) -> None:
        settings = _settings(data_backend="gcs", allow_inline_scan=True)
        assert not cloud_inline_compute_blocked(settings)
        assert not bounded_inline_compute_blocked(settings)

    def test_bounded_flag_is_irrelevant_outside_cloud_mode(self) -> None:
        """A local deployment is never gated, whatever the bounded flag says."""
        settings = _settings(
            data_backend="local", allow_bounded_inline_compute=False
        )
        assert not bounded_inline_compute_blocked(settings)


class TestRequireInlineComputeAllowed:
    def test_universe_scan_raises_409_in_cloud_mode(self) -> None:
        with pytest.raises(HTTPException) as exc:
            require_inline_compute_allowed(
                _settings(data_backend="gcs"),
                operation="morning scanner",
                job_hint="tyche-options-scanner-batch",
            )
        assert exc.value.status_code == 409
        assert "morning scanner" in exc.value.detail
        assert "tyche-options-scanner-batch" in exc.value.detail
        assert "TYCHE_ALLOW_INLINE_SCAN" in exc.value.detail

    def test_bounded_operation_passes_in_cloud_mode(self) -> None:
        require_inline_compute_allowed(
            _settings(data_backend="gcs"),
            operation="options explore",
            job_hint="n/a",
            bounded=True,
        )

    def test_bounded_operation_names_its_own_override_when_blocked(self) -> None:
        with pytest.raises(HTTPException) as exc:
            require_inline_compute_allowed(
                _settings(
                    data_backend="gcs", allow_bounded_inline_compute=False
                ),
                operation="options explore",
                job_hint="n/a",
                bounded=True,
            )
        assert exc.value.status_code == 409
        assert "TYCHE_ALLOW_BOUNDED_INLINE_COMPUTE" in exc.value.detail


class TestCloudModeRoutes:
    """End-to-end: which endpoints survive GCS mode without an override."""

    @pytest.fixture
    def cloud_client(self, tmp_path) -> TestClient:
        app = create_app()
        app.dependency_overrides[deps.get_broker] = lambda: MockBroker()
        app.dependency_overrides[deps.get_analysis_agent] = lambda: None
        # No gcs_bucket on purpose. Resolving any GCS-backed store raises
        # DataStoreError, so these tests fail loudly if a blocked route
        # constructs a store or loads a model before returning its 409.
        app.dependency_overrides[deps.get_settings] = lambda: _settings(
            data_backend="gcs",
            data_dir=str(tmp_path),
            db_dir=str(tmp_path),
            api_prefer_published_signals=False,
            api_allow_local_db_fallback=True,
        )
        return TestClient(app)

    def test_explore_is_allowed(self, cloud_client: TestClient) -> None:
        resp = cloud_client.post("/api/v1/scanner/explore?symbols=AAPL")
        assert resp.status_code == 200
        assert resp.json()["symbols_requested"] == 1

    def test_full_scan_is_still_blocked(self, cloud_client: TestClient) -> None:
        resp = cloud_client.post("/api/v1/scanner/scan")
        assert resp.status_code == 409
        assert "morning scanner" in resp.json()["detail"]

    def test_ohlcv_refresh_is_still_blocked(
        self, cloud_client: TestClient
    ) -> None:
        resp = cloud_client.post("/api/v1/stocks/ohlcv/refresh")
        assert resp.status_code == 409
