"""Unit tests for the Stock Deep Dive precompute batch workflow."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tyche.analysis.ticker_deep_dive import TickerDeepDive
from tyche.storage.paths import StorageContext
from tyche.workflow.deep_dive_batch import DeepDiveBatchResult, run_deep_dive_batch


def _ctx(tmp_path) -> StorageContext:
    return StorageContext(backend="local", local_root=tmp_path)


def _ohlcv_store(tickers: list[str]) -> MagicMock:
    store = MagicMock()
    store.exists = True
    store.get_all_tickers.return_value = tickers
    return store


def _meta_store(equity: list[str], caps: dict[str, float]) -> MagicMock:
    store = MagicMock()
    store.exists = True
    store.filter_equity_only.side_effect = lambda tickers: [
        t for t in tickers if t in equity
    ]
    store.get_market_caps.return_value = caps
    return store


def _fake_result(ticker: str, last_close: float = 100.0) -> TickerDeepDive:
    result = TickerDeepDive(ticker=ticker)
    result.last_close = last_close
    result.as_of_date = "2026-07-10"
    return result


class TestUniverseFiltering:
    @pytest.mark.asyncio
    async def test_equity_only_and_cap_floor_filtering(self, tmp_path):
        """Non-equity tickers and sub-floor caps are excluded before analysis."""
        ohlcv_store = _ohlcv_store(["AAPL", "MSFT", "SPY", "PENNY"])
        meta_store = _meta_store(
            equity=["AAPL", "MSFT", "PENNY"],  # SPY excluded (ETF)
            caps={"AAPL": 3_000_000_000_000, "MSFT": 3_000_000_000_000, "PENNY": 1_000_000},
        )

        analyzed: list[str] = []

        def _fake_analyze(self, ticker):
            analyzed.append(ticker)
            return _fake_result(ticker)

        with patch(
            "tyche.workflow.deep_dive_batch.TickerDeepDiveEngine.analyze",
            _fake_analyze,
        ):
            result = await run_deep_dive_batch(
                ohlcv_store=ohlcv_store,
                meta_store=meta_store,
                min_market_cap_millions=1000,
                ctx=_ctx(tmp_path),
            )

        # PENNY has no data below floor and non-zero cap -> excluded.
        # SPY is not equity -> excluded before cap filtering ever sees it.
        assert set(analyzed) == {"AAPL", "MSFT"}
        assert result.universe_size == 2
        assert result.tickers_computed == 2

    @pytest.mark.asyncio
    async def test_tickers_with_no_cap_data_pass_through(self, tmp_path):
        """Matches existing filter semantics: missing cap data doesn't exclude a ticker."""
        ohlcv_store = _ohlcv_store(["AAPL", "NEWCO"])
        meta_store = _meta_store(
            equity=["AAPL", "NEWCO"],
            caps={"AAPL": 3_000_000_000_000},  # NEWCO absent -> caps.get returns 0
        )

        with patch(
            "tyche.workflow.deep_dive_batch.TickerDeepDiveEngine.analyze",
            lambda self, ticker: _fake_result(ticker),
        ):
            result = await run_deep_dive_batch(
                ohlcv_store=ohlcv_store,
                meta_store=meta_store,
                min_market_cap_millions=1000,
                ctx=_ctx(tmp_path),
            )

        assert result.universe_size == 2
        assert result.tickers_computed == 2


class TestSkipZeroClose:
    @pytest.mark.asyncio
    async def test_skips_zero_close_tickers(self, tmp_path):
        ohlcv_store = _ohlcv_store(["AAPL", "DEAD"])
        meta_store = _meta_store(
            equity=["AAPL", "DEAD"],
            caps={"AAPL": 3_000_000_000_000, "DEAD": 3_000_000_000_000},
        )

        def _fake_analyze(self, ticker):
            return _fake_result(ticker, last_close=0.0 if ticker == "DEAD" else 100.0)

        with patch(
            "tyche.workflow.deep_dive_batch.TickerDeepDiveEngine.analyze",
            _fake_analyze,
        ):
            result = await run_deep_dive_batch(
                ohlcv_store=ohlcv_store,
                meta_store=meta_store,
                min_market_cap_millions=1000,
                ctx=_ctx(tmp_path),
            )

        assert result.tickers_computed == 1
        assert result.tickers_skipped == 1


