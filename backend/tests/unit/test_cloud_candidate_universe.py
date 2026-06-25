"""Tests for metadata-first candidate universe builder (Slice 3)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from tyche.config import TycheSettings
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.market_data.polygon import DailyBar, TickerInfo
from tyche.market_data.universe_candidates_store import (
    OPTIONS_CANDIDATES_REL,
    STOCKS_CANDIDATES_REL,
    load_candidates_parquet,
)
from tyche.storage import write_parquet
from tyche.storage.paths import StorageContext
from tyche.workflow.candidate_universe import (
    compute_priority_score,
    meta_first_tickers,
    run_candidate_universe_batch,
)


def _write_meta(tmp_path: Path, rows: list[dict]) -> TickerMetaStore:
    store = TickerMetaStore(data_dir=str(tmp_path))
    tickers = [
        TickerInfo(
            ticker=row["ticker"],
            name=row.get("name", row["ticker"]),
            market="stocks",
            locale="us",
            type=row.get("type", "CS"),
            active=True,
            primary_exchange=row.get("primary_exchange", "XNAS"),
            market_cap=row.get("market_cap", 0.0),
        )
        for row in rows
    ]
    store.write_meta(tickers)
    return store


def _write_ohlcv(tmp_path: Path, ticker: str, *, price: float, volume: float) -> None:
    store = OHLCVStore(data_dir=str(tmp_path))
    start = date(2026, 6, 1)
    bars = [
        DailyBar(
            ticker=ticker,
            date=start + timedelta(days=i),
            open=price,
            high=price + 1,
            low=price - 1,
            close=price,
            volume=volume,
            vwap=price,
        )
        for i in range(25)
    ]
    store.write_bars(bars)


def _write_alpha_rows(tmp_path: Path, rows: list[dict]) -> None:
    ctx = StorageContext(backend="local", local_root=tmp_path)
    frame = pd.DataFrame(rows)
    frame["as_of_date"] = "2026-06-24"
    frame["computed_at"] = "2026-06-24T04:00:00+00:00"
    write_parquet(frame, "alpha_signals_sustained.parquet", atomic=True, ctx=ctx)


def _write_conviction_rows(tmp_path: Path, rows: list[dict]) -> None:
    ctx = StorageContext(backend="local", local_root=tmp_path)
    frame = pd.DataFrame(rows)
    frame["as_of_date"] = "2026-06-24"
    write_parquet(frame, "signals/stocks/conviction.parquet", atomic=True, ctx=ctx)


@pytest.fixture
def settings(tmp_path: Path) -> TycheSettings:
    return TycheSettings(
        tradier_api_token="t",
        tradier_account_id="a",
        gemini_api_key="g",
        data_dir=str(tmp_path),
        data_backend="local",
        options_candidate_max_tickers=2,
        stocks_derived_max_tickers=3,
        options_snapshot_min_market_cap=4e9,
        min_market_cap_millions=4000,
        conviction_batch_min_price=5.0,
        conviction_batch_min_avg_volume=500_000,
        require_optionable=True,
    )


@pytest.fixture
def ctx(tmp_path: Path) -> StorageContext:
    return StorageContext(backend="local", local_root=tmp_path)


def test_meta_first_tickers_uses_meta_not_ohlcv_scan(tmp_path: Path) -> None:
    meta = _write_meta(
        tmp_path,
        [
            {"ticker": "AAPL", "type": "CS", "market_cap": 5e12},
            {"ticker": "SPY", "type": "ETF", "market_cap": 5e12},
        ],
    )
    _write_ohlcv(tmp_path, "ZZZZ", price=100.0, volume=1_000_000)
    assert meta_first_tickers(meta) == ["AAPL"]


def test_compute_priority_score_prefers_csp_eligible() -> None:
    high = compute_priority_score(
        conviction={
            "csp_eligible": True,
            "conviction_score": 0.8,
            "trend_state": "pullback_to_8ema",
            "csp_safety_prob": 0.9,
            "iv_rank": 60,
            "vrp": 0.1,
        },
        alpha={"alpha_score": 70, "signal": "buy"},
    )
    low = compute_priority_score(
        conviction={"csp_eligible": False, "conviction_score": 0.2},
        alpha=None,
    )
    assert high > low


def test_run_candidate_universe_batch_exports_ranked_options(
    tmp_path: Path,
    settings: TycheSettings,
    ctx: StorageContext,
) -> None:
    meta = _write_meta(
        tmp_path,
        [
            {"ticker": "AAA", "type": "CS", "market_cap": 5e12},
            {"ticker": "BBB", "type": "CS", "market_cap": 4.5e12},
            {"ticker": "CCC", "type": "CS", "market_cap": 4.2e12},
        ],
    )
    for ticker, vol in [("AAA", 1_000_000), ("BBB", 900_000), ("CCC", 800_000)]:
        _write_ohlcv(tmp_path, ticker, price=100.0, volume=vol)
    _write_alpha_rows(
        tmp_path,
        [
            {"ticker": "AAA", "alpha_score": 80, "signal": "buy", "horizon": "swing", "move_prob": 0.55},
            {"ticker": "BBB", "alpha_score": 60, "signal": "buy", "horizon": "swing", "move_prob": 0.50},
        ],
    )
    _write_conviction_rows(
        tmp_path,
        [
            {
                "ticker": "AAA",
                "trend_state": "pullback_to_8ema",
                "conviction_level": "high",
                "csp_eligible": True,
                "conviction_score": 0.8,
                "csp_safety_prob": 0.9,
                "iv_rank": 60.0,
                "vrp": 0.1,
            },
            {
                "ticker": "BBB",
                "trend_state": "pullback_to_8ema",
                "conviction_level": "high",
                "csp_eligible": True,
                "conviction_score": 0.7,
                "csp_safety_prob": 0.85,
                "iv_rank": 55.0,
                "vrp": 0.08,
            },
            {
                "ticker": "CCC",
                "trend_state": "uptrend",
                "conviction_level": "medium",
                "csp_eligible": False,
                "conviction_score": 0.2,
                "csp_safety_prob": 0.4,
                "iv_rank": 30.0,
                "vrp": 0.02,
            },
        ],
    )

    store = OHLCVStore(data_dir=str(tmp_path))
    result = run_candidate_universe_batch(
        settings=settings,
        data_store=store,
        meta_store=meta,
        ctx=ctx,
        as_of_date=date(2026, 6, 24),
    )

    assert result.options_candidates == 2
    assert result.stocks_candidates == 3
    options, as_of = load_candidates_parquet(rel_path=OPTIONS_CANDIDATES_REL, ctx=ctx)
    assert as_of == "2026-06-24"
    assert [row["ticker"] for row in options] == ["AAA", "BBB"]
    assert options[0]["rank"] == 1
    assert options[0]["priority_score"] >= options[1]["priority_score"]

    stocks, _ = load_candidates_parquet(rel_path=STOCKS_CANDIDATES_REL, ctx=ctx)
    assert len(stocks) == 3
    assert stocks[0]["ticker"] == "AAA"
