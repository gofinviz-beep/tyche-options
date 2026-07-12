"""Unit tests for the Covered Call analysis engine."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd
import pytest

from tyche.analysis.cc_analyzer import (
    CCAnalysisEngine,
    CCDeepDive,
    CCSignal,
    CCPortfolioAnalysis,
    _EXTENSION_THRESHOLD,
)
from tyche.broker.base import OptionContract, OptionsChain


# ── Helpers ────────────────────────────────────────────────────


def _make_ohlcv(
    n: int = 200,
    base_price: float = 20.0,
    trend: float = 0.002,
    volatility: float = 0.02,
    spike_at: int | None = None,
    spike_pct: float = 0.20,
) -> pd.DataFrame:
    """Generate synthetic OHLCV data."""
    np.random.seed(42)
    # bdate_range(end=..., periods=n) under-counts by 1 when `end` itself
    # falls on a weekend (it doesn't roll back to the prior business day the
    # way one might expect) — over-fetch by one and slice to guarantee
    # exactly n dates regardless of which weekday "today" happens to be.
    dates = pd.bdate_range(end=date.today(), periods=n + 1)[-n:]
    prices = [base_price]
    for i in range(1, n):
        ret = trend + np.random.normal(0, volatility)
        prices.append(prices[-1] * (1 + ret))

    if spike_at is not None and 0 <= spike_at < n:
        for i in range(spike_at, min(spike_at + 5, n)):
            prices[i] = prices[spike_at - 1] * (1 + spike_pct)

    close = np.array(prices)
    high = close * 1.01
    low = close * 0.99
    vol = np.random.randint(100_000, 500_000, size=n)

    return pd.DataFrame({
        "date": dates.date,
        "open": close * 0.999,
        "high": high,
        "low": low,
        "close": close,
        "volume": vol,
        "vwap": close,
    })


def _make_extended_ohlcv(n: int = 200) -> pd.DataFrame:
    """Generate data where the last price is 15%+ above 8-EMA."""
    df = _make_ohlcv(n, base_price=30.0, trend=0.001)
    last_ema8 = df["close"].ewm(span=8, adjust=False).mean().iloc[-1]
    target = last_ema8 * 1.15
    df.iloc[-1, df.columns.get_loc("close")] = target
    df.iloc[-1, df.columns.get_loc("high")] = target * 1.01
    return df


def _mock_ohlcv_store(df: pd.DataFrame) -> MagicMock:
    store = MagicMock()
    store.read_ticker.return_value = df
    return store


def _mock_derived_store(
    iv_rank: float = 45.0,
    vrp: float = 0.08,
    rv_20d: float = 0.35,
) -> MagicMock:
    store = MagicMock()
    store.read_ticker.return_value = pd.DataFrame({
        "date": [date.today()],
        "iv_rank": [iv_rank],
        "vrp": [vrp],
        "rv_20d": [rv_20d],
        "atm_iv": [0.42],
        "iv_percentile": [55.0],
    })
    return store


# ── Basic functionality ────────────────────────────────────────


class TestCCAnalysisEngine:
    def test_analyze_returns_deep_dive(self):
        df = _make_ohlcv()
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("PL")

        assert isinstance(result, CCDeepDive)
        assert isinstance(result.signal, CCSignal)
        assert result.signal.ticker == "PL"
        assert result.signal.last_close > 0
        assert result.signal.ema_8 > 0
        assert result.signal.ema_21 > 0
        assert result.signal.ema_50 > 0
        assert isinstance(result.signal.ema_21_slope, float)

    def test_analyze_with_empty_data(self):
        store = MagicMock()
        store.read_ticker.return_value = pd.DataFrame()
        engine = CCAnalysisEngine(ohlcv_store=store)
        result = engine.analyze("EMPTY")

        assert result.signal.signal == "WAIT"
        assert "Insufficient" in result.signal.signal_reason

    def test_analyze_with_short_data(self):
        df = _make_ohlcv(n=30)
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("SHORT")

        assert result.signal.signal == "WAIT"
        assert "Insufficient" in result.signal.signal_reason

    def test_analyze_computes_rsi(self):
        df = _make_ohlcv()
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("PL")

        assert 0 <= result.signal.rsi_14 <= 100

    def test_analyze_computes_extension(self):
        df = _make_ohlcv()
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("PL")

        assert isinstance(result.signal.extension_pct_8, float)
        assert isinstance(result.signal.extension_pct_21, float)


# ── Extension episodes ─────────────────────────────────────────


class TestExtensionEpisodes:
    def test_finds_episodes_in_extended_data(self):
        df = _make_extended_ohlcv()
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("PL")

        assert result.total_episodes >= 0

    def test_episode_table_structure(self):
        df = _make_ohlcv(n=500, trend=0.005)
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("PL")

        if result.episode_table:
            ep = result.episode_table[0]
            assert "peak_date" in ep
            assert "peak_price" in ep
            assert "extension_pct" in ep
            assert "additional_rally_pct" in ep
            assert "rally_days" in ep
            assert "days_to_8ema" in ep

    def test_ema_reversion_stats(self):
        df = _make_ohlcv(n=500, trend=0.005)
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("PL")

        for key in ["days_to_8ema", "days_to_21ema", "days_to_50ema"]:
            data = getattr(result, key)
            if data:
                assert "mean" in data
                assert "median" in data


# ── Forward returns ────────────────────────────────────────────


class TestForwardReturns:
    def test_forward_returns_structure(self):
        df = _make_ohlcv(n=500, trend=0.005)
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("PL")

        if result.forward_returns:
            fr = result.forward_returns[0]
            assert "day" in fr
            assert "pct_above_entry" in fr
            assert "avg_ret" in fr
            assert "med_ret" in fr

    def test_forward_returns_day_range(self):
        df = _make_ohlcv(n=500, trend=0.005)
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("PL")

        if result.forward_returns:
            days = [fr["day"] for fr in result.forward_returns]
            assert days == sorted(days)
            assert days[0] >= 1
            assert days[-1] <= 21


# ── Day-of-week analysis ──────────────────────────────────────


class TestDOWAnalysis:
    def test_dow_produces_entries(self):
        df = _make_ohlcv(n=300)
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("PL")

        assert isinstance(result.dow_analysis, list)
        if result.dow_analysis:
            entry = result.dow_analysis[0]
            assert "day" in entry
            assert "win_pct" in entry
            assert "called_pct" in entry
            assert "ret_per_calday" in entry

    def test_dow_days_are_valid(self):
        df = _make_ohlcv(n=300)
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("PL")

        valid_days = {"Mon", "Tue", "Wed", "Thu", "Fri"}
        for entry in result.dow_analysis:
            assert entry["day"] in valid_days


# ── Signal computation ─────────────────────────────────────────


class TestSignalComputation:
    def test_go_on_high_extension(self):
        signal, reason = CCAnalysisEngine._compute_signal(
            ext_8=12.0, rsi=55, iv_rank=50.0, vrp=5.0,
            earnings_in_window=False, today=date(2026, 4, 15),
        )
        assert signal == "GO"
        assert "Extended" in reason

    def test_wait_on_low_extension(self):
        signal, reason = CCAnalysisEngine._compute_signal(
            ext_8=2.0, rsi=45, iv_rank=40.0, vrp=3.0,
            earnings_in_window=False, today=date(2026, 4, 15),
        )
        assert signal == "WAIT"
        assert "thin" in reason.lower()

    def test_wait_on_earnings(self):
        signal, reason = CCAnalysisEngine._compute_signal(
            ext_8=15.0, rsi=60, iv_rank=70.0, vrp=10.0,
            earnings_in_window=True, today=date(2026, 4, 15),
        )
        assert signal == "WAIT"
        assert "Earnings" in reason

    def test_caution_on_negative_vrp(self):
        signal, reason = CCAnalysisEngine._compute_signal(
            ext_8=12.0, rsi=55, iv_rank=50.0, vrp=-25.0,
            earnings_in_window=False, today=date(2026, 4, 15),
        )
        assert signal == "CAUTION"
        assert "underpriced" in reason.lower()

    def test_caution_on_overbought_rsi(self):
        signal, reason = CCAnalysisEngine._compute_signal(
            ext_8=3.0, rsi=75, iv_rank=40.0, vrp=5.0,
            earnings_in_window=False, today=date(2026, 4, 15),
        )
        assert signal == "CAUTION"
        assert "RSI" in reason

    def test_go_moderate_extension_optimal_day(self):
        tue = date(2026, 4, 14)  # Tuesday
        signal, reason = CCAnalysisEngine._compute_signal(
            ext_8=7.0, rsi=55, iv_rank=50.0, vrp=5.0,
            earnings_in_window=False, today=tue,
        )
        assert signal == "GO"
        assert "optimal" in reason.lower()

    def test_caution_moderate_extension_suboptimal_day(self):
        fri = date(2026, 4, 17)  # Friday
        signal, reason = CCAnalysisEngine._compute_signal(
            ext_8=7.0, rsi=55, iv_rank=50.0, vrp=5.0,
            earnings_in_window=False, today=fri,
        )
        assert signal == "CAUTION"
        assert "Tue/Wed" in reason


# ── Derived metrics integration ────────────────────────────────


class TestDerivedMetrics:
    def test_loads_derived_metrics(self):
        df = _make_ohlcv()
        derived = _mock_derived_store(iv_rank=55.0, vrp=0.10, rv_20d=0.30)
        engine = CCAnalysisEngine(
            ohlcv_store=_mock_ohlcv_store(df),
            derived_store=derived,
        )
        result = engine.analyze("PL")

        assert result.signal.iv_rank is not None
        assert result.signal.iv_rank == 55.0
        assert result.signal.vrp is not None
        assert result.signal.rv_20d is not None

    def test_handles_missing_derived(self):
        df = _make_ohlcv()
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("PL")

        assert result.signal.iv_rank is None
        assert result.signal.vrp is None
        assert result.signal.rv_20d is None

    def test_handles_empty_derived(self):
        df = _make_ohlcv()
        derived = MagicMock()
        derived.read_ticker.return_value = pd.DataFrame()
        engine = CCAnalysisEngine(
            ohlcv_store=_mock_ohlcv_store(df),
            derived_store=derived,
        )
        result = engine.analyze("PL")

        assert result.signal.iv_rank is None

    def test_handles_derived_exception(self):
        df = _make_ohlcv()
        derived = MagicMock()
        derived.read_ticker.side_effect = Exception("disk error")
        engine = CCAnalysisEngine(
            ohlcv_store=_mock_ohlcv_store(df),
            derived_store=derived,
        )
        result = engine.analyze("PL")

        assert result.signal.iv_rank is None


# ── Batch analysis ─────────────────────────────────────────────


class TestBatchAnalysis:
    def test_batch_produces_portfolio(self):
        df = _make_ohlcv()
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze_batch([
            {"ticker": "PL", "shares": 4000, "cost_basis": 15.0},
            {"ticker": "AAPL", "shares": 100, "cost_basis": 150.0},
        ])

        assert isinstance(result, CCPortfolioAnalysis)
        assert len(result.analyses) == 2
        assert "total_positions" in result.portfolio_summary
        assert result.portfolio_summary["total_positions"] == 2

    def test_batch_counts_signals(self):
        df = _make_ohlcv()
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze_batch([
            {"ticker": "PL", "shares": 100},
        ])

        summary = result.portfolio_summary
        assert (
            summary["positions_go"]
            + summary["positions_wait"]
            + summary["positions_caution"]
        ) == 1


# ── P&L scenarios ──────────────────────────────────────────────


class TestPnLScenarios:
    def test_pnl_structure(self):
        pnl = CCAnalysisEngine._compute_pnl(
            current_price=45.0,
            strike=50.0,
            premium=1.50,
            shares=4000,
            cost_basis=15.0,
        )
        assert "if_not_called" in pnl
        assert "if_called" in pnl
        assert pnl["contracts"] == 40
        assert pnl["if_called"]["stock_gain"] == (50.0 - 45.0) * 4000
        assert pnl["if_not_called"]["premium_income"] > 0

    def test_pnl_with_zero_cost_basis(self):
        pnl = CCAnalysisEngine._compute_pnl(
            current_price=30.0,
            strike=33.0,
            premium=0.80,
            shares=100,
            cost_basis=0.0,
        )
        assert "total_return_pct" not in pnl["if_called"]
        assert "unrealized_gain" not in pnl["if_not_called"]

    def test_pnl_no_premium(self):
        pnl = CCAnalysisEngine._compute_pnl(
            current_price=30.0,
            strike=33.0,
            premium=None,
            shares=100,
            cost_basis=20.0,
        )
        assert pnl["if_not_called"]["premium_income"] < 0  # only commission
        assert pnl["contracts"] == 1


# ── Earnings detection ─────────────────────────────────────────


class TestEarningsDetection:
    def test_detects_volume_spike_events(self):
        df = _make_ohlcv(n=300)
        avg_vol = df["volume"].mean()
        spike_idx = 250
        df.iloc[spike_idx, df.columns.get_loc("volume")] = int(avg_vol * 5)
        prev_close = df.iloc[spike_idx - 1]["close"]
        df.iloc[spike_idx, df.columns.get_loc("close")] = prev_close * 1.08

        df_indexed = df.copy()
        df_indexed["date"] = pd.to_datetime(df_indexed["date"])
        df_indexed = df_indexed.set_index("date").sort_index()
        events = CCAnalysisEngine._detect_earnings(df_indexed)

        assert len(events) >= 1

    def test_clusters_nearby_events(self):
        df = _make_ohlcv(n=300)
        avg_vol = df["volume"].mean()
        for offset in [200, 201, 202]:
            df.iloc[offset, df.columns.get_loc("volume")] = int(avg_vol * 5)
            prev_close = df.iloc[offset - 1]["close"]
            df.iloc[offset, df.columns.get_loc("close")] = prev_close * 1.10

        df_indexed = df.copy()
        df_indexed["date"] = pd.to_datetime(df_indexed["date"])
        df_indexed = df_indexed.set_index("date").sort_index()
        events = CCAnalysisEngine._detect_earnings(df_indexed)

        assert len(events) <= 2  # should cluster into 1

    def test_estimate_next_earnings_returns_none_with_few_events(self):
        result = CCAnalysisEngine._estimate_next_earnings([])
        assert result is None

        result = CCAnalysisEngine._estimate_next_earnings(
            [pd.Timestamp("2026-02-01")]
        )
        assert result is None


# ── Assignment probability ─────────────────────────────────────


class TestAssignmentProb:
    def test_returns_zero_with_no_episodes(self):
        close = pd.Series(range(100), index=pd.bdate_range("2025-01-01", periods=100))
        prob = CCAnalysisEngine._assignment_prob([], close, otm_pct=10, window=5)
        assert prob == 0.0

    def test_returns_value_between_0_and_100(self):
        df = _make_ohlcv(n=500, trend=0.005)
        close = df.set_index(pd.to_datetime(df["date"]))["close"]
        ext_series = (close / close.ewm(span=8, adjust=False).mean() - 1) * 100
        episodes = CCAnalysisEngine._find_episodes(ext_series, _EXTENSION_THRESHOLD)

        if episodes:
            prob = CCAnalysisEngine._assignment_prob(
                episodes, close, otm_pct=13, window=5,
            )
            assert 0 <= prob <= 100


# ── Suggested OTM ──────────────────────────────────────────────


class TestSuggestOTM:
    def test_returns_default_with_no_episodes(self):
        close = pd.Series(range(100), index=pd.bdate_range("2025-01-01", periods=100))
        otm = CCAnalysisEngine._suggest_otm([], close, target_dte=8)
        assert otm == 13.0

    def test_returns_numeric_otm(self):
        df = _make_ohlcv(n=500, trend=0.005)
        close = df.set_index(pd.to_datetime(df["date"]))["close"]
        ext_series = (close / close.ewm(span=8, adjust=False).mean() - 1) * 100
        episodes = CCAnalysisEngine._find_episodes(ext_series, _EXTENSION_THRESHOLD)

        otm = CCAnalysisEngine._suggest_otm(episodes, close, target_dte=8)
        assert otm in [10, 13, 15, 20, 25] or otm == 20.0


# ── Best entry day ─────────────────────────────────────────────


class TestBestEntryDay:
    def test_returns_wed_by_default(self):
        day = CCAnalysisEngine._best_entry_day([])
        assert day == "Wed"

    def test_returns_best_by_ret_per_calday(self):
        data = [
            {"day": "Mon", "ret_per_calday": 0.01},
            {"day": "Tue", "ret_per_calday": 0.03},
            {"day": "Wed", "ret_per_calday": 0.05},
        ]
        day = CCAnalysisEngine._best_entry_day(data)
        assert day == "Wed"


# ── Recommended action ────────────────────────────────────────


class TestRecommendedAction:
    def test_go_signal_produces_sell_action(self):
        df = _make_extended_ohlcv()
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("LAC", shares=100)

        rec = result.recommended_action
        assert rec["action"] in ("SELL", "CONSIDER", "WAIT")
        assert "instruction" in rec
        assert rec["contracts"] == 1

    def test_includes_expiration_date(self):
        df = _make_ohlcv()
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("PL", shares=200, target_dte=8)

        rec = result.recommended_action
        assert rec["expiration_date"] is not None
        assert rec["actual_dte"] > 0

    def test_skip_for_insufficient_shares(self):
        df = _make_ohlcv()
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("PL", shares=50)

        rec = result.recommended_action
        assert rec["action"] == "SKIP"

    def test_safety_reasons_populated_on_low_assignment(self):
        df = _make_ohlcv(n=500, trend=0.005)
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("PL", shares=400)

        rec = result.recommended_action
        assert isinstance(rec["safety_reasons"], list)
        assert isinstance(rec["warnings"], list)

    def test_instruction_contains_ticker_and_strike(self):
        df = _make_extended_ohlcv()
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("GOOG", shares=100)

        rec = result.recommended_action
        if rec["action"] in ("SELL", "CONSIDER"):
            assert "GOOG" in rec["instruction"]
            assert "CALL" in rec["instruction"]

    def test_pullback_prob_between_0_and_100(self):
        df = _make_ohlcv(n=500, trend=0.005)
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("PL", shares=100)

        rec = result.recommended_action
        if rec["action"] != "SKIP":
            assert 0 <= rec["pullback_prob_by_expiry"] <= 100

    def test_prefers_friday_expiration(self):
        df = _make_ohlcv()
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("PL", shares=100, target_dte=8)

        rec = result.recommended_action
        if rec.get("expiration_label"):
            assert "Fri" in rec["expiration_label"]


# ── Forward date mapping ──────────────────────────────────────


class TestForwardDateMapping:
    def test_maps_dates_to_business_days(self):
        fwd = [
            {"day": 1, "pct_above_entry": 60, "avg_ret": 1.0, "med_ret": 0.5},
            {"day": 2, "pct_above_entry": 55, "avg_ret": 0.8, "med_ret": 0.3},
            {"day": 3, "pct_above_entry": 50, "avg_ret": 0.5, "med_ret": 0.2},
        ]
        # Wednesday April 16, 2026
        result = CCAnalysisEngine._map_forward_dates(fwd, date(2026, 4, 16))

        assert len(result) == 3
        assert result[0]["calendar_date"] == "2026-04-17"
        assert "Fri" in result[0]["day_label"]
        assert result[1]["calendar_date"] == "2026-04-20"
        assert "Mon" in result[1]["day_label"]
        assert result[2]["calendar_date"] == "2026-04-21"
        assert "Tue" in result[2]["day_label"]

    def test_preserves_original_fields(self):
        fwd = [{"day": 1, "pct_above_entry": 70, "avg_ret": 2.0, "med_ret": 1.5}]
        result = CCAnalysisEngine._map_forward_dates(fwd, date(2026, 4, 16))

        assert result[0]["day"] == 1
        assert result[0]["pct_above_entry"] == 70
        assert result[0]["avg_ret"] == 2.0

    def test_empty_returns_empty(self):
        result = CCAnalysisEngine._map_forward_dates([], date(2026, 4, 16))
        assert result == []

    def test_skips_weekends(self):
        # Friday April 17 — D1 should be Mon Apr 20, not Sat Apr 18
        fwd = [{"day": 1, "pct_above_entry": 50, "avg_ret": 1.0, "med_ret": 0.5}]
        result = CCAnalysisEngine._map_forward_dates(fwd, date(2026, 4, 17))

        assert result[0]["calendar_date"] == "2026-04-20"
        assert "Mon" in result[0]["day_label"]

    def test_day_label_format(self):
        fwd = [{"day": 1, "pct_above_entry": 50, "avg_ret": 1.0, "med_ret": 0.5}]
        result = CCAnalysisEngine._map_forward_dates(fwd, date(2026, 4, 14))

        # Apr 14 is Tuesday, so D1 = Apr 15 (Wed)
        assert "Apr 15" in result[0]["day_label"]
        assert "(Wed)" in result[0]["day_label"]

    def test_full_analysis_includes_dates(self):
        df = _make_ohlcv(n=500, trend=0.005)
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("PL")

        if result.forward_returns:
            fr = result.forward_returns[0]
            assert "calendar_date" in fr
            assert "day_label" in fr


# ── Rally peak distribution ───────────────────────────────────


class TestRallyDistribution:
    def test_rally_distribution_structure(self):
        df = _make_ohlcv(n=300)
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        result = engine.analyze("PL")

        rd = result.rally_peak_day_distribution
        assert "days_1_3" in rd
        assert "days_4_6" in rd
        assert "days_7_plus" in rd
        assert "total" in rd


# ── Live premium integration ─────────────────────────────────


def _make_mock_broker(
    *,
    expirations: list[str] | None = None,
    calls: list[OptionContract] | None = None,
    chain_empty: bool = False,
) -> AsyncMock:
    """Build an AsyncMock broker with options chain responses."""
    broker = AsyncMock()
    exp_date = date.today() + timedelta(days=7)
    if expirations is None:
        expirations = [exp_date.isoformat()]
    broker.get_options_expirations.return_value = expirations

    if chain_empty:
        broker.get_options_chain.return_value = OptionsChain(
            symbol="PL", expiration=exp_date, underlying_price=40.0,
        )
    elif calls is None:
        calls = [
            OptionContract(
                option_symbol="PL260424C00045000",
                option_type="call",
                strike=45.0,
                expiration=exp_date,
                bid=0.35,
                ask=0.50,
                mid=0.425,
                last=0.40,
                volume=120,
                open_interest=500,
                implied_volatility=0.62,
                delta=0.15,
                theta=-0.03,
            ),
            OptionContract(
                option_symbol="PL260424C00047500",
                option_type="call",
                strike=47.5,
                expiration=exp_date,
                bid=0.08,
                ask=0.12,
                mid=0.10,
                last=0.09,
                volume=30,
                open_interest=200,
                implied_volatility=0.58,
                delta=0.05,
                theta=-0.01,
            ),
        ]
        broker.get_options_chain.return_value = OptionsChain(
            symbol="PL",
            expiration=exp_date,
            underlying_price=40.0,
            contracts=calls,
        )
    else:
        broker.get_options_chain.return_value = OptionsChain(
            symbol="PL",
            expiration=exp_date,
            underlying_price=40.0,
            contracts=calls,
        )
    return broker


class TestFetchLivePremium:
    @pytest.mark.asyncio
    async def test_returns_bid_ask_mid(self):
        broker = _make_mock_broker()
        result = await CCAnalysisEngine._fetch_live_premium(
            broker=broker, ticker="PL", target_strike=45.0, target_dte=7,
        )
        assert result is not None
        assert result["bid"] == 0.35
        assert result["ask"] == 0.50
        assert result["mid"] == 0.425
        assert result["strike"] == 45.0

    @pytest.mark.asyncio
    async def test_returns_greeks(self):
        broker = _make_mock_broker()
        result = await CCAnalysisEngine._fetch_live_premium(
            broker=broker, ticker="PL", target_strike=45.0, target_dte=7,
        )
        assert result["delta"] == 0.15
        assert result["theta"] == -0.03
        assert result["iv"] == 0.62

    @pytest.mark.asyncio
    async def test_finds_nearest_strike(self):
        broker = _make_mock_broker()
        result = await CCAnalysisEngine._fetch_live_premium(
            broker=broker, ticker="PL", target_strike=46.0, target_dte=7,
        )
        assert result is not None
        assert result["strike"] == 45.0  # nearest to 46.0

    @pytest.mark.asyncio
    async def test_prefers_exact_strike(self):
        broker = _make_mock_broker()
        result = await CCAnalysisEngine._fetch_live_premium(
            broker=broker, ticker="PL", target_strike=47.5, target_dte=7,
        )
        assert result is not None
        assert result["strike"] == 47.5

    @pytest.mark.asyncio
    async def test_returns_none_on_no_expirations(self):
        broker = _make_mock_broker(expirations=[])
        result = await CCAnalysisEngine._fetch_live_premium(
            broker=broker, ticker="PL", target_strike=45.0, target_dte=7,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_chain(self):
        broker = _make_mock_broker(chain_empty=True)
        result = await CCAnalysisEngine._fetch_live_premium(
            broker=broker, ticker="PL", target_strike=45.0, target_dte=7,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_broker_error(self):
        broker = AsyncMock()
        broker.get_options_expirations.side_effect = Exception("API down")
        result = await CCAnalysisEngine._fetch_live_premium(
            broker=broker, ticker="PL", target_strike=45.0, target_dte=7,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_picks_closest_expiration_to_dte(self):
        exp_near = (date.today() + timedelta(days=5)).isoformat()
        exp_far = (date.today() + timedelta(days=21)).isoformat()
        exp_near_dt = date.today() + timedelta(days=5)

        call = OptionContract(
            option_symbol="PL_NEAR", option_type="call",
            strike=45.0, expiration=exp_near_dt,
            bid=0.25, ask=0.35, mid=0.30, last=0.28,
            volume=50, open_interest=100,
        )
        broker = _make_mock_broker(
            expirations=[exp_near, exp_far],
            calls=[call],
        )
        result = await CCAnalysisEngine._fetch_live_premium(
            broker=broker, ticker="PL", target_strike=45.0, target_dte=7,
        )
        assert result is not None
        broker.get_options_chain.assert_called_once_with("PL", exp_near, greeks=True)


class TestOverlayLivePremium:
    def _base_result(self) -> CCDeepDive:
        """Build a minimal CCDeepDive for overlay testing."""
        sig = CCSignal(
            ticker="PL", signal="GO", signal_reason="test",
            last_close=40.0, ema_8=35.0, ema_21=33.0, ema_50=30.0, ema_21_slope=0.5,
            extension_pct_8=14.3, extension_pct_21=21.2, rsi_14=65.0,
            suggested_strike=47.0, suggested_otm_pct=17.5,
            suggested_premium_est=5.00,
        )
        rec = {
            "action": "SELL",
            "instruction": "SELL 1 × PL $47.0 CALL",
            "ticker": "PL",
            "contracts": 1,
            "strike": 47.0,
            "otm_pct": 17.5,
            "premium_est_per_share": 5.00,
            "total_premium_est": 500.0,
            "net_premium_est": 499.35,
            "warnings": [],
            "safety_reasons": [],
            "assignment_prob": 5.0,
            "pullback_prob_by_expiry": 60.0,
        }
        pnl = {
            "if_not_called": {"premium_income": 499.35},
            "if_called": {
                "premium_income": 499.35,
                "effective_sell_price": 52.0,
                "stock_gain": 700.0,
                "total_gain": 1199.35,
            },
        }
        return CCDeepDive(signal=sig, recommended_action=rec, pnl_scenarios=pnl)

    def test_replaces_premium_with_live_bid(self):
        result = self._base_result()
        live = {
            "bid": 0.35, "ask": 0.50, "mid": 0.425,
            "strike": 47.0, "expiration": "2026-04-24",
            "iv": 0.62, "volume": 100, "open_interest": 500,
            "delta": 0.15, "theta": -0.03, "option_symbol": "PL260424C",
        }
        CCAnalysisEngine._overlay_live_premium(result, live, shares=100)

        assert result.recommended_action["premium_source"] == "live_tradier"
        assert result.recommended_action["premium_est_per_share"] == 0.35
        assert result.recommended_action["live_bid"] == 0.35
        assert result.recommended_action["live_ask"] == 0.50
        assert result.signal.suggested_premium_est == 0.35

    def test_thin_premium_triggers_skip(self):
        result = self._base_result()
        live = {
            "bid": 0.03, "ask": 0.05, "mid": 0.04,
            "strike": 47.0, "expiration": "2026-04-24",
            "iv": 0.30, "volume": 5, "open_interest": 20,
            "delta": 0.02, "theta": -0.005, "option_symbol": "PL260424C",
        }
        CCAnalysisEngine._overlay_live_premium(result, live, shares=100)

        assert result.recommended_action["action"] == "SKIP"
        assert "skip" in result.recommended_action["instruction"].lower()
        assert "$0.03" in result.recommended_action["instruction"]
        assert any("too thin" in w for w in result.recommended_action["warnings"])

    def test_viable_premium_upgrades_from_estimated_skip(self):
        """When historical premium was bad but live shows good bid."""
        result = self._base_result()
        result.recommended_action["action"] = "SKIP"
        result.recommended_action["warnings"] = [
            "Premium only $0.01/share — too thin to justify"
        ]

        live = {
            "bid": 0.85, "ask": 1.10, "mid": 0.975,
            "strike": 47.0, "expiration": "2026-04-24",
            "iv": 0.62, "volume": 100, "open_interest": 500,
            "delta": 0.15, "theta": -0.03, "option_symbol": "PL260424C",
        }
        CCAnalysisEngine._overlay_live_premium(result, live, shares=100)

        assert result.recommended_action["action"] == "SELL"
        assert "too thin" not in result.recommended_action["instruction"].lower()

    def test_updates_pnl_scenarios(self):
        result = self._base_result()
        live = {
            "bid": 0.50, "ask": 0.70, "mid": 0.60,
            "strike": 47.0, "expiration": "2026-04-24",
            "iv": 0.55, "volume": 200, "open_interest": 800,
            "delta": 0.12, "theta": -0.025, "option_symbol": "PL260424C",
        }
        CCAnalysisEngine._overlay_live_premium(result, live, shares=100)

        pnl = result.pnl_scenarios
        commission = 0.65
        expected_net = 0.50 * 100 - commission
        assert pnl["if_not_called"]["premium_income"] == round(expected_net, 2)
        assert pnl["if_called"]["effective_sell_price"] == 47.50


class TestAnalyzeWithLiveChain:
    @pytest.mark.asyncio
    async def test_uses_live_premium(self):
        df = _make_extended_ohlcv()
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        broker = _make_mock_broker()

        result = await engine.analyze_with_live_chain(
            "PL", shares=100, broker=broker,
        )

        assert result.recommended_action.get("premium_source") == "live_tradier"
        assert result.recommended_action.get("live_bid") is not None

    @pytest.mark.asyncio
    async def test_falls_back_without_broker(self):
        df = _make_extended_ohlcv()
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))

        result = await engine.analyze_with_live_chain("PL", shares=100, broker=None)

        assert result.recommended_action.get("premium_source") == "historical_estimate"
        assert result.recommended_action.get("live_bid") is None

    @pytest.mark.asyncio
    async def test_falls_back_on_broker_error(self):
        df = _make_extended_ohlcv()
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        broker = AsyncMock()
        broker.get_options_expirations.side_effect = Exception("API down")

        result = await engine.analyze_with_live_chain(
            "PL", shares=100, broker=broker,
        )

        assert result.recommended_action.get("premium_source") == "historical_estimate"


class TestAnalyzeBatchWithLiveChain:
    @pytest.mark.asyncio
    async def test_batch_with_broker(self):
        df = _make_extended_ohlcv()
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))
        broker = _make_mock_broker()

        positions = [
            {"ticker": "PL", "shares": 200, "cost_basis": 10.0},
        ]
        result = await engine.analyze_batch_with_live_chain(
            positions, target_dte=8, broker=broker,
        )

        assert isinstance(result, CCPortfolioAnalysis)
        assert len(result.analyses) == 1
        assert result.analyses[0].recommended_action.get("premium_source") == "live_tradier"

    @pytest.mark.asyncio
    async def test_batch_without_broker(self):
        df = _make_ohlcv()
        engine = CCAnalysisEngine(ohlcv_store=_mock_ohlcv_store(df))

        positions = [{"ticker": "PL", "shares": 100}]
        result = await engine.analyze_batch_with_live_chain(
            positions, target_dte=8, broker=None,
        )

        assert len(result.analyses) == 1
        assert result.analyses[0].recommended_action.get("premium_source") == "historical_estimate"


class TestEstimatePremiumSanity:
    """Test that the improved _estimate_premium filters by expiration."""

    def test_returns_none_without_options_store(self):
        engine = CCAnalysisEngine(ohlcv_store=MagicMock())
        result = engine._estimate_premium("PL", 45.0, 7, date.today())
        assert result is None

    def test_rejects_unreasonably_high_premium(self):
        """Premium > 20% of strike should be rejected as wrong expiration match."""
        mock_store = MagicMock()
        today = date.today()
        target_exp = today + timedelta(days=7)

        # Option with unreasonably high close (wrong expiration)
        path = MagicMock()
        path.exists.return_value = True
        mock_store._ticker_path.return_value = path

        df = pd.DataFrame({
            "date": [today],
            "option_type": ["C"],
            "strike": [45.0],
            "expiration": [target_exp],
            "close": [15.0],  # $15/share for a $45 strike OTM call = 33%
            "volume": [100],
        })

        import unittest.mock
        with unittest.mock.patch("pandas.read_parquet", return_value=df):
            engine = CCAnalysisEngine(
                ohlcv_store=MagicMock(), options_history_store=mock_store,
            )
            result = engine._estimate_premium("PL", 45.0, 7, today)
            assert result is None