class TestErrorIsolation:
    @pytest.mark.asyncio
    async def test_per_ticker_error_does_not_abort_batch(self, tmp_path):
        ohlcv_store = _ohlcv_store(["AAPL", "BOOM", "MSFT"])
        meta_store = _meta_store(
            equity=["AAPL", "BOOM", "MSFT"],
            caps={"AAPL": 3e12, "BOOM": 3e12, "MSFT": 3e12},
        )

        def _fake_analyze(self, ticker):
            if ticker == "BOOM":
                raise ValueError("boom")
            return _fake_result(ticker)

        with patch(
            "tyche.workflow.deep_dive_batch.TickerDeepDiveEngine.analyze",
            _fake_analyze,
        ):
            result = await run_deep_dive_batch(
                ohlcv_store=ohlcv_store,
                meta_store=meta_store,
                min_market_cap_millions=1000,
                ctx=_ctx(tmp_path),
            )

        assert result.tickers_computed == 2
        assert result.tickers_skipped == 1
        assert not result.errors  # per-ticker failures are isolated, not batch-level


class TestWriteCount:
    @pytest.mark.asyncio
    async def test_write_batch_persists_one_file_per_ticker(self, tmp_path):
        from tyche.market_data.deep_dive_store import DEEP_DIVE_REL, DeepDiveStore

        ohlcv_store = _ohlcv_store(["AAPL", "MSFT"])
        meta_store = _meta_store(
            equity=["AAPL", "MSFT"], caps={"AAPL": 3e12, "MSFT": 3e12}
        )

        with patch(
            "tyche.workflow.deep_dive_batch.TickerDeepDiveEngine.analyze",
            lambda self, ticker: _fake_result(ticker),
        ):
            result = await run_deep_dive_batch(
                ohlcv_store=ohlcv_store,
                meta_store=meta_store,
                min_market_cap_millions=1000,
                ctx=_ctx(tmp_path),
            )

        assert result.tickers_written == 2
        store = DeepDiveStore(ctx=_ctx(tmp_path))
        deep_dive_dir = tmp_path / DEEP_DIVE_REL
        files = sorted(p.name for p in deep_dive_dir.glob("*.parquet"))
        assert files == ["AAPL.parquet", "MSFT.parquet"]
        assert store.read_ticker("AAPL") is not None

    @pytest.mark.asyncio
    async def test_no_ctx_skips_write_with_error(self, tmp_path):
        ohlcv_store = _ohlcv_store(["AAPL"])
        meta_store = _meta_store(equity=["AAPL"], caps={"AAPL": 3e12})

        with patch(
            "tyche.workflow.deep_dive_batch.TickerDeepDiveEngine.analyze",
            lambda self, ticker: _fake_result(ticker),
        ):
            result = await run_deep_dive_batch(
                ohlcv_store=ohlcv_store,
                meta_store=meta_store,
                min_market_cap_millions=1000,
                ctx=None,
            )

        assert result.tickers_written == 0
        assert result.errors


class TestEmptyStore:
    @pytest.mark.asyncio
    async def test_missing_ohlcv_store_returns_error(self, tmp_path):
        ohlcv_store = MagicMock()
        ohlcv_store.exists = False
        meta_store = MagicMock()

        result = await run_deep_dive_batch(
            ohlcv_store=ohlcv_store,
            meta_store=meta_store,
            min_market_cap_millions=1000,
            ctx=_ctx(tmp_path),
        )

        assert isinstance(result, DeepDiveBatchResult)
        assert result.errors
        assert result.tickers_computed == 0

    @pytest.mark.asyncio
    async def test_empty_universe_returns_error(self, tmp_path):
        ohlcv_store = _ohlcv_store([])
        meta_store = _meta_store(equity=[], caps={})

        result = await run_deep_dive_batch(
            ohlcv_store=ohlcv_store,
            meta_store=meta_store,
            min_market_cap_millions=1000,
            ctx=_ctx(tmp_path),
        )

        assert result.universe_size == 0
        assert result.errors
