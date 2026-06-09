"""Tests for Cloud Run job registry."""

from __future__ import annotations

import pytest

from tyche.ops.gcp_jobs import JOB_NAMES, execute_job


def test_job_names_match_spec() -> None:
    assert "ingest-demand-data" in JOB_NAMES
    assert "ingest-news" in JOB_NAMES
    assert "ingest-edgar" in JOB_NAMES
    assert "nightly-pipeline" in JOB_NAMES
    assert "audit-snapshots" in JOB_NAMES
    assert "publish-signals" in JOB_NAMES
    assert len(JOB_NAMES) == 10


@pytest.mark.asyncio
async def test_execute_job_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown job"):
        await execute_job("not-a-real-job")
