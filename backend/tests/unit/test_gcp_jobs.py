"""Tests for Cloud Run job registry."""

from __future__ import annotations

import pytest

from tyche.ops.gcp_jobs import JOB_NAMES, _subprocess_exit_hint, execute_job


def test_job_names_match_spec() -> None:
    assert "ingest-demand-data" in JOB_NAMES
    assert "ingest-news" in JOB_NAMES
    assert "ingest-edgar" in JOB_NAMES
    assert "stocks-conviction-batch" in JOB_NAMES
    assert "nightly-pipeline" in JOB_NAMES
    assert "audit-snapshots" in JOB_NAMES
    assert "publish-signals" in JOB_NAMES
    assert "stocks-derived-batch" in JOB_NAMES
    assert "candidate-universe-batch" in JOB_NAMES
    assert "options-chain-prep-batch" in JOB_NAMES
    assert "options-scanner-batch" in JOB_NAMES
    assert "options-snapshot-batch" in JOB_NAMES
    assert len(JOB_NAMES) == 16


@pytest.mark.asyncio
async def test_execute_job_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown job"):
        await execute_job("not-a-real-job")


def test_subprocess_exit_hint_oom() -> None:
    hint = _subprocess_exit_hint(-9)
    assert "OOM" in hint
    assert "32 GiB" in hint or "SIGKILL" in hint


def test_subprocess_exit_hint_signal() -> None:
    assert "signal 15" in _subprocess_exit_hint(-15)


def test_subprocess_exit_hint_normal_exit() -> None:
    assert _subprocess_exit_hint(1) == "exit 1"
