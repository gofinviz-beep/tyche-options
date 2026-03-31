"""Tests for the stocks API routes."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tyche.api.routes.stocks import (
    _build_action_text,
    _market_cap_label,
    _snapshot_to_pullback_alert,
    _snapshot_to_response,
    _transition_to_response,
)


def _make_snapshot_model(
    ticker: str = "AAPL",
    trend_state: str = "pullback_to_8ema",
    conviction_level: str = "high",
    raw_conviction: str | None = None,
    last_close: float = 183.0,
    ema_8: float = 184.0,
    ema_21: float = 180.0,
    ema_8_slope: float = 0.4,
    ema_21_slope: float = 0.3,
    volume_declining: bool = True,
    csp_eligible: bool = True,
    as_of_date=None,
    computed_at=None,
):
    from tyche.models.conviction import ConvictionSnapshot

    return ConvictionSnapshot(
        ticker=ticker,
        as_of_date=as_of_date or date(2026, 3, 28),
        trend_state=trend_state,
        conviction_level=conviction_level,
        raw_conviction=raw_conviction or conviction_level,
        csp_eligible=csp_eligible,
        last_close=last_close,
        ema_8=ema_8,
        ema_21=ema_21,
        ema_8_slope=ema_8_slope,
        ema_21_slope=ema_21_slope,
        price_to_8ema_pct=-0.54,
        price_to_21ema_pct=1.67,
        volume_declining=volume_declining,
        days_above_both_emas=7,
        avg_volume_20d=65_000_000,
        latest_volume=55_000_000,
        computed_at=computed_at or datetime(2026, 3, 28, 14, 30, tzinfo=timezone.utc),
    )


def _make_transition_model(
    ticker: str = "AAPL",
    from_state: str = "uptrend",
    to_state: str = "pullback_to_8ema",
):
    import uuid as _uuid
    from tyche.models.conviction import ConvictionTransition

    return ConvictionTransition(
        id=str(_uuid.uuid4()),
        ticker=ticker,
        from_state=from_state,
        to_state=to_state,
        transition_date=date(2026, 3, 28),
        last_close=183.0,
        ema_8=184.0,
        ema_21=180.0,
        ema_8_slope=0.4,
        ema_21_slope=0.3,
        conviction_level="medium",
        raw_conviction="medium",
        detected_at=datetime(2026, 3, 28, 14, 30, tzinfo=timezone.utc),
    )


class TestSnapshotToResponse:
    def test_basic_conversion(self):
        snap = _make_snapshot_model()
        resp = _snapshot_to_response(snap)
        assert resp.ticker == "AAPL"
        assert resp.trend_state == "pullback_to_8ema"
        assert resp.conviction_level == "high"
        assert resp.last_close == 183.0


class TestTransitionToResponse:
    def test_basic_conversion(self):
        t = _make_transition_model()
        resp = _transition_to_response(t)
        assert resp.ticker == "AAPL"
        assert resp.from_state == "uptrend"
        assert resp.to_state == "pullback_to_8ema"


class TestSnapshotToPullbackAlert:
    def test_8ema_pullback(self):
        snap = _make_snapshot_model(trend_state="pullback_to_8ema")
        resp = _snapshot_to_pullback_alert(snap, inst_pct=0.65)
        assert resp.ticker == "AAPL"
        assert resp.alert_type == "pullback_8ema"
        assert resp.severity == "info"
        assert resp.position_size_hint == "standard"
        assert resp.institutional_pct == 0.65

    def test_21ema_pullback(self):
        snap = _make_snapshot_model(trend_state="pullback_to_21ema")
        resp = _snapshot_to_pullback_alert(snap)
        assert resp.alert_type == "pullback_21ema"
        assert resp.severity == "high"
        assert resp.position_size_hint == "large"

    def test_21ema_with_volume_declining(self):
        snap = _make_snapshot_model(
            trend_state="pullback_to_21ema", volume_declining=True
        )
        resp = _snapshot_to_pullback_alert(snap)
        assert "institutional defense" in resp.suggested_action.lower()

    def test_21ema_without_volume_declining(self):
        snap = _make_snapshot_model(
            trend_state="pullback_to_21ema", volume_declining=False
        )
        resp = _snapshot_to_pullback_alert(snap)
        assert "institutional defense" in resp.suggested_action.lower()
        assert "conviction: high" in resp.suggested_action.lower()

    def test_21ema_low_conviction_with_volume(self):
        snap = _make_snapshot_model(
            trend_state="pullback_to_21ema",
            volume_declining=True,
            conviction_level="low",
            raw_conviction="low",
        )
        resp = _snapshot_to_pullback_alert(snap)
        assert "conviction: low" in resp.suggested_action.lower()
        assert "high-conviction entry" not in resp.suggested_action.lower()

    def test_raw_conviction_used_in_response(self):
        """When CSP-adjusted is 'low' but raw is 'high', stocks should show 'high'."""
        snap = _make_snapshot_model(
            trend_state="pullback_to_21ema",
            conviction_level="low",
            raw_conviction="high",
            volume_declining=True,
        )
        resp = _snapshot_to_pullback_alert(snap)
        assert resp.conviction_level == "high"
        assert resp.raw_conviction == "high"
        assert "high-conviction entry zone" in resp.suggested_action.lower()

    def test_legacy_none_raw_conviction_falls_back(self):
        """Pre-migration rows with raw_conviction='none' should fall back to conviction_level."""
        snap = _make_snapshot_model(
            trend_state="pullback_to_21ema",
            conviction_level="low",
            raw_conviction="none",
            volume_declining=True,
        )
        resp = _snapshot_to_pullback_alert(snap)
        assert resp.conviction_level == "low"
        assert "conviction: low" in resp.suggested_action.lower()

    def test_8ema_with_volume_declining(self):
        snap = _make_snapshot_model(
            trend_state="pullback_to_8ema", volume_declining=True
        )
        resp = _snapshot_to_pullback_alert(snap)
        assert "standard position" in resp.suggested_action.lower()

    def test_8ema_without_volume_declining(self):
        snap = _make_snapshot_model(
            trend_state="pullback_to_8ema", volume_declining=False
        )
        resp = _snapshot_to_pullback_alert(snap)
        assert "volume confirmation" in resp.suggested_action.lower()

    def test_no_institutional_pct(self):
        snap = _make_snapshot_model()
        resp = _snapshot_to_pullback_alert(snap, inst_pct=None)
        assert resp.institutional_pct is None

    def test_stop_loss_present(self):
        snap = _make_snapshot_model()
        resp = _snapshot_to_pullback_alert(snap)
        assert resp.stop_loss_level > 0

    def test_metadata_fields_with_meta(self):
        snap = _make_snapshot_model()
        meta = {"market_cap": 2_500_000_000_000, "exchange": "XNAS", "name": "Apple Inc."}
        resp = _snapshot_to_pullback_alert(snap, meta=meta)
        assert resp.market_cap == 2_500_000_000_000
        assert resp.market_cap_label == "Mega Cap"
        assert resp.exchange == "XNAS"
        assert resp.name == "Apple Inc."

    def test_metadata_fields_without_meta(self):
        snap = _make_snapshot_model()
        resp = _snapshot_to_pullback_alert(snap)
        assert resp.market_cap is None
        assert resp.market_cap_label == ""
        assert resp.exchange == ""
        assert resp.name == ""

    def test_additional_snapshot_fields(self):
        snap = _make_snapshot_model()
        resp = _snapshot_to_pullback_alert(snap)
        assert resp.days_above_both_emas == 7
        assert resp.avg_volume_20d == 65_000_000
        assert resp.price_to_8ema_pct == -0.54
        assert resp.price_to_21ema_pct == 1.67


class TestMarketCapLabel:
    def test_mega_cap(self):
        assert _market_cap_label(200_000_000_001) == "Mega Cap"

    def test_large_cap(self):
        assert _market_cap_label(50_000_000_000) == "Large Cap"

    def test_mid_cap(self):
        assert _market_cap_label(5_000_000_000) == "Mid Cap"

    def test_small_cap(self):
        assert _market_cap_label(500_000_000) == "Small Cap"

    def test_micro_cap(self):
        assert _market_cap_label(100_000_000) == "Micro Cap"

    def test_none(self):
        assert _market_cap_label(None) == ""


class TestBuildActionText:
    def test_21ema_high_vol_declining(self):
        text = _build_action_text(is_21ema=True, conviction_level="high", volume_declining=True)
        assert "high-conviction entry zone" in text.lower()

    def test_21ema_low_vol_declining(self):
        text = _build_action_text(is_21ema=True, conviction_level="low", volume_declining=True)
        assert "conviction: low" in text.lower()
        assert "high-conviction" not in text.lower()

    def test_21ema_no_volume(self):
        text = _build_action_text(is_21ema=True, conviction_level="medium", volume_declining=False)
        assert "conviction: medium" in text.lower()

    def test_8ema_high_vol_declining(self):
        text = _build_action_text(is_21ema=False, conviction_level="high", volume_declining=True)
        assert "high conviction" in text.lower()

    def test_8ema_low_vol_declining(self):
        text = _build_action_text(is_21ema=False, conviction_level="low", volume_declining=True)
        assert "conviction: low" in text.lower()

    def test_8ema_no_volume(self):
        text = _build_action_text(is_21ema=False, conviction_level="none", volume_declining=False)
        assert "conviction: none" in text.lower()


class TestConvictionHistoryEndpointUnit:
    @pytest.mark.asyncio
    @patch("tyche.api.routes.stocks.get_transitions", new_callable=AsyncMock)
    @patch("tyche.api.routes.stocks.get_ticker_history", new_callable=AsyncMock)
    async def test_returns_history(self, mock_history, mock_transitions):
        snap = _make_snapshot_model()
        mock_history.return_value = [snap]
        mock_transitions.return_value = [_make_transition_model()]

        from tyche.api.routes.stocks import get_conviction_history_endpoint

        resp = await get_conviction_history_endpoint(ticker="AAPL", days=30)
        assert resp.ticker == "AAPL"
        assert len(resp.snapshots) == 1
        assert len(resp.transitions) == 1

    @pytest.mark.asyncio
    @patch("tyche.api.routes.stocks.get_transitions", new_callable=AsyncMock)
    @patch("tyche.api.routes.stocks.get_ticker_history", new_callable=AsyncMock)
    async def test_empty_history(self, mock_history, mock_transitions):
        mock_history.return_value = []
        mock_transitions.return_value = []

        from tyche.api.routes.stocks import get_conviction_history_endpoint

        resp = await get_conviction_history_endpoint(ticker="UNKNOWN", days=30)
        assert resp.ticker == "UNKNOWN"
        assert len(resp.snapshots) == 0
        assert len(resp.transitions) == 0


class TestTransitionsEndpointUnit:
    @pytest.mark.asyncio
    @patch("tyche.api.routes.stocks.get_transitions", new_callable=AsyncMock)
    async def test_returns_transitions(self, mock_transitions):
        mock_transitions.return_value = [_make_transition_model()]

        from tyche.api.routes.stocks import get_transitions_endpoint

        resp = await get_transitions_endpoint(days=7, to_states=None)
        assert len(resp.transitions) == 1

    @pytest.mark.asyncio
    @patch("tyche.api.routes.stocks.get_transitions", new_callable=AsyncMock)
    async def test_with_state_filter(self, mock_transitions):
        mock_transitions.return_value = [_make_transition_model()]

        from tyche.api.routes.stocks import get_transitions_endpoint

        resp = await get_transitions_endpoint(
            days=7, to_states="pullback_to_8ema,pullback_to_21ema"
        )
        assert len(resp.transitions) == 1
        mock_transitions.assert_called_once()


class TestRefreshConvictionEndpointUnit:
    @pytest.mark.asyncio
    @patch("tyche.workflow.conviction_batch.run_conviction_batch", new_callable=AsyncMock)
    async def test_triggers_batch(self, mock_batch):
        from tyche.workflow.conviction_batch import ConvictionBatchResult

        mock_batch.return_value = ConvictionBatchResult(
            as_of_date=date(2026, 3, 28),
            signals_computed=100,
            snapshots_upserted=100,
            transitions_detected=5,
            new_pullback_transitions=2,
            duration_ms=5000.0,
        )

        from tyche.api.routes.stocks import refresh_conviction

        settings = MagicMock()
        settings.conviction_batch_min_market_cap_millions = 500.0
        settings.conviction_batch_min_price = 5.0
        settings.conviction_batch_min_avg_volume = 500_000
        settings.conviction_snapshot_retention_days = 90

        engine = MagicMock()
        store = MagicMock()
        meta = MagicMock()

        resp = await refresh_conviction(settings, engine, store, meta)
        assert resp.signals_computed == 100
        assert resp.snapshots_upserted == 100


class TestConvictionSnapshotsEndpoint:
    @pytest.mark.asyncio
    @patch("tyche.api.routes.stocks.get_snapshots_for_date", new_callable=AsyncMock)
    async def test_returns_snapshots_for_today(self, mock_snaps):
        mock_snaps.return_value = [_make_snapshot_model(), _make_snapshot_model(ticker="NVDA")]

        from tyche.api.routes.stocks import get_conviction_snapshots_endpoint

        resp = await get_conviction_snapshots_endpoint(as_of_date=None)
        assert len(resp) == 2
        assert resp[0].ticker == "AAPL"
        assert resp[1].ticker == "NVDA"

    @pytest.mark.asyncio
    @patch("tyche.api.routes.stocks.get_snapshots_for_date", new_callable=AsyncMock)
    async def test_returns_empty(self, mock_snaps):
        mock_snaps.return_value = []

        from tyche.api.routes.stocks import get_conviction_snapshots_endpoint

        resp = await get_conviction_snapshots_endpoint(as_of_date="2026-03-28")
        assert len(resp) == 0

    @pytest.mark.asyncio
    @patch("tyche.api.routes.stocks.get_snapshots_for_date", new_callable=AsyncMock)
    async def test_falls_back_to_previous_day(self, mock_snaps):
        mock_snaps.side_effect = [[], [_make_snapshot_model()]]

        from tyche.api.routes.stocks import get_conviction_snapshots_endpoint

        resp = await get_conviction_snapshots_endpoint(as_of_date=None)
        assert len(resp) == 1
        assert mock_snaps.call_count == 2


class TestTickerGatesEndpoint:
    @pytest.mark.asyncio
    async def test_returns_gates(self):
        from tyche.api.routes.stocks import get_ticker_gates
        from tyche.conviction.engine import ConvictionSignal, GateResult, TrendState

        mock_engine = MagicMock()
        mock_signal = ConvictionSignal(
            ticker="AAPL",
            trend_state=TrendState.STRONG_UPTREND,
            conviction_level="high",
            csp_eligible=True,
            last_close=190.0,
            ema_8=188.0,
            ema_21=185.0,
            days_above_both_emas=10,
            gate_results=[
                GateResult(
                    gate="Trend State",
                    passed=True,
                    actual="strong_uptrend",
                    threshold="uptrend or better",
                    reason="Price above both EMAs",
                ),
            ],
        )
        mock_engine.analyze.return_value = mock_signal

        mock_store = MagicMock()
        mock_store.exists = True
        mock_store.read_tickers.return_value = {"AAPL": MagicMock()}

        resp = await get_ticker_gates("AAPL", mock_engine, mock_store)
        assert resp["ticker"] == "AAPL"
        assert len(resp["gate_results"]) == 1
        assert resp["gate_results"][0]["passed"] is True

    @pytest.mark.asyncio
    async def test_no_data_store(self):
        from tyche.api.routes.stocks import get_ticker_gates

        mock_engine = MagicMock()
        mock_store = MagicMock()
        mock_store.exists = False

        resp = await get_ticker_gates("AAPL", mock_engine, mock_store)
        assert resp["error"] == "No OHLCV data"
        assert resp["gate_results"] == []

    @pytest.mark.asyncio
    async def test_ticker_not_in_store(self):
        from tyche.api.routes.stocks import get_ticker_gates

        mock_engine = MagicMock()
        mock_store = MagicMock()
        mock_store.exists = True
        mock_store.read_tickers.return_value = {}

        resp = await get_ticker_gates("UNKNOWN", mock_engine, mock_store)
        assert resp["error"] == "Ticker not in store"


class TestRecommendationsEndpointUnit:
    @pytest.mark.asyncio
    @patch("tyche.api.routes.stocks.generate_recommendations_from_snapshots")
    @patch("tyche.api.routes.stocks.filter_by_institutional_ownership", new_callable=AsyncMock)
    @patch("tyche.api.routes.stocks.get_active_pullbacks", new_callable=AsyncMock)
    async def test_returns_recs_from_db(self, mock_pullbacks, mock_inst, mock_gen):
        snap = _make_snapshot_model()
        mock_pullbacks.return_value = [snap]
        mock_inst.return_value = (["AAPL"], {"AAPL": 0.75})

        from tyche.workflow.stock_recommender import StockBuyRecommendation

        mock_rec = StockBuyRecommendation(
            ticker="AAPL",
            entry_type="pullback_8ema",
            entry_price=183.0,
            target_ema_value=184.0,
            stop_loss=178.2,
            conviction="high",
            institutional_pct=0.75,
            institutional_label="Strong",
            volume_confirmation=True,
            position_size_hint="standard",
            days_above_emas=7,
            ema_8_slope=0.4,
            ema_21_slope=0.3,
            related_csp_strike=None,
            has_active_csp=False,
            recommendation="Buy AAPL",
            risk_reward_note="Risk: 2.7%",
        )
        mock_gen.return_value = [mock_rec]

        from tyche.api.routes.stocks import get_stock_recommendations_endpoint

        settings = MagicMock()
        settings.min_institutional_pct_stock_buy = 0.50

        resp = await get_stock_recommendations_endpoint(settings)
        assert len(resp.recommendations) == 1
        assert resp.recommendations[0].ticker == "AAPL"
        mock_gen.assert_called_once()

    @pytest.mark.asyncio
    @patch("tyche.api.routes.stocks.get_active_pullbacks", new_callable=AsyncMock)
    async def test_empty_pullbacks(self, mock_pullbacks):
        mock_pullbacks.return_value = []

        from tyche.api.routes.stocks import get_stock_recommendations_endpoint

        settings = MagicMock()
        settings.min_institutional_pct_stock_buy = 0.50

        resp = await get_stock_recommendations_endpoint(settings)
        assert len(resp.recommendations) == 0


class TestCspExpiryEndpoints:
    @pytest.mark.asyncio
    async def test_get_expired_csps_empty(self):
        from tyche.api.routes.stocks import get_expired_csps

        tracker = MagicMock()
        tracker.get_all_records.return_value = []

        resp = await get_expired_csps(expiry_tracker=tracker)
        assert resp == []

    @pytest.mark.asyncio
    async def test_get_expired_csps_with_records(self):
        from tyche.api.routes.stocks import get_expired_csps

        record = MagicMock()
        record.ticker = "AAPL"
        record.expired_strike = 180.0
        record.expiry_date = "2026-03-21"
        record.premium_collected = 2.50
        record.recorded_at = "2026-03-21T10:00:00"

        tracker = MagicMock()
        tracker.get_all_records.return_value = [record]

        resp = await get_expired_csps(expiry_tracker=tracker)
        assert len(resp) == 1
        assert resp[0].ticker == "AAPL"

    @pytest.mark.asyncio
    async def test_remove_csp_expiry(self):
        from tyche.api.routes.stocks import remove_csp_expiry

        tracker = MagicMock()
        tracker.remove_ticker.return_value = 1

        resp = await remove_csp_expiry("aapl", expiry_tracker=tracker)
        assert resp["ticker"] == "AAPL"
        assert resp["removed"] == 1
