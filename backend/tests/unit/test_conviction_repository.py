"""Tests for conviction repository — upsert, transitions, queries, cleanup."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tyche.models.conviction import ConvictionSnapshot, ConvictionTransition
from tyche.persistence import conviction_repository as repo


def _make_signal(
    ticker: str = "AAPL",
    trend_state: str = "uptrend",
    conviction_level: str = "high",
    csp_eligible: bool = True,
    as_of_date: date | None = None,
    last_close: float = 185.0,
    ema_8: float = 184.0,
    ema_21: float = 180.0,
    ema_8_slope: float = 0.4,
    ema_21_slope: float = 0.3,
    price_to_8ema_pct: float = 0.5,
    price_to_21ema_pct: float = 2.8,
    volume_declining: bool = False,
    days_above_both_emas: int = 7,
    avg_volume_20d: int = 50_000_000,
    latest_volume: int = 45_000_000,
) -> MagicMock:
    """Create a mock ConvictionSignal."""
    from enum import Enum

    class MockTrendState(Enum):
        uptrend = "uptrend"
        pullback_to_8ema = "pullback_to_8ema"
        pullback_to_21ema = "pullback_to_21ema"

    sig = MagicMock()
    sig.ticker = ticker
    sig.as_of_date = as_of_date or date(2026, 3, 28)
    sig.trend_state = MockTrendState[trend_state] if trend_state in [e.name for e in MockTrendState] else MagicMock(value=trend_state)
    sig.conviction_level = conviction_level
    sig.raw_conviction = conviction_level
    sig.csp_eligible = csp_eligible
    sig.last_close = last_close
    sig.ema_8 = ema_8
    sig.ema_21 = ema_21
    sig.ema_8_slope = ema_8_slope
    sig.ema_21_slope = ema_21_slope
    sig.price_to_8ema_pct = price_to_8ema_pct
    sig.price_to_21ema_pct = price_to_21ema_pct
    sig.volume_declining_on_pullback = volume_declining
    sig.days_above_both_emas = days_above_both_emas
    sig.avg_volume_20d = avg_volume_20d
    sig.latest_volume = latest_volume
    return sig


class TestPreviousTradingDay:
    def test_friday_to_thursday(self):
        result = repo._previous_trading_day(date(2026, 3, 27))  # Friday
        assert result == date(2026, 3, 26)  # Thursday

    def test_monday_to_friday(self):
        result = repo._previous_trading_day(date(2026, 3, 30))  # Monday
        assert result == date(2026, 3, 27)  # Friday

    def test_tuesday_to_monday(self):
        result = repo._previous_trading_day(date(2026, 3, 31))  # Tuesday
        assert result == date(2026, 3, 30)  # Monday

    def test_sunday_to_friday(self):
        result = repo._previous_trading_day(date(2026, 3, 29))  # Sunday
        assert result == date(2026, 3, 27)  # Friday

    def test_saturday_to_friday(self):
        result = repo._previous_trading_day(date(2026, 3, 28))  # Saturday
        assert result == date(2026, 3, 27)  # Friday


class TestUpsertSnapshots:
    @pytest.mark.asyncio
    async def test_empty_signals_returns_zero(self):
        result = await repo.upsert_snapshots([], date(2026, 3, 28))
        assert result == 0

    @pytest.mark.asyncio
    @patch("tyche.persistence.conviction_repository.get_session")
    async def test_upserts_signals(self, mock_get_session):
        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_get_session.return_value = mock_ctx

        signals = [
            _make_signal("AAPL"),
            _make_signal("MSFT", ema_8=420.0, ema_21=415.0, last_close=421.0),
        ]

        result = await repo.upsert_snapshots(signals, date(2026, 3, 28))
        assert result == 2
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    @patch("tyche.persistence.conviction_repository.get_session")
    async def test_upserts_large_batch(self, mock_get_session):
        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_get_session.return_value = mock_ctx

        signals = [_make_signal(f"T{i:04d}") for i in range(750)]
        result = await repo.upsert_snapshots(signals, date(2026, 3, 28))
        assert result == 750
        assert mock_session.execute.call_count == 2  # 500 + 250
        mock_session.commit.assert_called_once()


class TestDetectAndRecordTransitions:
    @pytest.mark.asyncio
    @patch("tyche.persistence.conviction_repository.get_session")
    async def test_no_transitions_when_states_match(self, mock_get_session):
        today_snap = MagicMock()
        today_snap.ticker = "AAPL"
        today_snap.trend_state = "uptrend"

        yesterday_snap = MagicMock()
        yesterday_snap.ticker = "AAPL"
        yesterday_snap.trend_state = "uptrend"

        mock_session = AsyncMock()
        mock_result_today = MagicMock()
        mock_result_today.scalars.return_value.all.return_value = [today_snap]
        mock_result_yesterday = MagicMock()
        mock_result_yesterday.scalars.return_value.all.return_value = [yesterday_snap]

        mock_session.execute = AsyncMock(
            side_effect=[mock_result_today, mock_result_yesterday]
        )

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_get_session.return_value = mock_ctx

        result = await repo.detect_and_record_transitions(date(2026, 3, 28))
        assert len(result) == 0

    @pytest.mark.asyncio
    @patch("tyche.persistence.conviction_repository.get_session")
    async def test_detects_state_change(self, mock_get_session):
        today_snap = MagicMock()
        today_snap.ticker = "AAPL"
        today_snap.trend_state = "pullback_to_8ema"
        today_snap.last_close = 183.0
        today_snap.ema_8 = 184.0
        today_snap.ema_21 = 180.0
        today_snap.ema_8_slope = 0.3
        today_snap.ema_21_slope = 0.2
        today_snap.conviction_level = "medium"
        today_snap.raw_conviction = "medium"

        yesterday_snap = MagicMock()
        yesterday_snap.ticker = "AAPL"
        yesterday_snap.trend_state = "uptrend"

        mock_session = AsyncMock()
        mock_result_today = MagicMock()
        mock_result_today.scalars.return_value.all.return_value = [today_snap]
        mock_result_yesterday = MagicMock()
        mock_result_yesterday.scalars.return_value.all.return_value = [yesterday_snap]

        call_count = [0]

        async def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_result_today
            return mock_result_yesterday

        mock_session.execute = AsyncMock(side_effect=side_effect)
        mock_session.add_all = MagicMock()
        mock_session.commit = AsyncMock()

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_get_session.return_value = mock_ctx

        result = await repo.detect_and_record_transitions(date(2026, 3, 28))
        assert len(result) == 1
        assert result[0].ticker == "AAPL"
        assert result[0].from_state == "uptrend"
        assert result[0].to_state == "pullback_to_8ema"

    @pytest.mark.asyncio
    @patch("tyche.persistence.conviction_repository.get_session")
    async def test_ignores_tickers_only_in_today(self, mock_get_session):
        today_snap = MagicMock()
        today_snap.ticker = "NEWT"
        today_snap.trend_state = "uptrend"

        mock_session = AsyncMock()
        mock_result_today = MagicMock()
        mock_result_today.scalars.return_value.all.return_value = [today_snap]
        mock_result_yesterday = MagicMock()
        mock_result_yesterday.scalars.return_value.all.return_value = []

        mock_session.execute = AsyncMock(
            side_effect=[mock_result_today, mock_result_yesterday]
        )

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_get_session.return_value = mock_ctx

        result = await repo.detect_and_record_transitions(date(2026, 3, 28))
        assert len(result) == 0


class TestGetActivePullbacks:
    @pytest.mark.asyncio
    @patch("tyche.persistence.conviction_repository.get_session")
    async def test_returns_pullback_snapshots(self, mock_get_session):
        snap = MagicMock()
        snap.ticker = "AAPL"
        snap.trend_state = "pullback_to_8ema"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [snap]
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_get_session.return_value = mock_ctx

        result = await repo.get_active_pullbacks(date(2026, 3, 28))
        assert len(result) == 1
        assert result[0].ticker == "AAPL"


class TestGetTickerHistory:
    @pytest.mark.asyncio
    @patch("tyche.persistence.conviction_repository.get_session")
    async def test_returns_ordered_snapshots(self, mock_get_session):
        snap1 = MagicMock()
        snap1.ticker = "AAPL"
        snap1.as_of_date = date(2026, 3, 27)
        snap2 = MagicMock()
        snap2.ticker = "AAPL"
        snap2.as_of_date = date(2026, 3, 28)

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [snap1, snap2]
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_get_session.return_value = mock_ctx

        result = await repo.get_ticker_history("AAPL", days=30)
        assert len(result) == 2


class TestGetTransitions:
    @pytest.mark.asyncio
    @patch("tyche.persistence.conviction_repository.get_session")
    async def test_with_filters(self, mock_get_session):
        t = MagicMock()
        t.ticker = "AAPL"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [t]
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_get_session.return_value = mock_ctx

        result = await repo.get_transitions(
            from_date=date(2026, 3, 21),
            to_date=date(2026, 3, 28),
            to_states=["pullback_to_8ema"],
            ticker="AAPL",
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    @patch("tyche.persistence.conviction_repository.get_session")
    async def test_without_filters(self, mock_get_session):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_get_session.return_value = mock_ctx

        result = await repo.get_transitions()
        assert result == []


class TestGetSnapshotsForDate:
    @pytest.mark.asyncio
    @patch("tyche.persistence.conviction_repository.get_session")
    async def test_with_ticker_filter(self, mock_get_session):
        snap = MagicMock()
        snap.ticker = "AAPL"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [snap]
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_get_session.return_value = mock_ctx

        result = await repo.get_snapshots_for_date(date(2026, 3, 28), tickers=["AAPL"])
        assert len(result) == 1


class TestCleanupOldSnapshots:
    @pytest.mark.asyncio
    @patch("tyche.persistence.conviction_repository.get_session")
    async def test_deletes_old_data(self, mock_get_session):
        mock_session = AsyncMock()
        snap_result = MagicMock()
        snap_result.rowcount = 50
        trans_result = MagicMock()
        trans_result.rowcount = 10
        mock_session.execute = AsyncMock(side_effect=[snap_result, trans_result])
        mock_session.commit = AsyncMock()

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_get_session.return_value = mock_ctx

        result = await repo.cleanup_old_snapshots(retention_days=90)
        assert result == 60  # 50 + 10

    @pytest.mark.asyncio
    @patch("tyche.persistence.conviction_repository.get_session")
    async def test_nothing_to_cleanup(self, mock_get_session):
        mock_session = AsyncMock()
        snap_result = MagicMock()
        snap_result.rowcount = 0
        trans_result = MagicMock()
        trans_result.rowcount = 0
        mock_session.execute = AsyncMock(side_effect=[snap_result, trans_result])
        mock_session.commit = AsyncMock()

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_get_session.return_value = mock_ctx

        result = await repo.cleanup_old_snapshots(retention_days=90)
        assert result == 0
