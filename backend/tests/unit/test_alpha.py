"""Tests for the Directional Alpha engine: labels, features, scoring, store."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from tyche.ml.features import (
    MOMENTUM_FEATURE_COLS,
    RS_FEATURE_COLS,
    add_relative_strength_features,
    extract_ticker_features,
)
from tyche.ml.labels import BIG_MOVE_SPECS, compute_labels_vectorized
from tyche.strategy.alpha_engine import AlphaScoreEngine, AlphaFactors


def _make_ohlcv(n: int = 400, base: float = 100.0, drift: float = 0.001, seed: int = 0) -> pd.DataFrame:
    dates = [date(2023, 1, 2) + timedelta(days=i) for i in range(n)]
    rng = np.random.default_rng(seed)
    closes = base * np.cumprod(1 + rng.normal(drift, 0.02, n))
    return pd.DataFrame({
        "date": dates,
        "open": closes,
        "high": closes * 1.01,
        "low": closes * 0.99,
        "close": closes,
        "volume": rng.integers(1_000_000, 5_000_000, n),
    })


class TestBigMoveLabels:
    def test_big_move_columns_present(self):
        labels = compute_labels_vectorized(_make_ohlcv(300))
        for horizon, gain in BIG_MOVE_SPECS:
            col = f"big_move_up_{int(gain)}pct_{horizon}d"
            assert col in labels.columns

    def test_big_move_fires_on_large_gain(self):
        # Build a series that jumps +50% within 40 days from day 0.
        n = 120
        dates = [date(2023, 1, 2) + timedelta(days=i) for i in range(n)]
        closes = np.concatenate([
            np.full(1, 100.0),
            np.linspace(100.0, 160.0, 40),
            np.full(n - 41, 160.0),
        ])
        df = pd.DataFrame({
            "date": dates, "open": closes, "high": closes,
            "low": closes, "close": closes, "volume": np.full(n, 1_000_000),
        })
        labels = compute_labels_vectorized(df)
        # Row 0 should see a +25% move within 40d.
        assert labels["big_move_up_25pct_40d"].iloc[0] == 1.0

    def test_big_move_zero_when_flat(self):
        n = 200
        dates = [date(2023, 1, 2) + timedelta(days=i) for i in range(n)]
        closes = np.full(n, 100.0)
        df = pd.DataFrame({
            "date": dates, "open": closes, "high": closes,
            "low": closes, "close": closes, "volume": np.full(n, 1_000_000),
        })
        labels = compute_labels_vectorized(df)
        assert labels["big_move_up_25pct_40d"].dropna().sum() == 0.0

    def test_tail_rows_are_nan(self):
        labels = compute_labels_vectorized(_make_ohlcv(200))
        # The last 40 rows can't have a complete 40d forward window.
        assert labels["big_move_up_25pct_40d"].iloc[-1] != labels["big_move_up_25pct_40d"].iloc[-1] \
            or np.isnan(labels["big_move_up_25pct_40d"].iloc[-1])


class TestMomentumFeatures:
    def test_momentum_columns_present(self):
        feats = extract_ticker_features(_make_ohlcv(400), market_cap=5e9, min_bars=60)
        for col in MOMENTUM_FEATURE_COLS:
            assert col in feats.columns, f"Missing: {col}"

    def test_ema_stack_score_range(self):
        feats = extract_ticker_features(_make_ohlcv(400), market_cap=5e9, min_bars=60)
        assert feats["ema_stack_score"].dropna().between(0, 3).all()

    def test_breakout_flags_binary(self):
        feats = extract_ticker_features(_make_ohlcv(400), market_cap=5e9, min_bars=60)
        assert set(feats["breakout_20d"].dropna().unique()).issubset({0, 1})

    def test_relative_strength_uses_spy(self):
        feats = extract_ticker_features(_make_ohlcv(400, seed=1), market_cap=5e9, min_bars=60)
        feats["ticker"] = "TEST"
        feats["date"] = pd.to_datetime(feats["date"])
        spy = _make_ohlcv(400, seed=2)
        out = add_relative_strength_features(feats, spy_ohlcv=spy)
        for col in RS_FEATURE_COLS:
            assert col in out.columns
        assert out["rs_126d"].notna().sum() > 0

    def test_relative_strength_nan_without_spy(self):
        feats = extract_ticker_features(_make_ohlcv(400), market_cap=5e9, min_bars=60)
        feats["ticker"] = "TEST"
        feats["date"] = pd.to_datetime(feats["date"])
        out = add_relative_strength_features(feats, spy_ohlcv=None)
        assert out["rs_126d"].isna().all()


class TestAlphaScoreEngine:
    def _feature_row(self, **overrides) -> pd.DataFrame:
        base = {
            "ticker": "TEST",
            "close": 100.0,
            "return_63d": 0.1,
            "return_126d": 0.25,
            "return_252d": 0.4,
            "rs_63d": 0.05,
            "rs_126d": 0.15,
            "ema_stack_score": 3,
            "slope_accel": 0.1,
            "price_to_200ema_pct": 12.0,
            "pct_off_52w_high": -3.0,
            "breakout_20d": 1,
            "breakout_63d": 1,
            "volume_thrust_ratio": 1.5,
        }
        base.update(overrides)
        return pd.DataFrame([base])

    def test_strong_setup_scores_high(self):
        engine = AlphaScoreEngine()
        signals = engine.score_from_features(self._feature_row())
        assert len(signals) == 1
        assert signals[0].alpha_score > 50
        assert signals[0].signal in ("buy", "strong_buy")

    def test_weak_setup_scores_low(self):
        engine = AlphaScoreEngine()
        row = self._feature_row(
            return_63d=-0.2, return_126d=-0.3, return_252d=-0.4,
            rs_63d=-0.1, rs_126d=-0.2, ema_stack_score=0,
            slope_accel=-0.1, price_to_200ema_pct=-15.0,
            pct_off_52w_high=-40.0, breakout_20d=0, breakout_63d=0,
            volume_thrust_ratio=0.8,
        )
        signals = engine.score_from_features(row)
        assert signals[0].alpha_score < 44
        assert signals[0].signal == "avoid"

    def test_ml_probabilities_boost_score(self):
        engine = AlphaScoreEngine()
        row = self._feature_row()
        no_ml = engine.score_from_features(row)[0].alpha_score
        probs = {
            "big_move_up_25pct_40d": np.array([0.9]),
            "big_move_up_40pct_60d": np.array([0.8]),
            "big_move_up_60pct_120d": np.array([0.7]),
        }
        with_ml = engine.score_from_features(row, breakout_probs=probs)[0].alpha_score
        assert with_ml > no_ml

    def test_horizon_from_ml(self):
        engine = AlphaScoreEngine()
        row = self._feature_row()
        probs = {
            "big_move_up_25pct_40d": np.array([0.2]),
            "big_move_up_40pct_60d": np.array([0.9]),
            "big_move_up_60pct_120d": np.array([0.3]),
        }
        signals = engine.score_from_features(row, breakout_probs=probs)
        assert signals[0].horizon == "trend"

    def test_empty_features(self):
        engine = AlphaScoreEngine()
        assert engine.score_from_features(pd.DataFrame()) == []

    def test_factor_blend_bounds(self):
        f = AlphaFactors(momentum=1.0, relative_strength=1.0, trend_quality=1.0,
                         breakout=1.0, volume_thrust=1.0)
        assert abs(f.blended() - 1.0) < 1e-9
        assert AlphaFactors().blended() == 0.0

    def test_signal_thresholds(self):
        engine = AlphaScoreEngine(strong_buy_threshold=72, buy_threshold=58, watch_threshold=44)
        assert engine._classify_signal(80) == "strong_buy"
        assert engine._classify_signal(60) == "buy"
        assert engine._classify_signal(45) == "watch"
        assert engine._classify_signal(30) == "avoid"

    def test_to_dict_serializable(self):
        engine = AlphaScoreEngine()
        sig = engine.score_from_features(self._feature_row())[0]
        d = sig.to_dict()
        assert d["ticker"] == "TEST"
        assert "factors" in d and "momentum" in d["factors"]
        assert isinstance(d["alpha_score"], float)


class TestBreakoutPredictor:
    def test_graceful_degradation_no_model(self, tmp_path):
        pytest.importorskip("xgboost")
        from tyche.ml.breakout import BreakoutPredictor

        predictor = BreakoutPredictor(data_dir=str(tmp_path))
        assert predictor.is_available is False
        assert predictor.predict_proba_batch(pd.DataFrame([{"a": 1}])) == {}


class TestAlphaSignalStore:
    def test_round_trip(self, tmp_path):
        from tyche.market_data.alpha_store import AlphaSignalStore

        store = AlphaSignalStore(data_dir=str(tmp_path))
        assert store.exists is False

        records = [{
            "ticker": "AAA",
            "alpha_score": 75.0,
            "signal": "strong_buy",
            "horizon": "swing",
            "factors": {"momentum": 0.8, "relative_strength": 0.7, "trend_quality": 0.9,
                        "breakout": 0.6, "volume_thrust": 0.5},
            "breakout_prob_swing": 0.85,
            "breakout_prob_trend": None,
            "breakout_prob_thematic": None,
            "last_close": 100.0,
            "ema_stack_score": 3,
        }]
        store.write(records, as_of=date(2026, 1, 15))
        assert store.exists

        out, as_of, computed = store.read_latest()
        assert as_of == "2026-01-15"
        assert computed is not None
        assert len(out) == 1
        assert out[0]["ticker"] == "AAA"
        assert out[0]["factors"]["momentum"] == 0.8
        assert out[0]["breakout_prob_trend"] is None

    def test_empty_read(self, tmp_path):
        from tyche.market_data.alpha_store import AlphaSignalStore

        store = AlphaSignalStore(data_dir=str(tmp_path))
        out, as_of, computed = store.read_latest()
        assert out == []
        assert as_of is None

    @staticmethod
    def _rec(ticker: str, score: float, horizon: str = "swing") -> dict:
        return {
            "ticker": ticker,
            "alpha_score": score,
            "signal": "strong_buy" if score >= 70 else "buy",
            "horizon": horizon,
            "factors": {"momentum": 0.8, "relative_strength": 0.7, "trend_quality": 0.9,
                        "breakout": 0.6, "volume_thrust": 0.5},
            "demand": {"net": 0.2},
            "breakout_prob_swing": 0.85,
            "breakout_prob_trend": 0.70,
            "breakout_prob_thematic": None,
            "market_cap": 5e9,
            "last_close": 100.0,
        }

    def test_write_creates_dated_snapshot_and_marker(self, tmp_path):
        from tyche.market_data.alpha_store import AlphaSignalStore

        store = AlphaSignalStore(data_dir=str(tmp_path), variant="sustained")
        store.write([self._rec("AAA", 75.0)], as_of=date(2026, 1, 15))

        assert store.list_snapshot_dates() == ["2026-01-15"]
        marker = store.current()
        assert marker is not None
        assert marker["latest_date"] == "2026-01-15"
        assert marker["variant"] == "sustained"
        assert marker["rows"] == 1
        assert marker["rel_path"].endswith("alpha_history/sustained/2026-01-15.parquet")

    def test_read_snapshot_round_trip(self, tmp_path):
        from tyche.market_data.alpha_store import AlphaSignalStore

        store = AlphaSignalStore(data_dir=str(tmp_path))
        store.write([self._rec("AAA", 75.0)], as_of=date(2026, 1, 15))

        out, as_of, computed = store.read_snapshot("2026-01-15")
        assert as_of == "2026-01-15"
        assert len(out) == 1 and out[0]["ticker"] == "AAA"
        assert out[0]["factors"]["momentum"] == 0.8
        assert out[0]["demand"]["net"] == 0.2

        empty, _, _ = store.read_snapshot("2020-01-01")
        assert empty == []

    def test_read_history_concats_recent_sessions(self, tmp_path):
        from tyche.market_data.alpha_store import AlphaSignalStore

        store = AlphaSignalStore(data_dir=str(tmp_path), variant="sustained")
        store.write([self._rec("AAA", 80.0), self._rec("BBB", 60.0)], as_of=date(2026, 1, 13))
        store.write([self._rec("AAA", 78.0), self._rec("BBB", 62.0)], as_of=date(2026, 1, 14))
        store.write([self._rec("AAA", 82.0), self._rec("BBB", 55.0)], as_of=date(2026, 1, 15))

        assert store.list_snapshot_dates() == ["2026-01-13", "2026-01-14", "2026-01-15"]

        panel = store.read_history(sessions=2)
        assert "date" in panel.columns
        assert set(panel["date"].unique()) == {"2026-01-14", "2026-01-15"}
        assert len(panel) == 4  # 2 tickers x 2 sessions

        full = store.read_history()
        assert full["date"].nunique() == 3
        assert set(full["ticker"].unique()) == {"AAA", "BBB"}

    def test_read_history_empty_when_no_snapshots(self, tmp_path):
        from tyche.market_data.alpha_store import AlphaSignalStore

        store = AlphaSignalStore(data_dir=str(tmp_path))
        assert store.read_history().empty
        assert store.list_snapshot_dates() == []
        assert store.current() is None

    def test_backfill_current_to_history(self, tmp_path):
        from tyche.market_data.alpha_store import AlphaSignalStore

        # Simulate a legacy latest-only snapshot with no dated history.
        store = AlphaSignalStore(data_dir=str(tmp_path))
        store.write([self._rec("AAA", 75.0)], as_of=date(2026, 1, 15))
        # Remove the dated history to emulate the pre-migration state.
        hist = tmp_path / "alpha_history" / "peak"
        for p in hist.glob("*"):
            p.unlink()
        assert store.list_snapshot_dates() == []

        seeded = store.backfill_current_to_history()
        assert seeded == "2026-01-15"
        assert store.list_snapshot_dates() == ["2026-01-15"]
        # Idempotent: a second call is a no-op.
        assert store.backfill_current_to_history() is None

    def test_same_day_rewrite_updates_snapshot_and_marker(self, tmp_path):
        from tyche.market_data.alpha_store import AlphaSignalStore

        store = AlphaSignalStore(data_dir=str(tmp_path), variant="sustained")
        store.write([self._rec("AAA", 70.0)], as_of=date(2026, 1, 15))
        store.write(
            [self._rec("AAA", 80.0), self._rec("BBB", 60.0)],
            as_of=date(2026, 1, 15),
        )

        assert store.list_snapshot_dates() == ["2026-01-15"]
        out, as_of, _ = store.read_snapshot("2026-01-15")
        assert as_of == "2026-01-15"
        assert {r["ticker"] for r in out} == {"AAA", "BBB"}
        assert next(r["alpha_score"] for r in out if r["ticker"] == "AAA") == 80.0
        marker = store.current()
        assert marker is not None
        assert marker["rows"] == 2
        assert marker["latest_date"] == "2026-01-15"
