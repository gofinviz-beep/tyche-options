"""Vectorized tabular feature extraction from OHLCV + derived metrics.

Computes per-(ticker, date) feature rows across the full history of each
ticker using the same EMA/RSI/slope formulas as ConvictionFeatureEngine,
but applied as rolling pandas operations for efficiency.

The output is a single DataFrame suitable for XGBoost training.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd
import structlog

from tyche.conviction.features import TrendState

logger = structlog.get_logger()

# Catalyst recency-weighting (mirrors CatalystSignalStore.aggregate).
_LN2 = math.log(2)
_HALF_LIFE_DAYS = 30.0
_CATALYST_LOOKBACK_DAYS = 180

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

# --- Demand Conviction (Directional Alpha v2) feature groups --------------
# All opt-in; absent from the default get_feature_columns() output so the
# deployed CSP model is unchanged. Each group degrades to NaN/0 defaults when
# its source store is unavailable.

# Anti-chase / over-extension (D-TECH). Computed in extract_ticker_features()
# from OHLCV only — no external data needed. Higher overextension_score = more
# stretched (the engine penalises it, so we don't chase parabolic names).
ANTI_CHASE_FEATURE_COLS: list[str] = [
    "overextension_score",
    "rsi_overbought",
    "parabolic_21d",
    "dist_above_200ema_capped",
]

# Fundamentals (D-FUND). Point-in-time quarterly growth/margins/cash/dilution.
# Added by add_fundamental_features() from FundamentalsStore (merge_asof on
# filing_date — leakage-safe).
FUNDAMENTAL_FEATURE_COLS: list[str] = [
    "f_rev_growth_yoy",
    "f_rev_growth_qoq",
    "f_rev_accel",
    "f_gross_margin",
    "f_gross_margin_trend",
    "f_operating_margin",
    "f_eps_growth_yoy",
    "f_fcf_margin",
    "f_fcf_positive",
    "f_share_growth_yoy",
    "f_quarters_since_filing",
]

# Estimates / revisions / surprises (D-EST). Point-in-time analyst consensus.
# Added by add_estimate_features() from EstimatesStore.
ESTIMATE_FEATURE_COLS: list[str] = [
    "e_eps_revision_90d",
    "e_rev_revision_90d",
    "e_rec_score",
    "e_rec_score_trend_90d",
    "e_eps_surprise_last",
    "e_eps_surprise_avg4",
    "e_price_target_upside",
]

# Short interest / squeeze pressure (D-TECH). Added by
# add_short_interest_features() from ShortInterestStore.
SHORT_INTEREST_FEATURE_COLS: list[str] = [
    "si_days_to_cover",
    "si_ratio",
    "si_pct_float",
    "si_change_pct",
]

# Demand catalysts + policy (D-CAT / D-POL). Added by add_catalyst_features()
# from CatalystSignalStore (news/8-K-derived) blended with the structural
# PolicyEventCalendar. All default to 0 when no signal exists.
CATALYST_FEATURE_COLS: list[str] = [
    "cat_demand_score",
    "cat_policy_score",
    "cat_count_90d",
    "cat_recency_days",
]

# Supply-chain demand propagation (D-GRAPH). Added by add_graph_features() from
# the curated SupplyChainGraph — for each supplier, the (same-date) demand of
# its upstream customers is the leading indicator. All default to 0 when the
# ticker has no upstream customers in the graph.
GRAPH_FEATURE_COLS: list[str] = [
    "graph_customer_mom",
    "graph_customer_catalyst",
    "graph_customer_est_rev",
    "graph_demand_propagation",
    "graph_customer_count",
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
        institutional_pct: Static institutional ownership as a 0-1 fraction.
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

    # --- Anti-chase / over-extension (ANTI_CHASE_FEATURE_COLS) ----------
    # These flag parabolic, already-run setups so the engine can penalise them
    # rather than chase. RSI overbought ramps 70->100; distance above the
    # 200-EMA is capped (a stock 200% above its 200-EMA is extreme); the 21d
    # run captures recent parabolic acceleration.
    df["rsi_overbought"] = ((df["rsi_14"] - 70.0).clip(lower=0.0) / 30.0).clip(upper=1.0)
    df["parabolic_21d"] = close.pct_change(21)
    dist_200 = df["price_to_200ema_pct"].clip(lower=0.0)
    df["dist_above_200ema_capped"] = (dist_200 / 100.0).clip(upper=2.0)
    # Composite 0..1 over-extension score: blends the three. ~0.6 weight on the
    # parabolic run, the dominant tell of an unsustainable spike.
    parabolic_ramp = (df["parabolic_21d"] / 0.50).clip(lower=0.0, upper=1.0)
    dist_ramp = (df["dist_above_200ema_capped"] / 1.0).clip(lower=0.0, upper=1.0)
    df["overextension_score"] = (
        0.5 * parabolic_ramp + 0.3 * df["rsi_overbought"] + 0.2 * dist_ramp
    ).clip(lower=0.0, upper=1.0)

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

    membership_counts = etf_store.get_membership_counts()
    membership_matrix = etf_store.get_membership_matrix()
    spy_weights = etf_store.get_etf_weights("SPY")
    qqq_weights = etf_store.get_etf_weights("QQQ")

    if "ticker" not in all_features.columns:
        for col in ETF_FEATURE_COLS:
            all_features[col] = 0.0
        return all_features

    all_features["etf_membership_count"] = (
        all_features["ticker"].map(membership_counts).fillna(0).astype(int)
    )
    all_features["in_spy"] = all_features["ticker"].map(
        lambda t: 1 if "SPY" in membership_matrix.get(t, []) else 0
    )
    all_features["in_qqq"] = all_features["ticker"].map(
        lambda t: 1 if "QQQ" in membership_matrix.get(t, []) else 0
    )
    all_features["in_dia"] = all_features["ticker"].map(
        lambda t: 1 if "DIA" in membership_matrix.get(t, []) else 0
    )
    all_features["spy_weight"] = all_features["ticker"].map(spy_weights).fillna(0.0)
    all_features["qqq_weight"] = all_features["ticker"].map(qqq_weights).fillna(0.0)

    all_etf_weights: dict[str, float] = {}
    for etf_ticker in ["SPY", "QQQ", "DIA", "XLK", "XLF", "XLE", "XLV", "SMH", "SOXX", "XLI"]:
        weights = etf_store.get_etf_weights(etf_ticker)
        for ticker, w in weights.items():
            if w is not None and (ticker not in all_etf_weights or w > all_etf_weights[ticker]):
                all_etf_weights[ticker] = w

    all_features["max_etf_weight"] = (
        all_features["ticker"].map(all_etf_weights).fillna(0.0)
    )

    return all_features


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

    betas_df = correlation_store.read_betas(as_of=as_of_date)
    corr_df = correlation_store.read_correlations(as_of=as_of_date)

    if "ticker" not in all_features.columns:
        for col in CORRELATION_FEATURE_COLS:
            all_features[col] = np.nan
        return all_features

    if not betas_df.empty:
        beta_lookup = betas_df.set_index("ticker")[["spy_beta_60d", "qqq_beta_60d"]].to_dict("index")
        all_features["spy_beta_60d"] = all_features["ticker"].map(
            lambda t: beta_lookup.get(t, {}).get("spy_beta_60d", np.nan)
        )
        all_features["qqq_beta_60d"] = all_features["ticker"].map(
            lambda t: beta_lookup.get(t, {}).get("qqq_beta_60d", np.nan)
        )
    else:
        all_features["spy_beta_60d"] = np.nan
        all_features["qqq_beta_60d"] = np.nan

    if not corr_df.empty:
        peer_stats: dict[str, dict[str, float]] = {}
        for ticker in all_features["ticker"].unique():
            mask = (corr_df["ticker_a"] == ticker) | (corr_df["ticker_b"] == ticker)
            sub = corr_df[mask]["correlation_60d"]
            if len(sub) > 0:
                peer_stats[ticker] = {
                    "mean": float(sub.mean()),
                    "max": float(sub.max()),
                    "min": float(sub.min()),
                }

        all_features["top_peer_corr_mean"] = all_features["ticker"].map(
            lambda t: peer_stats.get(t, {}).get("mean", np.nan)
        )
        all_features["top_peer_corr_max"] = all_features["ticker"].map(
            lambda t: peer_stats.get(t, {}).get("max", np.nan)
        )
        all_features["top_peer_corr_min"] = all_features["ticker"].map(
            lambda t: peer_stats.get(t, {}).get("min", np.nan)
        )
    else:
        all_features["top_peer_corr_mean"] = np.nan
        all_features["top_peer_corr_max"] = np.nan
        all_features["top_peer_corr_min"] = np.nan

    return all_features


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

    is_dipping = all_features["price_to_21ema_pct"] <= dip_threshold_pct
    dip_frame = all_features[[date_col]].copy()
    dip_frame["_is_dipping"] = is_dipping.astype(int)
    date_aggs = dip_frame.groupby(date_col).agg(
        concurrent_dips=("_is_dipping", "sum"),
        _total_tickers=("_is_dipping", "count"),
    ).reset_index()
    date_aggs["market_dip_breadth"] = (
        date_aggs["concurrent_dips"] / date_aggs["_total_tickers"]
    )
    date_aggs.drop(columns=["_total_tickers"], inplace=True)
    _merge_assign_inplace(
        all_features,
        date_aggs,
        on=date_col,
        cols=["concurrent_dips", "market_dip_breadth"],
    )

    if spy_ohlcv is not None and not spy_ohlcv.empty:
        spy = spy_ohlcv.copy().sort_values("date").reset_index(drop=True)
        spy_close = spy["close"].astype(float)

        spy["spy_return_5d"] = spy_close.pct_change(5)
        spy["spy_return_10d"] = spy_close.pct_change(10)

        rolling_high = spy_close.rolling(50, min_periods=10).max()
        spy["spy_drawdown_from_high"] = (spy_close - rolling_high) / rolling_high

        spy["spy_rsi_14"] = _rsi_series(spy_close, 14)

        spy["date_key"] = pd.to_datetime(spy["date"]).dt.date
        all_features["date_key"] = pd.to_datetime(all_features[date_col]).dt.date

        spy_cols = spy[
            ["date_key", "spy_return_5d", "spy_return_10d", "spy_drawdown_from_high", "spy_rsi_14"]
        ].drop_duplicates("date_key")
        _merge_assign_inplace(
            all_features,
            spy_cols,
            on="date_key",
            cols=["spy_return_5d", "spy_return_10d", "spy_drawdown_from_high", "spy_rsi_14"],
        )
        all_features.drop(columns=["date_key"], inplace=True, errors="ignore")
    else:
        for col in ["spy_return_5d", "spy_return_10d", "spy_drawdown_from_high", "spy_rsi_14"]:
            if col not in all_features.columns:
                all_features[col] = np.nan

    return all_features


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

    if spy_ohlcv is None or spy_ohlcv.empty or "return_63d" not in all_features.columns:
        for col in RS_FEATURE_COLS:
            if col not in all_features.columns:
                all_features[col] = np.nan
        return all_features

    spy = spy_ohlcv.copy().sort_values("date").reset_index(drop=True)
    spy_close = spy["close"].astype(float)
    spy["spy_ret_63"] = spy_close.pct_change(63)
    spy["spy_ret_126"] = spy_close.pct_change(126)
    spy["spy_ret_252"] = spy_close.pct_change(252)
    spy["date_key"] = pd.to_datetime(spy["date"]).dt.date

    all_features["date_key"] = pd.to_datetime(all_features[date_col]).dt.date
    sub = spy[["date_key", "spy_ret_63", "spy_ret_126", "spy_ret_252"]].drop_duplicates("date_key")
    _merge_assign_inplace(
        all_features,
        sub,
        on="date_key",
        cols=["spy_ret_63", "spy_ret_126", "spy_ret_252"],
    )

    all_features["rs_63d"] = all_features["return_63d"] - all_features["spy_ret_63"]
    all_features["rs_126d"] = all_features["return_126d"] - all_features["spy_ret_126"]
    all_features["rs_252d"] = all_features["return_252d"] - all_features["spy_ret_252"]

    all_features.drop(
        columns=["date_key", "spy_ret_63", "spy_ret_126", "spy_ret_252"],
        inplace=True,
        errors="ignore",
    )

    return all_features


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

    keys = [date_col, sector_col]
    grouped = all_features.groupby(keys)

    aggs = grouped.agg(
        sector_avg_rsi=("rsi_14", "mean"),
        sector_avg_ema8_slope=("ema_8_slope", "mean"),
        sector_avg_ema21_slope=("ema_21_slope", "mean"),
        sector_avg_return_5d=("return_5d", "mean"),
        sector_count=("rsi_14", "count"),
    ).reset_index()

    above_8 = (all_features["price_to_8ema_pct"] > 0).astype(float)
    above_21 = (all_features["price_to_21ema_pct"] > 0).astype(float)
    breadth_src = pd.DataFrame(
        {
            date_col: all_features[date_col],
            sector_col: all_features[sector_col],
            "_above_8": above_8,
            "_above_21": above_21,
        }
    )
    breadth = breadth_src.groupby(keys).agg(
        sector_breadth_8ema=("_above_8", "mean"),
        sector_breadth_21ema=("_above_21", "mean"),
    ).reset_index()

    iv_aggs = grouped.agg(
        sector_avg_iv_rank=("iv_rank", "mean"),
        sector_avg_vrp=("vrp", "mean"),
    ).reset_index()

    aggs = aggs.merge(breadth, on=keys, how="left")
    aggs = aggs.merge(iv_aggs, on=keys, how="left")
    _merge_assign_inplace(all_features, aggs, on=keys, cols=NEIGHBOR_FEATURE_COLS)

    return all_features


def _safe_growth(curr: pd.Series, prev: pd.Series) -> pd.Series:
    """YoY/QoQ growth ratio, NaN when the prior base is non-positive."""
    base = prev.where(prev > 0)
    return curr / base - 1.0


def _fill_defaults(df: pd.DataFrame, cols: list[str], value=np.nan) -> pd.DataFrame:
    for col in cols:
        if col not in df.columns:
            df[col] = value
    return df


def _merge_assign_inplace(
    df: pd.DataFrame,
    right: pd.DataFrame,
    on: str | list[str],
    cols: list[str],
) -> None:
    """Left-join *cols* from *right* onto *df* without copying the full frame."""
    keys = [on] if isinstance(on, str) else list(on)
    merged = df[keys].merge(right[keys + cols], on=keys, how="left")
    for col in cols:
        df[col] = merged[col].to_numpy()


def add_fundamental_features(
    all_features: pd.DataFrame,
    fundamentals_store=None,
    date_col: str = "date",
) -> pd.DataFrame:
    """Augment feature rows with point-in-time quarterly fundamentals (D-FUND).

    For each ticker, builds quarterly growth/margin/dilution series from
    ``FundamentalsStore`` and joins them onto each feature date with
    ``merge_asof`` keyed on ``filing_date`` (so only already-filed statements
    are visible — leakage-safe). Defaults to NaN when no data exists.
    """
    if all_features.empty or fundamentals_store is None or "ticker" not in all_features.columns:
        return _fill_defaults(all_features, FUNDAMENTAL_FEATURE_COLS)

    _fill_defaults(all_features, FUNDAMENTAL_FEATURE_COLS)
    date_ns = pd.to_datetime(all_features[date_col]).astype("datetime64[ns]")
    assign_cols = FUNDAMENTAL_FEATURE_COLS + ["f_quarters_since_filing"]

    for ticker, group in all_features.groupby("ticker", sort=False):
        idx = group.index
        sorted_idx = date_ns.loc[idx].sort_values(kind="stable").index
        left = pd.DataFrame({"_date": date_ns.loc[sorted_idx].to_numpy()})

        fund = fundamentals_store.read_ticker(ticker, timeframe="quarterly")
        if fund is None or fund.empty:
            continue

        f = fund.sort_values("period_end").reset_index(drop=True)
        f["filing_dt"] = pd.to_datetime(f["filing_date"]).astype("datetime64[ns]")
        rev = f["revenue"]
        eps = f["eps_diluted"]
        shares = f["shares_diluted"]

        derived = pd.DataFrame({"filing_dt": f["filing_dt"]})
        derived["f_rev_growth_yoy"] = _safe_growth(rev, rev.shift(4))
        derived["f_rev_growth_qoq"] = _safe_growth(rev, rev.shift(1))
        prior_yoy = _safe_growth(rev.shift(1), rev.shift(5))
        derived["f_rev_accel"] = derived["f_rev_growth_yoy"] - prior_yoy
        derived["f_gross_margin"] = f["gross_margin"]
        derived["f_gross_margin_trend"] = f["gross_margin"] - f["gross_margin"].shift(4)
        derived["f_operating_margin"] = f["operating_margin"]
        derived["f_eps_growth_yoy"] = _safe_growth(eps, eps.shift(4))
        derived["f_fcf_margin"] = np.where(
            rev > 0, f["free_cash_flow"] / rev * 100.0, np.nan
        )
        derived["f_fcf_positive"] = (f["free_cash_flow"] > 0).astype(float)
        derived["f_share_growth_yoy"] = _safe_growth(shares, shares.shift(4))
        derived = derived.dropna(subset=["filing_dt"]).sort_values("filing_dt")

        merged = pd.merge_asof(
            left,
            derived,
            left_on="_date",
            right_on="filing_dt",
            direction="backward",
        )
        merged["f_quarters_since_filing"] = (
            (merged["_date"] - merged["filing_dt"]).dt.days / 91.0
        )
        for col in assign_cols:
            if col in merged.columns:
                all_features.loc[sorted_idx, col] = merged[col].to_numpy()

    return _fill_defaults(all_features, FUNDAMENTAL_FEATURE_COLS)


def _rec_score_from_counts(row: pd.Series) -> float:
    """Weighted analyst recommendation score in [-1, 1] (NaN if no coverage)."""
    sb = row.get("rec_strong_buy", 0) or 0
    b = row.get("rec_buy", 0) or 0
    h = row.get("rec_hold", 0) or 0
    s = row.get("rec_sell", 0) or 0
    ss = row.get("rec_strong_sell", 0) or 0
    total = sb + b + h + s + ss
    if total <= 0:
        return np.nan
    return (2 * sb + b - s - 2 * ss) / (2 * total)


def add_estimate_features(
    all_features: pd.DataFrame,
    estimates_store=None,
    date_col: str = "date",
) -> pd.DataFrame:
    """Augment feature rows with analyst estimate/revision/surprise demand (D-EST).

    Uses ``EstimatesStore`` (tidy long format). For each (ticker, feature date)
    computes consensus revisions (front-period EPS/revenue change over ~90d),
    recommendation score + its 90d trend, the latest and 4-quarter-average EPS
    surprise, and price-target upside. All point-in-time (snapshot_date <= date).
    """
    if all_features.empty or estimates_store is None or "ticker" not in all_features.columns:
        return _fill_defaults(all_features, ESTIMATE_FEATURE_COLS)

    _fill_defaults(all_features, ESTIMATE_FEATURE_COLS)

    for ticker, group in all_features.groupby("ticker", sort=False):
        idx = group.index
        dates = pd.to_datetime(all_features.loc[idx, date_col])
        raw = estimates_store.read_ticker(ticker)
        if raw is None or raw.empty:
            continue

        raw = raw.copy()
        raw["snap_dt"] = pd.to_datetime(raw["snapshot_date"])

        # Build a per-snapshot-date time series of the metrics we diff/score.
        front_eps = _front_period_series(raw, "eps_est_avg")
        front_rev = _front_period_series(raw, "rev_est_avg")
        rec_series = _recommendation_score_series(raw)
        pt_mean = _front_period_series(raw, "price_target_mean", use_period=False)
        surprise = (
            raw[raw["metric"] == "eps_surprise_pct"][["snap_dt", "value"]]
            .sort_values("snap_dt")
        )

        dates_90 = dates - pd.Timedelta(days=90)

        eps_now = _asof_col(front_eps, dates)
        eps_90 = _asof_col(front_eps, dates_90)
        rev_now = _asof_col(front_rev, dates)
        rev_90 = _asof_col(front_rev, dates_90)
        rec_now = _asof_col(rec_series, dates)
        rec_90 = _asof_col(rec_series, dates_90)
        pt = _asof_col(pt_mean, dates)

        if not surprise.empty:
            surp = surprise.copy()
            surp_last_series = surp[["snap_dt", "value"]]
            surp_avg4_series = surp.assign(
                value=surp["value"].rolling(4, min_periods=1).mean()
            )[["snap_dt", "value"]]
            surp_last = _asof_col(surp_last_series, dates)
            surp_avg4 = _asof_col(surp_avg4_series, dates)
        else:
            surp_last = np.full(len(idx), np.nan)
            surp_avg4 = np.full(len(idx), np.nan)

        if "close" in all_features.columns:
            close = pd.to_numeric(all_features.loc[idx, "close"], errors="coerce").to_numpy()
        else:
            close = np.full(len(idx), np.nan)

        with np.errstate(divide="ignore", invalid="ignore"):
            pt_upside = np.where(
                (~np.isnan(pt)) & (close > 0), (pt / close - 1.0) * 100.0, np.nan
            )

        all_features.loc[idx, "e_eps_revision_90d"] = _pct_change_arr(eps_now, eps_90)
        all_features.loc[idx, "e_rev_revision_90d"] = _pct_change_arr(rev_now, rev_90)
        all_features.loc[idx, "e_rec_score"] = rec_now
        all_features.loc[idx, "e_rec_score_trend_90d"] = rec_now - rec_90
        all_features.loc[idx, "e_eps_surprise_last"] = surp_last
        all_features.loc[idx, "e_eps_surprise_avg4"] = surp_avg4
        all_features.loc[idx, "e_price_target_upside"] = pt_upside

    return _fill_defaults(all_features, ESTIMATE_FEATURE_COLS)


def _asof_col(series_df: pd.DataFrame, when: pd.Series) -> np.ndarray:
    """Backward as-of lookup of a ``(snap_dt, value)`` frame for each timestamp
    in *when*. Returns a float array aligned to *when*'s original order
    (``NaN`` where no snapshot is at/before the timestamp)."""
    n = len(when)
    if series_df is None or series_df.empty:
        return np.full(n, np.nan)
    # Normalise both keys to ns resolution — merge_asof requires identical units.
    left_k = pd.to_datetime(when).to_numpy().astype("datetime64[ns]")
    left = pd.DataFrame({"_k": left_k, "_ord": np.arange(n)}).sort_values(
        "_k", kind="stable"
    )
    right = series_df[["snap_dt", "value"]].rename(columns={"snap_dt": "_k"}).copy()
    right["_k"] = pd.to_datetime(right["_k"]).to_numpy().astype("datetime64[ns]")
    right = right.dropna(subset=["_k"]).sort_values("_k", kind="stable")
    merged = pd.merge_asof(left, right, on="_k", direction="backward")
    merged = merged.sort_values("_ord")
    return pd.to_numeric(merged["value"], errors="coerce").to_numpy()


def _pct_change_arr(curr: np.ndarray, prev: np.ndarray) -> np.ndarray:
    """Vectorised percent change (curr vs prev), ``NaN`` when prev is 0/NaN."""
    curr = np.asarray(curr, dtype=float)
    prev = np.asarray(prev, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        res = (curr - prev) / np.abs(prev) * 100.0
    res[(prev == 0) | np.isnan(prev) | np.isnan(curr)] = np.nan
    return res


def _front_period_series(
    raw: pd.DataFrame, metric: str, use_period: bool = True
) -> pd.DataFrame:
    """Time series (snap_dt -> value) for the nearest-period estimate metric."""
    sub = raw[raw["metric"] == metric].copy()
    if sub.empty:
        return pd.DataFrame(columns=["snap_dt", "value"])
    if use_period:
        # For each snapshot, take the earliest (front) period's estimate.
        sub = sub.sort_values(["snap_dt", "period"])
        sub = sub.groupby("snap_dt", as_index=False).first()
    else:
        sub = sub.sort_values("snap_dt").groupby("snap_dt", as_index=False).last()
    return sub[["snap_dt", "value"]].sort_values("snap_dt")


def _recommendation_score_series(raw: pd.DataFrame) -> pd.DataFrame:
    """Per-snapshot weighted recommendation score series."""
    rec_metrics = ["rec_strong_buy", "rec_buy", "rec_hold", "rec_sell", "rec_strong_sell"]
    sub = raw[raw["metric"].isin(rec_metrics)]
    if sub.empty:
        return pd.DataFrame(columns=["snap_dt", "value"])
    pivot = sub.pivot_table(
        index="snap_dt", columns="metric", values="value", aggfunc="last"
    ).fillna(0.0)
    scores = pivot.apply(_rec_score_from_counts, axis=1)
    return pd.DataFrame({"snap_dt": scores.index, "value": scores.values}).sort_values("snap_dt")


def _asof_value(series_df: pd.DataFrame, when: pd.Timestamp) -> float | None:
    """Latest value at/before *when* from a (snap_dt, value) frame."""
    if series_df is None or series_df.empty:
        return None
    sub = series_df[series_df["snap_dt"] <= when]
    if sub.empty:
        return None
    val = sub["value"].iloc[-1]
    return None if pd.isna(val) else float(val)


def _pct_change(curr: float | None, prev: float | None) -> float:
    if curr is None or prev is None or prev == 0:
        return np.nan
    return (curr - prev) / abs(prev) * 100.0


def add_short_interest_features(
    all_features: pd.DataFrame,
    short_interest_store=None,
    date_col: str = "date",
) -> pd.DataFrame:
    """Augment feature rows with point-in-time short interest (D-TECH)."""
    if all_features.empty or short_interest_store is None or "ticker" not in all_features.columns:
        return _fill_defaults(all_features, SHORT_INTEREST_FEATURE_COLS)

    _fill_defaults(all_features, SHORT_INTEREST_FEATURE_COLS)
    date_ns = pd.to_datetime(all_features[date_col]).astype("datetime64[ns]")
    si_cols = SHORT_INTEREST_FEATURE_COLS

    for ticker, group in all_features.groupby("ticker", sort=False):
        idx = group.index
        sorted_idx = date_ns.loc[idx].sort_values(kind="stable").index
        left = pd.DataFrame({"_date": date_ns.loc[sorted_idx].to_numpy()})

        si = short_interest_store.read_ticker(ticker)
        if si is None or si.empty:
            continue

        s = si.sort_values("settlement_date").reset_index(drop=True)
        s["settle_dt"] = pd.to_datetime(s["settlement_date"]).astype("datetime64[ns]")
        s["si_change_pct"] = s["short_interest"].pct_change() * 100.0
        cols = pd.DataFrame(
            {
                "settle_dt": s["settle_dt"],
                "si_days_to_cover": s["days_to_cover"],
                "si_ratio": s["short_interest_ratio"],
                "si_pct_float": s["short_pct_float"],
                "si_change_pct": s["si_change_pct"],
            }
        ).sort_values("settle_dt")

        merged = pd.merge_asof(
            left, cols, left_on="_date", right_on="settle_dt", direction="backward"
        )
        for col in si_cols:
            if col in merged.columns:
                all_features.loc[sorted_idx, col] = merged[col].to_numpy()

    return _fill_defaults(all_features, SHORT_INTEREST_FEATURE_COLS)


def add_catalyst_features(
    all_features: pd.DataFrame,
    catalyst_store=None,
    policy_calendar=None,
    sectors: dict[str, str] | None = None,
    date_col: str = "date",
) -> pd.DataFrame:
    """Augment feature rows with demand-catalyst + policy signals (D-CAT/D-POL).

    Blends news/8-K-derived catalysts (``CatalystSignalStore``, recency-
    weighted, point-in-time) with the structural ``PolicyEventCalendar``
    tailwind score. Defaults to 0 when no source is available.
    """
    if all_features.empty or "ticker" not in all_features.columns:
        out = _fill_defaults(all_features, CATALYST_FEATURE_COLS)
        out["cat_demand_score"] = out["cat_demand_score"].fillna(0.0)
        out["cat_policy_score"] = out["cat_policy_score"].fillna(0.0)
        out["cat_count_90d"] = out["cat_count_90d"].fillna(0.0)
        return out

    all_features["_date"] = pd.to_datetime(all_features[date_col])
    sectors = sectors or {}

    # Defaults — overwritten per ticker below. Vectorised per-ticker numpy
    # broadcast replaces the previous O(rows) per-row ``aggregate()`` calls
    # (which re-read each ticker's Parquet for every feature date).
    all_features["cat_demand_score"] = 0.0
    all_features["cat_policy_score"] = 0.0
    all_features["cat_count_90d"] = 0.0
    all_features["cat_recency_days"] = np.nan

    cat_tickers = (
        set(catalyst_store.get_all_tickers()) if catalyst_store is not None else set()
    )

    for ticker, group in all_features.groupby("ticker", sort=False):
        idx = group.index
        sd = group["_date"].to_numpy().astype("datetime64[D]")
        tkr = str(ticker).upper()

        # ── News/8-K catalyst aggregates (recency-weighted, point-in-time) ──
        if tkr in cat_tickers:
            try:
                ev = catalyst_store.read_ticker(ticker)
            except Exception:
                ev = None
            if ev is not None and not ev.empty:
                ed = pd.to_datetime(ev["event_date"]).to_numpy().astype("datetime64[D]")
                impact = pd.to_numeric(ev["signed_impact"], errors="coerce").to_numpy()
                is_demand = (ev["kind"].to_numpy() == "demand")
                is_policy = (ev["kind"].to_numpy() == "policy")
                # age[d, e] in days; valid within [0, 180]; exp-decay weight.
                age = (sd[:, None] - ed[None, :]) / np.timedelta64(1, "D")
                valid = (age >= 0) & (age <= _CATALYST_LOOKBACK_DAYS)
                w = np.where(valid, np.exp(-_LN2 * np.clip(age, 0, None) / _HALF_LIFE_DAYS), 0.0)

                wd = w * is_demand[None, :]
                dsum = wd.sum(axis=1)
                dnum = (wd * impact[None, :]).sum(axis=1)
                all_features.loc[idx, "cat_demand_score"] = np.where(
                    dsum > 0, dnum / np.where(dsum > 0, dsum, 1.0), 0.0
                )
                all_features.loc[idx, "cat_count_90d"] = (
                    ((age >= 0) & (age <= 90) & is_demand[None, :]).sum(axis=1).astype(float)
                )
                dage = np.where(valid & is_demand[None, :], age, np.nan)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    recency = np.nanmin(dage, axis=1)
                all_features.loc[idx, "cat_recency_days"] = recency

                wp = w * is_policy[None, :]
                psum = wp.sum(axis=1)
                pnum = (wp * impact[None, :]).sum(axis=1)
                all_features.loc[idx, "cat_policy_score"] = np.where(
                    psum > 0, pnum / np.where(psum > 0, psum, 1.0), 0.0
                )

        # ── Structural policy tailwind (strongest signed wins vs news) ──────
        if policy_calendar is not None:
            cal_pol = _policy_score_vec(policy_calendar, tkr, sectors.get(ticker), sd)
            cur = all_features.loc[idx, "cat_policy_score"].to_numpy()
            all_features.loc[idx, "cat_policy_score"] = np.where(
                np.abs(cal_pol) > np.abs(cur), cal_pol, cur
            )

    all_features.drop(columns=["_date"], inplace=True, errors="ignore")
    all_features["cat_demand_score"] = all_features["cat_demand_score"].fillna(0.0)
    all_features["cat_policy_score"] = all_features["cat_policy_score"].fillna(0.0)
    all_features["cat_count_90d"] = all_features["cat_count_90d"].fillna(0.0)
    return all_features


def _policy_score_vec(
    policy_calendar, ticker_upper: str, sector: str | None, dates_np: np.ndarray
) -> np.ndarray:
    """Vectorised ``PolicyEventCalendar.policy_score`` over a date array for one
    ticker. Mirrors the scalar logic: explicit ticker match weights full,
    sector-only match weights half, strongest signed contribution wins."""
    from tyche.analysis.catalyst_taxonomy import policy_polarity

    best = np.zeros(len(dates_np))
    for tw in policy_calendar.tailwinds:
        pol = policy_polarity(tw.policy_tag)
        if pol == 0.0:
            continue
        if ticker_upper in {t.upper() for t in tw.tickers}:
            contrib = pol * tw.strength
        elif sector and sector in tw.sectors:
            contrib = pol * tw.strength * 0.5
        else:
            continue
        active = (dates_np >= np.datetime64(tw.start, "D")) & (
            dates_np <= np.datetime64(tw.end, "D")
        )
        cand = np.where(active, contrib, 0.0)
        best = np.where(np.abs(cand) > np.abs(best), cand, best)
    return np.clip(best, -1.0, 1.0)


def add_graph_features(
    all_features: pd.DataFrame,
    graph=None,
    date_col: str = "date",
) -> pd.DataFrame:
    """Augment rows with upstream-customer demand propagation (D-GRAPH).

    For each supplier row, look up its upstream customers in the curated
    ``SupplyChainGraph`` and aggregate those customers' *same-date* demand
    reads (3m momentum, demand-catalyst score, EPS revision) weighted by edge
    strength. This cascade is the leading indicator — a hyperscaler's capex /
    a chip vendor's demand shows up upstream before the supplier confirms.

    Strictly cross-sectional (same date) — no forward look, no leakage.
    """
    cols = GRAPH_FEATURE_COLS
    if all_features.empty or "ticker" not in all_features.columns or graph is None:
        for c in cols:
            if c not in all_features.columns:
                all_features[c] = 0.0
            else:
                all_features[c] = all_features[c].fillna(0.0)
        return all_features

    all_features["_date"] = pd.to_datetime(all_features[date_col]).dt.normalize()
    all_features["_tkr_u"] = all_features["ticker"].astype(str).str.upper()

    mom_col = "return_63d" if "return_63d" in all_features.columns else None
    cat_col = "cat_demand_score" if "cat_demand_score" in all_features.columns else None
    est_col = "e_eps_revision_90d" if "e_eps_revision_90d" in all_features.columns else None

    # Defaults — only supplier rows (tickers with curated customers) get
    # nonzero values, so the rest are filled vectorised without iteration.
    all_features["graph_customer_mom"] = 0.0
    all_features["graph_customer_catalyst"] = 0.0
    all_features["graph_customer_est_rev"] = 0.0
    all_features["graph_demand_propagation"] = 0.0
    all_features["graph_customer_count"] = 0.0

    suppliers = {e.supplier.upper() for e in graph.edges}
    customers_all = {e.customer.upper() for e in graph.edges}

    # Per-customer date-indexed reads (small: ~dozens of customer tickers).
    cust_reads: dict[str, pd.DataFrame] = {}
    cmask = all_features["_tkr_u"].isin(customers_all)
    if cmask.any():
        sub = all_features.loc[cmask, ["_tkr_u", "_date"]].copy()
        sub["mom"] = all_features.loc[cmask, mom_col] if mom_col else np.nan
        sub["cat"] = all_features.loc[cmask, cat_col] if cat_col else np.nan
        sub["est"] = all_features.loc[cmask, est_col] if est_col else np.nan
        sub["_present"] = 1.0
        for cust, g in sub.groupby("_tkr_u", sort=False):
            cust_reads[cust] = (
                g.drop_duplicates(subset="_date", keep="last")
                .set_index("_date")[["mom", "cat", "est", "_present"]]
            )

    for tkr, group in all_features.groupby("_tkr_u", sort=False):
        if tkr not in suppliers:
            continue
        customers = graph.customers_of(tkr)
        if not customers:
            continue
        idx = group.index
        sd = pd.DatetimeIndex(group["_date"].to_numpy())
        n = len(idx)
        acc = {k: np.zeros(n) for k in ("mom", "cat", "est")}
        wpres = {k: np.zeros(n) for k in ("mom", "cat", "est")}
        n_present = np.zeros(n)

        for cust, w in customers:
            creads = cust_reads.get(cust)
            if creads is None:
                continue
            aligned = creads.reindex(sd)
            present_mask = ~np.isnan(aligned["_present"].to_numpy())
            n_present += present_mask.astype(float)
            for k in ("mom", "cat", "est"):
                vals = aligned[k].to_numpy()
                ok = ~np.isnan(vals)
                acc[k][ok] += vals[ok] * w
                wpres[k][ok] += w

        mom = np.where(wpres["mom"] > 0, acc["mom"] / np.where(wpres["mom"] > 0, wpres["mom"], 1.0), 0.0)
        cat = np.where(wpres["cat"] > 0, acc["cat"] / np.where(wpres["cat"] > 0, wpres["cat"], 1.0), 0.0)
        est = np.where(wpres["est"] > 0, acc["est"] / np.where(wpres["est"] > 0, wpres["est"], 1.0), 0.0)
        mom_ramp = np.clip(mom / 0.30, 0.0, 1.0)
        cat_pos = np.clip(cat, 0.0, 1.0)
        est_ramp = np.clip(est / 0.10, 0.0, 1.0)
        prop = np.minimum(1.0, 0.45 * mom_ramp + 0.35 * cat_pos + 0.20 * est_ramp)

        all_features.loc[idx, "graph_customer_mom"] = np.round(mom, 4)
        all_features.loc[idx, "graph_customer_catalyst"] = np.round(cat, 4)
        all_features.loc[idx, "graph_customer_est_rev"] = np.round(est, 4)
        all_features.loc[idx, "graph_demand_propagation"] = np.round(prop, 4)
        all_features.loc[idx, "graph_customer_count"] = n_present

    all_features.drop(columns=["_date", "_tkr_u"], inplace=True, errors="ignore")
    return all_features
