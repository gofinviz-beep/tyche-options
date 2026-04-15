"""Tests for the pullback alert detection system."""

from __future__ import annotations

import pytest

from tyche.conviction.alerts import (
    PullbackAlert,
    _compute_stop_loss,
    _institutional_label,
    detect_pullback_alerts,
)
from tyche.conviction.engine import ConvictionSignal, TrendState


def _make_signal(
    ticker: str = "AAPL",
    trend_state: TrendState = TrendState.PULLBACK_TO_21EMA,
    conviction: str = "high",
    raw_conviction: str | None = None,
    last_close: float = 190.0,
    ema_8: float = 192.0,
    ema_21: float = 189.0,
    ema_8_slope: float = 0.5,
    ema_21_slope: float = 0.3,
    vol_declining: bool = True,
) -> ConvictionSignal:
    return ConvictionSignal(
        ticker=ticker,
        trend_state=trend_state,
        conviction_level=conviction,
        raw_conviction=raw_conviction or conviction,
        csp_eligible=True,
        last_close=last_close,
        ema_8=ema_8,
        ema_21=ema_21,
        ema_8_slope=ema_8_slope,
        ema_21_slope=ema_21_slope,
        volume_declining_on_pullback=vol_declining,
        days_above_both_emas=7,
    )


class TestInstitutionalLabel:
    def test_strong(self):
        assert _institutional_label(0.75) == "Strong institutional backing"

    def test_adequate(self):
        assert _institutional_label(0.55) == "Adequate institutional backing"

    def test_moderate(self):
        assert _institutional_label(0.42) == "Moderate institutional backing — caution"

    def test_low(self):
        assert _institutional_label(0.20) == "Low institutional backing"

    def test_none(self):
        assert _institutional_label(None) == "Unknown"


class TestComputeStopLoss:
    def test_8ema_stop_below_21ema(self):
        stop = _compute_stop_loss("pullback_8ema", 100.0)
        assert stop == 99.0

    def test_21ema_stop_2pct_below(self):
        stop = _compute_stop_loss("pullback_21ema", 100.0)
        assert stop == 98.0


