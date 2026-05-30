"""Vectorized tabular feature extraction from OHLCV + derived metrics.

Computes per-(ticker, date) feature rows across the full history of each
ticker using the same EMA/RSI/slope formulas as ConvictionFeatureEngine,
but applied as rolling pandas operations for efficiency.

The output is a single DataFrame suitable for XGBoost training.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import structlog

from tyche.conviction.features import TrendState

logger = structlog.get_logger()

_TREND_STATE_ORD: dict[str, int] = {
    TrendState.DOWNTREND.value: 0,
    TrendState.INSUFFICIENT_DATA.value: 0,
    TrendState.CONSOLIDATION.value: 1,
    TrendState.UPTREND.value: 2,
    TrendState.PULLBACK_TO_8EMA.value: 3,
    TrendState.PULLBACK_TO_21EMA.value: 4,
    TrendState.STRONG_UPTREND.value: 5,
    TrendState.OVERSOLD_21EMA.value: 6,
    TrendState.OVERSOLD_50EMA.value: 7,
}

FEATURE_COLS: list[str] = [
    "ema_8",
    "ema_21",
    "ema_50",
    "price_to_8ema_pct",
    "price_to_21ema_pct",
    "price_to_50ema_pct",
    "ema_8_slope",
    "ema_21_slope",
    "ema_50_slope",
    "rsi_14",
    "days_above_both_emas",
    "prior_streak",
    "trend_state_ord",
    "volume_ratio",
    "volume_declining",
    "return_1d",
    "return_5d",
    "return_10d",
    "return_20d",
    "volatility_20d",
    "iv_rank",
    "iv_percentile",
    "atm_iv",
    "vrp",
    "rv_20d",
    "log_market_cap",
    "institutional_pct",
    "sector_encoded",
]

NEIGHBOR_FEATURE_COLS: list[str] = [
    "sector_avg_rsi",
    "sector_avg_ema8_slope",
    "sector_avg_ema21_slope",
    "sector_breadth_8ema",
    "sector_breadth_21ema",
    "sector_avg_iv_rank",
    "sector_avg_vrp",
    "sector_avg_return_5d",
    "sector_count",
]

ETF_FEATURE_COLS: list[str] = [
    "etf_membership_count",
    "in_spy",
    "in_qqq",
    "in_dia",
    "spy_weight",
    "qqq_weight",
    "max_etf_weight",
]

CORRELATION_FEATURE_COLS: list[str] = [
    "spy_beta_60d",
    "qqq_beta_60d",
    "top_peer_corr_mean",
    "top_peer_corr_max",
    "top_peer_corr_min",
]

MARKET_CONTEXT_COLS: list[str] = [
    "concurrent_dips",
    "spy_return_5d",
    "spy_return_10d",
    "spy_drawdown_from_high",
    "spy_rsi_14",
    "market_dip_breadth",
]

# Directional momentum / trend-acceleration features for the Alpha engine.
# Computed per ticker in extract_ticker_features(). This is an OPT-IN group:
# it is NOT part of the default get_feature_columns() output, so the deployed
# CSP model's feature set is unchanged.
MOMENTUM_FEATURE_COLS: list[str] = [
    "return_63d",
    "return_126d",
    "return_252d",
    "ema_200",
    "ema_200_slope",
    "price_to_200ema_pct",
    "ema_stack_score",
    "pct_off_52w_high",
    "pct_above_52w_low",
    "breakout_20d",
    "breakout_63d",
    "volume_thrust_ratio",
    "slope_accel",
]

# Relative-strength features (cross-sectional vs SPY). Added by
# add_relative_strength_features(). Part of the opt-in momentum group.
RS_FEATURE_COLS: list[str] = [
    "rs_63d",
    "rs_126d",
    "rs_252d",
]

# NOTE: A MACD-histogram + multi-timeframe (weekly/monthly/quarterly) trend-
# alignment feature group was prototyped and walk-forward validated against the
# big-move targets (see scripts/train_alpha.py ablation, May 2026). It produced
# only noise-level AUC lift on top of the existing momentum/EMA-stack features
# (+0.0003 to +0.0005), i.e. redundant. It was dropped to keep the nightly
# feature pipeline lean. Re-evaluate if/when fundamental or analyst partner
# data is added, since the interaction may differ.


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI as a full series (vectorised Wilder smoothing)."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = np.where(loss == 0, np.inf, gain / loss)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return pd.Series(np.where(np.isinf(rs), 100.0, rsi), index=close.index)


def _slope_series(series: pd.Series, periods: int = 3) -> pd.Series:
    """Rolling linear-regression slope over *periods* bars."""
    x = np.arange(periods, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()

    def _lr_slope(window: np.ndarray) -> float:
        if len(window) < periods:
            return 0.0
        y = window.astype(float)
        if np.std(y) == 0:
            return 0.0
        return float(np.sum((x - x_mean) * (y - y.mean())) / x_var)

    return series.rolling(periods).apply(_lr_slope, raw=True)


def _streak_above(above_mask: pd.Series) -> pd.Series:
    """Count consecutive True values ending at each row."""
    groups = (~above_mask).cumsum()
    return above_mask.groupby(groups).cumsum().astype(int)


def _prior_streak_series(above_both: pd.Series) -> pd.Series:
    """For rows where above_both is False, count the streak of True before.

    For rows where above_both is True, return 0 (not in pullback).
    """
    result = pd.Series(0, index=above_both.index, dtype=int)
    vals = above_both.values
    n = len(vals)
    last_streak_end = -1
    streak_len = 0

    for i in range(n):
        if vals[i]:
            streak_len += 1
            last_streak_end = i
        else:
            if last_streak_end == i - 1 and streak_len > 0:
                result.iloc[i] = streak_len
            elif i > 0 and not vals[i - 1]:
                result.iloc[i] = result.iloc[i - 1]
            streak_len = 0

    return result


def _classify_trend_vec(
    close: pd.Series,
    ema_8: pd.Series,
    ema_21: pd.Series,
    slope_8: pd.Series,
    slope_21: pd.Series,
    pct_to_8: pd.Series,
    pct_to_21: pd.Series,
    proximity_pct: float = 2.0,
    pct_to_50: pd.Series | None = None,
    ema_50: pd.Series | None = None,
    oversold_dip_21: float = 5.0,
    oversold_dip_50: float = 5.0,
) -> pd.Series:
    """Vectorised trend-state classification matching ConvictionFeatureEngine."""
    above_8 = close > ema_8
    above_21 = close > ema_21
    both_slopes_up = (slope_8 > 0) & (slope_21 > 0)

    result = pd.Series(TrendState.CONSOLIDATION.value, index=close.index)

    strong = above_8 & above_21 & both_slopes_up & (pct_to_8 > 1.0)
    result[strong] = TrendState.STRONG_UPTREND.value

    uptrend = above_8 & above_21 & ~strong
    result[uptrend] = TrendState.UPTREND.value

    pullback_8 = above_21 & ~above_8 & (pct_to_8.abs() <= proximity_pct)
    result[pullback_8] = TrendState.PULLBACK_TO_8EMA.value

    pullback_21_above = (
        above_21 & ~above_8 & (pct_to_8.abs() > proximity_pct)
        & (pct_to_21.abs() <= proximity_pct)
    )
    result[pullback_21_above] = TrendState.PULLBACK_TO_21EMA.value

    pullback_21_below = (
        ~above_21 & (pct_to_21.abs() <= proximity_pct) & (slope_21 > 0)
    )
    result[pullback_21_below] = TrendState.PULLBACK_TO_21EMA.value

    below_both = ~above_8 & ~above_21 & ~pullback_21_below

    if ema_50 is not None:
        slope_50_series = _slope_series(ema_50, 3)
        not_chronic = slope_50_series > -0.3
    else:
        not_chronic = pd.Series(False, index=close.index)

    if pct_to_50 is not None and ema_50 is not None:
        above_50 = close > ema_50
        oversold_50 = below_both & ~above_50 & (pct_to_50 <= -oversold_dip_50) & not_chronic
        result[oversold_50] = TrendState.OVERSOLD_50EMA.value
        oversold_21 = below_both & ~oversold_50 & (pct_to_21 <= -oversold_dip_21) & not_chronic
        result[oversold_21] = TrendState.OVERSOLD_21EMA.value
        downtrend = below_both & ~oversold_50 & ~oversold_21
    else:
        oversold_21 = below_both & (pct_to_21 <= -oversold_dip_21) & not_chronic
        result[oversold_21] = TrendState.OVERSOLD_21EMA.value
        downtrend = below_both & ~oversold_21

    result[downtrend] = TrendState.DOWNTREND.value

    return result


def extract_ticker_features(
    ohlcv: pd.DataFrame,
    derived: pd.DataFrame | None = None,
    market_cap: float | None = None,
    institutional_pct: float | None = None,
    sector: str | None = None,
    sector_map: dict[str, int] | None = None,
    min_bars: int = 50,
) -> pd.DataFrame:
    """Extract tabular features for one ticker across its full OHLCV history.

    Args:
        ohlcv: OHLCV DataFrame with columns date, open, high, low, close, volume.
        derived: Optional DerivedMetricsStore DataFrame with date, iv_rank, etc.
        market_cap: Static market cap (from TickerMetaStore).
        institutional_pct: Static institutional ownership %.
        sector: Sector name string.
        sector_map: Mapping of sector name → integer encoding.
        min_bars: Minimum bars needed before producing features.

    Returns:
        DataFrame indexed by date with one row per trading day (after warm-up).
    """
    if len(ohlcv) < min_bars:
        return pd.DataFrame()

    df = ohlcv.copy().sort_values("date").reset_index(drop=True)
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)

    ema_8 = _ema(close, 8)
    ema_21 = _ema(close, 21)
    ema_50 = _ema(close, 50)

    df["ema_8"] = ema_8
    df["ema_21"] = ema_21
    df["ema_50"] = ema_50

    df["price_to_8ema_pct"] = (close - ema_8) / ema_8 * 100
    df["price_to_21ema_pct"] = (close - ema_21) / ema_21 * 100
    df["price_to_50ema_pct"] = (close - ema_50) / ema_50 * 100

    df["ema_8_slope"] = _slope_series(ema_8, 3)
    df["ema_21_slope"] = _slope_series(ema_21, 3)
    df["ema_50_slope"] = _slope_series(ema_50, 3)

    df["rsi_14"] = _rsi_series(close, 14)

    above_both = (close > ema_8) & (close > ema_21)
    df["days_above_both_emas"] = _streak_above(above_both)
    df["prior_streak"] = _prior_streak_series(above_both)

    trend = _classify_trend_vec(
        close, ema_8, ema_21,
        df["ema_8_slope"], df["ema_21_slope"],
        df["price_to_8ema_pct"], df["price_to_21ema_pct"],
        pct_to_50=df["price_to_50ema_pct"],
        ema_50=ema_50,
    )
    df["trend_state"] = trend
    df["trend_state_ord"] = trend.map(_TREND_STATE_ORD).fillna(0).astype(int)

    avg_vol_20 = volume.rolling(20, min_periods=5).mean()
    df["volume_ratio"] = volume / avg_vol_20.replace(0, np.nan)

    prior_avg_vol = volume.rolling(10).mean().shift(5)
    recent_avg_vol = volume.rolling(5).mean()
    df["volume_declining"] = (
        (recent_avg_vol < prior_avg_vol) & (close < ema_8)
    ).astype(int)

    df["return_1d"] = close.pct_change(1)
    df["return_5d"] = close.pct_change(5)
    df["return_10d"] = close.pct_change(10)
    df["return_20d"] = close.pct_change(20)

    log_returns = np.log(close / close.shift(1))
    df["volatility_20d"] = log_returns.rolling(20).std() * np.sqrt(252)

    # --- Momentum / trend-acceleration (opt-in MOMENTUM_FEATURE_COLS) ---
    df["return_63d"] = close.pct_change(63)
    df["return_126d"] = close.pct_change(126)
    df["return_252d"] = close.pct_change(252)

    ema_200 = _ema(close, 200)
    df["ema_200"] = ema_200
    df["ema_200_slope"] = _slope_series(ema_200, 5)
    df["price_to_200ema_pct"] = (close - ema_200) / ema_200.replace(0, np.nan) * 100

    df["ema_stack_score"] = (
        (ema_8 > ema_21).astype(int)
        + (ema_21 > ema_50).astype(int)
        + (ema_50 > ema_200).astype(int)
    )

    roll_max_252 = close.rolling(252, min_periods=60).max()
    roll_min_252 = close.rolling(252, min_periods=60).min()
    df["pct_off_52w_high"] = (close - roll_max_252) / roll_max_252 * 100
    df["pct_above_52w_low"] = (close - roll_min_252) / roll_min_252.replace(0, np.nan) * 100

    prior_max_20 = close.shift(1).rolling(20, min_periods=20).max()
    prior_max_63 = close.shift(1).rolling(63, min_periods=63).max()
    df["breakout_20d"] = (close >= prior_max_20).astype(int)
    df["breakout_63d"] = (close >= prior_max_63).astype(int)

    vol_5 = volume.rolling(5, min_periods=1).mean()
    vol_50 = volume.rolling(50, min_periods=10).mean()
    df["volume_thrust_ratio"] = vol_5 / vol_50.replace(0, np.nan)

    df["slope_accel"] = df["ema_8_slope"] - df["ema_21_slope"]

    if derived is not None and not derived.empty:
        derived_clean = derived.copy()
        derived_clean["date"] = pd.to_datetime(derived_clean["date"]).dt.date
        df["date_key"] = pd.to_datetime(df["date"]).dt.date
        derived_clean = derived_clean.rename(columns={"date": "date_key"})
        iv_cols = ["date_key", "iv_rank", "iv_percentile", "atm_iv", "vrp", "rv_20d"]
        available = [c for c in iv_cols if c in derived_clean.columns]
        df = df.merge(derived_clean[available], on="date_key", how="left")
        df.drop(columns=["date_key"], inplace=True, errors="ignore")
    else:
        for col in ["iv_rank", "iv_percentile", "atm_iv", "vrp", "rv_20d"]:
            if col not in df.columns:
                df[col] = np.nan

    df["log_market_cap"] = np.log1p(market_cap) if market_cap and market_cap > 0 else np.nan
    df["institutional_pct"] = institutional_pct if institutional_pct else np.nan

    sector_code = 0
    if sector and sector_map:
        sector_code = sector_map.get(sector, 0)
    df["sector_encoded"] = sector_code

    df = df.iloc[min_bars:].reset_index(drop=True)

    return df


def build_sector_map(sectors: dict[str, str]) -> dict[str, int]:
    """Build a deterministic sector-name → integer mapping."""
    unique = sorted(set(s for s in sectors.values() if s))
    return {name: i + 1 for i, name in enumerate(unique)}


def add_etf_features(
    all_features: pd.DataFrame,
    etf_store=None,
) -> pd.DataFrame:
    """Augment per-ticker feature rows with ETF membership features.

    Adds columns for ETF membership count, binary membership in key ETFs,
    and weight in SPY/QQQ where available.
    """
    if all_features.empty or etf_store is None:
        for col in ETF_FEATURE_COLS:
            if col not in all_features.columns:
                all_features[col] = 0.0
        return all_features

    df = all_features.copy()

    membership_counts = etf_store.get_membership_counts()
    membership_matrix = etf_store.get_membership_matrix()
    spy_weights = etf_store.get_etf_weights("SPY")
    qqq_weights = etf_store.get_etf_weights("QQQ")

    if "ticker" not in df.columns:
        for col in ETF_FEATURE_COLS:
            df[col] = 0.0
        return df

    df["etf_membership_count"] = df["ticker"].map(membership_counts).fillna(0).astype(int)
    df["in_spy"] = df["ticker"].map(
        lambda t: 1 if "SPY" in membership_matrix.get(t, []) else 0
    )
    df["in_qqq"] = df["ticker"].map(
        lambda t: 1 if "QQQ" in membership_matrix.get(t, []) else 0
    )
    df["in_dia"] = df["ticker"].map(
        lambda t: 1 if "DIA" in membership_matrix.get(t, []) else 0
    )
    df["spy_weight"] = df["ticker"].map(spy_weights).fillna(0.0)
    df["qqq_weight"] = df["ticker"].map(qqq_weights).fillna(0.0)

    all_etf_weights: dict[str, float] = {}
    for etf_ticker in ["SPY", "QQQ", "DIA", "XLK", "XLF", "XLE", "XLV", "SMH", "SOXX", "XLI"]:
        weights = etf_store.get_etf_weights(etf_ticker)
        for ticker, w in weights.items():
            if w is not None and (ticker not in all_etf_weights or w > all_etf_weights[ticker]):
                all_etf_weights[ticker] = w

    df["max_etf_weight"] = df["ticker"].map(all_etf_weights).fillna(0.0)

    return df


def add_correlation_features(
    all_features: pd.DataFrame,
    correlation_store=None,
    as_of_date=None,
) -> pd.DataFrame:
    """Augment per-ticker feature rows with correlation/beta features.

    Adds SPY/QQQ betas and top-peer correlation statistics.
    """
    if all_features.empty or correlation_store is None:
        for col in CORRELATION_FEATURE_COLS:
            if col not in all_features.columns:
                all_features[col] = np.nan
        return all_features

    df = all_features.copy()

    betas_df = correlation_store.read_betas(as_of=as_of_date)
    corr_df = correlation_store.read_correlations(as_of=as_of_date)

    if "ticker" not in df.columns:
        for col in CORRELATION_FEATURE_COLS:
            df[col] = np.nan
        return df

    if not betas_df.empty:
        beta_lookup = betas_df.set_index("ticker")[["spy_beta_60d", "qqq_beta_60d"]].to_dict("index")
        df["spy_beta_60d"] = df["ticker"].map(
            lambda t: beta_lookup.get(t, {}).get("spy_beta_60d", np.nan)
        )
        df["qqq_beta_60d"] = df["ticker"].map(
            lambda t: beta_lookup.get(t, {}).get("qqq_beta_60d", np.nan)
        )
    else:
        df["spy_beta_60d"] = np.nan
        df["qqq_beta_60d"] = np.nan

    if not corr_df.empty:
        peer_stats: dict[str, dict[str, float]] = {}
        for ticker in df["ticker"].unique():
            mask = (corr_df["ticker_a"] == ticker) | (corr_df["ticker_b"] == ticker)
            sub = corr_df[mask]["correlation_60d"]
            if len(sub) > 0:
                peer_stats[ticker] = {
                    "mean": float(sub.mean()),
                    "max": float(sub.max()),
                    "min": float(sub.min()),
                }

        df["top_peer_corr_mean"] = df["ticker"].map(
            lambda t: peer_stats.get(t, {}).get("mean", np.nan)
        )
        df["top_peer_corr_max"] = df["ticker"].map(
            lambda t: peer_stats.get(t, {}).get("max", np.nan)
        )
        df["top_peer_corr_min"] = df["ticker"].map(
            lambda t: peer_stats.get(t, {}).get("min", np.nan)
        )
    else:
        df["top_peer_corr_mean"] = np.nan
        df["top_peer_corr_max"] = np.nan
        df["top_peer_corr_min"] = np.nan

    return df


def add_market_context_features(
    all_features: pd.DataFrame,
    spy_ohlcv: pd.DataFrame | None = None,
    dip_threshold_pct: float = -5.0,
    date_col: str = "date",
) -> pd.DataFrame:
    """Add market-wide context features: concurrent dip count and SPY state.

    These are cross-sectional features computed per date across all tickers.
    They capture whether a dip is stock-specific or part of a broad selloff,
    which is the strongest predictor of recovery probability.

    Args:
        all_features: Full dataset with all tickers and dates.
        spy_ohlcv: SPY OHLCV DataFrame (date, close, high, low).
        dip_threshold_pct: price_to_21ema_pct threshold to count as "dipping".
        date_col: Name of the date column.
    """
    if all_features.empty:
        for col in MARKET_CONTEXT_COLS:
            if col not in all_features.columns:
                all_features[col] = np.nan
        return all_features

    df = all_features.copy()

    is_dipping = df["price_to_21ema_pct"] <= dip_threshold_pct
    df["_is_dipping"] = is_dipping.astype(int)

    date_aggs = df.groupby(date_col).agg(
        concurrent_dips=("_is_dipping", "sum"),
        _total_tickers=("_is_dipping", "count"),
    ).reset_index()
    date_aggs["market_dip_breadth"] = date_aggs["concurrent_dips"] / date_aggs["_total_tickers"]
    date_aggs.drop(columns=["_total_tickers"], inplace=True)

    df = df.merge(date_aggs, on=date_col, how="left")
    df.drop(columns=["_is_dipping"], inplace=True)

    if spy_ohlcv is not None and not spy_ohlcv.empty:
        spy = spy_ohlcv.copy().sort_values("date").reset_index(drop=True)
        spy_close = spy["close"].astype(float)

        spy["spy_return_5d"] = spy_close.pct_change(5)
        spy["spy_return_10d"] = spy_close.pct_change(10)

        rolling_high = spy_close.rolling(50, min_periods=10).max()
        spy["spy_drawdown_from_high"] = (spy_close - rolling_high) / rolling_high

        spy["spy_rsi_14"] = _rsi_series(spy_close, 14)

        spy["date_key"] = pd.to_datetime(spy["date"]).dt.date
        df["date_key"] = pd.to_datetime(df[date_col]).dt.date

        spy_cols = spy[["date_key", "spy_return_5d", "spy_return_10d",
                        "spy_drawdown_from_high", "spy_rsi_14"]].drop_duplicates("date_key")
        df = df.merge(spy_cols, on="date_key", how="left")
        df.drop(columns=["date_key"], inplace=True)
    else:
        for col in ["spy_return_5d", "spy_return_10d", "spy_drawdown_from_high", "spy_rsi_14"]:
            if col not in df.columns:
                df[col] = np.nan

    return df


def add_relative_strength_features(
    all_features: pd.DataFrame,
    spy_ohlcv: pd.DataFrame | None = None,
    date_col: str = "date",
) -> pd.DataFrame:
    """Add relative-strength features: ticker return minus SPY return.

    Relative strength is one of the strongest directional signals — names
    that outperform the benchmark over 3/6/12 months tend to continue. Excess
    return is computed per date against SPY's same-window return.

    Requires ``return_63d``/``return_126d``/``return_252d`` (from the momentum
    feature group). Defaults to NaN when SPY data is unavailable.
    """
    if all_features.empty:
        for col in RS_FEATURE_COLS:
            if col not in all_features.columns:
                all_features[col] = np.nan
        return all_features

    df = all_features.copy()

    if spy_ohlcv is None or spy_ohlcv.empty or "return_63d" not in df.columns:
        for col in RS_FEATURE_COLS:
            if col not in df.columns:
                df[col] = np.nan
        return df

    spy = spy_ohlcv.copy().sort_values("date").reset_index(drop=True)
    spy_close = spy["close"].astype(float)
    spy["spy_ret_63"] = spy_close.pct_change(63)
    spy["spy_ret_126"] = spy_close.pct_change(126)
    spy["spy_ret_252"] = spy_close.pct_change(252)
    spy["date_key"] = pd.to_datetime(spy["date"]).dt.date

    df["date_key"] = pd.to_datetime(df[date_col]).dt.date
    sub = spy[["date_key", "spy_ret_63", "spy_ret_126", "spy_ret_252"]].drop_duplicates("date_key")
    df = df.merge(sub, on="date_key", how="left")

    df["rs_63d"] = df["return_63d"] - df["spy_ret_63"]
    df["rs_126d"] = df["return_126d"] - df["spy_ret_126"]
    df["rs_252d"] = df["return_252d"] - df["spy_ret_252"]

    df.drop(columns=["date_key", "spy_ret_63", "spy_ret_126", "spy_ret_252"], inplace=True)

    return df


def add_neighbor_features(
    all_features: pd.DataFrame,
    sector_col: str = "sector_encoded",
    date_col: str = "date",
) -> pd.DataFrame:
    """Augment per-ticker feature rows with sector-aggregated neighbor features.

    Groups by (date, sector) and computes cross-sectional aggregates.
    """
    if all_features.empty:
        return all_features

    df = all_features.copy()

    grouped = df.groupby([date_col, sector_col])

    aggs = grouped.agg(
        sector_avg_rsi=("rsi_14", "mean"),
        sector_avg_ema8_slope=("ema_8_slope", "mean"),
        sector_avg_ema21_slope=("ema_21_slope", "mean"),
        sector_avg_return_5d=("return_5d", "mean"),
        sector_count=("rsi_14", "count"),
    ).reset_index()

    above_8 = df["price_to_8ema_pct"] > 0
    above_21 = df["price_to_21ema_pct"] > 0
    df["_above_8"] = above_8.astype(float)
    df["_above_21"] = above_21.astype(float)

    breadth = df.groupby([date_col, sector_col]).agg(
        sector_breadth_8ema=("_above_8", "mean"),
        sector_breadth_21ema=("_above_21", "mean"),
    ).reset_index()

    iv_aggs = df.groupby([date_col, sector_col]).agg(
        sector_avg_iv_rank=("iv_rank", "mean"),
        sector_avg_vrp=("vrp", "mean"),
    ).reset_index()

    aggs = aggs.merge(breadth, on=[date_col, sector_col], how="left")
    aggs = aggs.merge(iv_aggs, on=[date_col, sector_col], how="left")

    df = df.merge(aggs, on=[date_col, sector_col], how="left")
    df.drop(columns=["_above_8", "_above_21"], inplace=True)

    return df
