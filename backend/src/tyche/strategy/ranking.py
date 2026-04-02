"""Post-gate composite ranking for CSP candidates.

After hard eligibility gates pass, candidates need a quality ranking.
Two modes are supported:

* ``legacy``    — current behavior: sort by ``ScoredCandidate.score``
                  (annualised return + optional 21-EMA bonus).
* ``composite`` — multi-factor score combining normalised conviction
                  strength, EMA proximity quality, trend persistence,
                  and liquidity.

The composite mode does NOT replace hard gates — it only re-ranks
candidates that already passed all eligibility checks.

Toggle via ``TYCHE_RANKING_MODE=legacy|composite`` or at call-time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from tyche.strategy.strategies.base import ScoredCandidate

logger = structlog.get_logger()


@dataclass(frozen=True)
class RankingWeights:
    """Relative weights for each composite factor.

    All weights are >= 0.  They are normalised internally so the user
    can use any convenient scale (e.g. all 1.0 for equal weighting).
    """

    conviction: float = 1.0
    ema_proximity: float = 1.0
    trend_persistence: float = 0.8
    liquidity: float = 0.6

    def total(self) -> float:
        return self.conviction + self.ema_proximity + self.trend_persistence + self.liquidity


# ── Factor Extractors ────────────────────────────────────────────────────

_CONVICTION_SCORES = {"high": 1.0, "medium": 0.65, "low": 0.3, "none": 0.0}


def _conviction_factor(candidate: ScoredCandidate, signals: dict[str, Any]) -> float:
    """Normalised conviction strength [0, 1]."""
    sig = signals.get(candidate.symbol)
    if sig is None:
        return 0.5
    level = getattr(sig, "conviction_level", "medium")
    return _CONVICTION_SCORES.get(level, 0.5)


def _ema_proximity_factor(candidate: ScoredCandidate, signals: dict[str, Any]) -> float:
    """How close the strike is to a support EMA [0, 1].

    Strikes near the 21-EMA (institutional defense) score highest.
    Falls off linearly to 0 at 15% away.
    """
    sig = signals.get(candidate.symbol)
    if sig is None:
        return 0.5

    ema_21 = getattr(sig, "ema_21", 0)
    if ema_21 <= 0:
        return 0.5

    distance_pct = abs(candidate.strike - ema_21) / ema_21 * 100
    return max(0.0, 1.0 - distance_pct / 15.0)


def _trend_persistence_factor(candidate: ScoredCandidate, signals: dict[str, Any]) -> float:
    """Trend durability [0, 1].

    Uses days_above_both_emas for uptrend path and prior_streak for
    pullback path.  Sweet spot (5-10 days) scores highest.
    """
    sig = signals.get(candidate.symbol)
    if sig is None:
        return 0.5

    trend_state = getattr(sig, "trend_state", "")
    trend_str = trend_state.value if hasattr(trend_state, "value") else str(trend_state)
    is_pullback = "pullback" in trend_str

    if is_pullback:
        streak = getattr(sig, "prior_streak", 0)
        if streak <= 0:
            streak = getattr(sig, "days_above_both_emas", 0)
    else:
        streak = getattr(sig, "days_above_both_emas", 0)

    if streak >= 5:
        return min(1.0, 0.5 + streak * 0.05)
    return streak / 10.0


def _liquidity_factor(candidate: ScoredCandidate) -> float:
    """Option liquidity quality [0, 1].

    Based on open interest and bid-ask spread.
    """
    oi_score = min(1.0, candidate.open_interest / 1000)

    spread = candidate.ask - candidate.bid
    mid = (candidate.ask + candidate.bid) / 2 if (candidate.ask + candidate.bid) > 0 else 1.0
    spread_pct = (spread / mid * 100) if mid > 0 else 50.0
    spread_score = max(0.0, 1.0 - spread_pct / 30.0)

    return oi_score * 0.6 + spread_score * 0.4


# ── Composite Scorer ─────────────────────────────────────────────────────


def compute_composite_score(
    candidate: ScoredCandidate,
    signals: dict[str, Any],
    weights: RankingWeights | None = None,
) -> float:
    """Compute a single composite ranking score for one candidate.

    Returns a value in [0, 1] that blends all normalised factors
    according to the provided weights.
    """
    w = weights or RankingWeights()
    total_w = w.total()
    if total_w == 0:
        return 0.0

    conv = _conviction_factor(candidate, signals)
    ema = _ema_proximity_factor(candidate, signals)
    trend = _trend_persistence_factor(candidate, signals)
    liq = _liquidity_factor(candidate)

    raw = (
        conv * w.conviction
        + ema * w.ema_proximity
        + trend * w.trend_persistence
        + liq * w.liquidity
    ) / total_w

    return round(raw, 6)


def rank_candidates(
    candidates: list[ScoredCandidate],
    signals: dict[str, Any],
    mode: str = "legacy",
    weights: RankingWeights | None = None,
) -> list[ScoredCandidate]:
    """Rank eligible candidates using the specified mode.

    Args:
        candidates: Already-filtered eligible candidates.
        signals: Conviction signals by ticker symbol.
        mode: ``legacy`` or ``composite``.
        weights: Factor weights (only used in composite mode).

    Returns:
        Candidates sorted best-first.  In composite mode, each
        candidate's ``.score`` is updated to the composite value.
    """
    if mode == "legacy":
        candidates.sort(key=lambda c: c.score, reverse=True)
        logger.info("ranking_applied", mode="legacy", count=len(candidates))
        return candidates

    if mode == "composite":
        for c in candidates:
            c.score = compute_composite_score(c, signals, weights)
        candidates.sort(key=lambda c: c.score, reverse=True)
        logger.info(
            "ranking_applied",
            mode="composite",
            count=len(candidates),
            weights=(weights or RankingWeights()).__dict__,
        )
        return candidates

    raise ValueError(f"Unknown ranking mode '{mode}'. Must be 'legacy' or 'composite'.")
