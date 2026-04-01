"""Tests for ConvictionSignalStore (disk cache) and engine integration."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from tyche.conviction.engine import (
    ConvictionEngine,
    ConvictionSignal,
    TrendState,
)
from tyche.market_data.data_store import ConvictionSignalStore


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


def _downtrend(n=80, start=200.0, loss=0.5):
    return [start - i * loss for i in range(n)]


def _make_signal(ticker: str, as_of: date, close: float = 150.0) -> ConvictionSignal:
    return ConvictionSignal(
        ticker=ticker,
        trend_state=TrendState.UPTREND,
        conviction_level="medium",
        raw_conviction="medium",
        csp_eligible=True,
        last_close=close,
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


class TestConvictionSignalStore:

    @pytest.fixture
    def store(self, tmp_path):
        return ConvictionSignalStore(data_dir=str(tmp_path))

    def test_empty_store(self, store):
        assert store.exists is False
        assert store.get_cached_date() is None
        assert store.read_signals(date(2026, 3, 31)) is None

    def test_write_and_read_roundtrip(self, store):
        as_of = date(2026, 3, 31)
        signals = [
            _make_signal("AAPL", as_of, close=253.0),
            _make_signal("MSFT", as_of, close=370.0),
        ]

        written = store.write_signals(signals)
        assert written == 2
        assert store.exists is True
        assert store.get_cached_date() == as_of

        rows = store.read_signals(as_of)
        assert rows is not None
        assert len(rows) == 2
        tickers = {r["ticker"] for r in rows}
        assert tickers == {"AAPL", "MSFT"}

        aapl = next(r for r in rows if r["ticker"] == "AAPL")
        assert aapl["last_close"] == pytest.approx(253.0)
        assert aapl["ema_8"] == pytest.approx(148.0)
        assert aapl["days_above_both_emas"] == 7
        assert aapl["prior_streak"] == 0

    def test_date_mismatch_returns_none(self, store):
        as_of = date(2026, 3, 31)
        store.write_signals([_make_signal("AAPL", as_of)])

        result = store.read_signals(date(2026, 4, 1))
        assert result is None

    def test_clear_removes_file(self, store):
        store.write_signals([_make_signal("AAPL", date(2026, 3, 31))])
        assert store.exists is True

        store.clear()
        assert store.exists is False
        assert store.get_cached_date() is None

    def test_clear_noop_when_empty(self, store):
        store.clear()
        assert store.exists is False

    def test_skips_signals_without_date(self, store):
        sig_with = _make_signal("AAPL", date(2026, 3, 31))
        sig_without = ConvictionSignal(
            ticker="BAD",
            trend_state=TrendState.INSUFFICIENT_DATA,
            conviction_level="none",
        )
        written = store.write_signals([sig_with, sig_without])
        assert written == 1

    def test_overwrite_on_rewrite(self, store):
        as_of = date(2026, 3, 31)
        store.write_signals([_make_signal("AAPL", as_of, close=100.0)])
        store.write_signals([_make_signal("AAPL", as_of, close=200.0)])

        rows = store.read_signals(as_of)
        assert len(rows) == 1
        assert rows[0]["last_close"] == pytest.approx(200.0)

    def test_empty_list_writes_nothing(self, store):
        assert store.write_signals([]) == 0
        assert store.exists is False


class TestEngineWithDiskCache:

    @pytest.fixture
    def store(self, tmp_path):
        return ConvictionSignalStore(data_dir=str(tmp_path))

    @pytest.fixture
    def engine(self, store):
        return ConvictionEngine(
            ema_fast=8, ema_slow=21,
            signal_store=store,
        )

    def test_batch_writes_to_store(self, engine, store):
        data = {
            "UP": _make_ohlcv(_fresh_uptrend(80)),
            "DN": _make_ohlcv(_downtrend(80)),
        }
        signals = engine.analyze_batch(data)
        assert len(signals) == 2
        assert store.exists is True

        cached_date = store.get_cached_date()
        assert cached_date is not None

        rows = store.read_signals(cached_date)
        assert rows is not None
        assert len(rows) >= 1

    def test_warm_from_store_on_restart(self, store):
        """Simulate restart: first engine computes + stores, second engine loads from store."""
        engine1 = ConvictionEngine(ema_fast=8, ema_slow=21, signal_store=store)
        data = {"UP": _make_ohlcv(_fresh_uptrend(80))}
        signals1 = engine1.analyze_batch(data)
        assert store.exists

        up_signal = next(s for s in signals1 if s.ticker == "UP")

        engine2 = ConvictionEngine(ema_fast=8, ema_slow=21, signal_store=store)
        assert engine2.cache_size == 0

        signals2 = engine2.analyze_batch(data)
        up_signal2 = next(s for s in signals2 if s.ticker == "UP")

        assert up_signal2.ema_8 == pytest.approx(up_signal.ema_8, rel=1e-4)
        assert up_signal2.ema_21 == pytest.approx(up_signal.ema_21, rel=1e-4)
        assert up_signal2.trend_state == up_signal.trend_state
        assert up_signal2.csp_eligible == up_signal.csp_eligible

    def test_invalidate_clears_store(self, engine, store):
        data = {"UP": _make_ohlcv(_fresh_uptrend(80))}
        engine.analyze_batch(data)
        assert store.exists

        engine.invalidate_cache()
        assert not store.exists
        assert engine.cache_size == 0

    def test_config_change_recomputes_gates(self, store):
        """Same EMA data, different config → different csp_eligible."""
        data = {"UP": _make_ohlcv(_fresh_uptrend(80, streak_days=8))}

        engine_wide = ConvictionEngine(
            ema_fast=8, ema_slow=21,
            min_days_above_emas=5, max_days_above_emas=10,
            signal_store=store,
        )
        signals_wide = engine_wide.analyze_batch(data)
        up_wide = next(s for s in signals_wide if s.ticker == "UP")

        engine_narrow = ConvictionEngine(
            ema_fast=8, ema_slow=21,
            min_days_above_emas=2, max_days_above_emas=3,
            signal_store=store,
        )
        signals_narrow = engine_narrow.analyze_batch(data)
        up_narrow = next(s for s in signals_narrow if s.ticker == "UP")

        assert up_wide.ema_8 == pytest.approx(up_narrow.ema_8, rel=1e-4)

        if up_wide.csp_eligible:
            assert up_narrow.csp_eligible is False or up_narrow.days_above_both_emas <= 3

    def test_no_store_works_normally(self):
        """Engine without a store behaves identically to before."""
        engine = ConvictionEngine(ema_fast=8, ema_slow=21, signal_store=None)
        data = {"UP": _make_ohlcv(_fresh_uptrend(80))}
        signals = engine.analyze_batch(data)
        assert len(signals) == 1
        assert signals[0].ticker == "UP"

    def test_store_not_written_when_all_from_cache(self, engine, store):
        """If everything comes from in-memory cache, no redundant store write."""
        data = {"UP": _make_ohlcv(_fresh_uptrend(80))}
        engine.analyze_batch(data)
        assert store.exists

        store.clear()
        assert not store.exists

        engine.analyze_batch(data)
        assert not store.exists
