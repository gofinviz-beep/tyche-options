"""Run manifest writer for scheduled GCP jobs (spec §12)."""

from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

import structlog

from tyche.storage import write_json
from tyche.storage.paths import StorageContext, join_uri

logger = structlog.get_logger()

RunStatus = Literal["success", "failed", "running"]


def new_run_id() -> str:
    """Return a UTC timestamp + short suffix suitable for ``runs/{job}/{run_id}/``."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def git_sha() -> str | None:
    """Best-effort short git SHA for the running image/checkout."""
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            .strip()
            or None
        )
    except (OSError, subprocess.CalledProcessError):
        return None


@dataclass
class RunManifest:
    """Structured manifest persisted under ``runs/{job_name}/{run_id}/manifest.json``."""

    job_name: str
    run_id: str
    data_backend: str
    started_at: str
    ended_at: str | None = None
    status: RunStatus = "running"
    git_sha: str | None = field(default_factory=git_sha)
    input_paths: list[str] = field(default_factory=list)
    output_paths: list[str] = field(default_factory=list)
    published_paths: list[str] = field(default_factory=list)
    tickers_requested: int = 0
    tickers_succeeded: int = 0
    tickers_failed: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def start(
        cls,
        *,
        job_name: str,
        run_id: str | None = None,
        data_backend: str = "local",
    ) -> RunManifest:
        rid = run_id or new_run_id()
        return cls(
            job_name=job_name,
            run_id=rid,
            data_backend=data_backend,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

    def finish(self, *, status: RunStatus) -> None:
        self.status = status
        self.ended_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "job_name": self.job_name,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status,
            "git_sha": self.git_sha,
            "data_backend": self.data_backend,
            "input_paths": self.input_paths,
            "output_paths": self.output_paths,
            "published_paths": self.published_paths,
            "tickers_requested": self.tickers_requested,
            "tickers_succeeded": self.tickers_succeeded,
            "tickers_failed": self.tickers_failed,
            "warnings": self.warnings,
            "errors": self.errors,
        }
        if self.extra:
            payload["extra"] = self.extra
        return payload

    def write(self, *, ctx: StorageContext | None = None) -> str:
        """Persist manifest and return its relative path."""
        rel = join_uri("runs", self.job_name, self.run_id, "manifest.json")
        write_json(self.to_dict(), rel, atomic=True, ctx=ctx)
        logger.info(
            "run_manifest_written",
            job=self.job_name,
            run_id=self.run_id,
            status=self.status,
            path=rel,
        )
        return rel
