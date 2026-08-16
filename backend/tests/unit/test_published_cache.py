"""Tests for the in-process published-artifact cache."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tyche.persistence.published_cache import (
    MANIFEST_REL,
    PublishedArtifactCache,
    context_key,
    invalidate_published_cache,
    read_manifest,
)
from tyche.storage.paths import StorageContext


@pytest.fixture(autouse=True)
def _reset_module_cache():
    invalidate_published_cache()
    yield
    invalidate_published_cache()


@pytest.fixture
def ctx(tmp_path: Path) -> StorageContext:
    return StorageContext(backend="local", local_root=tmp_path)


def _write_manifest(
    tmp_path: Path,
    run_id: str,
    *,
    routes: list[dict] | None = None,
    generated_at: str = "2026-08-16T07:00:00Z",
) -> None:
    path = tmp_path / MANIFEST_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "generated_at": generated_at,
                "routes": routes if routes is not None else [],
            }
        )
    )


class TestReadManifest:
    def test_missing_manifest_returns_none(self, tmp_path: Path, ctx) -> None:
        assert read_manifest(ctx) is None

    def test_parses_run_id_and_routes(self, tmp_path: Path, ctx) -> None:
        _write_manifest(
            tmp_path,
            "run-1",
            routes=[
                {"route": "/stocks/screener", "status": "ok", "as_of": "2026-08-15"},
            ],
        )
        manifest = read_manifest(ctx)
        assert manifest is not None
        assert manifest.run_id == "run-1"
        assert manifest.route_count == 1
        assert manifest.statuses() == {"/stocks/screener": "ok"}

    def test_manifest_without_run_id_is_unusable(self, tmp_path: Path, ctx) -> None:
        # Without a run_id there is no cache key, so it must not engage.
        path = tmp_path / MANIFEST_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"generated_at": "2026-08-16T07:00:00Z"}))
        assert read_manifest(ctx) is None

    def test_corrupt_manifest_is_tolerated(self, tmp_path: Path, ctx) -> None:
        path = tmp_path / MANIFEST_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        assert read_manifest(ctx) is None


class TestCaching:
    def test_second_read_is_served_from_cache(self, tmp_path: Path, ctx) -> None:
        _write_manifest(tmp_path, "run-1")
        cache = PublishedArtifactCache()
        calls = []

        def loader() -> str:
            calls.append(1)
            return "payload"

        assert cache.get_or_load("route:a", loader, ctx=ctx) == "payload"
        assert cache.get_or_load("route:a", loader, ctx=ctx) == "payload"
        assert len(calls) == 1

    def test_distinct_keys_are_cached_separately(self, tmp_path: Path, ctx) -> None:
        _write_manifest(tmp_path, "run-1")
        cache = PublishedArtifactCache()

        assert cache.get_or_load("route:a", lambda: "A", ctx=ctx) == "A"
        assert cache.get_or_load("route:b", lambda: "B", ctx=ctx) == "B"
        assert cache.get_or_load("route:a", lambda: "CHANGED", ctx=ctx) == "A"

    def test_none_is_cached(self, tmp_path: Path, ctx) -> None:
        """A missing artifact must not be re-fetched on every request."""
        _write_manifest(tmp_path, "run-1")
        cache = PublishedArtifactCache()
        calls = []

        def loader() -> None:
            calls.append(1)
            return None

        assert cache.get_or_load("route:a", loader, ctx=ctx) is None
        assert cache.get_or_load("route:a", loader, ctx=ctx) is None
        assert len(calls) == 1


class TestRunIdEviction:
    def test_new_run_id_drops_previous_entries(self, tmp_path: Path, ctx) -> None:
        _write_manifest(tmp_path, "run-1")
        cache = PublishedArtifactCache(manifest_ttl_seconds=0.0)
        assert cache.get_or_load("route:a", lambda: "old", ctx=ctx) == "old"

        # A new publish lands.
        _write_manifest(tmp_path, "run-2")
        assert cache.get_or_load("route:a", lambda: "new", ctx=ctx) == "new"
        assert cache.stats(ctx)["run_id"] == "run-2"

    def test_same_run_id_keeps_entries(self, tmp_path: Path, ctx) -> None:
        _write_manifest(tmp_path, "run-1")
        cache = PublishedArtifactCache(manifest_ttl_seconds=0.0)
        assert cache.get_or_load("route:a", lambda: "first", ctx=ctx) == "first"

        # Manifest re-read, but the run did not change.
        _write_manifest(tmp_path, "run-1", generated_at="2026-08-16T08:00:00Z")
        assert cache.get_or_load("route:a", lambda: "second", ctx=ctx) == "first"

    def test_ttl_defers_the_manifest_re_read(self, tmp_path: Path, ctx) -> None:
        """Within the TTL a new publish is not yet visible — bounded staleness."""
        _write_manifest(tmp_path, "run-1")
        cache = PublishedArtifactCache(manifest_ttl_seconds=3600.0)
        cache.get_or_load("route:a", lambda: "old", ctx=ctx)

        _write_manifest(tmp_path, "run-2")
        assert cache.get_or_load("route:a", lambda: "new", ctx=ctx) == "old"


class TestNegativeCaching:
    """An absent manifest must be remembered, not re-probed per request."""

    def test_absent_manifest_is_probed_once_per_ttl(
        self, tmp_path: Path, ctx, monkeypatch
    ) -> None:
        # Regression: only a non-None manifest was cached, so before any publish
        # run every request re-probed storage — a GCS round trip per request.
        probes = []

        def counting_read(_ctx):
            probes.append(1)
            return None

        monkeypatch.setattr(
            "tyche.persistence.published_cache.read_manifest", counting_read
        )
        cache = PublishedArtifactCache(manifest_ttl_seconds=3600.0)

        for _ in range(5):
            cache.get_or_load("route:a", lambda: "x", ctx=ctx)

        assert len(probes) == 1

    def test_expired_ttl_probes_again(self, tmp_path: Path, ctx, monkeypatch) -> None:
        probes = []

        def counting_read(_ctx):
            probes.append(1)
            return None

        monkeypatch.setattr(
            "tyche.persistence.published_cache.read_manifest", counting_read
        )
        cache = PublishedArtifactCache(manifest_ttl_seconds=0.0)

        cache.manifest(ctx)
        cache.manifest(ctx)
        assert len(probes) == 2


class TestPassThroughWithoutManifest:
    def test_no_manifest_means_no_caching(self, tmp_path: Path, ctx) -> None:
        """Local dev and tests must behave exactly as before the cache existed."""
        cache = PublishedArtifactCache()
        calls = []

        def loader() -> str:
            calls.append(1)
            return f"call-{len(calls)}"

        assert cache.get_or_load("route:a", loader, ctx=ctx) == "call-1"
        assert cache.get_or_load("route:a", loader, ctx=ctx) == "call-2"
        assert len(calls) == 2
        assert cache.stats(ctx)["entries"] == 0


class TestContextIsolation:
    """Artifacts belong to the location they came from."""

    def test_two_contexts_do_not_share_entries(self, tmp_path: Path) -> None:
        # Regression: keying only on route_key let one storage root serve
        # another's cached artifacts (it surfaced as cross-test pollution).
        a_root = tmp_path / "a"
        b_root = tmp_path / "b"
        a_root.mkdir()
        b_root.mkdir()
        _write_manifest(a_root, "run-a")
        _write_manifest(b_root, "run-b")
        ctx_a = StorageContext(backend="local", local_root=a_root)
        ctx_b = StorageContext(backend="local", local_root=b_root)

        cache = PublishedArtifactCache()
        assert cache.get_or_load("route:x", lambda: "from-a", ctx=ctx_a) == "from-a"
        assert cache.get_or_load("route:x", lambda: "from-b", ctx=ctx_b) == "from-b"
        # And each stays cached independently.
        assert cache.get_or_load("route:x", lambda: "nope", ctx=ctx_a) == "from-a"
        assert cache.get_or_load("route:x", lambda: "nope", ctx=ctx_b) == "from-b"

    def test_context_key_distinguishes_backends(self, tmp_path: Path) -> None:
        local = StorageContext(backend="local", local_root=tmp_path)
        gcs = StorageContext(
            backend="gcs", local_root=tmp_path, gcs_bucket="tyche-data-prod"
        )
        prefixed = StorageContext(
            backend="gcs",
            local_root=tmp_path,
            gcs_bucket="tyche-data-prod",
            gcs_prefix="staging",
        )
        keys = {context_key(local), context_key(gcs), context_key(prefixed)}
        assert len(keys) == 3


class TestInvalidate:
    def test_invalidate_clears_entries(self, tmp_path: Path, ctx) -> None:
        _write_manifest(tmp_path, "run-1")
        cache = PublishedArtifactCache()
        cache.get_or_load("route:a", lambda: "old", ctx=ctx)

        cache.invalidate()
        assert cache.stats(ctx)["entries"] == 0
        assert cache.get_or_load("route:a", lambda: "fresh", ctx=ctx) == "fresh"
