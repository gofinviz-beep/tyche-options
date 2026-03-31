"""Tests for the exit monitor workflow."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from tyche.models.backtest import StockPosition
from tyche.workflow.exit_monitor import ExitCheckResult, check_exit_signals


def _make_position(
    ticker: str = "AAPL",
    purchase_price: float = 180.0,
    target_exit_price: float | None = 194.0,
    status: str = "active",
) -> StockPosition:
    now = datetime.now(timezone.utc)
    return StockPosition(
        id=f"pos-{ticker}",
        ticker=ticker,
        quantity=10,
        purchase_date=date(2026, 3, 1),
        purchase_price=purchase_price,
        pullback_type="8ema",
        target_exit_pct=7.78 if target_exit_price else None,
        target_exit_price=target_exit_price,
        stop_loss_price=178.0,
        current_price=purchase_price,
        current_gain_pct=0.0,
        status=status,
        created_at=now,
        updated_at=now,
    )


def _make_ohlcv(
    closes: list[float], days: int = 20
) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame for testing."""
    n = max(len(closes), days)
    if len(closes) < n:
        closes = [closes[0]] * (n - len(closes)) + closes
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n),
        "open": closes,
        "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes],
        "close": closes,
        "volume": [1_000_000] * n,
    })


@pytest.mark.asyncio
async def test_no_active_positions():
    """Exit monitor with no active positions returns immediately."""
    with patch(
        "tyche.workflow.exit_monitor.repo.get_active_positions",
        new_callable=AsyncMock,
        return_value=[],
    ):
        store = MagicMock()
        result = await check_exit_signals(store)
        assert result.positions_checked == 0
        assert result.signals == []


@pytest.mark.asyncio
async def test_profit_target_hit():
    """Triggers profit_target signal when current_price >= target_exit_price."""
    position = _make_position(
        ticker="AAPL",
        purchase_price=180.0,
        target_exit_price=194.0,
    )
    closes = [190.0] * 19 + [195.0]
    df = _make_ohlcv(closes)

    store = MagicMock()
    store.read_tickers.return_value = {"AAPL": df}

    mock_signal = MagicMock()
    mock_signal.to_dict.return_value = {
        "id": "sig-1",
        "position_id": "pos-AAPL",
        "ticker": "AAPL",
        "signal_type": "profit_target",
        "trigger_price": 194.0,
        "current_price": 195.0,
        "gain_pct": 8.33,
        "triggered_at": "2026-03-25T16:05:00+00:00",
    }

    with (
        patch(
            "tyche.workflow.exit_monitor.repo.get_active_positions",
            new_callable=AsyncMock,
            return_value=[position],
        ),
        patch(
            "tyche.workflow.exit_monitor.repo.update_position_prices",
            new_callable=AsyncMock,
        ) as mock_update,
        patch(
            "tyche.workflow.exit_monitor.repo.record_exit_signal",
            new_callable=AsyncMock,
            return_value=mock_signal,
        ) as mock_record,
    ):
        result = await check_exit_signals(store)

        assert result.positions_checked == 1
        assert result.prices_updated == 1
        assert result.profit_targets_hit == 1
        assert result.stop_losses_hit == 0
        assert len(result.signals) == 1
        assert result.signals[0]["signal_type"] == "profit_target"

        mock_update.assert_called_once()
        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args
        assert call_kwargs.kwargs["signal_type"] == "profit_target"


@pytest.mark.asyncio
async def test_stop_loss_hit():
    """Triggers stop_loss signal when close < 8-EMA."""
    position = _make_position(
        ticker="PL",
        purchase_price=5.00,
        target_exit_price=5.50,
    )
    closes = [5.20] * 15 + [5.10, 5.05, 5.00, 4.95, 4.50]
    df = _make_ohlcv(closes)

    store = MagicMock()
    store.read_tickers.return_value = {"PL": df}

    mock_signal = MagicMock()
    mock_signal.to_dict.return_value = {
        "id": "sig-2",
        "position_id": "pos-PL",
        "ticker": "PL",
        "signal_type": "stop_loss",
        "trigger_price": 4.90,
        "current_price": 4.50,
        "gain_pct": -10.0,
        "triggered_at": "2026-03-25T16:05:00+00:00",
    }

    with (
        patch(
            "tyche.workflow.exit_monitor.repo.get_active_positions",
            new_callable=AsyncMock,
            return_value=[position],
        ),
        patch(
            "tyche.workflow.exit_monitor.repo.update_position_prices",
            new_callable=AsyncMock,
        ),
        patch(
            "tyche.workflow.exit_monitor.repo.record_exit_signal",
            new_callable=AsyncMock,
            return_value=mock_signal,
        ) as mock_record,
    ):
        result = await check_exit_signals(store)

        assert result.stop_losses_hit == 1
        assert result.profit_targets_hit == 0
        call_kwargs = mock_record.call_args
        assert call_kwargs.kwargs["signal_type"] == "stop_loss"


