"""Tests for the CSP expiry tracker and fallback alert system."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from tyche.conviction.alerts import PullbackAlert
from tyche.conviction.engine import TrendState
from tyche.workflow.expiry_tracker import (
    CSPFallbackAlert,
    ExpiredCSP,
    ExpiryTracker,
)


def _make_pullback_alert(
    ticker: str = "AAPL",
    alert_type: str = "pullback_21ema",
) -> PullbackAlert:
    return PullbackAlert(
        ticker=ticker,
        alert_type=alert_type,
        severity="high" if "21" in alert_type else "info",
        trend_state=TrendState.PULLBACK_TO_21EMA if "21" in alert_type else TrendState.PULLBACK_TO_8EMA,
        conviction_level="high",
        last_close=190.0,
        ema_8=192.0,
        ema_21=189.0,
        ema_8_slope=0.5,
        ema_21_slope=0.3,
        ema_50=185.0,
        ema_50_slope=0.2,
        rsi_14=45.0,
        volume_declining=True,
        institutional_pct=0.70,
        institutional_label="Strong institutional backing",
        suggested_action="Consider buying",
        position_size_hint="large",
        stop_loss_level=185.22,
    )


class TestExpiryTracker:
    @pytest.fixture
    def tracker(self, tmp_path):
        return ExpiryTracker(db_dir=str(tmp_path))

    def test_record_expiry(self, tracker):
        tracker.record_expiry("AAPL", 185.0, "2026-03-27", 150.0)
        records = tracker.get_all_records()
        assert len(records) == 1
        assert records[0].ticker == "AAPL"
        assert records[0].expired_strike == 185.0
        assert records[0].premium_collected == 150.0

    def test_duplicate_not_added(self, tracker):
        tracker.record_expiry("AAPL", 185.0, "2026-03-27", 150.0)
        tracker.record_expiry("AAPL", 185.0, "2026-03-27", 150.0)
        assert len(tracker.get_all_records()) == 1

    def test_different_strikes_both_recorded(self, tracker):
        tracker.record_expiry("AAPL", 185.0, "2026-03-27", 150.0)
        tracker.record_expiry("AAPL", 180.0, "2026-03-27", 120.0)
        assert len(tracker.get_all_records()) == 2

    def test_date_object_accepted(self, tracker):
        tracker.record_expiry("AAPL", 185.0, date(2026, 3, 27), 150.0)
        records = tracker.get_all_records()
        assert records[0].expiry_date == "2026-03-27"

    def test_watched_tickers(self, tracker):
        tracker.record_expiry("AAPL", 185.0, "2026-03-27", 150.0)
        tracker.record_expiry("NVDA", 800.0, "2026-03-27", 200.0)
        watched = tracker.get_watched_tickers()
        assert set(watched) == {"AAPL", "NVDA"}

    def test_remove_ticker(self, tracker):
        tracker.record_expiry("AAPL", 185.0, "2026-03-27", 150.0)
        tracker.record_expiry("AAPL", 180.0, "2026-03-20", 120.0)
        removed = tracker.remove_ticker("AAPL")
        assert removed == 2
        assert len(tracker.get_all_records()) == 0

    def test_remove_nonexistent(self, tracker):
        assert tracker.remove_ticker("AAPL") == 0

    def test_cleanup_old(self, tracker):
        old_date = (date.today() - timedelta(days=60)).isoformat()
        recent_date = (date.today() - timedelta(days=5)).isoformat()
        tracker.record_expiry("OLD", 100.0, old_date, 50.0)
        tracker.record_expiry("RECENT", 200.0, recent_date, 100.0)
        removed = tracker.cleanup_old(max_age_days=30)
        assert removed == 1
        remaining = tracker.get_all_records()
        assert len(remaining) == 1
        assert remaining[0].ticker == "RECENT"

    def test_persistence(self, tmp_path):
        tracker1 = ExpiryTracker(db_dir=str(tmp_path))
        tracker1.record_expiry("AAPL", 185.0, "2026-03-27", 150.0)

        tracker2 = ExpiryTracker(db_dir=str(tmp_path))
        records = tracker2.get_all_records()
        assert len(records) == 1
        assert records[0].ticker == "AAPL"

    def test_persistence_file_created(self, tmp_path):
        tracker = ExpiryTracker(db_dir=str(tmp_path))
        tracker.record_expiry("AAPL", 185.0, "2026-03-27", 150.0)
        assert (tmp_path / "expired_csps.json").exists()

    def test_empty_tracker(self, tracker):
        assert tracker.get_all_records() == []
        assert tracker.get_watched_tickers() == []


class TestFallbackAlerts:
    @pytest.fixture
    def tracker(self, tmp_path):
        t = ExpiryTracker(db_dir=str(tmp_path))
        t.record_expiry("AAPL", 185.0, "2026-03-27", 150.0)
        t.record_expiry("NVDA", 800.0, "2026-03-27", 200.0)
        return t

    def test_generates_fallback_for_watched_ticker(self, tracker):
        alerts = [_make_pullback_alert(ticker="AAPL")]
        fallbacks = tracker.generate_fallback_alerts(alerts)
        assert len(fallbacks) == 1
        assert fallbacks[0].ticker == "AAPL"
        assert fallbacks[0].expired_strike == 185.0
        assert "$185.00" in fallbacks[0].message

    def test_no_fallback_for_unwatched_ticker(self, tracker):
        alerts = [_make_pullback_alert(ticker="MSFT")]
        fallbacks = tracker.generate_fallback_alerts(alerts)
        assert len(fallbacks) == 0

    def test_message_includes_ema_type(self, tracker):
        alerts = [_make_pullback_alert(ticker="AAPL", alert_type="pullback_21ema")]
        fallbacks = tracker.generate_fallback_alerts(alerts)
        assert "21-EMA" in fallbacks[0].message

    def test_message_includes_8ema(self, tracker):
        alerts = [_make_pullback_alert(ticker="AAPL", alert_type="pullback_8ema")]
        fallbacks = tracker.generate_fallback_alerts(alerts)
        assert "8-EMA" in fallbacks[0].message

    def test_multiple_watched_tickers(self, tracker):
        alerts = [
            _make_pullback_alert(ticker="AAPL"),
            _make_pullback_alert(ticker="NVDA"),
        ]
        fallbacks = tracker.generate_fallback_alerts(alerts)
        assert len(fallbacks) == 2
        tickers = {f.ticker for f in fallbacks}
        assert tickers == {"AAPL", "NVDA"}

    def test_to_dict(self, tracker):
        alerts = [_make_pullback_alert(ticker="AAPL")]
        fallbacks = tracker.generate_fallback_alerts(alerts)
        d = fallbacks[0].to_dict()
        assert d["ticker"] == "AAPL"
        assert d["expired_strike"] == 185.0
        assert "pullback_alert" in d
        assert "message" in d

    def test_empty_pullback_alerts(self, tracker):
        fallbacks = tracker.generate_fallback_alerts([])
        assert fallbacks == []
