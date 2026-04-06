"""Tests for the stock buy recommendation engine."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from tyche.conviction.alerts import PullbackAlert
from tyche.conviction.engine import ConvictionSignal, TrendState
from tyche.models.conviction import ConvictionSnapshot
from tyche.workflow.stock_recommender import (
    StockBuyRecommendation,
    _find_active_csp,
    generate_recommendations_from_snapshots,
    generate_stock_recommendations,
)


def _make_alert(
    ticker: str = "AAPL",
    alert_type: str = "pullback_21ema",
    severity: str = "high",
    last_close: float = 190.0,
    ema_8: float = 192.0,
    ema_21: float = 189.0,
    stop_loss: float = 185.22,
    conviction: str = "high",
    vol_declining: bool = True,
    inst_pct: float | None = 0.70,
    position_size: str = "large",
) -> PullbackAlert:
    return PullbackAlert(
        ticker=ticker,
        alert_type=alert_type,
        severity=severity,
        trend_state=TrendState.PULLBACK_TO_21EMA if "21" in alert_type else TrendState.PULLBACK_TO_8EMA,
        conviction_level=conviction,
        last_close=last_close,
        ema_8=ema_8,
        ema_21=ema_21,
        ema_8_slope=0.5,
        ema_21_slope=0.3,
        ema_50=185.0,
        ema_50_slope=0.2,
        rsi_14=45.0,
        volume_declining=vol_declining,
        institutional_pct=inst_pct,
        institutional_label="Strong institutional backing",
        suggested_action="Consider buying",
        position_size_hint=position_size,
        stop_loss_level=stop_loss,
    )


def _make_signal(ticker: str = "AAPL") -> ConvictionSignal:
    return ConvictionSignal(
        ticker=ticker,
        trend_state=TrendState.PULLBACK_TO_21EMA,
        conviction_level="high",
        csp_eligible=True,
        last_close=190.0,
        ema_8=192.0,
        ema_21=189.0,
        days_above_both_emas=7,
    )


class TestFindActiveCSP:
    def test_no_positions(self):
        has, strike = _find_active_csp("AAPL", None)
        assert has is False
        assert strike is None

    def test_empty_positions(self):
        has, strike = _find_active_csp("AAPL", [])
        assert has is False
        assert strike is None

    def test_no_matching_ticker(self):
        positions = [{"symbol": "MSFT", "option_type": "put", "strike": 380.0}]
        has, strike = _find_active_csp("AAPL", positions)
        assert has is False

    def test_matching_csp(self):
        positions = [
            {"symbol": "AAPL", "option_type": "put", "strike": 185.0},
            {"symbol": "AAPL", "option_type": "put", "strike": 180.0},
        ]
        has, strike = _find_active_csp("AAPL", positions)
        assert has is True
        assert strike == 180.0

    def test_short_put_type(self):
        positions = [{"symbol": "AAPL", "option_type": "short_put", "strike": 185.0}]
        has, strike = _find_active_csp("AAPL", positions)
        assert has is True
        assert strike == 185.0

    def test_call_ignored(self):
        positions = [{"symbol": "AAPL", "option_type": "call", "strike": 200.0}]
        has, strike = _find_active_csp("AAPL", positions)
        assert has is False

    def test_case_insensitive(self):
        positions = [{"symbol": "aapl", "option_type": "put", "strike": 185.0}]
        has, strike = _find_active_csp("AAPL", positions)
        assert has is True


class TestGenerateStockRecommendations:
    def test_basic_recommendation(self):
        alert = _make_alert()
        recs = generate_stock_recommendations([alert])
        assert len(recs) == 1
        assert recs[0].ticker == "AAPL"
        assert recs[0].entry_type == "pullback_21ema"
        assert recs[0].has_active_csp is False

    def test_with_active_csp(self):
        alert = _make_alert()
        positions = [{"symbol": "AAPL", "option_type": "put", "strike": 185.0}]
        recs = generate_stock_recommendations([alert], positions=positions)
        assert recs[0].has_active_csp is True
        assert recs[0].related_csp_strike == 185.0
        assert "Active CSP" in recs[0].recommendation

    def test_without_csp_21ema(self):
        alert = _make_alert(alert_type="pullback_21ema")
        recs = generate_stock_recommendations([alert])
        assert "21-EMA" in recs[0].recommendation
        assert "Larger position" in recs[0].recommendation

    def test_without_csp_8ema(self):
        alert = _make_alert(alert_type="pullback_8ema")
        recs = generate_stock_recommendations([alert])
        assert "8-EMA" in recs[0].recommendation
        assert "Standard" in recs[0].recommendation

    def test_target_ema_21(self):
        alert = _make_alert(alert_type="pullback_21ema", ema_21=189.0)
        recs = generate_stock_recommendations([alert])
        assert recs[0].target_ema_value == 189.0

    def test_target_ema_8(self):
        alert = _make_alert(alert_type="pullback_8ema", ema_8=192.0)
        recs = generate_stock_recommendations([alert])
        assert recs[0].target_ema_value == 192.0

    def test_days_from_conviction_signal(self):
        alert = _make_alert()
        sig = _make_signal()
        sig.days_above_both_emas = 8
        recs = generate_stock_recommendations(
            [alert], conviction_signals={"AAPL": sig}
        )
        assert recs[0].days_above_emas == 8

    def test_days_zero_without_signal(self):
        alert = _make_alert()
        recs = generate_stock_recommendations([alert])
        assert recs[0].days_above_emas == 0

    def test_risk_reward_note_includes_risk_pct(self):
        alert = _make_alert(last_close=100.0, stop_loss=95.0)
        recs = generate_stock_recommendations([alert])
        assert "Risk to stop: 5.0%" in recs[0].risk_reward_note

    def test_risk_reward_volume_declining(self):
        alert = _make_alert(vol_declining=True)
        recs = generate_stock_recommendations([alert])
        assert "bullish" in recs[0].risk_reward_note

    def test_risk_reward_volume_not_declining(self):
        alert = _make_alert(vol_declining=False)
        recs = generate_stock_recommendations([alert])
        assert "confirmation" in recs[0].risk_reward_note

    def test_to_dict(self):
        alert = _make_alert()
        recs = generate_stock_recommendations([alert])
        d = recs[0].to_dict()
        assert d["ticker"] == "AAPL"
        assert "created_at" in d
        assert isinstance(d["entry_price"], float)

    def test_empty_alerts(self):
        recs = generate_stock_recommendations([])
        assert recs == []

    def test_multiple_alerts(self):
        alerts = [
            _make_alert(ticker="AAPL"),
            _make_alert(ticker="NVDA", alert_type="pullback_8ema"),
        ]
        recs = generate_stock_recommendations(alerts)
        assert len(recs) == 2
        tickers = {r.ticker for r in recs}
        assert tickers == {"AAPL", "NVDA"}


def _make_snapshot(
    ticker: str = "AAPL",
    trend_state: str = "pullback_to_21ema",
    conviction_level: str = "high",
    raw_conviction: str | None = None,
    last_close: float = 190.0,
    ema_8: float = 192.0,
    ema_21: float = 189.0,
    volume_declining: bool = True,
) -> ConvictionSnapshot:
    return ConvictionSnapshot(
        ticker=ticker,
        as_of_date=date(2026, 3, 28),
        trend_state=trend_state,
        conviction_level=conviction_level,
        raw_conviction=raw_conviction or conviction_level,
        csp_eligible=True,
        last_close=last_close,
        ema_8=ema_8,
        ema_21=ema_21,
        ema_8_slope=0.5,
        ema_21_slope=0.3,
        price_to_8ema_pct=-1.05,
        price_to_21ema_pct=0.53,
        volume_declining=volume_declining,
        days_above_both_emas=7,
        avg_volume_20d=65_000_000,
        latest_volume=55_000_000,
        computed_at=datetime(2026, 3, 28, 14, 30, tzinfo=timezone.utc),
    )


class TestGenerateRecommendationsFromSnapshots:
    def test_basic_21ema_snapshot(self):
        snap = _make_snapshot()
        recs = generate_recommendations_from_snapshots([snap])
        assert len(recs) == 1
        r = recs[0]
        assert r.ticker == "AAPL"
        assert r.entry_type == "pullback_21ema"
        assert r.entry_price == 190.0
        assert r.target_ema_value == 189.0
        assert r.conviction == "high"
        assert r.position_size_hint == "large"
        assert "21-EMA" in r.recommendation

    def test_basic_8ema_snapshot(self):
        snap = _make_snapshot(trend_state="pullback_to_8ema")
        recs = generate_recommendations_from_snapshots([snap])
        assert len(recs) == 1
        r = recs[0]
        assert r.entry_type == "pullback_8ema"
        assert r.target_ema_value == 192.0
        assert r.position_size_hint == "standard"
        assert "8-EMA" in r.recommendation

    def test_uses_raw_conviction(self):
        snap = _make_snapshot(conviction_level="low", raw_conviction="high")
        recs = generate_recommendations_from_snapshots([snap])
        assert recs[0].conviction == "high"

    def test_falls_back_when_raw_is_none_string(self):
        snap = _make_snapshot(conviction_level="medium", raw_conviction="none")
        recs = generate_recommendations_from_snapshots([snap])
        assert recs[0].conviction == "medium"

    def test_institutional_map(self):
        snap = _make_snapshot()
        recs = generate_recommendations_from_snapshots(
            [snap], institutional_map={"AAPL": 0.72}
        )
        assert recs[0].institutional_pct == 0.72

    def test_csp_cross_reference(self):
        snap = _make_snapshot()
        positions = [{"symbol": "AAPL", "option_type": "put", "strike": 185.0}]
        recs = generate_recommendations_from_snapshots([snap], positions=positions)
        assert recs[0].has_active_csp is True
        assert recs[0].related_csp_strike == 185.0
        assert "Active CSP" in recs[0].recommendation

    def test_risk_reward_note(self):
        snap = _make_snapshot(last_close=100.0, ema_21=95.0)
        recs = generate_recommendations_from_snapshots([snap])
        assert "Risk to stop:" in recs[0].risk_reward_note

    def test_volume_declining_note(self):
        snap = _make_snapshot(volume_declining=True)
        recs = generate_recommendations_from_snapshots([snap])
        assert "bullish" in recs[0].risk_reward_note

    def test_volume_not_declining_note(self):
        snap = _make_snapshot(volume_declining=False)
        recs = generate_recommendations_from_snapshots([snap])
        assert "confirmation" in recs[0].risk_reward_note

    def test_empty_snapshots(self):
        recs = generate_recommendations_from_snapshots([])
        assert recs == []

    def test_multiple_snapshots(self):
        snaps = [
            _make_snapshot(ticker="AAPL"),
            _make_snapshot(ticker="NVDA", trend_state="pullback_to_8ema"),
        ]
        recs = generate_recommendations_from_snapshots(snaps)
        assert len(recs) == 2
        tickers = {r.ticker for r in recs}
        assert tickers == {"AAPL", "NVDA"}

    def test_to_dict(self):
        snap = _make_snapshot()
        recs = generate_recommendations_from_snapshots([snap])
        d = recs[0].to_dict()
        assert d["ticker"] == "AAPL"
        assert "created_at" in d
        assert isinstance(d["entry_price"], float)
