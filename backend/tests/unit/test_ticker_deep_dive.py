"""Unit tests for TickerDeepDiveEngine."""

from __future__ import annotations

import math
from datetime import date, timedelta
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from tyche.analysis.ticker_deep_dive import TickerDeepDiveEngine, TickerDeepDive
from tyche.schemas.deep_dive import (
    RSIReadingResponse,
    TickerDeepDiveResponse,
    to_response,
)


def _make_ohlcv(n: int = 300, base_price: float = 100.0) -> pd.DataFrame:
    """Generate synthetic OHLCV data with mild uptrend + noise."""
    np.random.seed(42)
    dates = pd.bdate_range(end=date.today(), periods=n)
    n = len(dates)  # bdate_range can return fewer periods depending on weekday alignment
    trend = np.linspace(base_price * 0.7, base_price, n)
    noise = np.random.normal(0, base_price * 0.01, n)
    closes = trend + noise
    highs = closes * (1 + np.abs(np.random.normal(0, 0.005, n)))
    lows = closes * (1 - np.abs(np.random.normal(0, 0.005, n)))
    opens = closes * (1 + np.random.normal(0, 0.003, n))
    volumes = np.random.randint(500_000, 5_000_000, n).astype(float)
    return pd.DataFrame({
        "date": dates.date,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


@pytest.fixture
def ohlcv_store():
    store = MagicMock()
    store.read_ticker.return_value = _make_ohlcv()
    return store


@pytest.fixture
def meta_store():
    store = MagicMock()
    store.get_meta_batch.return_value = {
        "TEST": {
            "name": "Test Inc.",
            "sector": "Technology",
            "market_cap": 50e9,
        }
    }
    store.get_institutional_pcts.return_value = {"TEST": 0.72}
    return store


@pytest.fixture
def fundamentals_store():
    store = MagicMock()
    today = date.today()
    df = pd.DataFrame([
        {
            "ticker": "TEST",
            "period_end": today - timedelta(days=90),
            "filing_date": today - timedelta(days=60),
            "fiscal_year": 2026,
            "fiscal_period": "Q1",
            "timeframe": "quarterly",
            "revenue": 5e9,
            "gross_profit": 3e9,
            "gross_margin": 0.60,
            "operating_income": 1.5e9,
            "operating_margin": 0.30,
            "net_income": 1e9,
            "net_margin": 0.20,
            "eps_diluted": 2.50,
            "cash_and_equivalents": 10e9,
            "operating_cash_flow": 2e9,
            "total_debt": 3e9,
        },
    ])
    store.read_ticker.return_value = df
    return store


@pytest.fixture
def estimates_store():
    store = MagicMock()
    today = date.today()
    df = pd.DataFrame([
        {"ticker": "TEST", "snapshot_date": today, "metric": "price_target_mean", "period": "", "value": 120.0},
        {"ticker": "TEST", "snapshot_date": today, "metric": "price_target_high", "period": "", "value": 150.0},
        {"ticker": "TEST", "snapshot_date": today, "metric": "price_target_low", "period": "", "value": 80.0},
        {"ticker": "TEST", "snapshot_date": today, "metric": "eps_est_avg", "period": "2026-Q2", "value": 2.80},
        {"ticker": "TEST", "snapshot_date": today, "metric": "eps_est_count", "period": "2026-Q2", "value": 12.0},
    ])
    store.read_ticker.return_value = df
    return store


@pytest.fixture
def catalyst_store():
    store = MagicMock()
    today = date.today()
    df = pd.DataFrame([
        {"ticker": "TEST", "event_date": today - timedelta(days=5), "kind": "demand", "tag": "revenue_beat", "signed_impact": 0.8, "source": "news", "ref_id": "a1"},
        {"ticker": "TEST", "event_date": today - timedelta(days=15), "kind": "demand", "tag": "guidance_raise", "signed_impact": 0.6, "source": "8k", "ref_id": "a2"},
    ])
    store.read_ticker.return_value = df
    return store


@pytest.fixture
def engine(ohlcv_store, meta_store, fundamentals_store, estimates_store, catalyst_store):
    return TickerDeepDiveEngine(
        ohlcv_store=ohlcv_store,
        meta_store=meta_store,
        fundamentals_store=fundamentals_store,
        estimates_store=estimates_store,
        catalyst_store=catalyst_store,
    )


class TestBasicAnalysis:
    def test_analyze_returns_deep_dive(self, engine):
        result = engine.analyze("TEST")
        assert isinstance(result, TickerDeepDive)
        assert result.ticker == "TEST"
        assert result.last_close > 0

    def test_metadata_populated(self, engine):
        result = engine.analyze("TEST")
        assert result.name == "Test Inc."
        assert result.sector == "Technology"
        assert result.market_cap == 50e9
        assert result.institutional_pct == 0.72

    def test_52w_stats(self, engine):
        result = engine.analyze("TEST")
        assert result.high_52w > 0
        assert result.low_52w > 0
        assert result.pct_off_52w_high >= 0

    def test_as_of_date_populated(self, engine):
        result = engine.analyze("TEST")
        assert result.as_of_date != ""


class TestMultiTimeframeRSI:
    def test_daily_rsi_in_range(self, engine):
        result = engine.analyze("TEST")
        assert 0 <= result.rsi.daily <= 100

    def test_weekly_rsi_in_range(self, engine):
        result = engine.analyze("TEST")
        assert 0 <= result.rsi.weekly <= 100

    def test_monthly_rsi_in_range(self, engine):
        result = engine.analyze("TEST")
        assert 0 <= result.rsi.monthly <= 100

    def test_quarterly_rsi_in_range(self, engine):
        result = engine.analyze("TEST")
        assert 0 <= result.rsi.quarterly <= 100

    def test_weekly_history_has_entries(self, engine):
        result = engine.analyze("TEST")
        assert len(result.rsi.weekly_history) > 0
        for r in result.rsi.weekly_history:
            assert 0 <= r.value <= 100
            assert r.close > 0
            assert r.date

    def test_monthly_history_has_entries(self, engine):
        result = engine.analyze("TEST")
        assert len(result.rsi.monthly_history) > 0

    def test_quarterly_history_has_entries(self, engine):
        result = engine.analyze("TEST")
        assert len(result.rsi.quarterly_history) > 0


class TestEMAStack:
    def test_ema_values_positive(self, engine):
        result = engine.analyze("TEST")
        assert result.ema_stack.ema_8 > 0
        assert result.ema_stack.ema_21 > 0
        assert result.ema_stack.ema_50 > 0

    def test_sma200_computed(self, engine):
        result = engine.analyze("TEST")
        assert result.ema_stack.sma_200 > 0

    def test_pct_vs_ema_computed(self, engine):
        result = engine.analyze("TEST")
        assert isinstance(result.ema_stack.pct_vs_ema_8, float)
        assert isinstance(result.ema_stack.pct_vs_ema_21, float)

    def test_slopes_computed(self, engine):
        result = engine.analyze("TEST")
        assert isinstance(result.ema_stack.slope_ema_8, float)
        assert isinstance(result.ema_stack.slope_ema_21, float)
        assert isinstance(result.ema_stack.slope_ema_50, float)

    def test_stack_score_range(self, engine):
        result = engine.analyze("TEST")
        assert 0 <= result.ema_stack.stack_score <= 3

    def test_days_above_non_negative(self, engine):
        result = engine.analyze("TEST")
        assert result.ema_stack.days_above_ema_8 >= 0
        assert result.ema_stack.days_above_ema_21 >= 0


class TestMACD:
    def test_macd_values_computed(self, engine):
        result = engine.analyze("TEST")
        assert isinstance(result.macd.macd_line, float)
        assert isinstance(result.macd.signal_line, float)
        assert isinstance(result.macd.histogram, float)


class TestBollingerBands:
    def test_bollinger_values_computed(self, engine):
        result = engine.analyze("TEST")
        assert result.bollinger.upper > result.bollinger.lower
        assert result.bollinger.middle > 0
        assert isinstance(result.bollinger.width_pct, float)
        assert isinstance(result.bollinger.pct_b, float)


class TestReturns:
    def test_returns_populated(self, engine):
        result = engine.analyze("TEST")
        assert "1W" in result.returns
        assert "1M" in result.returns
        assert "1Y" in result.returns


class TestPriceHistory:
    def test_price_history_populated(self, engine):
        result = engine.analyze("TEST")
        assert len(result.price_history) > 0
        for p in result.price_history:
            assert p.close > 0
            assert p.date


class TestVolumeHistory:
    def test_volume_bars_populated(self, engine):
        result = engine.analyze("TEST")
        assert len(result.volume_bars) == 60
        for v in result.volume_bars:
            assert v.volume >= 0
            assert v.date


class TestFundamentals:
    def test_fundamentals_populated(self, engine):
        result = engine.analyze("TEST")
        assert len(result.fundamentals) >= 1
        f = result.fundamentals[0]
        assert f.revenue == 5e9
        assert f.gross_margin == 0.60
        assert f.cash == 10e9

    def test_fundamentals_none_when_store_missing(self, ohlcv_store):
        engine = TickerDeepDiveEngine(ohlcv_store=ohlcv_store)
        result = engine.analyze("TEST")
        assert result.fundamentals == []


class TestEstimates:
    def test_estimates_populated(self, engine):
        result = engine.analyze("TEST")
        assert result.estimates.pt_mean == 120.0
        assert result.estimates.pt_high == 150.0
        assert result.estimates.pt_low == 80.0
        assert result.estimates.analyst_count == 12

    def test_forward_eps_populated(self, engine):
        result = engine.analyze("TEST")
        assert len(result.estimates.forward_eps) >= 1
        assert result.estimates.forward_eps[0]["period"] == "2026-Q2"
        assert result.estimates.forward_eps[0]["value"] == 2.80

    def test_estimates_none_when_store_missing(self, ohlcv_store):
        engine = TickerDeepDiveEngine(ohlcv_store=ohlcv_store)
        result = engine.analyze("TEST")
        assert result.estimates.pt_mean is None


class TestCatalysts:
    def test_catalysts_populated(self, engine):
        result = engine.analyze("TEST")
        assert len(result.catalysts) == 2
        # Sorted ascending by event_date: guidance_raise (day-15) then revenue_beat (day-5)
        assert result.catalysts[0].tag == "guidance_raise"
        assert result.catalysts[0].impact == 0.6
        assert result.catalysts[0].source == "8k"
        assert result.catalysts[1].tag == "revenue_beat"
        assert result.catalysts[1].impact == 0.8

    def test_catalysts_empty_when_store_missing(self, ohlcv_store):
        engine = TickerDeepDiveEngine(ohlcv_store=ohlcv_store)
        result = engine.analyze("TEST")
        assert result.catalysts == []


class TestInsufficientData:
    def test_returns_empty_result_for_short_data(self, ohlcv_store):
        ohlcv_store.read_ticker.return_value = _make_ohlcv(n=20)
        engine = TickerDeepDiveEngine(ohlcv_store=ohlcv_store)
        result = engine.analyze("TEST")
        assert result.last_close == 0.0

    def test_returns_empty_result_for_none(self, ohlcv_store):
        ohlcv_store.read_ticker.return_value = None
        engine = TickerDeepDiveEngine(ohlcv_store=ohlcv_store)
        result = engine.analyze("TEST")
        assert result.last_close == 0.0

    def test_returns_empty_result_for_empty_df(self, ohlcv_store):
        ohlcv_store.read_ticker.return_value = pd.DataFrame()
        engine = TickerDeepDiveEngine(ohlcv_store=ohlcv_store)
        result = engine.analyze("TEST")
        assert result.last_close == 0.0


class TestGracefulDegradation:
    def test_works_with_ohlcv_only(self, ohlcv_store):
        engine = TickerDeepDiveEngine(ohlcv_store=ohlcv_store)
        result = engine.analyze("TEST")
        assert result.last_close > 0
        assert 0 <= result.rsi.daily <= 100
        assert result.ema_stack.ema_8 > 0
        assert result.fundamentals == []
        assert result.catalysts == []
        assert result.estimates.pt_mean is None

    def test_empty_fundamentals_store(self, ohlcv_store, fundamentals_store):
        fundamentals_store.read_ticker.return_value = pd.DataFrame()
        engine = TickerDeepDiveEngine(ohlcv_store=ohlcv_store, fundamentals_store=fundamentals_store)
        result = engine.analyze("TEST")
        assert result.fundamentals == []

    def test_empty_estimates_store(self, ohlcv_store, estimates_store):
        estimates_store.read_ticker.return_value = pd.DataFrame()
        engine = TickerDeepDiveEngine(ohlcv_store=ohlcv_store, estimates_store=estimates_store)
        result = engine.analyze("TEST")
        assert result.estimates.pt_mean is None

    def test_empty_catalyst_store(self, ohlcv_store, catalyst_store):
        catalyst_store.read_ticker.return_value = pd.DataFrame()
        engine = TickerDeepDiveEngine(ohlcv_store=ohlcv_store, catalyst_store=catalyst_store)
        result = engine.analyze("TEST")
        assert result.catalysts == []


class TestShortHistoryRSISerialization:
    """Regression: recent-IPO / short-history tickers (e.g. NTSK) whose weekly/
    monthly RSI warmup produces undefined values must still serialize + round-trip.
    Previously these emitted null RSI values that broke the read schema -> 404.
    """

    def _short_history_response(self, ohlcv_store):
        # ~4 months of bars: not enough weekly/monthly bars for a valid RSI(14),
        # so the warmup window would otherwise emit NaN/None history points.
        ohlcv_store.read_ticker.return_value = _make_ohlcv(n=80)
        engine = TickerDeepDiveEngine(ohlcv_store=ohlcv_store)
        result = engine.analyze("NTSK")
        assert result.last_close > 0  # not insufficient-data
        return to_response(result)

    def test_no_null_or_nan_rsi_history_points(self, ohlcv_store):
        resp = self._short_history_response(ohlcv_store)
        for history in (
            resp.rsi.weekly_history,
            resp.rsi.monthly_history,
            resp.rsi.quarterly_history,
        ):
            for point in history:
                assert point.value is not None
                assert not math.isnan(point.value)

    def test_response_json_round_trips(self, ohlcv_store):
        resp = self._short_history_response(ohlcv_store)
        # The failure mode was a ValidationError on read (null RSI value).
        restored = TickerDeepDiveResponse.model_validate_json(resp.model_dump_json())
        assert restored.ticker == "NTSK"

    def test_schema_tolerates_legacy_null_value(self):
        # Legacy payloads already persisted in GCS may contain a null RSI value;
        # the nullable field lets them parse instead of 404ing.
        point = RSIReadingResponse.model_validate({"date": "2025-01-31", "value": None, "close": 12.3})
        assert point.value is None
