"""Tests for the Phase 1 Demand Conviction feature groups + anti-chase.

Covers anti-chase over-extension features, point-in-time fundamentals (D-FUND),
estimates/revisions (D-EST), short interest (D-TECH), the feature-column flag
plumbing, the sustained big-move labels, and the alpha-engine over-extension
penalty.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from tyche.ml.features import (
    ANTI_CHASE_FEATURE_COLS,
    ESTIMATE_FEATURE_COLS,
    FUNDAMENTAL_FEATURE_COLS,
    SHORT_INTEREST_FEATURE_COLS,
    add_estimate_features,
    add_fundamental_features,
    add_short_interest_features,
    extract_ticker_features,
)
from tyche.ml.labels import compute_labels_vectorized
from tyche.ml.xgb_baseline import demand_feature_columns, get_feature_columns


def _ohlcv(n: int, closes) -> pd.DataFrame:
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(n)]
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": np.full(n, 1_000_000),
        }
    )


# ── Anti-chase ─────────────────────────────────────────────────────────


class TestAntiChase:
    def test_columns_present_and_bounded(self):
        n = 300
        closes = 100 + np.cumsum(np.full(n, 0.1))
        feats = extract_ticker_features(_ohlcv(n, closes), min_bars=60)
        for col in ANTI_CHASE_FEATURE_COLS:
            assert col in feats.columns
        assert feats["overextension_score"].between(0.0, 1.0).all()
        assert feats["rsi_overbought"].between(0.0, 1.0).all()

    def test_parabolic_run_scores_high(self):
        # Flat then a sharp parabolic spike → high over-extension at the top.
        n = 300
        flat = np.full(220, 100.0)
        spike = 100.0 * (1.0 + np.linspace(0, 1.2, 80))  # +120% run
        closes = np.concatenate([flat, spike])
        feats = extract_ticker_features(_ohlcv(n, closes), min_bars=60)
        assert feats["overextension_score"].iloc[-1] > 0.55

    def test_calm_uptrend_scores_low(self):
        n = 300
        closes = 100 + np.cumsum(np.full(n, 0.05))  # gentle drift
        feats = extract_ticker_features(_ohlcv(n, closes), min_bars=60)
        assert feats["overextension_score"].iloc[-1] < 0.4


# ── Fundamentals (D-FUND) ──────────────────────────────────────────────


class _FakeFundStore:
    def __init__(self, df: pd.DataFrame):
        self._df = df

    def read_ticker(self, ticker, timeframe="quarterly", as_of=None):
        return self._df.copy()


class TestFundamentalFeatures:
    def _fund(self) -> pd.DataFrame:
        # 8 quarters so YoY (4-back) is computable for both feature dates.
        periods = [
            "2023-12-31", "2024-03-31", "2024-06-30", "2024-09-30",
            "2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30",
        ]
        filings = [
            "2024-01-28", "2024-04-28", "2024-07-28", "2024-10-28",
            "2025-01-28", "2025-04-28", "2025-07-28", "2025-10-28",
        ]
        rev = [600, 650, 700, 800, 850, 900, 950, 1200]
        return pd.DataFrame(
            {
                "period_end": pd.to_datetime(periods).date,
                "filing_date": pd.to_datetime(filings).date,
                "timeframe": "quarterly",
                "revenue": rev,
                "gross_profit": [r * 0.4 for r in rev],
                "operating_income": [r * 0.2 for r in rev],
                "net_income": [r * 0.15 for r in rev],
                "eps_diluted": [0.8, 0.9, 1.0, 1.0, 1.1, 1.2, 1.3, 1.6],
                "free_cash_flow": [r * 0.1 for r in rev],
                "shares_diluted": [100] * 8,
                "gross_margin": [40.0] * 8,
                "operating_margin": [20.0] * 8,
                "net_margin": [15.0] * 8,
            }
        )

    def test_growth_and_pit(self):
        store = _FakeFundStore(self._fund())
        # Two feature dates: one before the latest filing, one after.
        feats = pd.DataFrame(
            {
                "ticker": ["MU", "MU"],
                "date": [date(2025, 10, 1), date(2025, 11, 1)],
                "close": [100.0, 110.0],
            }
        )
        out = add_fundamental_features(feats, fundamentals_store=store)
        for col in FUNDAMENTAL_FEATURE_COLS:
            assert col in out.columns

        before = out[out["date"] == date(2025, 10, 1)].iloc[0]
        after = out[out["date"] == date(2025, 11, 1)].iloc[0]
        # Before the 2025-10-28 filing, latest visible quarter is 2025-06-30
        # (filed 2025-07-28) → YoY = 950/700 - 1 (vs 2024-06-30).
        assert before["f_rev_growth_yoy"] == pytest.approx(950 / 700 - 1, abs=1e-6)
        # After the filing, latest is 2025-09-30 → YoY = 1200/800 - 1 = 0.5.
        assert after["f_rev_growth_yoy"] == pytest.approx(0.5, abs=1e-6)
        assert after["f_fcf_positive"] == 1.0

    def test_no_store_fills_defaults(self):
        feats = pd.DataFrame({"ticker": ["MU"], "date": [date(2025, 10, 1)], "close": [100.0]})
        out = add_fundamental_features(feats, fundamentals_store=None)
        assert out["f_rev_growth_yoy"].isna().all()

    def test_mixed_datetime_resolution(self):
        """Regression: a Parquet round-trip yields datetime64[s]/[us] filing
        dates while the feature frame is a different unit. merge_asof must not
        raise — before the fix this MergeError was swallowed and D-FUND was
        zeroed for the ENTIRE universe (every ticker forced to narrative regime).
        """
        fund = self._fund()
        fund["filing_date"] = pd.to_datetime(fund["filing_date"]).astype("datetime64[s]")
        store = _FakeFundStore(fund)
        feats = pd.DataFrame(
            {
                "ticker": ["MU"],
                "date": pd.Series([pd.Timestamp("2025-11-01")], dtype="datetime64[us]"),
                "close": [110.0],
            }
        )
        out = add_fundamental_features(feats, fundamentals_store=store)
        assert out["f_rev_growth_yoy"].notna().all()
        assert out.iloc[0]["f_rev_growth_yoy"] == pytest.approx(0.5, abs=1e-6)


# ── Estimates (D-EST) ──────────────────────────────────────────────────


class _FakeEstStore:
    def __init__(self, df: pd.DataFrame):
        self._df = df

    def read_ticker(self, ticker, metric=None, as_of=None):
        return self._df.copy()


class TestEstimateFeatures:
    def _est(self) -> pd.DataFrame:
        rows = []
        # EPS estimate rising over time (positive revision).
        rows.append({"snapshot_date": "2026-02-01", "metric": "eps_est_avg", "period": "2026-12-31", "value": 5.0})
        rows.append({"snapshot_date": "2026-05-01", "metric": "eps_est_avg", "period": "2026-12-31", "value": 5.8})
        # Recommendation counts at two snapshots (improving).
        for snap, sb in (("2026-02-01", 8), ("2026-05-01", 14)):
            rows.append({"snapshot_date": snap, "metric": "rec_strong_buy", "period": "", "value": sb})
            rows.append({"snapshot_date": snap, "metric": "rec_hold", "period": "", "value": 4})
        # Surprise history.
        rows.append({"snapshot_date": "2026-01-15", "metric": "eps_surprise_pct", "period": "2025-12-31", "value": 9.0})
        rows.append({"snapshot_date": "2026-05-01", "metric": "price_target_mean", "period": "", "value": 150.0})
        return pd.DataFrame(rows)

    def test_revisions_and_score(self):
        store = _FakeEstStore(self._est())
        feats = pd.DataFrame({"ticker": ["MU"], "date": [date(2026, 5, 10)], "close": [100.0]})
        out = add_estimate_features(feats, estimates_store=store)
        for col in ESTIMATE_FEATURE_COLS:
            assert col in out.columns
        row = out.iloc[0]
        # EPS revised 5.0 -> 5.8 over ~90 days → positive revision.
        assert row["e_eps_revision_90d"] > 0
        # Recommendation score in [-1, 1] and positive (mostly strong buys).
        assert -1.0 <= row["e_rec_score"] <= 1.0
        assert row["e_rec_score"] > 0
        assert row["e_eps_surprise_last"] == pytest.approx(9.0)
        # Price target 150 vs close 100 → +50% upside.
        assert row["e_price_target_upside"] == pytest.approx(50.0, abs=1.0)

    def test_no_store_fills_defaults(self):
        feats = pd.DataFrame({"ticker": ["MU"], "date": [date(2026, 5, 10)], "close": [100.0]})
        out = add_estimate_features(feats, estimates_store=None)
        assert out["e_rec_score"].isna().all()


# ── Short interest (D-TECH) ────────────────────────────────────────────


class _FakeSIStore:
    def __init__(self, df: pd.DataFrame):
        self._df = df

    def read_ticker(self, ticker, as_of=None):
        return self._df.copy()


class TestShortInterestFeatures:
    def test_point_in_time_merge(self):
        si = pd.DataFrame(
            {
                "settlement_date": pd.to_datetime(["2026-04-15", "2026-04-30"]).date,
                "short_interest": [8e6, 10e6],
                "days_to_cover": [4.0, 5.0],
                "short_interest_ratio": [4.0, 5.0],
                "short_pct_float": [0.1, 0.12],
            }
        )
        store = _FakeSIStore(si)
        feats = pd.DataFrame(
            {
                "ticker": ["MU", "MU"],
                "date": [date(2026, 4, 20), date(2026, 5, 5)],
                "close": [100.0, 100.0],
            }
        )
        out = add_short_interest_features(feats, short_interest_store=store)
        for col in SHORT_INTEREST_FEATURE_COLS:
            assert col in out.columns
        early = out[out["date"] == date(2026, 4, 20)].iloc[0]
        late = out[out["date"] == date(2026, 5, 5)].iloc[0]
        assert early["si_days_to_cover"] == pytest.approx(4.0)  # only 04-15 visible
        assert late["si_days_to_cover"] == pytest.approx(5.0)  # 04-30 now visible

    def test_mixed_datetime_resolution(self):
        """Regression: Parquet-deserialized settlement dates (datetime64[s]) vs a
        [us]/[ns] feature frame must not raise in merge_asof — before the fix this
        zeroed D-TECH for the whole universe."""
        si = pd.DataFrame(
            {
                "settlement_date": pd.Series(
                    pd.to_datetime(["2026-04-15", "2026-04-30"]), dtype="datetime64[s]"
                ),
                "short_interest": [8e6, 10e6],
                "days_to_cover": [4.0, 5.0],
                "short_interest_ratio": [4.0, 5.0],
                "short_pct_float": [0.1, 0.12],
            }
        )
        store = _FakeSIStore(si)
        feats = pd.DataFrame(
            {
                "ticker": ["MU"],
                "date": pd.Series([pd.Timestamp("2026-05-05")], dtype="datetime64[us]"),
                "close": [100.0],
            }
        )
        out = add_short_interest_features(feats, short_interest_store=store)
        assert out["si_days_to_cover"].notna().all()
        assert out.iloc[0]["si_days_to_cover"] == pytest.approx(5.0)


class TestDemandApplyOnce:
    """Regression: demand augmentation must run exactly once at serving time."""

    def test_second_fundamental_pass_is_idempotent(self):
        """In-place augmentation must be safe if called twice (no _x/_y suffix corruption)."""
        fund_df = TestFundamentalFeatures()._fund()
        store = _FakeFundStore(fund_df)
        feats = pd.DataFrame(
            {"ticker": ["MU"], "date": [date(2025, 11, 1)], "close": [100.0]}
        )
        once = add_fundamental_features(feats, fundamentals_store=store)
        assert once["f_rev_growth_yoy"].notna().all()
        twice = add_fundamental_features(once, fundamentals_store=store)
        assert twice["f_rev_growth_yoy"].notna().all()
        assert twice["f_rev_growth_yoy"].iloc[0] == pytest.approx(once["f_rev_growth_yoy"].iloc[0])


# ── Feature column plumbing ────────────────────────────────────────────


class TestFeatureColumns:
    def test_flags_extend(self):
        base = get_feature_columns()
        assert not any(c.startswith("f_") for c in base)
        with_fund = get_feature_columns(include_fundamental=True)
        assert all(c in with_fund for c in FUNDAMENTAL_FEATURE_COLS)

    def test_demand_set_is_superset(self):
        cols = demand_feature_columns()
        for group in (
            ANTI_CHASE_FEATURE_COLS,
            FUNDAMENTAL_FEATURE_COLS,
            ESTIMATE_FEATURE_COLS,
            SHORT_INTEREST_FEATURE_COLS,
        ):
            assert all(c in cols for c in group)
        # No duplicate columns.
        assert len(cols) == len(set(cols))


# ── Sustained big-move labels ──────────────────────────────────────────


class TestSustainedLabels:
    def test_sustained_requires_close_at_horizon(self):
        # Spike at +30d then full retrace by +40d: peak label fires, sustained
        # label does not.
        n = 120
        closes = np.full(n, 100.0)
        closes[20:30] = 140.0  # transient +40% spike around day 20-30
        # day 0's forward-40d close is back at 100 → sustained = 0; peak = 1.
        labels = compute_labels_vectorized(_ohlcv(n, closes))
        assert "big_move_sustained_25pct_40d" in labels.columns
        assert labels["big_move_up_25pct_40d"].iloc[0] == 1.0
        assert labels["big_move_sustained_25pct_40d"].iloc[0] == 0.0

    def test_sustained_fires_when_held(self):
        n = 120
        closes = np.full(n, 100.0)
        closes[40:] = 140.0  # steps up +40% and holds
        labels = compute_labels_vectorized(_ohlcv(n, closes))
        assert labels["big_move_sustained_25pct_40d"].iloc[0] == 1.0


# ── Over-extension penalty in the alpha engine ─────────────────────────


class TestOverextensionPenalty:
    def test_penalty_demotes_parabolic(self):
        from tyche.strategy.alpha_engine import AlphaScoreEngine

        engine = AlphaScoreEngine()
        common = {
            "ticker": "X",
            "return_63d": 0.6,
            "return_126d": 0.9,
            "return_252d": 1.5,
            "rs_126d": 0.5,
            "rs_63d": 0.3,
            "ema_stack_score": 3,
            "slope_accel": 0.1,
            "price_to_200ema_pct": 80.0,
            "pct_off_52w_high": 0.0,
            "breakout_20d": 1,
            "volume_thrust_ratio": 1.5,
            "close": 100.0,
        }
        fresh = dict(common, overextension_score=0.0)
        stretched = dict(common, overextension_score=1.0)
        fresh_sig = engine.score_from_features(pd.DataFrame([fresh]))[0]
        stretched_sig = engine.score_from_features(pd.DataFrame([stretched]))[0]

        assert stretched_sig.alpha_score < fresh_sig.alpha_score
        assert stretched_sig.overextension_penalty == pytest.approx(0.55, abs=1e-6)
        assert fresh_sig.overextension_penalty == pytest.approx(1.0, abs=1e-6)
