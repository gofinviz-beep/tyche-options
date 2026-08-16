"""In-process cache for published route artifacts, keyed on the publish run.

Every daily UI route reads one ``published/routes/*.json`` object, and the whole
published set is a few MiB — small enough to hold in memory. What makes it safe
to cache is that ``publish_signals`` stamps every artifact with the ``run_id`` of
the batch that produced it and records the same id in ``published/manifest.json``.
That single small object is therefore both the freshness probe and the cache key:
when ``run_id`` changes, everything cached from the previous run is dropped.

Deliberately not Redis. One warm Cloud Run instance holds the entire day of data,
and GCS already plays the role of the shared store for anything that scales out.

The cache only engages when a manifest exists (i.e. the artifacts came from a
real publish run). Without one, reads pass straight through, so local development
and tests behave exactly as they did before.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from tyche.exceptions import DataStoreError
from tyche.storage import exists as storage_exists, read_json
from tyche.storage.paths import StorageContext, join_uri

logger = structlog.get_logger()

MANIFEST_REL = join_uri("published", "manifest.json")


@dataclass(frozen=True)
class PublishedManifest:
    """Parsed ``published/manifest.json``."""

    run_id: str
    generated_at: str
    routes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def route_count(self) -> int:
        return len(self.routes)

    def statuses(self) -> dict[str, str]:
        """Return ``{route path: status}`` for every published route."""
        return {
            str(r.get("route") or ""): str(r.get("status") or "unknown")
            for r in self.routes
            if r.get("route")
        }


def read_manifest(ctx: StorageContext) -> PublishedManifest | None:
    """Read the publish manifest, or None when it is absent or unreadable."""
    try:
        if not storage_exists(MANIFEST_REL, ctx=ctx):
            return None
        raw = read_json(MANIFEST_REL, ctx=ctx)
    except DataStoreError as exc:
        logger.warning("published_manifest_unreadable", error=str(exc))
        return None
    if not isinstance(raw, dict):
        return None
    run_id = str(raw.get("run_id") or "")
    if not run_id:
        return None
    return PublishedManifest(
        run_id=run_id,
        generated_at=str(raw.get("generated_at") or ""),
        routes=list(raw.get("routes") or []),
    )


def context_key(ctx: StorageContext) -> str:
    """Return a stable identity for a storage context.

    Cached artifacts belong to the location they came from. Production has a
    single context for the process lifetime, but keying on it keeps a changed
    bucket, prefix, or data dir from serving another location's artifacts.
    """
    if ctx.backend == "gcs":
        return f"gcs://{ctx.gcs_bucket}/{ctx.gcs_prefix}"
    return f"local://{ctx.local_root}"


@dataclass
class _ContextState:
    """Cached artifacts for one storage context and publish run."""

    entries: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    manifest: PublishedManifest | None = None
    manifest_read_at: float = 0.0
    # Whether a read has been attempted in the current TTL window. Tracked
    # separately from `manifest` so an ABSENT manifest is also remembered:
    # otherwise every request re-probes storage, which before any publish run
    # means a GCS round trip per request.
    manifest_probed: bool = False


class PublishedArtifactCache:
    """Caches route envelopes for the lifetime of one publish run.

    Thread-safe: blocking storage reads are dispatched to worker threads, so
    several may resolve the same route concurrently.
    """

    def __init__(self, *, manifest_ttl_seconds: float = 30.0) -> None:
        self._manifest_ttl = manifest_ttl_seconds
        self._lock = threading.Lock()
        self._states: dict[str, _ContextState] = {}
        self._hits = 0
        self._misses = 0

    def _state(self, ctx_key: str) -> _ContextState:
        """Return the state for a context. Caller must hold the lock."""
        state = self._states.get(ctx_key)
        if state is None:
            state = _ContextState()
            self._states[ctx_key] = state
        return state

    # -- manifest ---------------------------------------------------------

    def manifest(
        self, ctx: StorageContext, *, force: bool = False
    ) -> PublishedManifest | None:
        """Return the manifest, re-reading at most once per TTL window.

        The TTL bounds how long a just-finished publish stays invisible; it is
        not a correctness boundary, since the ``run_id`` comparison is what
        actually evicts stale entries.
        """
        ctx_key = context_key(ctx)

        with self._lock:
            state = self._state(ctx_key)
            fresh = (time.monotonic() - state.manifest_read_at) < self._manifest_ttl
            if not force and fresh and state.manifest_probed:
                return state.manifest

        loaded = read_manifest(ctx)

        with self._lock:
            state = self._state(ctx_key)
            state.manifest = loaded
            state.manifest_read_at = time.monotonic()
            state.manifest_probed = True
            if loaded is not None and loaded.run_id != state.run_id:
                if state.run_id is not None:
                    logger.info(
                        "published_cache_run_changed",
                        previous_run_id=state.run_id,
                        run_id=loaded.run_id,
                        dropped=len(state.entries),
                    )
                state.entries.clear()
                state.run_id = loaded.run_id
            return loaded

    # -- artifacts --------------------------------------------------------

    def get_or_load(
        self,
        key: str,
        loader: Callable[[], Any],
        *,
        ctx: StorageContext,
    ) -> Any:
        """Return a cached artifact for the current run, else load and store it.

        Falls through to ``loader`` uncached when no manifest is available.
        """
        manifest = self.manifest(ctx)
        if manifest is None:
            return loader()

        ctx_key = context_key(ctx)
        with self._lock:
            state = self._state(ctx_key)
            if key in state.entries:
                self._hits += 1
                return state.entries[key]

        value = loader()

        with self._lock:
            state = self._state(ctx_key)
            # Re-check the run: a publish may have landed while we were reading,
            # in which case manifest() already cleared the entries and this value
            # belongs to the previous run.
            if state.run_id == manifest.run_id:
                state.entries[key] = value
                self._misses += 1
        return value

    def invalidate(self) -> None:
        """Drop all cached artifacts and force a manifest re-read."""
        with self._lock:
            self._states.clear()

    def stats(self, ctx: StorageContext | None = None) -> dict[str, Any]:
        with self._lock:
            if ctx is not None:
                state = self._state(context_key(ctx))
                return {
                    "run_id": state.run_id,
                    "entries": len(state.entries),
                    "hits": self._hits,
                    "misses": self._misses,
                }
            return {
                "contexts": len(self._states),
                "entries": sum(len(s.entries) for s in self._states.values()),
                "hits": self._hits,
                "misses": self._misses,
            }


_cache: PublishedArtifactCache | None = None
_cache_lock = threading.Lock()


def get_published_cache(
    *,
    manifest_ttl_seconds: float | None = None,
) -> PublishedArtifactCache:
    """Return the process-wide artifact cache."""
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = PublishedArtifactCache(
                manifest_ttl_seconds=(
                    30.0 if manifest_ttl_seconds is None else manifest_ttl_seconds
                )
            )
        return _cache


def invalidate_published_cache() -> None:
    """Clear the process-wide cache (config change, or a local batch finishing)."""
    with _cache_lock:
        if _cache is not None:
            _cache.invalidate()


__all__ = [
    "MANIFEST_REL",
    "PublishedArtifactCache",
    "PublishedManifest",
    "context_key",
    "get_published_cache",
    "invalidate_published_cache",
    "read_manifest",
]
