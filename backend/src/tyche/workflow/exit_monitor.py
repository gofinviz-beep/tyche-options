"""Exit monitor — checks active stock positions against profit targets and stop losses.

Runs after market close (or on-demand via API) to:
1. Update each active position's current price and 8-EMA stop loss.
2. Check if profit target (p75 exit) or stop loss (close < 8-EMA) was hit.
3. Record exit signals for triggered positions.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from tyche.conviction.engine import compute_ema
from tyche.market_data.data_store import OHLCVStore
from tyche.models.backtest import ExitSignal
from tyche.persistence import position_repository as repo

logger = structlog.get_logger()

EMA_PERIOD = 8


@dataclass
class ExitCheckResult:
    """Summary of an exit monitor run."""

    positions_checked: int
    prices_updated: int
    profit_targets_hit: int
    stop_losses_hit: int
    errors: int
    signals: list[dict]


async def check_exit_signals(data_store: OHLCVStore) -> ExitCheckResult:
    """Check all active positions for exit conditions.

    For each active position:
    - Reads latest OHLCV data to get current close and compute 8-EMA
    - Updates position with current_price, stop_loss_price, current_gain_pct
    - Triggers profit_target signal if current_price >= target_exit_price
    - Triggers stop_loss signal if close < 8-EMA
    """
    positions = await repo.get_active_positions()

    if not positions:
        logger.info("exit_monitor_no_active_positions")
        return ExitCheckResult(
            positions_checked=0,
            prices_updated=0,
            profit_targets_hit=0,
            stop_losses_hit=0,
            errors=0,
            signals=[],
        )

    tickers = list({p.ticker for p in positions})
    ticker_data = data_store.read_tickers(tickers)

    prices_updated = 0
    profit_hits = 0
    stop_hits = 0
    errors = 0
    signals: list[dict] = []

    for position in positions:
        try:
            df = ticker_data.get(position.ticker)
            if df is None or df.empty or len(df) < EMA_PERIOD:
                logger.warning(
                    "exit_monitor_no_data",
                    ticker=position.ticker,
                    bars=len(df) if df is not None else 0,
                )
                continue

            close = df["close"].astype(float)
            ema_8 = compute_ema(close, EMA_PERIOD)

            current_price = float(close.iloc[-1])
            current_ema_8 = float(ema_8.iloc[-1])
            gain_pct = (
                (current_price - position.purchase_price) / position.purchase_price
            ) * 100

            await repo.update_position_prices(
                position_id=position.id,
                current_price=current_price,
                stop_loss_price=current_ema_8,
                current_gain_pct=gain_pct,
            )
            prices_updated += 1

            if (
                position.target_exit_price
                and current_price >= position.target_exit_price
            ):
                signal = await repo.record_exit_signal(
                    position_id=position.id,
                    ticker=position.ticker,
                    signal_type="profit_target",
                    trigger_price=position.target_exit_price,
                    current_price=current_price,
                    gain_pct=gain_pct,
                )
                signals.append(signal.to_dict())
                profit_hits += 1
                logger.info(
                    "exit_signal_profit_target",
                    ticker=position.ticker,
                    target=position.target_exit_price,
                    current=current_price,
                    gain_pct=round(gain_pct, 2),
                )

            elif current_price < current_ema_8:
                signal = await repo.record_exit_signal(
                    position_id=position.id,
                    ticker=position.ticker,
                    signal_type="stop_loss",
                    trigger_price=current_ema_8,
                    current_price=current_price,
                    gain_pct=gain_pct,
                )
                signals.append(signal.to_dict())
                stop_hits += 1
                logger.info(
                    "exit_signal_stop_loss",
                    ticker=position.ticker,
                    ema_8=round(current_ema_8, 2),
                    current=current_price,
                    gain_pct=round(gain_pct, 2),
                )

        except Exception:
            errors += 1
            logger.warning(
                "exit_monitor_position_error",
                ticker=position.ticker,
                position_id=position.id,
                exc_info=True,
            )

    result = ExitCheckResult(
        positions_checked=len(positions),
        prices_updated=prices_updated,
        profit_targets_hit=profit_hits,
        stop_losses_hit=stop_hits,
        errors=errors,
        signals=signals,
    )

    logger.info(
        "exit_monitor_complete",
        checked=result.positions_checked,
        updated=result.prices_updated,
        profit_targets=result.profit_targets_hit,
        stop_losses=result.stop_losses_hit,
        errors=result.errors,
    )
    return result
