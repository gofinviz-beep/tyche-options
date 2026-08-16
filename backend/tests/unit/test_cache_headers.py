"""Tests for conditional GET on published-artifact routes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tyche.api.cache_headers import PublishedCacheHeadersMiddleware
from tyche.config import TycheSettings
from tyche.persistence.published_cache import MANIFEST_REL, invalidate_published_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    invalidate_published_cache()
    yield
    invalidate_published_cache()


@pytest.fixture
def app_factory(tmp_path: Path, monkeypatch):
    """Build an app whose middleware sees an isolated storage root."""

    def _build(*, with_manifest: bool, run_id: str = "run-1") -> TestClient:
        if with_manifest:
            path = tmp_path / MANIFEST_REL
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "generated_at": "2026-08-16T07:00:00Z",
                        "routes": [],
                    }
                )
            )

        settings = TycheSettings(
            data_backend="local",
            data_dir=str(tmp_path),
            db_dir=str(tmp_path),
        )
        monkeypatch.setattr(
            "tyche.api.cache_headers.get_settings", lambda: settings
        )

        app = FastAPI()
        app.add_middleware(PublishedCacheHeadersMiddleware)

        @app.get("/api/v1/stocks/screener")
        async def screener() -> dict[str, str]:
            return {"payload": "big"}

        @app.get("/api/v1/account/balances")
        async def balances() -> dict[str, float]:
            return {"cash": 1.0}

        return TestClient(app)

    return _build


class TestEtagOnPublishedRoutes:
    def test_adds_etag_and_cache_control(self, app_factory) -> None:
        client = app_factory(with_manifest=True)
        resp = client.get("/api/v1/stocks/screener")
        assert resp.status_code == 200
        assert resp.headers["etag"].startswith('W/"')
        assert "max-age=60" in resp.headers["cache-control"]

    def test_matching_if_none_match_returns_304(self, app_factory) -> None:
        client = app_factory(with_manifest=True)
        etag = client.get("/api/v1/stocks/screener").headers["etag"]

        resp = client.get(
            "/api/v1/stocks/screener", headers={"If-None-Match": etag}
        )
        assert resp.status_code == 304
        assert resp.content == b""

    def test_query_string_changes_the_etag(self, app_factory) -> None:
        """These routes filter server-side, so each query is a distinct payload."""
        client = app_factory(with_manifest=True)
        a = client.get("/api/v1/stocks/screener?sector=Technology").headers["etag"]
        b = client.get("/api/v1/stocks/screener?sector=Energy").headers["etag"]
        assert a != b

    def test_new_publish_run_changes_the_etag(self, tmp_path: Path, app_factory) -> None:
        client = app_factory(with_manifest=True, run_id="run-1")
        first = client.get("/api/v1/stocks/screener").headers["etag"]

        invalidate_published_cache()
        client = app_factory(with_manifest=True, run_id="run-2")
        second = client.get("/api/v1/stocks/screener").headers["etag"]
        assert first != second

    def test_stale_etag_is_not_honoured(self, app_factory) -> None:
        client = app_factory(with_manifest=True)
        resp = client.get(
            "/api/v1/stocks/screener", headers={"If-None-Match": 'W/"stale"'}
        )
        assert resp.status_code == 200
        assert resp.json() == {"payload": "big"}


class TestScope:
    def test_non_published_routes_are_untouched(self, app_factory) -> None:
        # Account data is live broker state — caching it would be wrong.
        client = app_factory(with_manifest=True)
        resp = client.get("/api/v1/account/balances")
        assert resp.status_code == 200
        assert "etag" not in resp.headers

    def test_no_manifest_means_no_etag(self, app_factory) -> None:
        """Local dev has no publish run, so there is nothing stable to key on."""
        client = app_factory(with_manifest=False)
        resp = client.get("/api/v1/stocks/screener")
        assert resp.status_code == 200
        assert "etag" not in resp.headers

    def test_post_is_untouched(self, app_factory) -> None:
        client = app_factory(with_manifest=True)
        resp = client.post("/api/v1/stocks/screener")
        assert resp.status_code == 405
        assert "etag" not in resp.headers
