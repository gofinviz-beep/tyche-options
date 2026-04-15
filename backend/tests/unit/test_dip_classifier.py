"""Tests for the DipCatalystClassifier."""

from __future__ import annotations

import pytest

from tyche.conviction.dip_classifier import (
    DipCatalyst,
    DipCatalystClassifier,
    DipClassification,
    DipRiskLevel,
)


@pytest.fixture
def classifier():
    return DipCatalystClassifier()


class TestDipCatalystClassifier:
    def test_no_data_returns_actionable(self, classifier):
        result = classifier.classify(
            "GOOG", dip_pct=8.0, prior_streak=15, rsi=25.0,
        )
        assert result.actionable is True
        assert result.risk_level == DipRiskLevel.LOW
        assert result.catalyst == DipCatalyst.MARKET_FEAR

    def test_negative_news_increases_risk(self, classifier):
        result = classifier.classify(
            "GOOG",
            dip_pct=8.0,
            prior_streak=15,
            rsi=30.0,
            news_signal={
                "news_impact_score": -0.5,
                "negative_count_24h": 4,
            },
        )
        assert result.risk_level in (DipRiskLevel.MEDIUM, DipRiskLevel.HIGH)

    def test_insider_cluster_sell_high_risk(self, classifier):
        result = classifier.classify(
            "TSLA",
            dip_pct=10.0,
            prior_streak=12,
            rsi=28.0,
            filing_signal={
                "insider_cluster_sell": True,
                "last_8k_impact": None,
                "insider_buy_count_30d": 0,
            },
        )
        assert result.risk_level in (DipRiskLevel.HIGH, DipRiskLevel.EXTREME)
        assert result.insider_cluster_sell is True
        assert result.catalyst == DipCatalyst.INSIDER_SELLING

    def test_regulatory_8k_high_risk(self, classifier):
        result = classifier.classify(
            "XYZ",
            dip_pct=12.0,
            prior_streak=10,
            rsi=35.0,
            filing_signal={
                "insider_cluster_sell": False,
                "last_8k_impact": -0.8,
                "insider_buy_count_30d": 0,
            },
        )
        assert result.risk_level in (DipRiskLevel.MEDIUM, DipRiskLevel.HIGH)
        assert result.catalyst == DipCatalyst.REGULATORY

    def test_insider_buying_boosts_confidence(self, classifier):
        result = classifier.classify(
            "AAPL",
            dip_pct=7.0,
            prior_streak=20,
            rsi=22.0,
            filing_signal={
                "insider_cluster_sell": False,
                "last_8k_impact": None,
                "insider_buy_count_30d": 3,
            },
        )
        assert result.actionable is True
        assert result.risk_level == DipRiskLevel.LOW

    def test_strong_prior_streak_reduces_risk(self, classifier):
        result = classifier.classify(
            "MSFT",
            dip_pct=6.0,
            prior_streak=25,
            rsi=28.0,
        )
        assert result.actionable is True
        assert result.risk_level == DipRiskLevel.LOW

    def test_rsi_not_oversold_adds_risk(self, classifier):
        result = classifier.classify(
            "META",
            dip_pct=6.0,
            prior_streak=8,
            rsi=55.0,
        )
        assert result.risk_level in (DipRiskLevel.MEDIUM, DipRiskLevel.HIGH)

    def test_very_deep_dip_adds_risk(self, classifier):
        result = classifier.classify(
            "NFLX",
            dip_pct=20.0,
            prior_streak=8,
            rsi=35.0,
        )
        assert any("Very deep dip" in r for r in result.reasons)

    def test_earnings_catalyst(self, classifier):
        result = classifier.classify(
            "AMZN",
            dip_pct=8.0,
            prior_streak=15,
            rsi=30.0,
            news_signal={
                "news_impact_score": -0.4,
                "negative_count_24h": 3,
                "dominant_event_types": ["earnings_miss"],
            },
        )
        assert result.catalyst == DipCatalyst.EARNINGS_REACTION

    def test_neutral_news_adds_confidence(self, classifier):
        result = classifier.classify(
            "GOOG",
            dip_pct=6.0,
            prior_streak=15,
            rsi=28.0,
            news_signal={
                "news_impact_score": 0.1,
                "negative_count_24h": 0,
            },
        )
        assert result.actionable is True
        assert result.risk_level == DipRiskLevel.LOW
        assert any("Neutral/positive" in r for r in result.reasons)

    def test_extreme_risk_blocks(self, classifier):
        result = classifier.classify(
            "BAD",
            dip_pct=20.0,
            prior_streak=3,
            rsi=55.0,
            news_signal={
                "news_impact_score": -0.8,
                "negative_count_24h": 5,
            },
            filing_signal={
                "insider_cluster_sell": True,
                "last_8k_impact": -0.9,
                "insider_buy_count_30d": 0,
            },
        )
        assert result.actionable is False
        assert result.risk_level == DipRiskLevel.EXTREME

    def test_to_dict_serialization(self, classifier):
        result = classifier.classify(
            "GOOG", dip_pct=8.0, prior_streak=15, rsi=25.0,
        )
        d = result.to_dict()
        assert d["ticker"] == "GOOG"
        assert d["catalyst"] == "market_fear"
        assert d["risk_level"] == "low"
        assert d["actionable"] is True
        assert isinstance(d["reasons"], list)

    def test_custom_thresholds(self):
        strict = DipCatalystClassifier(
            news_risk_threshold=-0.1,
            eightk_impact_threshold=-0.3,
            min_prior_streak_for_high_confidence=20,
        )
        result = strict.classify(
            "TEST",
            dip_pct=6.0,
            prior_streak=12,
            rsi=30.0,
            news_signal={"news_impact_score": -0.15, "negative_count_24h": 1},
        )
        assert any("Negative news" in r for r in result.reasons)
