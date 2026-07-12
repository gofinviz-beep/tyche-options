"""Tests for Cloud Run job registry."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tyche.config import TycheSettings
from tyche.ops.gcp_jobs import (
    JOB_NAMES,
    _subprocess_exit_hint,
    execute_job,
    run_deep_dive_batch_job,
)
from tyche.storage.paths import StorageContext
from tyche.workflow.deep_dive_batch import DeepDiveBatchResult


def test_job_names_match_spec() -> None:
    assert "ingest-demand-data" in JOB_NAMES
    assert "ingest-news" in JOB_NAMES
    assert "ingest-edgar" in JOB_NAMES
    assert "stocks-conviction-batch" in JOB_NAMES
    assert "nightly-pipeline" in JOB_NAMES
    assert "audit-snapshots" in JOB_NAMES
    assert "publish-signals" in JOB_NAMES
    assert "stocks-derived-batch" in JOB_NAMES
    assert "stocks-deep-dive-batch" in JOB_NAMES
    assert "stocks-screener-index-batch" in JOB_NAMES
    assert "candidate-universe-batch" in JOB_NAMES
    assert "options-chain-prep-batch" in JOB_NAMES
    assert "options-scanner-batch" in JOB_NAMES
    assert "options-snapshot-batch" in JOB_NAMES
    assert len(JOB_NAMES) == 18


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


class TestDeepDiveBatchJob:
    @pytest.mark.asyncio
    async def test_run_deep_dive_batch_job_success(self, tmp_path: Path) -> None:
        settings = TycheSettings(
            tradier_api_token="t",
            tradier_account_id="a",
            gemini_api_key="g",
            data_dir=str(tmp_path),
            deep_dive_batch_min_market_cap_millions=1000,
        )
        ctx = StorageContext(backend="local", local_root=tmp_path)
        from datetime import date

        fake_result = DeepDiveBatchResult(
            as_of_date=date.today(),
            universe_size=2,
            tickers_computed=2,
            tickers_written=2,
        )

        with patch(
            "tyche.workflow.deep_dive_batch.run_deep_dive_batch",
            new_callable=AsyncMock,
            return_value=fake_result,
        ):
            result = await run_deep_dive_batch_job(settings=settings, ctx=ctx, run_id="run-test")

        assert result.status == "success"
        assert result.job_name == "stocks-deep-dive-batch"
        assert result.summary["tickers_written"] == 2

    @pytest.mark.asyncio
    async def test_run_deep_dive_batch_job_no_writes_fails(self, tmp_path: Path) -> None:
        settings = TycheSettings(
            tradier_api_token="t",
            tradier_account_id="a",
            gemini_api_key="g",
            data_dir=str(tmp_path),
        )
        ctx = StorageContext(backend="local", local_root=tmp_path)
        from datetime import date

        fake_result = DeepDiveBatchResult(
            as_of_date=date.today(),
            universe_size=0,
            tickers_computed=0,
            tickers_written=0,
            errors=["No tickers in the filtered universe"],
        )

        with patch(
            "tyche.workflow.deep_dive_batch.run_deep_dive_batch",
            new_callable=AsyncMock,
            return_value=fake_result,
        ):
            result = await run_deep_dive_batch_job(settings=settings, ctx=ctx, run_id="run-test")

        assert result.status == "failed"
