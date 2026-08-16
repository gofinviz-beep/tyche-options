"""Tests for cloud CSP scanner batch (Slice 5)."""

from __future__ import annotations

from datetime import date, timedelta
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


# The CSP scan path derives DTE and picks target expirations from
# ``date.today()`` (not the passed ``as_of_date``), so fixed fixture dates go
# stale: once the expiration is in the past every contract is dropped and the
# batch silently yields zero candidates.
_AS_OF = date.today()
_CHAIN_DATE = _AS_OF - timedelta(days=1)
_EXPIRATION = _AS_OF + timedelta(days=21)
_STRIKE = 98.0


def _occ_symbol(ticker: str, expiration: date, strike: float) -> str:
    return f"O:{ticker}{expiration:%y%m%d}P{round(strike * 1000):08d}"


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
                    "as_of_date": _AS_OF.isoformat(),
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
                    "as_of_date": _AS_OF.isoformat(),
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
                    "option_symbol": _occ_symbol("AAA", _EXPIRATION, _STRIKE),
                    "chain_date": _CHAIN_DATE.isoformat(),
                    "expiration": _EXPIRATION.isoformat(),
                    "strike": _STRIKE,
                    "option_type": "put",
                    "bid": 2.0,
                    "ask": 2.0,
                    "mid": 2.0,
                    "last": 2.0,
                    "volume": 50,
                    "open_interest": 0,
                    "dte": (_EXPIRATION - _AS_OF).days,
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
        as_of_date=_AS_OF,
    )

    assert result.symbols_scanned == 1
    assert result.csp_candidates >= 1
    rows, as_of = load_scanner_parquet(ctx=ctx)
    assert as_of == _AS_OF.isoformat()
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
        as_of_date=_AS_OF,
    )

    assert result.symbols_scanned == 1
    assert result.csp_candidates >= 1
