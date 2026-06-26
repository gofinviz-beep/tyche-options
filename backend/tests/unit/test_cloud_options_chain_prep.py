"""Tests for flatfile-based options chain prep batch (Slice 4)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from tyche.config import TycheSettings
from tyche.market_data.options_chain_snapshot_store import (
    OPTIONS_CHAIN_CONTRACTS_REL,
    OPTIONS_CHAIN_PREP_REPORT_REL,
    load_prep_contracts_parquet,
)
from tyche.market_data.options_history_store import OptionsHistoryStore
from tyche.market_data.universe_candidates_store import OPTIONS_CANDIDATES_REL
from tyche.storage import read_json, write_parquet
from tyche.storage.paths import StorageContext
from tyche.workflow.options_chain_prep import run_options_chain_prep_batch


@pytest.fixture
def settings(tmp_path: Path) -> TycheSettings:
    return TycheSettings(
        tradier_api_token="token",
        tradier_account_id="acct",
        gemini_api_key="g",
        data_dir=str(tmp_path),
        data_backend="local",
        options_snapshot_max_tickers=2,
        options_snapshot_min_dte=1,
        options_snapshot_max_dte=45,
        ingest_window="morning",
    )


@pytest.fixture
def ctx(tmp_path: Path) -> StorageContext:
    return StorageContext(backend="local", local_root=tmp_path)


def _seed_candidate_and_history(tmp_path: Path) -> None:
    ctx = StorageContext(backend="local", local_root=tmp_path)
    write_parquet(
        pd.DataFrame(
            [
                {"ticker": "AAA", "rank": 1, "as_of_date": "2026-06-24"},
                {"ticker": "BBB", "rank": 2, "as_of_date": "2026-06-24"},
            ]
        ),
        OPTIONS_CANDIDATES_REL,
        atomic=True,
        ctx=ctx,
    )
    store = OptionsHistoryStore(data_dir=str(tmp_path))
    store.write_ticker_data(
        "AAA",
        pd.DataFrame(
            [
                {
                    "date": date(2026, 6, 23),
                    "option_ticker": "O:AAA260717P00180000",
                    "underlying": "AAA",
                    "expiration": date(2026, 7, 17),
                    "strike": 180.0,
                    "option_type": "P",
                    "open": 2.0,
                    "close": 2.5,
                    "high": 2.6,
                    "low": 1.9,
                    "volume": 120,
                    "transactions": 10,
                    "dte": 24,
                }
            ]
        ),
    )


def test_run_options_chain_prep_batch_builds_contract_artifact(
    tmp_path: Path,
    settings: TycheSettings,
    ctx: StorageContext,
) -> None:
    _seed_candidate_and_history(tmp_path)

    result = run_options_chain_prep_batch(
        settings=settings,
        ctx=ctx,
        run_id="run-test",
        chain_date=date(2026, 6, 23),
    )

    assert result.tickers_requested == 2
    assert result.tickers_with_contracts == 1
    assert result.contract_rows == 1

    contracts, chain_date = load_prep_contracts_parquet(ctx=ctx)
    assert chain_date == "2026-06-23"
    assert len(contracts) == 1
    assert contracts[0]["ticker"] == "AAA"
    assert contracts[0]["source"] == "flatfile"
    assert contracts[0]["bid"] == 2.5
    assert contracts[0]["open_interest"] == 0

    report = read_json(OPTIONS_CHAIN_PREP_REPORT_REL, ctx=ctx)
    assert report["source"] == "flatfile"
    assert report["run_id"] == "run-test"
    assert OPTIONS_CHAIN_CONTRACTS_REL in report["output_paths"]