class TestDetectPullbackAlerts:
    def test_detects_21ema_pullback(self):
        sig = _make_signal(trend_state=TrendState.PULLBACK_TO_21EMA)
        alerts = detect_pullback_alerts({"AAPL": sig})
        assert len(alerts) == 1
        assert alerts[0].alert_type == "pullback_21ema"
        assert alerts[0].severity == "high"
        assert alerts[0].position_size_hint == "large"

    def test_detects_8ema_pullback(self):
        sig = _make_signal(trend_state=TrendState.PULLBACK_TO_8EMA)
        alerts = detect_pullback_alerts({"AAPL": sig})
        assert len(alerts) == 1
        assert alerts[0].alert_type == "pullback_8ema"
        assert alerts[0].severity == "info"
        assert alerts[0].position_size_hint == "standard"

    def test_skips_uptrend(self):
        sig = _make_signal(trend_state=TrendState.UPTREND)
        alerts = detect_pullback_alerts({"AAPL": sig})
        assert len(alerts) == 0

    def test_skips_downtrend(self):
        sig = _make_signal(trend_state=TrendState.DOWNTREND)
        alerts = detect_pullback_alerts({"AAPL": sig})
        assert len(alerts) == 0

    def test_skips_negative_ema8_slope(self):
        sig = _make_signal(ema_8_slope=-0.1)
        alerts = detect_pullback_alerts({"AAPL": sig})
        assert len(alerts) == 0

    def test_skips_negative_ema21_slope(self):
        sig = _make_signal(ema_21_slope=-0.1)
        alerts = detect_pullback_alerts({"AAPL": sig})
        assert len(alerts) == 0

    def test_skips_low_institutional(self):
        sig = _make_signal()
        alerts = detect_pullback_alerts(
            {"AAPL": sig},
            institutional_map={"AAPL": 0.30},
            min_institutional_pct=0.50,
        )
        assert len(alerts) == 0

    def test_passes_unknown_institutional(self):
        sig = _make_signal()
        alerts = detect_pullback_alerts(
            {"AAPL": sig},
            institutional_map={},
            min_institutional_pct=0.50,
        )
        assert len(alerts) == 1

    def test_passes_high_institutional(self):
        sig = _make_signal()
        alerts = detect_pullback_alerts(
            {"AAPL": sig},
            institutional_map={"AAPL": 0.70},
            min_institutional_pct=0.50,
        )
        assert len(alerts) == 1
        assert alerts[0].institutional_pct == 0.70

    def test_21ema_with_declining_volume_is_high_severity(self):
        sig = _make_signal(
            trend_state=TrendState.PULLBACK_TO_21EMA,
            vol_declining=True,
        )
        alerts = detect_pullback_alerts({"AAPL": sig})
        assert alerts[0].severity == "high"
        assert "High-conviction" in alerts[0].suggested_action

    def test_21ema_without_declining_volume(self):
        sig = _make_signal(
            trend_state=TrendState.PULLBACK_TO_21EMA,
            vol_declining=False,
        )
        alerts = detect_pullback_alerts({"AAPL": sig})
        assert alerts[0].severity == "high"
        assert "confirmation" in alerts[0].suggested_action

    def test_8ema_with_declining_volume(self):
        sig = _make_signal(
            trend_state=TrendState.PULLBACK_TO_8EMA,
            vol_declining=True,
        )
        alerts = detect_pullback_alerts({"AAPL": sig})
        assert "standard position" in alerts[0].suggested_action

    def test_8ema_without_declining_volume(self):
        sig = _make_signal(
            trend_state=TrendState.PULLBACK_TO_8EMA,
            vol_declining=False,
        )
        alerts = detect_pullback_alerts({"AAPL": sig})
        assert "lighter entry" in alerts[0].suggested_action

    def test_sorted_high_first(self):
        sig_high = _make_signal(ticker="AAPL", trend_state=TrendState.PULLBACK_TO_21EMA)
        sig_info = _make_signal(ticker="MSFT", trend_state=TrendState.PULLBACK_TO_8EMA)
        alerts = detect_pullback_alerts([sig_info, sig_high])
        assert alerts[0].ticker == "AAPL"
        assert alerts[0].severity == "high"

    def test_accepts_list_input(self):
        sig = _make_signal()
        alerts = detect_pullback_alerts([sig])
        assert len(alerts) == 1

    def test_stop_loss_21ema(self):
        sig = _make_signal(
            trend_state=TrendState.PULLBACK_TO_21EMA,
            ema_21=100.0,
        )
        alerts = detect_pullback_alerts({"AAPL": sig})
        assert alerts[0].stop_loss_level == 98.0

    def test_stop_loss_8ema(self):
        sig = _make_signal(
            trend_state=TrendState.PULLBACK_TO_8EMA,
            ema_21=100.0,
        )
        alerts = detect_pullback_alerts({"AAPL": sig})
        assert alerts[0].stop_loss_level == 99.0

    def test_to_dict(self):
        sig = _make_signal()
        alerts = detect_pullback_alerts({"AAPL": sig})
        d = alerts[0].to_dict()
        assert d["ticker"] == "AAPL"
        assert "detected_at" in d
        assert isinstance(d["stop_loss_level"], float)

    def test_multiple_tickers(self):
        signals = {
            "AAPL": _make_signal(ticker="AAPL", trend_state=TrendState.PULLBACK_TO_21EMA),
            "NVDA": _make_signal(ticker="NVDA", trend_state=TrendState.PULLBACK_TO_8EMA),
            "MSFT": _make_signal(ticker="MSFT", trend_state=TrendState.UPTREND),
        }
        alerts = detect_pullback_alerts(signals)
        assert len(alerts) == 2
        tickers = {a.ticker for a in alerts}
        assert tickers == {"AAPL", "NVDA"}

    def test_empty_signals(self):
        alerts = detect_pullback_alerts({})
        assert alerts == []

    def test_zero_institutional_threshold(self):
        sig = _make_signal()
        alerts = detect_pullback_alerts(
            {"AAPL": sig},
            institutional_map={"AAPL": 0.10},
            min_institutional_pct=0.0,
        )
        assert len(alerts) == 1


