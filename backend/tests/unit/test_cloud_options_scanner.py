"""Tests for cloud CSP scanner batch (Slice 5)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from tyche.config import TycheSettings
from tyche.market_data.options_chain_snapshot_store import OPTIONS_CHAIN_CONTRACTS_REL
from tyche.market_data.options_scanner_store import load_scanner_parquet, load_scanner_report
from tyche.market_data.universe_candidates_store import CSP_SCAN_TICKERS_REL
from tyche.storage import write_parquet
from tyche.storage.paths import StorageContext
from tyche.workflow.options_scanner_batch import run_options_scanner_batch


@pytest.fixture
def settings(tmp_path: Path) -> TycheSettings:
    return TycheSettings(
        tradier_api_token="token",
        tradier_account_id="acct",
        gemini_api_key="g",
        data_dir=str(tmp_path),
        data_backend="local",
        min_institutional_pct=0.0,
        csp_min_bid=0.10,
        csp_min_premium_pct=0.1,
        csp_min_volume=1,
        min_scan_dte=1,
        target_dte_sweet_spot=14,
        available_capital=500_000.0,
    )


@pytest.fixture
def ctx(tmp_path: Path) -> StorageContext:
    return StorageContext(backend="local", local_root=tmp_path)


def _seed_scanner_inputs(tmp_path: Path) -> None:
    ctx = StorageContext(backend="local", local_root=tmp_path)
    write_parquet(
        pd.DataFrame(
            [
                {
                    "ticker": "AAA",
                    "rank": 1,
                    "last_close": 100.0,
                    "csp_eligible": True,
                    "as_of_date": "2026-06-24",
                }
            ]
        ),
        CSP_SCAN_TICKERS_REL,
        atomic=True,
        ctx=ctx,
    )
    write_parquet(
        pd.DataFrame(
            [
                {
                    "ticker": "AAA",
                    "trend_state": "pullback_to_8ema",
                    "conviction_level": "high",
                    "csp_eligible": True,
                    "last_close": 100.0,
                    "ema_8": 102.0,
                    "ema_21": 98.0,
                    "ema_8_slope": 0.2,
                    "ema_21_slope": 0.1,
                    "price_to_8ema_pct": -2.0,
                    "price_to_21ema_pct": 2.0,
                    "conviction_score": 0.8,
                    "csp_safety_prob": 0.9,
                    "iv_rank": 60.0,
                    "vrp": 0.1,
                    "as_of_date": "2026-06-24",
                }
            ]
        ),
        "signals/stocks/conviction.parquet",
        atomic=True,
        ctx=ctx,
    )
    write_parquet(
        pd.DataFrame(
            [
                {
                    "ticker": "AAA",
                    "option_symbol": "O:AAA260717P00090000",
                    "chain_date": "2026-06-23",
                    "expiration": "2026-07-17",
                    "strike": 98.0,
                    "option_type": "put",
                    "bid": 2.0,
                    "ask": 2.0,
                    "mid": 2.0,
                    "last": 2.0,
                    "volume": 50,
                    "open_interest": 0,
                    "dte": 24,
                    "source": "flatfile",
                }
            ]
        ),
        OPTIONS_CHAIN_CONTRACTS_REL,
        atomic=True,
        ctx=ctx,
    )


@pytest.mark.asyncio
async def test_run_options_scanner_batch_exports_candidates(
    tmp_path: Path,
    settings: TycheSettings,
    ctx: StorageContext,
) -> None:
    _seed_scanner_inputs(tmp_path)
    result = await run_options_scanner_batch(
        settings=settings,
        ctx=ctx,
        as_of_date=date(2026, 6, 24),
    )

    assert result.symbols_scanned == 1
    assert result.csp_candidates >= 1
    rows, as_of = load_scanner_parquet(ctx=ctx)
    assert as_of == "2026-06-24"
    assert rows[0]["symbol"] == "AAA"
    assert rows[0]["chain_source"] == "flatfile"

    report = load_scanner_report(ctx=ctx)
    assert report is not None
    assert report["candidate_source"] == CSP_SCAN_TICKERS_REL
    assert report["chain_source"] == "flatfile"


@pytest.mark.asyncio
async def test_run_options_scanner_batch_accepts_flatfile_option_type_code(
    tmp_path: Path,
    settings: TycheSettings,
    ctx: StorageContext,
) -> None:
    """Flatfile chains store OCC ``P``/``C`` — broker expects ``put``/``call``."""
    _seed_scanner_inputs(tmp_path)
    chain_ctx = StorageContext(backend="local", local_root=tmp_path)
    chain_df = pd.read_parquet(tmp_path / OPTIONS_CHAIN_CONTRACTS_REL)
    chain_df["option_type"] = "P"
    write_parquet(chain_df, OPTIONS_CHAIN_CONTRACTS_REL, atomic=True, ctx=chain_ctx)

    result = await run_options_scanner_batch(
        settings=settings,
        ctx=ctx,
        as_of_date=date(2026, 6, 24),
    )

    assert result.symbols_scanned == 1
    assert result.csp_candidates >= 1
