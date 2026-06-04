"""Discovery-mode AlphaScoreEngine (percentile signals, demand-adjusted extension)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from tyche.strategy.alpha_engine import AlphaScoreEngine, build_alpha_score_engine


def _synthetic_row(**overrides) -> dict:
    base = {
        "ticker": "SYN",
        "close": 100.0,
        "return_63d": 0.10,
        "return_126d": 0.20,
        "return_252d": 0.30,
        "rs_63d": 0.05,
        "rs_126d": 0.10,
        "ema_stack_score": 2,
        "slope_accel": 1,
        "price_to_200ema_pct": 5.0,
        "rsi_14": 55.0,
        "volume_thrust_ratio": 1.2,
        "f_quarters_since_filing": 1.0,
        "f_rev_growth_yoy": 0.15,
        "overextension_score": 0.2,
    }
    base.update(overrides)
    return base


def test_conservative_engine_matches_default_fixture_behavior() -> None:
    engine = AlphaScoreEngine()
    row = _synthetic_row()
    sig = engine.score_from_features(pd.DataFrame([row]))[0]
    assert sig.score_percentile is None
    assert sig.demand_adjusted_extension_applied is False


def test_percentile_signals_top_one_percent_strong_buy() -> None:
    engine = AlphaScoreEngine(percentile_signals=True)
    rows = [
        _synthetic_row(ticker=f"T{i}", return_126d=0.02 + i * 0.002, return_252d=i * 0.001)
        for i in range(500)
    ]
    probs = {"big_move_up_40pct_60d": np.linspace(0.15, 0.85, 500)}
    signals = engine.score_from_features(pd.DataFrame(rows), breakout_probs=probs)
    strong = sum(1 for s in signals if s.signal == "strong_buy")
    assert 4 <= strong <= 6
    assert all(s.score_percentile is not None for s in signals)


def test_demand_adjusted_extension_favors_high_demand_net() -> None:
    engine = AlphaScoreEngine(demand_adjusted_extension=True)
    high = _synthetic_row(
        ticker="HIGH",
        overextension_score=0.9,
        cat_demand_score=0.8,
        cat_policy_score=0.5,
        f_rev_growth_yoy=0.30,
        e_rec_score=0.5,
    )
    low = _synthetic_row(
        ticker="LOW",
        overextension_score=0.9,
        cat_demand_score=-0.6,
        cat_policy_score=-0.4,
        f_rev_growth_yoy=-0.05,
    )
    probs = {"big_move_up_40pct_60d": np.array([0.5, 0.5])}
    out = engine.score_from_features(pd.DataFrame([high, low]), breakout_probs=probs)
    by_ticker = {s.ticker: s for s in out}
    assert by_ticker["HIGH"].alpha_score > by_ticker["LOW"].alpha_score
    assert by_ticker["HIGH"].demand_adjusted_extension_applied is True


def test_build_alpha_score_engine_discovery_gated() -> None:
    off = build_alpha_score_engine(discovery_enabled=False, percentile_signals=True)
    assert off._percentile_signals is False
    on = build_alpha_score_engine(
        discovery_enabled=True,
        percentile_signals=True,
        demand_adjusted_extension=True,
    )
    assert on._percentile_signals is True
    assert on._demand_adjusted_extension is True
