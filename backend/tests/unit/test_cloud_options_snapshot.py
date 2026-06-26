"""Tests for cloud options chain snapshot batch (Slice 4)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from tyche.config import TycheSettings
from tyche.market_data.options_chain_snapshot_store import (
    OPTIONS_CHAIN_SNAPSHOT_SUMMARY_REL,
    OPTIONS_TRADIER_SNAPSHOT_REPORT_REL,
    load_snapshot_summary_parquet,
)
from tyche.market_data.universe_candidates_store import CSP_SCAN_TICKERS_REL
from tyche.storage import read_json, write_parquet
from tyche.storage.paths import StorageContext
from tyche.workflow.options_snapshot import SnapshotStats
from tyche.workflow.options_snapshot_batch import (
    load_snapshot_candidate_tickers,
    run_options_snapshot_batch,
)


@pytest.fixture
def settings(tmp_path: Path) -> TycheSettings:
    return TycheSettings(
        tradier_api_token="token",
        tradier_account_id="acct",
        gemini_api_key="g",
        data_dir=str(tmp_path),
        data_backend="local",
        options_snapshot_max_tickers=2,
        ingest_window="morning",
    )


@pytest.fixture
def ctx(tmp_path: Path) -> StorageContext:
    return StorageContext(backend="local", local_root=tmp_path)


def _write_candidates(tmp_path: Path, tickers: list[str]) -> None:
    ctx = StorageContext(backend="local", local_root=tmp_path)
    write_parquet(
        pd.DataFrame(
            [
                {
                    "ticker": ticker,
                    "rank": idx + 1,
                    "priority_score": 100 - idx,
                    "as_of_date": "2026-06-24",
                }
                for idx, ticker in enumerate(tickers)
            ]
        ),
        CSP_SCAN_TICKERS_REL,
        atomic=True,
        ctx=ctx,
    )


def test_load_snapshot_candidate_tickers_respects_max(tmp_path: Path, ctx: StorageContext) -> None:
    _write_candidates(tmp_path, ["AAA", "BBB", "CCC"])
    tickers = load_snapshot_candidate_tickers(ctx=ctx, max_tickers=2)
    assert tickers == ["AAA", "BBB"]


@pytest.mark.asyncio
async def test_run_options_snapshot_batch_writes_summary_and_report(
    tmp_path: Path,
    settings: TycheSettings,
    ctx: StorageContext,
) -> None:
    _write_candidates(tmp_path, ["AAA", "BBB"])

    mock_stats = SnapshotStats(
        tickers_requested=2,
        tickers_succeeded=1,
        tickers_skipped=1,
        contracts_stored=12,
        rows_added=12,
        api_calls=4,
        elapsed_seconds=1.5,
        ticker_status={"AAA": "ok", "BBB": "skipped"},
        ticker_contracts={"AAA": 12},
        ticker_rows_added={"AAA": 12},
    )

    with patch(
        "tyche.workflow.options_snapshot_batch.run_options_snapshot",
        new=AsyncMock(return_value=mock_stats),
    ):
        result = await run_options_snapshot_batch(
            settings=settings,
            ctx=ctx,
            run_id="run-test",
            snapshot_date=date(2026, 6, 24),
        )

    assert result.tickers_requested == 2
    assert result.tickers_succeeded == 1
    assert result.errors == []

    summary, as_of = load_snapshot_summary_parquet(ctx=ctx)
    assert as_of == "2026-06-24"
    assert len(summary) == 2
    assert summary[0]["ticker"] == "AAA"
    assert summary[0]["status"] == "ok"
    assert summary[0]["contract_count"] == 12

    report = read_json(OPTIONS_TRADIER_SNAPSHOT_REPORT_REL, ctx=ctx)
    assert report["tickers_succeeded"] == 1
    assert report["candidate_source"] == CSP_SCAN_TICKERS_REL
    assert report["run_id"] == "run-test"


@pytest.mark.asyncio
async def test_run_options_snapshot_batch_requires_tradier_token(
    tmp_path: Path,
    ctx: StorageContext,
) -> None:
    settings = TycheSettings(
        tradier_api_token="",
        tradier_account_id="",
        gemini_api_key="g",
        data_dir=str(tmp_path),
        data_backend="local",
    )
    result = await run_options_snapshot_batch(settings=settings, ctx=ctx)
    assert "missing_tradier_api_token" in result.errors
