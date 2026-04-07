"""Tests for DerivedMetricsStore.read_latest_batch and IV field integration.

Validates:
- read_latest_batch returns correct latest rows per ticker
- read_latest_batch handles missing tickers, NaN values, date filtering
- FeatureSignal carries IV fields through analyze/analyze_batch
- ConvictionSignalStore round-trips IV fields through Parquet
- ConvictionSignal to_dict includes IV fields
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from tyche.conviction.engine import ConvictionEngine, ConvictionSignal, TrendState
from tyche.conviction.features import ConvictionFeatureEngine, FeatureSignal
from tyche.market_data.data_store import ConvictionSignalStore
from tyche.market_data.derived_store import DerivedMetricsStore


def _make_ohlcv(prices, volumes=None, start_date=None):
    n = len(prices)
    start = start_date or date(2026, 1, 1)
    dates = [start + timedelta(days=i) for i in range(n)]
    if volumes is None:
        volumes = [1_000_000] * n
    return pd.DataFrame({
        "date": dates,
        "open": [p * 0.99 for p in prices],
        "high": [p * 1.01 for p in prices],
        "low": [p * 0.98 for p in prices],
        "close": prices,
        "volume": volumes,
        "vwap": prices,
    })


def _fresh_uptrend(n=80, start=100.0, gain=0.5, streak_days=8):
    dip_at = n - streak_days - 2
    prices = []
    for i in range(n):
        base = start + i * gain
        if dip_at <= i < dip_at + 2:
            base -= gain * 20
        prices.append(base)
    return prices


def _seed_derived(store: DerivedMetricsStore, ticker: str, dates: list[date], iv_rank: float):
    """Write synthetic derived metrics for a ticker."""
    rows = []
    for d in dates:
        rows.append({
            "date": d,
            "atm_iv": 0.30,
            "iv_rank": iv_rank,
            "iv_percentile": iv_rank * 0.9,
            "rv_20d": 0.25,
            "vrp": 0.05,
        })
    df = pd.DataFrame(rows)
    store.write_metrics(ticker, df)


class TestReadLatestBatch:

    @pytest.fixture
    def store(self, tmp_path):
        return DerivedMetricsStore(data_dir=str(tmp_path))

    def test_basic_read(self, store):
        dates = [date(2026, 3, 28), date(2026, 3, 29), date(2026, 3, 30)]
        _seed_derived(store, "AAPL", dates, iv_rank=45.0)

        result = store.read_latest_batch(["AAPL"], date(2026, 3, 30))
        assert "AAPL" in result
        assert result["AAPL"]["iv_rank"] == pytest.approx(45.0)
        assert result["AAPL"]["atm_iv"] == pytest.approx(0.30)
        assert result["AAPL"]["vrp"] == pytest.approx(0.05)

    def test_date_filter(self, store):
        """Only returns rows on or before as_of_date."""
        _seed_derived(store, "AAPL", [date(2026, 3, 28)], iv_rank=30.0)
        _seed_derived(store, "MSFT", [date(2026, 4, 1)], iv_rank=80.0)

        result = store.read_latest_batch(["AAPL", "MSFT"], date(2026, 3, 30))
        assert "AAPL" in result
        assert "MSFT" not in result

    def test_missing_ticker_omitted(self, store):
        result = store.read_latest_batch(["MISSING"], date(2026, 3, 30))
        assert result == {}

    def test_multiple_tickers(self, store):
        _seed_derived(store, "AAPL", [date(2026, 3, 30)], iv_rank=20.0)
        _seed_derived(store, "MSFT", [date(2026, 3, 30)], iv_rank=70.0)

        result = store.read_latest_batch(["AAPL", "MSFT"], date(2026, 3, 30))
        assert len(result) == 2
        assert result["AAPL"]["iv_rank"] == pytest.approx(20.0)
        assert result["MSFT"]["iv_rank"] == pytest.approx(70.0)

    def test_latest_row_returned(self, store):
        """When multiple dates exist, returns the latest on or before as_of."""
        dates = [date(2026, 3, 28), date(2026, 3, 29), date(2026, 3, 30)]
        _seed_derived(store, "AAPL", [dates[0]], iv_rank=20.0)
        _seed_derived(store, "AAPL", [dates[1]], iv_rank=40.0)
        _seed_derived(store, "AAPL", [dates[2]], iv_rank=60.0)

        result = store.read_latest_batch(["AAPL"], date(2026, 3, 29))
        assert result["AAPL"]["iv_rank"] == pytest.approx(40.0)

    def test_nan_values_become_none(self, store):
        df = pd.DataFrame([{
            "date": date(2026, 3, 30),
            "atm_iv": 0.30,
            "iv_rank": np.nan,
            "iv_percentile": np.nan,
            "rv_20d": 0.25,
            "vrp": np.nan,
        }])
        store.write_metrics("NAN_TEST", df)

        result = store.read_latest_batch(["NAN_TEST"], date(2026, 3, 30))
        assert result["NAN_TEST"]["iv_rank"] is None
        assert result["NAN_TEST"]["iv_percentile"] is None
        assert result["NAN_TEST"]["vrp"] is None
        assert result["NAN_TEST"]["atm_iv"] == pytest.approx(0.30)


class TestFeatureSignalIVFields:

    def test_defaults_are_none(self):
        sig = FeatureSignal(ticker="X", trend_state=TrendState.UPTREND)
        assert sig.iv_rank is None
        assert sig.iv_percentile is None
        assert sig.atm_iv is None
        assert sig.vrp is None

    def test_to_dict_includes_iv_fields(self):
        sig = FeatureSignal(
            ticker="X",
            trend_state=TrendState.UPTREND,
            iv_rank=45.0,
            iv_percentile=50.0,
            atm_iv=0.3,
            vrp=0.05,
        )
        d = sig.to_dict()
        assert d["iv_rank"] == 45.0
        assert d["iv_percentile"] == 50.0
        assert d["atm_iv"] == 0.3
        assert d["vrp"] == 0.05

    def test_to_dict_none_when_missing(self):
        sig = FeatureSignal(ticker="X", trend_state=TrendState.UPTREND)
        d = sig.to_dict()
        assert d["iv_rank"] is None
        assert d["vrp"] is None


class TestConvictionSignalIVFields:

    def test_to_dict_includes_iv_fields(self):
        sig = ConvictionSignal(
            ticker="X",
            trend_state=TrendState.UPTREND,
            conviction_level="medium",
            iv_rank=80.0,
            vrp=-0.02,
        )
        d = sig.to_dict()
        assert d["iv_rank"] == 80.0
        assert d["vrp"] == -0.02
        assert d["iv_percentile"] is None
        assert d["atm_iv"] is None


class TestEngineWithDerivedStore:

    @pytest.fixture
    def derived_store(self, tmp_path):
        return DerivedMetricsStore(data_dir=str(tmp_path))

    @pytest.fixture
    def signal_store(self, tmp_path):
        return ConvictionSignalStore(data_dir=str(tmp_path / "signals"))

    def test_analyze_batch_attaches_iv(self, derived_store, signal_store):
        prices = _fresh_uptrend(80)
        as_of = date(2026, 1, 1) + timedelta(days=79)
        _seed_derived(derived_store, "UP", [as_of], iv_rank=55.0)

        engine = ConvictionFeatureEngine(
            signal_store=signal_store,
            derived_store=derived_store,
        )
        data = {"UP": _make_ohlcv(prices)}
        signals = engine.analyze_batch(data)

        up = next(s for s in signals if s.ticker == "UP")
        assert up.iv_rank == pytest.approx(55.0)
        assert up.atm_iv == pytest.approx(0.30)
        assert up.vrp == pytest.approx(0.05)

    def test_analyze_batch_no_derived_store(self):
        engine = ConvictionFeatureEngine(derived_store=None)
        data = {"UP": _make_ohlcv(_fresh_uptrend(80))}
        signals = engine.analyze_batch(data)

        up = next(s for s in signals if s.ticker == "UP")
        assert up.iv_rank is None
        assert up.vrp is None

    def test_conviction_engine_forwards_iv(self, derived_store):
        prices = _fresh_uptrend(80)
        as_of = date(2026, 1, 1) + timedelta(days=79)
        _seed_derived(derived_store, "UP", [as_of], iv_rank=30.0)

        engine = ConvictionEngine(
            ema_fast=8, ema_slow=21,
            derived_store=derived_store,
        )
        data = {"UP": _make_ohlcv(prices)}
        signals = engine.analyze_batch(data)

        up = next(s for s in signals if s.ticker == "UP")
        assert up.iv_rank == pytest.approx(30.0)
        assert up.vrp == pytest.approx(0.05)


class TestSignalStoreIVRoundtrip:

    @pytest.fixture
    def store(self, tmp_path):
        return ConvictionSignalStore(data_dir=str(tmp_path))

    def test_iv_fields_survive_write_read(self, store):
        as_of = date(2026, 3, 31)
        sig = ConvictionSignal(
            ticker="AAPL",
            trend_state=TrendState.UPTREND,
            conviction_level="medium",
            last_close=150.0,
            ema_8=148.0,
            ema_21=145.0,
            ema_8_slope=0.5,
            ema_21_slope=0.3,
            price_to_8ema_pct=1.35,
            price_to_21ema_pct=3.45,
            volume_declining_on_pullback=False,
            avg_volume_20d=1_000_000,
            latest_volume=900_000,
            days_above_both_emas=7,
            prior_streak=0,
            as_of_date=as_of,
            iv_rank=65.0,
            iv_percentile=72.0,
            atm_iv=0.35,
            vrp=0.08,
        )
        store.write_signals([sig])
        rows = store.read_signals(as_of)
        assert rows is not None
        aapl = rows[0]
        assert aapl["iv_rank"] == pytest.approx(65.0)
        assert aapl["iv_percentile"] == pytest.approx(72.0)
        assert aapl["atm_iv"] == pytest.approx(0.35)
        assert aapl["vrp"] == pytest.approx(0.08)

    def test_iv_fields_none_survive_roundtrip(self, store):
        as_of = date(2026, 3, 31)
        sig = ConvictionSignal(
            ticker="AAPL",
            trend_state=TrendState.UPTREND,
            conviction_level="medium",
            last_close=150.0,
            ema_8=148.0,
            ema_21=145.0,
            ema_8_slope=0.5,
            ema_21_slope=0.3,
            price_to_8ema_pct=1.35,
            price_to_21ema_pct=3.45,
            volume_declining_on_pullback=False,
            avg_volume_20d=1_000_000,
            latest_volume=900_000,
            days_above_both_emas=7,
            prior_streak=0,
            as_of_date=as_of,
        )
        store.write_signals([sig])
        rows = store.read_signals(as_of)
        assert rows is not None
        aapl = rows[0]
        assert np.isnan(aapl["iv_rank"]) or aapl["iv_rank"] is None
