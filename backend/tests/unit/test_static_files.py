"""Tests for serving the built SPA from the API process."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from tyche.api.static_files import mount_spa


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    """A minimal Vite-shaped build output."""
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><div id=root></div>")
    (root / "assets" / "index-abc123.js").write_text("console.log(1)")
    (root / "favicon.svg").write_text("<svg/>")
    return root


def _app_with_api() -> FastAPI:
    """An app with API routes registered, mirroring create_app's order."""
    app = FastAPI()
    router = APIRouter()

    @router.get("/account/balances")
    async def balances() -> dict[str, float]:
        return {"cash": 1.0}

    app.include_router(router, prefix="/api/v1")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


@pytest.fixture
def client(dist: Path) -> TestClient:
    app = _app_with_api()
    assert mount_spa(app, str(dist)) is True
    return TestClient(app)


class TestMountGuards:
    def test_no_static_dir_is_a_noop(self) -> None:
        app = _app_with_api()
        assert mount_spa(app, "") is False
        # The catch-all must not exist, so an unknown path still 404s.
        assert TestClient(app).get("/stocks/screener").status_code == 404

    def test_missing_index_is_a_noop(self, tmp_path: Path) -> None:
        empty = tmp_path / "nope"
        empty.mkdir()
        assert mount_spa(_app_with_api(), str(empty)) is False


class TestApiStillWins:
    """The catch-all is registered last, so it must not shadow the API."""

    def test_api_route_is_not_shadowed(self, client: TestClient) -> None:
        resp = client.get("/api/v1/account/balances")
        assert resp.status_code == 200
        assert resp.json() == {"cash": 1.0}

    def test_health_is_not_shadowed(self, client: TestClient) -> None:
        assert client.get("/health").json() == {"status": "ok"}

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/does/not/exist",
            "/health/nope",
            "/openapi.json/nope",
            "/docs/nope",
        ],
    )
    def test_unknown_api_paths_404_instead_of_serving_html(
        self, client: TestClient, path: str
    ) -> None:
        """An API caller must get a 404, not index.html with a 200."""
        resp = client.get(path)
        assert resp.status_code == 404
        assert "<div id=root>" not in resp.text


class TestSpaFallback:
    @pytest.mark.parametrize(
        "path", ["/", "/stocks/screener", "/stocks/deep-dive", "/deep/nested/route"]
    )
    def test_client_routes_serve_index(self, client: TestClient, path: str) -> None:
        """Deep links must survive a refresh — the server only React knows."""
        resp = client.get(path)
        assert resp.status_code == 200
        assert "<div id=root>" in resp.text

    def test_index_is_revalidated(self, client: TestClient) -> None:
        # index.html points at hashed asset names, so caching it would pin the
        # browser to a previous deploy's bundle.
        assert client.get("/").headers["cache-control"] == "no-cache"


class TestStaticAssets:
    def test_hashed_asset_is_served_immutable(self, client: TestClient) -> None:
        resp = client.get("/assets/index-abc123.js")
        assert resp.status_code == 200
        assert resp.text == "console.log(1)"
        assert "immutable" in resp.headers["cache-control"]

    def test_root_file_is_served_but_revalidated(self, client: TestClient) -> None:
        resp = client.get("/favicon.svg")
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "no-cache"

    def test_missing_asset_falls_back_to_index(self, client: TestClient) -> None:
        # Vite hashes asset names, so a miss means a stale client asking for an
        # old bundle. Serving index lets it reload into the current one.
        assert client.get("/assets/gone-000.js").status_code == 200

    @pytest.mark.parametrize(
        "path",
        [
            "/../conftest.py",
            "/assets/../../conftest.py",
            "/%2e%2e/conftest.py",
        ],
    )
    def test_path_traversal_cannot_escape_the_static_root(
        self, client: TestClient, path: str
    ) -> None:
        resp = client.get(path)
        # Either normalized away by the client/router or resolved back to index —
        # never a file from outside dist/.
        assert resp.status_code in (200, 404)
        assert "import" not in resp.text
