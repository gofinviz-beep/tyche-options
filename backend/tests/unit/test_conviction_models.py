"""Tests for ConvictionSnapshot and ConvictionTransition models."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest

from tyche.models.conviction import ConvictionSnapshot, ConvictionTransition


class TestConvictionSnapshot:
    def _make_snapshot(self, **overrides) -> ConvictionSnapshot:
        defaults = {
            "ticker": "AAPL",
            "as_of_date": date(2026, 3, 28),
            "trend_state": "uptrend",
            "conviction_level": "high",
            "csp_eligible": True,
            "last_close": 185.50,
            "ema_8": 184.20,
            "ema_21": 180.10,
            "ema_8_slope": 0.45,
            "ema_21_slope": 0.32,
            "price_to_8ema_pct": 0.71,
            "price_to_21ema_pct": 3.0,
            "volume_declining": False,
            "days_above_both_emas": 7,
            "avg_volume_20d": 65_000_000,
            "latest_volume": 55_000_000,
            "computed_at": datetime(2026, 3, 28, 14, 30, tzinfo=timezone.utc),
        }
        defaults.update(overrides)
        return ConvictionSnapshot(**defaults)

    def test_create_snapshot(self):
        snap = self._make_snapshot()
        assert snap.ticker == "AAPL"
        assert snap.as_of_date == date(2026, 3, 28)
        assert snap.trend_state == "uptrend"
        assert snap.conviction_level == "high"
        assert snap.csp_eligible is True
        assert snap.last_close == 185.50
        assert snap.ema_8 == 184.20
        assert snap.ema_21 == 180.10

    def test_raw_conviction_default(self):
        snap = self._make_snapshot()
        assert snap.raw_conviction is None

    def test_raw_conviction_explicit(self):
        snap = self._make_snapshot(raw_conviction="high")
        assert snap.raw_conviction == "high"

    def test_to_dict(self):
        snap = self._make_snapshot(raw_conviction="medium")
        d = snap.to_dict()
        assert d["ticker"] == "AAPL"
        assert d["as_of_date"] == "2026-03-28"
        assert d["trend_state"] == "uptrend"
        assert d["conviction_level"] == "high"
        assert d["raw_conviction"] == "medium"
        assert d["csp_eligible"] is True
        assert d["last_close"] == 185.50
        assert d["ema_8"] == 184.20
        assert d["ema_21"] == 180.10
        assert d["ema_8_slope"] == 0.45
        assert d["ema_21_slope"] == 0.32
        assert d["price_to_8ema_pct"] == 0.71
        assert d["price_to_21ema_pct"] == 3.0
        assert d["volume_declining"] is False
        assert d["days_above_both_emas"] == 7
        assert d["avg_volume_20d"] == 65_000_000
        assert d["latest_volume"] == 55_000_000
        assert "computed_at" in d

    def test_to_dict_rounding(self):
        snap = self._make_snapshot(
            last_close=185.5678,
            ema_8=184.12345,
            ema_21=180.09876,
            ema_8_slope=0.4567891,
            ema_21_slope=0.3234567,
            price_to_8ema_pct=0.7123,
            price_to_21ema_pct=3.0567,
        )
        d = snap.to_dict()
        assert d["last_close"] == 185.57
        assert d["ema_8"] == round(184.12345, 4)
        assert d["ema_21"] == round(180.09876, 4)
        assert d["ema_8_slope"] == round(0.4567891, 6)
        assert d["ema_21_slope"] == round(0.3234567, 6)
        assert d["price_to_8ema_pct"] == 0.71
        assert d["price_to_21ema_pct"] == 3.06

    def test_to_dict_none_dates(self):
        snap = self._make_snapshot(as_of_date=None, computed_at=None)
        d = snap.to_dict()
        assert d["as_of_date"] is None
        assert d["computed_at"] is None

    def test_pullback_states(self):
        snap_8 = self._make_snapshot(trend_state="pullback_to_8ema")
        assert snap_8.trend_state == "pullback_to_8ema"

        snap_21 = self._make_snapshot(trend_state="pullback_to_21ema")
        assert snap_21.trend_state == "pullback_to_21ema"

    def test_tablename(self):
        assert ConvictionSnapshot.__tablename__ == "conviction_snapshots"


class TestConvictionTransition:
    def _make_transition(self, **overrides) -> ConvictionTransition:
        defaults = {
            "id": str(uuid.uuid4()),
            "ticker": "MSFT",
            "from_state": "uptrend",
            "to_state": "pullback_to_8ema",
            "transition_date": date(2026, 3, 28),
            "last_close": 420.50,
            "ema_8": 421.10,
            "ema_21": 415.30,
            "ema_8_slope": 0.35,
            "ema_21_slope": 0.28,
            "conviction_level": "medium",
            "detected_at": datetime(2026, 3, 28, 14, 30, tzinfo=timezone.utc),
        }
        defaults.update(overrides)
        return ConvictionTransition(**defaults)

    def test_create_transition(self):
        t = self._make_transition()
        assert t.ticker == "MSFT"
        assert t.from_state == "uptrend"
        assert t.to_state == "pullback_to_8ema"
        assert t.transition_date == date(2026, 3, 28)
        assert t.last_close == 420.50

    def test_raw_conviction_default(self):
        t = self._make_transition()
        assert t.raw_conviction is None

    def test_raw_conviction_explicit(self):
        t = self._make_transition(raw_conviction="high")
        assert t.raw_conviction == "high"

    def test_to_dict(self):
        t = self._make_transition(raw_conviction="high")
        d = t.to_dict()
        assert d["ticker"] == "MSFT"
        assert d["from_state"] == "uptrend"
        assert d["to_state"] == "pullback_to_8ema"
        assert d["transition_date"] == "2026-03-28"
        assert d["last_close"] == 420.50
        assert d["ema_8"] == 421.10
        assert d["ema_21"] == 415.30
        assert d["conviction_level"] == "medium"
        assert d["raw_conviction"] == "high"
        assert "detected_at" in d
        assert "id" in d

    def test_to_dict_rounding(self):
        t = self._make_transition(
            last_close=420.5678,
            ema_8=421.12345,
            ema_21=415.09876,
            ema_8_slope=0.3567891,
            ema_21_slope=0.2834567,
        )
        d = t.to_dict()
        assert d["last_close"] == 420.57
        assert d["ema_8"] == round(421.12345, 4)
        assert d["ema_21"] == round(415.09876, 4)
        assert d["ema_8_slope"] == round(0.3567891, 6)
        assert d["ema_21_slope"] == round(0.2834567, 6)

    def test_to_dict_none_dates(self):
        t = self._make_transition(transition_date=None, detected_at=None)
        d = t.to_dict()
        assert d["transition_date"] is None
        assert d["detected_at"] is None

    def test_tablename(self):
        assert ConvictionTransition.__tablename__ == "conviction_transitions"

    def test_explicit_conviction_level(self):
        t = ConvictionTransition(
            ticker="GOOG",
            from_state="uptrend",
            to_state="pullback_to_21ema",
            transition_date=date(2026, 3, 28),
            conviction_level="low",
            detected_at=datetime.now(timezone.utc),
        )
        assert t.conviction_level == "low"
