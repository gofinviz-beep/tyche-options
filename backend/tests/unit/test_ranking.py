"""Tests for tyche.strategy.ranking — post-gate composite ranking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from tyche.strategy.ranking import (
    RankingWeights,
    _conviction_factor,
    _ema_proximity_factor,
    _liquidity_factor,
    _trend_persistence_factor,
    compute_composite_score,
    rank_candidates,
)
from tyche.strategy.strategies.base import ScoredCandidate


# ── Fixtures ─────────────────────────────────────────────────────────────

@dataclass
class _FakeSignal:
    ticker: str
    conviction_level: str = "high"
    ema_21: float = 100.0
    ema_8: float = 102.0
    price_to_8ema_pct: float = 1.0
    days_above_both_emas: int = 7
    prior_streak: int = 0
    trend_state: str = "uptrend"


def _make_candidate(
    symbol: str = "AAPL",
    strike: float = 95.0,
    score: float = 50.0,
    open_interest: int = 500,
    bid: float = 1.50,
    ask: float = 1.70,
) -> ScoredCandidate:
    return ScoredCandidate(
        symbol=symbol,
        option_symbol=f"{symbol}260403P00095000",
        option_type="put",
        strike=strike,
        expiration=date(2026, 4, 10),
        dte=8,
        bid=bid,
        ask=ask,
        mid=(bid + ask) / 2,
        volume=100,
        open_interest=open_interest,
        implied_volatility=0.30,
        underlying_price=100.0,
        strategy="csp",
        premium_per_contract=150.0,
        collateral_required=9500.0,
        annualized_return_pct=20.0,
        score=score,
    )


# ── Factor Tests ─────────────────────────────────────────────────────────

class TestConvictionFactor:
    def test_high(self):
        c = _make_candidate()
        sig = {"AAPL": _FakeSignal("AAPL", conviction_level="high")}
        assert _conviction_factor(c, sig) == 1.0

    def test_medium(self):
        c = _make_candidate()
        sig = {"AAPL": _FakeSignal("AAPL", conviction_level="medium")}
        assert _conviction_factor(c, sig) == 0.65

    def test_low(self):
        c = _make_candidate()
        sig = {"AAPL": _FakeSignal("AAPL", conviction_level="low")}
        assert _conviction_factor(c, sig) == 0.3

    def test_no_signal(self):
        c = _make_candidate()
        assert _conviction_factor(c, {}) == 0.5


class TestEmaProximityFactor:
    def test_strike_at_21ema(self):
        c = _make_candidate(strike=100.0)
        sig = {"AAPL": _FakeSignal("AAPL", ema_21=100.0)}
        assert _ema_proximity_factor(c, sig) == 1.0

    def test_strike_far_from_21ema(self):
        c = _make_candidate(strike=80.0)
        sig = {"AAPL": _FakeSignal("AAPL", ema_21=100.0)}
        factor = _ema_proximity_factor(c, sig)
        assert factor < 0.5

    def test_no_signal(self):
        c = _make_candidate()
        assert _ema_proximity_factor(c, {}) == 0.5


class TestTrendPersistenceFactor:
    def test_sweet_spot_streak(self):
        c = _make_candidate()
        sig = {"AAPL": _FakeSignal("AAPL", days_above_both_emas=8)}
        factor = _trend_persistence_factor(c, sig)
        assert factor >= 0.8

    def test_short_streak(self):
        c = _make_candidate()
        sig = {"AAPL": _FakeSignal("AAPL", days_above_both_emas=2)}
        factor = _trend_persistence_factor(c, sig)
        assert factor < 0.5

    def test_pullback_uses_prior_streak(self):
        c = _make_candidate()
        sig = {"AAPL": _FakeSignal("AAPL", trend_state="pullback_to_8ema", prior_streak=10, days_above_both_emas=0)}
        factor = _trend_persistence_factor(c, sig)
        assert factor >= 0.8

    def test_no_signal(self):
        c = _make_candidate()
        assert _trend_persistence_factor(c, {}) == 0.5


class TestLiquidityFactor:
    def test_high_oi_tight_spread(self):
        c = _make_candidate(open_interest=2000, bid=1.50, ask=1.52)
        factor = _liquidity_factor(c)
        assert factor > 0.8

    def test_low_oi_wide_spread(self):
        c = _make_candidate(open_interest=50, bid=1.00, ask=2.00)
        factor = _liquidity_factor(c)
        assert factor < 0.5


# ── Composite Score ──────────────────────────────────────────────────────

class TestCompositeScore:
    def test_returns_between_0_and_1(self):
        c = _make_candidate()
        sig = {"AAPL": _FakeSignal("AAPL")}
        score = compute_composite_score(c, sig)
        assert 0 <= score <= 1

    def test_high_quality_scores_higher(self):
        high = _make_candidate(open_interest=2000, bid=1.50, ask=1.52)
        low = _make_candidate(open_interest=50, bid=1.00, ask=3.00, strike=80.0)
        sig = {"AAPL": _FakeSignal("AAPL", conviction_level="high")}
        sig_low = {"AAPL": _FakeSignal("AAPL", conviction_level="low", ema_21=120.0)}
        assert compute_composite_score(high, sig) > compute_composite_score(low, sig_low)

    def test_zero_weights(self):
        c = _make_candidate()
        w = RankingWeights(conviction=0, ema_proximity=0, trend_persistence=0, liquidity=0)
        assert compute_composite_score(c, {}, w) == 0.0

    def test_custom_weights(self):
        c = _make_candidate()
        sig = {"AAPL": _FakeSignal("AAPL")}
        w1 = RankingWeights(conviction=10, ema_proximity=0, trend_persistence=0, liquidity=0)
        w2 = RankingWeights(conviction=0, ema_proximity=0, trend_persistence=0, liquidity=10)
        s1 = compute_composite_score(c, sig, w1)
        s2 = compute_composite_score(c, sig, w2)
        assert s1 != s2


# ── rank_candidates ──────────────────────────────────────────────────────

class TestRankCandidates:
    def test_legacy_mode_sorts_by_score(self):
        c1 = _make_candidate("AAPL", score=10.0)
        c2 = _make_candidate("PL", score=20.0)
        c3 = _make_candidate("MSFT", score=15.0)
        result = rank_candidates([c1, c2, c3], {}, mode="legacy")
        assert result[0].symbol == "PL"
        assert result[1].symbol == "MSFT"
        assert result[2].symbol == "AAPL"

    def test_composite_mode_reranks(self):
        c_high = _make_candidate("AAPL", score=5.0, open_interest=2000, bid=1.5, ask=1.52, strike=100.0)
        c_low = _make_candidate("PL", score=50.0, open_interest=20, bid=0.5, ask=2.0, strike=80.0)
        signals = {
            "AAPL": _FakeSignal("AAPL", conviction_level="high", ema_21=100.0, days_above_both_emas=8),
            "PL": _FakeSignal("PL", conviction_level="low", ema_21=120.0, days_above_both_emas=1),
        }
        result = rank_candidates([c_high, c_low], signals, mode="composite")
        assert result[0].symbol == "AAPL"

    def test_composite_updates_score(self):
        c = _make_candidate("AAPL", score=999.0)
        signals = {"AAPL": _FakeSignal("AAPL")}
        result = rank_candidates([c], signals, mode="composite")
        assert result[0].score != 999.0
        assert 0 <= result[0].score <= 1

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown ranking mode"):
            rank_candidates([], {}, mode="quantum")

    def test_deterministic_ordering(self):
        """Same inputs always produce same output order."""
        candidates = [
            _make_candidate("A", score=10, open_interest=500, bid=1.5, ask=1.6, strike=95),
            _make_candidate("B", score=20, open_interest=800, bid=1.3, ask=1.4, strike=98),
            _make_candidate("C", score=15, open_interest=300, bid=2.0, ask=2.5, strike=90),
        ]
        signals = {
            "A": _FakeSignal("A", conviction_level="medium", ema_21=100, days_above_both_emas=6),
            "B": _FakeSignal("B", conviction_level="high", ema_21=100, days_above_both_emas=8),
            "C": _FakeSignal("C", conviction_level="low", ema_21=100, days_above_both_emas=3),
        }
        r1 = [c.symbol for c in rank_candidates(list(candidates), signals, mode="composite")]
        r2 = [c.symbol for c in rank_candidates(list(candidates), signals, mode="composite")]
        assert r1 == r2

    def test_empty_candidates(self):
        assert rank_candidates([], {}, mode="legacy") == []
        assert rank_candidates([], {}, mode="composite") == []


# ── RankingWeights ───────────────────────────────────────────────────────

class TestRankingWeights:
    def test_total(self):
        w = RankingWeights()
        assert w.total() == 3.4  # 1.0 + 1.0 + 0.8 + 0.6

    def test_custom_total(self):
        w = RankingWeights(conviction=2.0, ema_proximity=1.0, trend_persistence=0.0, liquidity=0.0)
        assert w.total() == 3.0

    def test_frozen(self):
        w = RankingWeights()
        with pytest.raises(AttributeError):
            w.conviction = 5.0  # type: ignore[misc]
