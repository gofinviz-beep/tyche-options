"""Dip catalyst classifier — determines if a deep dip is recoverable or fundamental.

Combines news sentiment, insider activity, and technical context to classify
oversold entries as either temporary (market fear, sector rotation, earnings
overreaction) or fundamental (regulatory, fraud, structural decline).

The classifier consumes existing signals from the news and EDGAR pipelines
without making its own API calls — it's a pure decision layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import structlog

logger = structlog.get_logger()


class DipCatalyst(str, Enum):
    """Classification of what caused the dip."""

    MARKET_FEAR = "market_fear"
    SECTOR_ROTATION = "sector_rotation"
    EARNINGS_REACTION = "earnings_reaction"
    NEWS_DRIVEN = "news_driven"
    INSIDER_SELLING = "insider_selling"
    REGULATORY = "regulatory"
    UNKNOWN = "unknown"


class DipRiskLevel(str, Enum):
    """Risk assessment for the dip — should we buy or avoid?"""

    LOW = "low"       # Temporary dip, strong recovery expected
    MEDIUM = "medium"  # Recoverable but uncertain, smaller position
    HIGH = "high"      # Fundamental risk, avoid or wait
    EXTREME = "extreme"  # Strong avoid signal


@dataclass
class DipClassification:
    """Result of classifying a deep dip for a single ticker."""

    ticker: str
    catalyst: DipCatalyst
    risk_level: DipRiskLevel
    reasons: list[str]
    actionable: bool  # True = proceed with buy + CC strategy

    news_impact_score: float | None = None
    negative_news_count: int = 0
    insider_cluster_sell: bool = False
    last_8k_impact: float | None = None
    rsi_at_entry: float = 50.0
    dip_pct: float = 0.0
    prior_streak: int = 0

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "catalyst": self.catalyst.value,
            "risk_level": self.risk_level.value,
            "reasons": self.reasons,
            "actionable": self.actionable,
            "news_impact_score": round(self.news_impact_score, 4) if self.news_impact_score is not None else None,
            "negative_news_count": self.negative_news_count,
            "insider_cluster_sell": self.insider_cluster_sell,
            "last_8k_impact": round(self.last_8k_impact, 4) if self.last_8k_impact is not None else None,
            "rsi_at_entry": round(self.rsi_at_entry, 2),
            "dip_pct": round(self.dip_pct, 2),
            "prior_streak": self.prior_streak,
        }


class DipCatalystClassifier:
    """Classifies deep dips as temporary vs fundamental using multi-signal fusion.

    All signals are optional — gracefully degrades when news/filing data
    is unavailable. The classifier is stateless (no cache, no I/O).
    """

    def __init__(
        self,
        *,
        news_risk_threshold: float = -0.3,
        insider_sell_blocks: bool = True,
        eightk_impact_threshold: float = -0.5,
        min_prior_streak_for_high_confidence: int = 15,
    ) -> None:
        self._news_risk = news_risk_threshold
        self._insider_blocks = insider_sell_blocks
        self._eightk_threshold = eightk_impact_threshold
        self._strong_streak = min_prior_streak_for_high_confidence

    def classify(
        self,
        ticker: str,
        *,
        dip_pct: float = 0.0,
        prior_streak: int = 0,
        rsi: float = 50.0,
        news_signal: dict | None = None,
        filing_signal: dict | None = None,
    ) -> DipClassification:
        """Classify a deep dip for a single ticker.

        Args:
            ticker: Stock symbol.
            dip_pct: How far below the EMA (positive = deeper).
            prior_streak: Days above both EMAs before the dip.
            rsi: RSI(14) at the dip entry point.
            news_signal: From news_signals table (news_impact_score,
                negative_count_24h, etc.). None if no news data.
            filing_signal: From filing_signals table (insider_cluster_sell,
                last_8k_impact, etc.). None if no filing data.
        """
        reasons: list[str] = []
        risk_factors = 0
        confidence_factors = 0

        news_impact = None
        neg_count = 0
        cluster_sell = False
        eightk_impact = None

        # --- News signals ---
        if news_signal:
            news_impact = news_signal.get("news_impact_score")
            neg_count = news_signal.get("negative_count_24h", 0)

            if news_impact is not None and news_impact < self._news_risk:
                risk_factors += 1
                reasons.append(
                    f"Negative news impact ({news_impact:.2f} < {self._news_risk})"
                )

            if neg_count >= 3:
                risk_factors += 1
                reasons.append(f"{neg_count} negative articles in 24h")

            if news_impact is not None and news_impact >= 0:
                confidence_factors += 1
                reasons.append("Neutral/positive news sentiment")

        # --- Insider / filing signals ---
        if filing_signal:
            cluster_sell = filing_signal.get("insider_cluster_sell", False)
            eightk_impact = filing_signal.get("last_8k_impact")

            if cluster_sell and self._insider_blocks:
                risk_factors += 2
                reasons.append("Insider cluster selling detected (3+ insiders)")

            if eightk_impact is not None and eightk_impact < self._eightk_threshold:
                risk_factors += 1
                reasons.append(
                    f"High-impact negative 8-K filing ({eightk_impact:.2f})"
                )

            buy_count = filing_signal.get("insider_buy_count_30d", 0)
            if buy_count >= 2:
                confidence_factors += 1
                reasons.append(f"{buy_count} insider buys in 30d — bullish signal")

        # --- Technical context ---
        if prior_streak >= self._strong_streak:
            confidence_factors += 1
            reasons.append(f"Strong prior uptrend ({prior_streak}d)")

        if rsi <= 25:
            confidence_factors += 1
            reasons.append(f"Deeply oversold RSI ({rsi:.0f})")
        elif rsi >= 50:
            risk_factors += 1
            reasons.append(f"RSI not oversold ({rsi:.0f}) — dip may continue")

        if dip_pct >= 15:
            risk_factors += 1
            reasons.append(f"Very deep dip ({dip_pct:.1f}%) — may be fundamental")

        # --- Catalyst classification ---
        catalyst = self._determine_catalyst(
            news_signal, filing_signal, cluster_sell, neg_count,
        )

        # --- Risk level determination ---
        risk_level = self._compute_risk_level(risk_factors, confidence_factors)

        actionable = risk_level in (DipRiskLevel.LOW, DipRiskLevel.MEDIUM)

        if not reasons:
            reasons.append("No news/filing data — technical-only assessment")

        result = DipClassification(
            ticker=ticker,
            catalyst=catalyst,
            risk_level=risk_level,
            reasons=reasons,
            actionable=actionable,
            news_impact_score=news_impact,
            negative_news_count=neg_count,
            insider_cluster_sell=cluster_sell,
            last_8k_impact=eightk_impact,
            rsi_at_entry=rsi,
            dip_pct=dip_pct,
            prior_streak=prior_streak,
        )

        logger.info(
            "dip_classified",
            ticker=ticker,
            catalyst=catalyst.value,
            risk_level=risk_level.value,
            actionable=actionable,
            risk_factors=risk_factors,
            confidence_factors=confidence_factors,
        )

        return result

    def _determine_catalyst(
        self,
        news_signal: dict | None,
        filing_signal: dict | None,
        cluster_sell: bool,
        neg_count: int,
    ) -> DipCatalyst:
        if cluster_sell:
            return DipCatalyst.INSIDER_SELLING

        if filing_signal:
            eightk_impact = filing_signal.get("last_8k_impact")
            if eightk_impact is not None and eightk_impact < -0.5:
                return DipCatalyst.REGULATORY

        if news_signal and neg_count >= 2:
            event_types = news_signal.get("dominant_event_types", [])
            if isinstance(event_types, str):
                event_types = [event_types]
            if any("earning" in et.lower() for et in event_types if et):
                return DipCatalyst.EARNINGS_REACTION
            return DipCatalyst.NEWS_DRIVEN

        if news_signal is None and filing_signal is None:
            return DipCatalyst.MARKET_FEAR

        return DipCatalyst.UNKNOWN

    @staticmethod
    def _compute_risk_level(
        risk_factors: int,
        confidence_factors: int,
    ) -> DipRiskLevel:
        net = risk_factors - confidence_factors

        if net >= 3:
            return DipRiskLevel.EXTREME
        if net >= 2:
            return DipRiskLevel.HIGH
        if net >= 1:
            return DipRiskLevel.MEDIUM
        return DipRiskLevel.LOW