@pytest.mark.asyncio
async def test_no_signal_above_ema_below_target():
    """No signal when price is above 8-EMA but below profit target."""
    position = _make_position(
        ticker="MSFT",
        purchase_price=400.0,
        target_exit_price=430.0,
    )
    closes = [410.0] * 20
    df = _make_ohlcv(closes)

    store = MagicMock()
    store.read_tickers.return_value = {"MSFT": df}

    with (
        patch(
            "tyche.workflow.exit_monitor.repo.get_active_positions",
            new_callable=AsyncMock,
            return_value=[position],
        ),
        patch(
            "tyche.workflow.exit_monitor.repo.update_position_prices",
            new_callable=AsyncMock,
        ),
        patch(
            "tyche.workflow.exit_monitor.repo.record_exit_signal",
            new_callable=AsyncMock,
        ) as mock_record,
    ):
        result = await check_exit_signals(store)

        assert result.positions_checked == 1
        assert result.prices_updated == 1
        assert result.profit_targets_hit == 0
        assert result.stop_losses_hit == 0
        assert result.signals == []
        mock_record.assert_not_called()


@pytest.mark.asyncio
async def test_missing_ohlcv_data():
    """Position with no OHLCV data is skipped without error."""
    position = _make_position(ticker="NOPE")

    store = MagicMock()
    store.read_tickers.return_value = {"NOPE": None}

    with (
        patch(
            "tyche.workflow.exit_monitor.repo.get_active_positions",
            new_callable=AsyncMock,
            return_value=[position],
        ),
        patch(
            "tyche.workflow.exit_monitor.repo.update_position_prices",
            new_callable=AsyncMock,
        ) as mock_update,
    ):
        result = await check_exit_signals(store)

        assert result.positions_checked == 1
        assert result.prices_updated == 0
        assert result.errors == 0
        mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_position_without_target():
    """Position without target_exit_price only checks stop loss."""
    position = _make_position(
        ticker="ABC",
        purchase_price=50.0,
        target_exit_price=None,
    )
    closes = [52.0] * 20
    df = _make_ohlcv(closes)

    store = MagicMock()
    store.read_tickers.return_value = {"ABC": df}

    with (
        patch(
            "tyche.workflow.exit_monitor.repo.get_active_positions",
            new_callable=AsyncMock,
            return_value=[position],
        ),
        patch(
            "tyche.workflow.exit_monitor.repo.update_position_prices",
            new_callable=AsyncMock,
        ),
        patch(
            "tyche.workflow.exit_monitor.repo.record_exit_signal",
            new_callable=AsyncMock,
        ) as mock_record,
    ):
        result = await check_exit_signals(store)

        assert result.positions_checked == 1
        assert result.prices_updated == 1
        assert result.profit_targets_hit == 0
        assert result.stop_losses_hit == 0
        mock_record.assert_not_called()


@pytest.mark.asyncio
async def test_multiple_positions():
    """Handles multiple positions across different tickers."""
    pos_a = _make_position("AAPL", 180.0, 194.0)
    pos_b = _make_position("PL", 5.0, 5.50)

    df_a = _make_ohlcv([190.0] * 19 + [195.0])
    df_b = _make_ohlcv([5.10] * 20)

    store = MagicMock()
    store.read_tickers.return_value = {"AAPL": df_a, "PL": df_b}

    mock_signal = MagicMock()
    mock_signal.to_dict.return_value = {
        "id": "s",
        "position_id": "p",
        "ticker": "AAPL",
        "signal_type": "profit_target",
        "trigger_price": 194.0,
        "current_price": 195.0,
        "gain_pct": 8.33,
        "triggered_at": None,
    }

    with (
        patch(
            "tyche.workflow.exit_monitor.repo.get_active_positions",
            new_callable=AsyncMock,
            return_value=[pos_a, pos_b],
        ),
        patch(
            "tyche.workflow.exit_monitor.repo.update_position_prices",
            new_callable=AsyncMock,
        ),
        patch(
            "tyche.workflow.exit_monitor.repo.record_exit_signal",
            new_callable=AsyncMock,
            return_value=mock_signal,
        ),
    ):
        result = await check_exit_signals(store)

        assert result.positions_checked == 2
        assert result.prices_updated == 2
        assert result.profit_targets_hit == 1
