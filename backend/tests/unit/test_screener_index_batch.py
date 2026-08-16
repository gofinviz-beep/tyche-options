"""Unit tests for the v3 Stock Screener index batch — the "Diamond Finder"."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tyche.analysis.ticker_deep_dive import EMAStack, MultiTimeframeRSI, TickerDeepDive
from tyche.market_data.screener_index_store import ScreenerIndexStore
from tyche.storage.paths import StorageContext
from tyche.workflow.screener_index_batch import (
    ScreenerIndexResult,
    _quality_score,
    build_screener_row,
    clamp,
    compute_setup_label,
    compute_setup_score,
    run_screener_index_batch,
)


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


def _deep_dive(ticker: str, last_close: float = 100.0, **overrides) -> TickerDeepDive:
    result = TickerDeepDive(ticker=ticker)
    result.last_close = last_close
    result.as_of_date = "2026-07-10"
    result.market_cap = overrides.get("market_cap", 15_000_000_000)
    # 0-1 fraction, matching TickerMetaStore.get_institutional_pcts().
    result.institutional_pct = overrides.get("institutional_pct", 0.60)
    result.name = f"{ticker} Inc"
    result.sector = "Technology"
    result.rsi = MultiTimeframeRSI(
        daily=overrides.get("rsi_daily", 42.0),
        weekly=overrides.get("rsi_weekly", 55.0),
        monthly=overrides.get("rsi_monthly", 55.0),
        quarterly=overrides.get("rsi_quarterly", 65.0),
    )
    result.ema_stack = EMAStack(
        ema_8=overrides.get("ema_8", last_close * 0.99),
        ema_21=overrides.get("ema_21", last_close * 0.97),
        ema_50=overrides.get("ema_50", last_close * 0.9),
        sma_200=overrides.get("sma_200", last_close * 0.85),
        pct_vs_ema_8=overrides.get("pct_vs_ema_8", 1.0),
        pct_vs_ema_21=overrides.get("pct_vs_ema_21", 3.0),
        slope_ema_21=overrides.get("slope_ema_21", 0.5),
    )
    result.returns = {"1M": 5.0, "3M": overrides.get("ret_3m", 15.0), "6M": 25.0, "1Y": 40.0}
    return result


# ── Diamond Finder formula unit tests ───────────────────────────────────


class TestClamp:
    def test_clamp_bounds(self):
        assert clamp(-5) == 0.0
        assert clamp(5) == 1.0
        assert clamp(0.5) == 0.5
        assert clamp(150, 0, 100) == 100
        assert clamp(-10, 0, 100) == 0


class TestInstitutionalOwnershipScale:
    """``institutional_pct`` is a 0-1 fraction, not a 0-100 percent.

    The Diamond Finder calibration is written on the percent scale ("full
    credit at 60% held"), so the quality component must convert. Feeding the
    raw fraction into a ``/ 60`` divisor collapsed all 7 institutional points
    to ~0 for every ticker in the universe.
    """

    # Mega-cap above its 50-EMA: 8 cap points + 5 trend points.
    _NON_INST_POINTS = 13.0

    def _row(self, institutional_pct: float | None) -> dict:
        return {
            "market_cap": 15_000_000_000,
            "institutional_pct": institutional_pct,
            "last_close": 100.0,
            "ema_50": 90.0,
        }

    def test_no_ownership_data_earns_no_institutional_points(self):
        assert _quality_score(self._row(None)) == pytest.approx(self._NON_INST_POINTS)

    def test_full_credit_at_the_60_pct_cap(self):
        assert _quality_score(self._row(0.60)) == pytest.approx(self._NON_INST_POINTS + 7.0)

    def test_half_credit_at_half_the_cap(self):
        assert _quality_score(self._row(0.30)) == pytest.approx(self._NON_INST_POINTS + 3.5)

    def test_typical_large_cap_ownership_is_capped_not_collapsed(self):
        """A real value (82% held) must earn the full component, not ~0."""
        earned = _quality_score(self._row(0.82)) - self._NON_INST_POINTS
        assert earned == pytest.approx(7.0)

    def test_quality_score_stays_within_its_0_20_budget(self):
        assert _quality_score(self._row(1.0)) == pytest.approx(20.0)


class TestPrimePullbackFixture:
    """The core diamond: strong quarterly structure + cooled daily timing."""

    def _prime_pullback_row(self) -> dict:
        return {
            "rsi_quarterly": 65.0,
            "rsi_monthly": 55.0,
            "rsi_weekly": 55.0,
            "rsi_daily": 42.0,
            "last_close": 110.0,
            "sma_200": 100.0,
            "ema_50": 90.0,
            "slope_ema_21": 0.5,
            "pct_vs_ema_8": 1.0,
            "pct_vs_ema_21": 3.0,
            "market_cap": 15_000_000_000,
            "institutional_pct": 0.70,
            "ret_3m": 15.0,
        }

    def test_prime_pullback_scores_at_least_70(self):
        row = self._prime_pullback_row()
        score = compute_setup_score(row)
        assert score >= 70.0

    def test_prime_pullback_label(self):
        row = self._prime_pullback_row()
        score = compute_setup_score(row)
        label = compute_setup_label(row, score)
        assert label == "Prime Pullback"

    def test_prime_pullback_no_haircut_applied(self):
        """Daily RSI 42 (< 70) means the anti-chase haircut must NOT fire."""
        row = self._prime_pullback_row()
        score = compute_setup_score(row)
        # Sanity: without the haircut, raw component sum would also be >= 70;
        # confirm the returned score isn't suspiciously small (i.e. haircut fired).
        assert score > 90.0


class TestOverextendedWeakFixture:
    """Overbought spike on weak quarterly structure — must be haircut + avoided."""

    def _overextended_row(self) -> dict:
        return {
            "rsi_quarterly": 45.0,
            "rsi_monthly": 50.0,
            "rsi_weekly": 60.0,
            "rsi_daily": 75.0,
            "last_close": 120.0,
            "sma_200": 100.0,
            "ema_50": 100.0,
            "slope_ema_21": 0.2,
            "pct_vs_ema_8": 15.0,
            "pct_vs_ema_21": 18.0,
            "market_cap": 5_000_000_000,
            "institutional_pct": 0.40,
            "ret_3m": 50.0,
        }

    def test_overextended_is_haircut(self):
        row = self._overextended_row()
        score = compute_setup_score(row)
        # Without the 0.6x anti-chase haircut this would score >30; with it, <25.
        assert score < 25.0

    def test_overextended_label(self):
        row = self._overextended_row()
        score = compute_setup_score(row)
        label = compute_setup_label(row, score)
        assert label == "Overextended"

    def test_haircut_only_fires_above_thresholds(self):
        """Same weak structure but not extended (pct_vs_ema_8 <= 10) skips the haircut."""
        row = self._overextended_row()
        row["pct_vs_ema_8"] = 8.0
        haircut_score = compute_setup_score(self._overextended_row())
        no_haircut_score = compute_setup_score(row)
        assert no_haircut_score > haircut_score


class TestSetupLabelOrdering:
    def test_structural_uptrend(self):
        row = {
            "rsi_quarterly": 58.0,
            "rsi_monthly": 50.0,
            "rsi_daily": 60.0,
            "last_close": 105.0,
            "sma_200": 100.0,
        }
        score = 65.0
        assert compute_setup_label(row, score) == "Structural Uptrend"

    def test_emerging_breakout(self):
        row = {
            "rsi_quarterly": 52.0,
            "rsi_monthly": 48.0,
            "rsi_daily": 45.0,
            "last_close": 102.0,
            "sma_200": 100.0,
        }
        score = 40.0
        assert compute_setup_label(row, score) == "Emerging Breakout"

    def test_weak_structure(self):
        row = {
            "rsi_quarterly": 35.0,
            "rsi_monthly": 40.0,
            "rsi_daily": 40.0,
            "last_close": 90.0,
            "sma_200": 100.0,
        }
        score = 15.0
        assert compute_setup_label(row, score) == "Weak Structure"

    def test_watch_base_building_fallback(self):
        row = {
            "rsi_quarterly": 50.0,
            "rsi_monthly": 50.0,
            "rsi_daily": 50.0,
            "last_close": 95.0,
            "sma_200": 100.0,
        }
        score = 45.0
        assert compute_setup_label(row, score) == "Watch / Base Building"


class TestBuildScreenerRow:
    def test_returns_none_for_zero_close(self):
        deep_dive = _deep_dive("DEAD", last_close=0.0)
        assert build_screener_row("DEAD", deep_dive) is None

    def test_returns_none_for_missing_deep_dive(self):
        assert build_screener_row("MISSING", None) is None

    def test_maps_uppercase_return_keys_to_lowercase_columns(self):
        deep_dive = _deep_dive("AAPL")
        row = build_screener_row("AAPL", deep_dive)
        assert row["ret_1m"] == 5.0
        assert row["ret_3m"] == 15.0
        assert row["ret_6m"] == 25.0
        assert row["ret_1y"] == 40.0

    def test_includes_setup_score_and_label(self):
        deep_dive = _deep_dive("AAPL")
        row = build_screener_row("AAPL", deep_dive)
        assert "setup_score" in row
        assert "setup_label" in row
        assert row["setup_label"] == "Prime Pullback"


# ── Full batch orchestration tests ──────────────────────────────────────


class TestDeepDiveStorePreference:
    @pytest.mark.asyncio
    async def test_prefers_precomputed_deep_dive_store(self, tmp_path):
        """When DeepDiveStore has a payload, the inline engine must NOT be called."""
        ohlcv_store = _ohlcv_store(["AAPL"])
        meta_store = _meta_store(equity=["AAPL"], caps={"AAPL": 3e12})

        deep_dive_store = MagicMock()
        deep_dive_store.read_ticker.return_value = (_deep_dive("AAPL"), "2026-07-10")

        engine_called: list[str] = []

        def _fake_analyze(self, ticker):
            engine_called.append(ticker)
            return _deep_dive(ticker)

        with patch(
            "tyche.workflow.screener_index_batch.TickerDeepDiveEngine.analyze",
            _fake_analyze,
        ):
            result = await run_screener_index_batch(
                deep_dive_store=deep_dive_store,
                ohlcv_store=ohlcv_store,
                meta_store=meta_store,
                min_market_cap_millions=1000,
                ctx=_ctx(tmp_path),
            )

        assert engine_called == []
        assert result.tickers_indexed == 1
        deep_dive_store.read_ticker.assert_called_once_with("AAPL")

    @pytest.mark.asyncio
    async def test_falls_back_to_inline_engine_when_absent(self, tmp_path):
        """No DeepDiveStore payload (e.g. deep-dive batch never ran) -> inline fallback."""
        ohlcv_store = _ohlcv_store(["AAPL"])
        meta_store = _meta_store(equity=["AAPL"], caps={"AAPL": 3e12})

        deep_dive_store = MagicMock()
        deep_dive_store.read_ticker.return_value = None

        engine_called: list[str] = []

        def _fake_analyze(self, ticker):
            engine_called.append(ticker)
            return _deep_dive(ticker)

        with patch(
            "tyche.workflow.screener_index_batch.TickerDeepDiveEngine.analyze",
            _fake_analyze,
        ):
            result = await run_screener_index_batch(
                deep_dive_store=deep_dive_store,
                ohlcv_store=ohlcv_store,
                meta_store=meta_store,
                min_market_cap_millions=1000,
                ctx=_ctx(tmp_path),
            )

        assert engine_called == ["AAPL"]
        assert result.tickers_indexed == 1

    @pytest.mark.asyncio
    async def test_falls_back_when_no_deep_dive_store_provided(self, tmp_path):
        """``deep_dive_store=None`` (never wired) still works via the inline engine."""
        ohlcv_store = _ohlcv_store(["AAPL"])
        meta_store = _meta_store(equity=["AAPL"], caps={"AAPL": 3e12})

        with patch(
            "tyche.workflow.screener_index_batch.TickerDeepDiveEngine.analyze",
            lambda self, ticker: _deep_dive(ticker),
        ):
            result = await run_screener_index_batch(
                deep_dive_store=None,
                ohlcv_store=ohlcv_store,
                meta_store=meta_store,
                min_market_cap_millions=1000,
                ctx=_ctx(tmp_path),
            )

        assert result.tickers_indexed == 1


class TestUniverseFiltering:
    @pytest.mark.asyncio
    async def test_equity_only_and_cap_floor_filtering(self, tmp_path):
        ohlcv_store = _ohlcv_store(["AAPL", "MSFT", "SPY", "PENNY"])
        meta_store = _meta_store(
            equity=["AAPL", "MSFT", "PENNY"],
            caps={"AAPL": 3e12, "MSFT": 3e12, "PENNY": 1_000_000},
        )
        deep_dive_store = MagicMock()
        deep_dive_store.read_ticker.return_value = None

        with patch(
            "tyche.workflow.screener_index_batch.TickerDeepDiveEngine.analyze",
            lambda self, ticker: _deep_dive(ticker),
        ):
            result = await run_screener_index_batch(
                deep_dive_store=deep_dive_store,
                ohlcv_store=ohlcv_store,
                meta_store=meta_store,
                min_market_cap_millions=1000,
                ctx=_ctx(tmp_path),
            )

        assert result.universe_size == 2
        assert result.tickers_indexed == 2


class TestSkipZeroClose:
    @pytest.mark.asyncio
    async def test_skips_zero_close_tickers(self, tmp_path):
        ohlcv_store = _ohlcv_store(["AAPL", "DEAD"])
        meta_store = _meta_store(equity=["AAPL", "DEAD"], caps={"AAPL": 3e12, "DEAD": 3e12})
        deep_dive_store = MagicMock()
        deep_dive_store.read_ticker.return_value = None

        def _fake_analyze(self, ticker):
            return _deep_dive(ticker, last_close=0.0 if ticker == "DEAD" else 100.0)

        with patch(
            "tyche.workflow.screener_index_batch.TickerDeepDiveEngine.analyze",
            _fake_analyze,
        ):
            result = await run_screener_index_batch(
                deep_dive_store=deep_dive_store,
                ohlcv_store=ohlcv_store,
                meta_store=meta_store,
                min_market_cap_millions=1000,
                ctx=_ctx(tmp_path),
            )

        assert result.tickers_indexed == 1
        assert result.tickers_skipped == 1


class TestErrorIsolation:
    @pytest.mark.asyncio
    async def test_per_ticker_error_does_not_abort_batch(self, tmp_path):
        ohlcv_store = _ohlcv_store(["AAPL", "BOOM", "MSFT"])
        meta_store = _meta_store(
            equity=["AAPL", "BOOM", "MSFT"], caps={"AAPL": 3e12, "BOOM": 3e12, "MSFT": 3e12}
        )
        deep_dive_store = MagicMock()
        deep_dive_store.read_ticker.return_value = None

        def _fake_analyze(self, ticker):
            if ticker == "BOOM":
                raise ValueError("boom")
            return _deep_dive(ticker)

        with patch(
            "tyche.workflow.screener_index_batch.TickerDeepDiveEngine.analyze",
            _fake_analyze,
        ):
            result = await run_screener_index_batch(
                deep_dive_store=deep_dive_store,
                ohlcv_store=ohlcv_store,
                meta_store=meta_store,
                min_market_cap_millions=1000,
                ctx=_ctx(tmp_path),
            )

        assert result.tickers_indexed == 2
        assert result.tickers_skipped == 1
        assert not result.errors


class TestWriteSingleFile:
    @pytest.mark.asyncio
    async def test_write_persists_single_compact_parquet(self, tmp_path):
        from tyche.market_data.screener_index_store import SCREENER_INDEX_REL

        ohlcv_store = _ohlcv_store(["AAPL", "MSFT"])
        meta_store = _meta_store(equity=["AAPL", "MSFT"], caps={"AAPL": 3e12, "MSFT": 3e12})
        deep_dive_store = MagicMock()
        deep_dive_store.read_ticker.return_value = None

        with patch(
            "tyche.workflow.screener_index_batch.TickerDeepDiveEngine.analyze",
            lambda self, ticker: _deep_dive(ticker),
        ):
            ctx = _ctx(tmp_path)
            result = await run_screener_index_batch(
                deep_dive_store=deep_dive_store,
                ohlcv_store=ohlcv_store,
                meta_store=meta_store,
                min_market_cap_millions=1000,
                ctx=ctx,
            )

        assert result.tickers_written == 2
        parquet_files = list(tmp_path.rglob("*.parquet"))
        assert len(parquet_files) == 1
        assert parquet_files[0] == tmp_path / SCREENER_INDEX_REL

        store = ScreenerIndexStore(ctx=ctx)
        df = store.read()
        assert df is not None
        assert set(df["ticker"]) == {"AAPL", "MSFT"}

    @pytest.mark.asyncio
    async def test_no_ctx_skips_write_with_error(self, tmp_path):
        ohlcv_store = _ohlcv_store(["AAPL"])
        meta_store = _meta_store(equity=["AAPL"], caps={"AAPL": 3e12})
        deep_dive_store = MagicMock()
        deep_dive_store.read_ticker.return_value = None

        with patch(
            "tyche.workflow.screener_index_batch.TickerDeepDiveEngine.analyze",
            lambda self, ticker: _deep_dive(ticker),
        ):
            result = await run_screener_index_batch(
                deep_dive_store=deep_dive_store,
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

        result = await run_screener_index_batch(
            deep_dive_store=None,
            ohlcv_store=ohlcv_store,
            meta_store=meta_store,
            min_market_cap_millions=1000,
            ctx=_ctx(tmp_path),
        )

        assert isinstance(result, ScreenerIndexResult)
        assert result.errors
        assert result.tickers_indexed == 0

    @pytest.mark.asyncio
    async def test_empty_universe_returns_error(self, tmp_path):
        ohlcv_store = _ohlcv_store([])
        meta_store = _meta_store(equity=[], caps={})

        result = await run_screener_index_batch(
            deep_dive_store=None,
            ohlcv_store=ohlcv_store,
            meta_store=meta_store,
            min_market_cap_millions=1000,
            ctx=_ctx(tmp_path),
        )

        assert result.universe_size == 0
        assert result.errors