class TestDipClassifierIntegration:
    """Tests for DipCatalystClassifier gate in detect_pullback_alerts."""

    def _oversold_signal(
        self,
        ticker: str = "GOOG",
        trend_state: TrendState = TrendState.OVERSOLD_50EMA,
        rsi: float = 25.0,
        prior_streak: int = 12,
        price_to_50ema_pct: float = -7.0,
    ) -> ConvictionSignal:
        return ConvictionSignal(
            ticker=ticker,
            trend_state=trend_state,
            conviction_level="high",
            raw_conviction="high",
            csp_eligible=False,
            last_close=273.0,
            ema_8=285.0,
            ema_21=290.0,
            ema_8_slope=-0.5,
            ema_21_slope=-0.2,
            ema_50=295.0,
            ema_50_slope=0.1,
            rsi_14=rsi,
            volume_declining_on_pullback=False,
            days_above_both_emas=0,
            prior_streak=prior_streak,
            price_to_21ema_pct=-5.8,
            price_to_50ema_pct=price_to_50ema_pct,
        )

    def test_oversold_passes_without_classifier(self):
        """Oversold alerts surface when no dip classifier is provided."""
        sig = self._oversold_signal()
        alerts = detect_pullback_alerts({"GOOG": sig})
        assert len(alerts) == 1
        assert alerts[0].alert_type == "oversold_50ema"
        assert alerts[0].dip_classification is None

    def test_oversold_passes_with_low_risk(self):
        """Oversold alert surfaces when classifier deems dip low risk."""
        from tyche.conviction.dip_classifier import DipCatalystClassifier

        classifier = DipCatalystClassifier()
        sig = self._oversold_signal()
        alerts = detect_pullback_alerts(
            {"GOOG": sig},
            dip_classifier=classifier,
        )
        assert len(alerts) == 1
        assert alerts[0].dip_classification is not None
        assert alerts[0].dip_classification.actionable is True

    def test_oversold_blocked_by_high_risk(self):
        """Oversold alert blocked when classifier finds high risk."""
        from tyche.conviction.dip_classifier import DipCatalystClassifier

        classifier = DipCatalystClassifier()
        sig = self._oversold_signal(rsi=55.0, prior_streak=3)
        alerts = detect_pullback_alerts(
            {"GOOG": sig},
            dip_classifier=classifier,
            news_signals={"GOOG": {
                "news_impact_score": -0.8,
                "negative_count_24h": 5,
            }},
            filing_signals={"GOOG": {
                "insider_cluster_sell": True,
            }},
        )
        assert len(alerts) == 0

    def test_oversold_blocked_insider_cluster_sell(self):
        """Insider cluster selling with weak technicals blocks oversold alert."""
        from tyche.conviction.dip_classifier import DipCatalystClassifier

        classifier = DipCatalystClassifier()
        sig = self._oversold_signal(rsi=55.0, prior_streak=5)
        alerts = detect_pullback_alerts(
            {"GOOG": sig},
            dip_classifier=classifier,
            filing_signals={"GOOG": {"insider_cluster_sell": True}},
        )
        assert len(alerts) == 0

    def test_pullback_unaffected_by_classifier(self):
        """Regular pullback alerts are not affected by the dip classifier."""
        from tyche.conviction.dip_classifier import DipCatalystClassifier

        classifier = DipCatalystClassifier()
        sig = _make_signal()
        alerts = detect_pullback_alerts(
            {"AAPL": sig},
            dip_classifier=classifier,
            news_signals={"AAPL": {
                "news_impact_score": -0.9,
                "negative_count_24h": 10,
            }},
        )
        assert len(alerts) == 1
        assert alerts[0].alert_type == "pullback_21ema"
        assert alerts[0].dip_classification is None

    def test_mixed_pullback_and_oversold_filtering(self):
        """Classifier only filters oversold alerts, pullbacks pass through."""
        from tyche.conviction.dip_classifier import DipCatalystClassifier

        classifier = DipCatalystClassifier()
        signals = {
            "AAPL": _make_signal(ticker="AAPL"),
            "GOOG": self._oversold_signal(ticker="GOOG", rsi=55.0, prior_streak=5),
        }
        alerts = detect_pullback_alerts(
            signals,
            dip_classifier=classifier,
            filing_signals={"GOOG": {"insider_cluster_sell": True}},
        )
        assert len(alerts) == 1
        assert alerts[0].ticker == "AAPL"

    def test_oversold_21ema_classified(self):
        """21-EMA oversold uses price_to_21ema_pct for dip_pct."""
        from tyche.conviction.dip_classifier import DipCatalystClassifier

        classifier = DipCatalystClassifier()
        sig = self._oversold_signal(trend_state=TrendState.OVERSOLD_21EMA)
        alerts = detect_pullback_alerts(
            {"GOOG": sig},
            dip_classifier=classifier,
        )
        assert len(alerts) == 1
        dc = alerts[0].dip_classification
        assert dc is not None
        assert dc.dip_pct == pytest.approx(5.8, abs=0.1)

    def test_dip_classification_in_to_dict(self):
        """DipClassification is included in alert's to_dict output."""
        from tyche.conviction.dip_classifier import DipCatalystClassifier

        classifier = DipCatalystClassifier()
        sig = self._oversold_signal()
        alerts = detect_pullback_alerts(
            {"GOOG": sig},
            dip_classifier=classifier,
        )
        d = alerts[0].to_dict()
        assert "dip_classification" in d
        assert d["dip_classification"]["ticker"] == "GOOG"
        assert d["dip_classification"]["actionable"] is True

    def test_insider_buying_boosts_confidence(self):
        """Insider buying alongside oversold dip keeps alert actionable."""
        from tyche.conviction.dip_classifier import DipCatalystClassifier

        classifier = DipCatalystClassifier()
        sig = self._oversold_signal()
        alerts = detect_pullback_alerts(
            {"GOOG": sig},
            dip_classifier=classifier,
            news_signals={"GOOG": {
                "news_impact_score": -0.35,
                "negative_count_24h": 1,
            }},
            filing_signals={"GOOG": {
                "insider_cluster_sell": False,
                "insider_buy_count_30d": 3,
            }},
        )
        assert len(alerts) == 1
        dc = alerts[0].dip_classification
        assert dc is not None
        assert dc.actionable is True
