"""Strategy-specific label construction for tabular ML baselines.

Computes forward-looking outcomes from OHLCV data for each (ticker, date)
row. Labels are built in a separate pipeline from features to prevent
data leakage — they use only raw OHLCV, never derived features.

Label definitions:
  - csp_win_{dte}: Would a 5% OTM CSP (strike = support_ema * 0.95) have
    expired worthless? Binary — 1 if min(low) over forward DTE window
    stayed above the strike.
  - pullback_recovery_{horizon}: Did price close above the support EMA
    within {horizon} trading days?
  - deep_dip_recovery_{horizon}: Did price recover above the 21-EMA
    within {horizon} trading days? For oversold/deep-dip entries.
  - forward_return_{horizon}: Percentage return over the next {horizon}
    trading days.
  - max_drawdown_{horizon}: Maximum intra-period drawdown (close-to-low)
    within {horizon} trading days.
  - direction_{horizon}: Categorical — "up" (>1%), "down" (<-1%), "flat".
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger()

LABEL_HORIZONS: list[int] = [5, 10, 14, 20]
DEEP_DIP_RECOVERY_HORIZONS: list[int] = [10, 20, 40]
CSP_DTE_LIST: list[int] = [5, 14]
CSP_STRIKE_OFFSET: float = 0.05
RECOVERY_MAGNITUDE_HORIZONS: list[int] = [10, 20, 40, 60]

# Directional "big move" labels for the Alpha engine. Each entry is
# (horizon_trading_days, min_gain_pct). A label fires (1) when the maximum
# close-to-close gain within the forward horizon meets/exceeds the threshold.
# These are intentionally additive — they do not alter any existing label.
BIG_MOVE_SPECS: list[tuple[int, float]] = [
    (40, 25.0),
    (60, 40.0),
    (120, 60.0),
]
BIG_MOVE_MAGNITUDE_HORIZONS: list[int] = [40, 60, 120]


def _forward_min_low(
    lows: pd.Series,
    horizon: int,
) -> pd.Series:
    """Rolling min of 'low' prices looking forward by *horizon* bars."""
    return lows[::-1].rolling(horizon, min_periods=horizon).min()[::-1]


def _forward_close(
    closes: pd.Series,
    horizon: int,
) -> pd.Series:
    """Close price *horizon* bars into the future."""
    return closes.shift(-horizon)


def _forward_max_close(
    closes: pd.Series,
    horizon: int,
) -> pd.Series:
    """Rolling max of close prices looking forward by *horizon* bars."""
    return closes[::-1].rolling(horizon, min_periods=horizon).max()[::-1]


def _add_big_move_labels(labels: pd.DataFrame, close: pd.Series) -> None:
    """Add directional big-move classification + magnitude labels in place.

    For each (horizon, min_gain_pct) in ``BIG_MOVE_SPECS`` produces a binary
    ``big_move_up_{pct}pct_{horizon}d`` label that is 1 when the maximum
    close-to-close gain over the forward window meets the threshold. Also
    emits ``max_gain_pct_{horizon}d`` regression targets. NaN where the
    forward window is incomplete (so those rows are excluded at train time).
    """
    for horizon, min_gain_pct in BIG_MOVE_SPECS:
        fwd_max = _forward_max_close(close, horizon)
        max_gain_pct = (fwd_max - close) / close * 100
        col = f"big_move_up_{int(min_gain_pct)}pct_{horizon}d"
        labels[col] = (max_gain_pct >= min_gain_pct).astype(float)
        labels.loc[fwd_max.isna(), col] = np.nan

    for horizon in BIG_MOVE_MAGNITUDE_HORIZONS:
        col = f"max_gain_pct_{horizon}d"
        if col not in labels.columns:
            fwd_max = _forward_max_close(close, horizon)
            labels[col] = (fwd_max - close) / close * 100


def _add_sustained_big_move_labels(labels: pd.DataFrame, close: pd.Series) -> None:
    """Add *sustained* big-move labels using the forward CLOSE at the horizon.

    The v1 ``big_move_up_*`` labels fire on the intra-window peak (close-to-
    close max), which rewards flash spikes that fully retrace. The sustained
    label requires the price to still be up by the threshold at the END of the
    horizon — a far more realistic target for a multi-week directional buy, and
    the basis for the de-biased Demand Conviction scorer. NaN where the forward
    window is incomplete.
    """
    for horizon, min_gain_pct in BIG_MOVE_SPECS:
        fwd_close = _forward_close(close, horizon)
        close_gain_pct = (fwd_close - close) / close * 100
        col = f"big_move_sustained_{int(min_gain_pct)}pct_{horizon}d"
        labels[col] = (close_gain_pct >= min_gain_pct).astype(float)
        labels.loc[fwd_close.isna(), col] = np.nan


def compute_labels(
    ohlcv: pd.DataFrame,
    support_ema: pd.Series | None = None,
) -> pd.DataFrame:
    """Compute all forward-looking labels for a single ticker.

    Args:
        ohlcv: OHLCV DataFrame sorted by date ascending.
        support_ema: Optional series of support EMA values (8 or 21 EMA).
            Used for pullback recovery labels and CSP strike.
            If None, uses 21-EMA computed from close prices.

    Returns:
        DataFrame aligned with ohlcv index, containing all label columns.
        Rows where labels cannot be computed (insufficient forward data)
        will have NaN values.
    """
    close = ohlcv["close"].astype(float)
    low = ohlcv["low"].astype(float)

    if support_ema is None:
        support_ema = close.ewm(span=21, adjust=False).mean()

    ema_21 = close.ewm(span=21, adjust=False).mean()

    labels = pd.DataFrame(index=ohlcv.index)

    for h in LABEL_HORIZONS:
        fwd_close = _forward_close(close, h)
        fwd_return = (fwd_close - close) / close
        labels[f"forward_return_{h}d"] = fwd_return

        fwd_min = _forward_min_low(low, h)
        labels[f"max_drawdown_{h}d"] = (fwd_min - close) / close

        fwd_max = _forward_max_close(close, h)
        labels[f"max_gain_{h}d"] = (fwd_max - close) / close

        labels[f"direction_{h}d"] = np.where(
            fwd_return > 0.01, 1,
            np.where(fwd_return < -0.01, -1, 0),
        )

    for dte in CSP_DTE_LIST:
        strike = support_ema * (1 - CSP_STRIKE_OFFSET)
        fwd_min = _forward_min_low(low, dte)
        labels[f"csp_win_{dte}d"] = (fwd_min > strike).astype(float)
        labels.loc[fwd_min.isna(), f"csp_win_{dte}d"] = np.nan

    for h in [5, 10]:
        recovered = pd.Series(np.nan, index=ohlcv.index)
        for i in range(len(close) - h):
            fwd_slice = close.iloc[i + 1 : i + 1 + h]
            ema_val = support_ema.iloc[i]
            if pd.notna(ema_val) and len(fwd_slice) == h:
                recovered.iloc[i] = float((fwd_slice > ema_val).any())
        labels[f"pullback_recovery_{h}d"] = recovered

    for h in DEEP_DIP_RECOVERY_HORIZONS:
        recovered = pd.Series(np.nan, index=ohlcv.index)
        for i in range(len(close) - h):
            fwd_slice = close.iloc[i + 1 : i + 1 + h]
            ema_val = ema_21.iloc[i]
            if pd.notna(ema_val) and len(fwd_slice) == h:
                recovered.iloc[i] = float((fwd_slice > ema_val).any())
        labels[f"deep_dip_recovery_{h}d"] = recovered

    for h in RECOVERY_MAGNITUDE_HORIZONS:
        fwd_max = _forward_max_close(close, h)
        labels[f"peak_recovery_pct_{h}d"] = (fwd_max - close) / close * 100
        fwd_close_h = close.shift(-h)
        labels[f"close_return_pct_{h}d"] = (fwd_close_h - close) / close * 100

    first_above = pd.Series(np.nan, index=ohlcv.index, dtype=float)
    n = len(close)
    max_horizon = max(RECOVERY_MAGNITUDE_HORIZONS)
    for i in range(n - 1):
        ema_val = ema_21.iloc[i]
        if pd.isna(ema_val):
            continue
        for j in range(1, min(max_horizon + 1, n - i)):
            if close.iloc[i + j] > ema_val:
                first_above.iloc[i] = float(j)
                break
    labels["days_to_ema_recovery"] = first_above

    _add_big_move_labels(labels, close)
    _add_sustained_big_move_labels(labels, close)

    return labels


def compute_labels_vectorized(
    ohlcv: pd.DataFrame,
    support_ema: pd.Series | None = None,
) -> pd.DataFrame:
    """Faster label computation using pure vectorised ops.

    Pullback recovery is approximated: checks if any close in the forward
    window exceeds the support EMA value at the prediction date.
    """
    close = ohlcv["close"].astype(float)
    low = ohlcv["low"].astype(float)

    if support_ema is None:
        support_ema = close.ewm(span=21, adjust=False).mean()

    ema_21 = close.ewm(span=21, adjust=False).mean()

    labels = pd.DataFrame(index=ohlcv.index)

    for h in LABEL_HORIZONS:
        fwd_close = close.shift(-h)
        fwd_return = (fwd_close - close) / close
        labels[f"forward_return_{h}d"] = fwd_return

        fwd_min = _forward_min_low(low, h)
        labels[f"max_drawdown_{h}d"] = (fwd_min - close) / close

        fwd_max = _forward_max_close(close, h)
        labels[f"max_gain_{h}d"] = (fwd_max - close) / close

        labels[f"direction_{h}d"] = np.where(
            fwd_return > 0.01, 1,
            np.where(fwd_return < -0.01, -1, 0),
        )

    for dte in CSP_DTE_LIST:
        strike = support_ema * (1 - CSP_STRIKE_OFFSET)
        fwd_min = _forward_min_low(low, dte)
        labels[f"csp_win_{dte}d"] = (fwd_min > strike).astype(float)
        labels.loc[fwd_min.isna(), f"csp_win_{dte}d"] = np.nan

    for h in [5, 10]:
        fwd_max_close = _forward_max_close(close, h)
        labels[f"pullback_recovery_{h}d"] = (
            fwd_max_close > support_ema
        ).astype(float)
        labels.loc[fwd_max_close.isna(), f"pullback_recovery_{h}d"] = np.nan

    for h in DEEP_DIP_RECOVERY_HORIZONS:
        fwd_max_close = _forward_max_close(close, h)
        labels[f"deep_dip_recovery_{h}d"] = (
            fwd_max_close > ema_21
        ).astype(float)
        labels.loc[fwd_max_close.isna(), f"deep_dip_recovery_{h}d"] = np.nan

    for h in RECOVERY_MAGNITUDE_HORIZONS:
        fwd_max = _forward_max_close(close, h)
        labels[f"peak_recovery_pct_{h}d"] = (fwd_max - close) / close * 100
        fwd_close_h = close.shift(-h)
        labels[f"close_return_pct_{h}d"] = (fwd_close_h - close) / close * 100

    first_above = pd.Series(np.nan, index=ohlcv.index, dtype=float)
    n = len(close)
    max_horizon = max(RECOVERY_MAGNITUDE_HORIZONS)
    for i in range(n - 1):
        ema_val = ema_21.iloc[i]
        if pd.isna(ema_val):
            continue
        for j in range(1, min(max_horizon + 1, n - i)):
            if close.iloc[i + j] > ema_val:
                first_above.iloc[i] = float(j)
                break
    labels["days_to_ema_recovery"] = first_above

    _add_big_move_labels(labels, close)
    _add_sustained_big_move_labels(labels, close)

    return labels
