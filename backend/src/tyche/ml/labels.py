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
CSP_DTE_LIST: list[int] = [5, 14]
CSP_STRIKE_OFFSET: float = 0.05


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

    return labels
