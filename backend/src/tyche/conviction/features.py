"""Conviction feature computation — pure EMA/trend analysis from OHLCV data.

This module contains the data-derived layer of the conviction engine:
- Compute 8-day and 21-day Exponential Moving Averages
- Classify trend state (uptrend, pullback, downtrend, etc.)
- Assess raw conviction quality from trend geometry
- Cache results in-memory and optionally to disk (Parquet)

Policy-specific logic (CSP eligibility gates, stock pullback filters)
lives in separate modules that consume FeatureSignal objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger()


class TrendState(str, Enum):
    """Stock trend classification based on 8/21/50 EMA position."""

    STRONG_UPTREND = "strong_uptrend"
    UPTREND = "uptrend"
    PULLBACK_TO_8EMA = "pullback_to_8ema"
    PULLBACK_TO_21EMA = "pullback_to_21ema"
    OVERSOLD_21EMA = "oversold_21ema"
    OVERSOLD_50EMA = "oversold_50ema"
    CONSOLIDATION = "consolidation"
    DOWNTREND = "downtrend"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass
class GateResult:
    """Result of a single eligibility gate check."""

    gate: str
    passed: bool
    actual: str
    threshold: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "passed": self.passed,
            "actual": self.actual,
            "threshold": self.threshold,
            "reason": self.reason,
        }


@dataclass
class FeatureSignal:
    """Data-derived conviction features for a single ticker.

    Contains only fields computed from OHLCV data plus minimal
    config-dependent classification (trend_state uses proximity_pct).
    No CSP eligibility, no conviction_level override, no gate results.
    """

    ticker: str
    trend_state: TrendState
    raw_conviction: str = "none"

    last_close: float = 0.0
    ema_8: float = 0.0
    ema_21: float = 0.0

    ema_8_slope: float = 0.0
    ema_21_slope: float = 0.0
    price_to_8ema_pct: float = 0.0
    price_to_21ema_pct: float = 0.0

    volume_declining_on_pullback: bool = False
    avg_volume_20d: int = 0
    latest_volume: int = 0

    days_above_both_emas: int = 0
    prior_streak: int = 0
    as_of_date: date | None = None

    ema_50: float = 0.0
    ema_50_slope: float = 0.0
    price_to_50ema_pct: float = 0.0
    rsi_14: float = 0.0

    iv_rank: float | None = None
    iv_percentile: float | None = None
    atm_iv: float | None = None
    vrp: float | None = None

    conviction_score: float = 0.0

    csp_safety_prob: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "trend_state": self.trend_state.value,
            "raw_conviction": self.raw_conviction,
            "conviction_score": round(self.conviction_score, 3),
            "csp_safety_prob": round(self.csp_safety_prob, 4) if self.csp_safety_prob is not None else None,
            "last_close": round(self.last_close, 2),
            "ema_8": round(self.ema_8, 4),
            "ema_21": round(self.ema_21, 4),
            "ema_8_slope": round(self.ema_8_slope, 6),
            "ema_21_slope": round(self.ema_21_slope, 6),
            "price_to_8ema_pct": round(self.price_to_8ema_pct, 2),
            "price_to_21ema_pct": round(self.price_to_21ema_pct, 2),
            "volume_declining_on_pullback": self.volume_declining_on_pullback,
            "avg_volume_20d": self.avg_volume_20d,
            "latest_volume": self.latest_volume,
            "days_above_both_emas": self.days_above_both_emas,
            "prior_streak": self.prior_streak,
            "as_of_date": self.as_of_date.isoformat() if self.as_of_date else None,
            "ema_50": round(self.ema_50, 4),
            "ema_50_slope": round(self.ema_50_slope, 6),
            "price_to_50ema_pct": round(self.price_to_50ema_pct, 2),
            "rsi_14": round(self.rsi_14, 2),
            "iv_rank": round(self.iv_rank, 1) if self.iv_rank is not None else None,
            "iv_percentile": round(self.iv_percentile, 1) if self.iv_percentile is not None else None,
            "atm_iv": round(self.atm_iv, 4) if self.atm_iv is not None else None,
            "vrp": round(self.vrp, 4) if self.vrp is not None else None,
        }


_TREND_BASE: dict[TrendState, float] = {
    TrendState.STRONG_UPTREND: 0.35,
    TrendState.PULLBACK_TO_21EMA: 0.30,
    TrendState.OVERSOLD_50EMA: 0.30,
    TrendState.OVERSOLD_21EMA: 0.25,
    TrendState.PULLBACK_TO_8EMA: 0.25,
    TrendState.UPTREND: 0.20,
    TrendState.CONSOLIDATION: 0.05,
    TrendState.DOWNTREND: 0.0,
    TrendState.INSUFFICIENT_DATA: 0.0,
}


def compute_conviction_score(sig: FeatureSignal) -> float:
    """Compute a 0–1 composite conviction score from feature signal fields.

    Components (max total = 1.0):
      trend_base  (0.00–0.35): trend state quality
      streak      (0.00–0.20): prior streak (pullback) or days above (uptrend)
      slope       (0.00–0.10): 21-EMA slope strength
      volume      (0.00–0.05): declining volume on pullback
      rsi         (0.00–0.10): RSI sweet-spot (30–50 ideal, penalise >70)
      iv_rank     (0.00–0.10): IV Rank sweet-spot (40–80 ideal)
      vrp         (0.00–0.10): positive VRP bonus
    """
    trend_base = _TREND_BASE.get(sig.trend_state, 0.0)

    is_pullback = sig.trend_state in (
        TrendState.PULLBACK_TO_8EMA,
        TrendState.PULLBACK_TO_21EMA,
    )
    is_oversold = sig.trend_state in (
        TrendState.OVERSOLD_21EMA,
        TrendState.OVERSOLD_50EMA,
    )
    streak_raw = sig.prior_streak if (is_pullback or is_oversold) else sig.days_above_both_emas
    streak = min(1.0, streak_raw / 15) * 0.20

    slope = min(1.0, max(0.0, sig.ema_21_slope) / 0.5) * 0.10

    volume = 0.05 if ((is_pullback or is_oversold) and sig.volume_declining_on_pullback) else 0.0

    rsi = sig.rsi_14
    if is_oversold:
        if 30 <= rsi <= 40:
            rsi_component = 0.10
        elif 20 <= rsi < 30:
            rsi_component = 0.10 * ((rsi - 20) / 10)
        elif 40 < rsi <= 50:
            rsi_component = 0.10 * (1.0 - (rsi - 40) / 10)
        else:
            rsi_component = 0.0
    elif 30 <= rsi <= 50:
        rsi_component = 0.10
    elif 50 < rsi <= 70:
        rsi_component = 0.10 * (1.0 - (rsi - 50) / 20)
    else:
        rsi_component = 0.0

    iv_rank_component = 0.0
    if sig.iv_rank is not None:
        ivr = sig.iv_rank
        if 40 <= ivr <= 80:
            iv_rank_component = 0.10
        elif 20 <= ivr < 40:
            iv_rank_component = 0.10 * ((ivr - 20) / 20)
        elif 80 < ivr <= 85:
            iv_rank_component = 0.10
        elif ivr > 85:
            iv_rank_component = 0.10 * max(0.0, 1.0 - (ivr - 85) / 15)
        # ivr < 20 → 0.0

    vrp_component = 0.0
    if sig.vrp is not None and sig.vrp > 0:
        vrp_component = min(1.0, sig.vrp / 30) * 0.10

    return round(
        trend_base + streak + slope + volume + rsi_component
        + iv_rank_component + vrp_component,
        3,
    )


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Compute EMA using Wilder-style smoothing (adjust=False)."""
    return series.ewm(span=period, adjust=False).mean()


def compute_rsi(close: pd.Series, period: int = 14) -> float:
    """Compute RSI using Wilder smoothing (ewm with alpha=1/period)."""
    if len(close) < period + 1:
        return 50.0
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    last_loss = float(loss.iloc[-1])
    if last_loss == 0:
        return 100.0
    rs = float(gain.iloc[-1]) / last_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_slope(series: pd.Series, periods: int = 3) -> float:
    """Compute the slope of the last N values via linear regression."""
    if len(series) < periods:
        return 0.0
    y = series.iloc[-periods:].values
    x = np.arange(periods, dtype=float)
    if np.std(y) == 0:
        return 0.0
    coeffs = np.polyfit(x, y, 1)
    return float(coeffs[0])


class ConvictionFeatureEngine:
    """Computes data-derived EMA features from OHLCV data.

    Owns the expensive computation (EMA calculation, slope regression,
    streak counting) plus its own in-memory cache and optional Parquet
    disk store.  Produces ``FeatureSignal`` objects that policy layers
    (CSP gates, stock pullback filters) consume without recomputation.

    Cache isolation: each instance has its own ``_cache`` and
    ``_signal_store``.  Policy layers are stateless and do not share
    or affect this cache.
    """

    def __init__(
        self,
        ema_fast: int = 8,
        ema_slow: int = 21,
        pullback_proximity_pct: float = 2.0,
        min_bars: int = 50,
        signal_store: Any | None = None,
        derived_store: Any | None = None,
        *,
        oversold_dip_pct_21ema: float = 5.0,
        oversold_dip_pct_50ema: float = 5.0,
        oversold_min_prior_uptrend: int = 10,
    ) -> None:
        self._fast = ema_fast
        self._slow = ema_slow
        self._proximity_pct = pullback_proximity_pct
        self._min_bars = min_bars
        self._signal_store = signal_store
        self._derived_store = derived_store
        self._oversold_dip_21 = oversold_dip_pct_21ema
        self._oversold_dip_50 = oversold_dip_pct_50ema
        self._oversold_min_prior = oversold_min_prior_uptrend

        self._cache: dict[str, FeatureSignal] = {}
        self._cache_date: str | None = None
        self._derived_cache: dict[str, dict] = {}
        self._derived_cache_date: str | None = None

    def invalidate_cache(self) -> None:
        """Clear the per-ticker feature cache and disk store."""
        count = len(self._cache)
        self._cache.clear()
        self._cache_date = None
        if self._signal_store is not None:
            self._signal_store.clear()
        if count:
            logger.info("feature_engine_cache_cleared", entries=count)

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    def analyze(self, ticker: str, df: pd.DataFrame) -> FeatureSignal:
        """Compute feature signal for a single ticker's OHLCV data.

        Results are cached per ticker. When the OHLCV date changes
        across calls, the entire cache is auto-cleared.
        """
        if len(df) < self._min_bars:
            return FeatureSignal(
                ticker=ticker,
                trend_state=TrendState.INSUFFICIENT_DATA,
                raw_conviction="none",
            )

        raw_as_of = df["date"].iloc[-1]
        as_of_str = raw_as_of if isinstance(raw_as_of, str) else raw_as_of.isoformat()

        if self._cache_date is not None and self._cache_date != as_of_str:
            logger.info(
                "feature_engine_cache_date_changed",
                old=self._cache_date,
                new=as_of_str,
                evicted=len(self._cache),
            )
            self._cache.clear()
        self._cache_date = as_of_str

        cache_key = ticker.upper()
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        close = df["close"].astype(float)
        volume = df["volume"].astype(int)

        ema_8 = compute_ema(close, self._fast)
        ema_21 = compute_ema(close, self._slow)
        ema_50 = compute_ema(close, 50)

        last_close = float(close.iloc[-1])
        last_ema_8 = float(ema_8.iloc[-1])
        last_ema_21 = float(ema_21.iloc[-1])
        last_ema_50 = float(ema_50.iloc[-1])
        last_volume = int(volume.iloc[-1])

        ema_8_slope = compute_slope(ema_8)
        ema_21_slope = compute_slope(ema_21)
        ema_50_slope = compute_slope(ema_50)

        rsi_14 = compute_rsi(close, 14)

        price_to_8 = ((last_close - last_ema_8) / last_ema_8 * 100) if last_ema_8 else 0
        price_to_21 = ((last_close - last_ema_21) / last_ema_21 * 100) if last_ema_21 else 0
        price_to_50 = ((last_close - last_ema_50) / last_ema_50 * 100) if last_ema_50 else 0

        avg_vol_20 = int(volume.iloc[-20:].mean()) if len(volume) >= 20 else int(volume.mean())

        above_both = (close > ema_8) & (close > ema_21)
        streak = 0
        for val in reversed(above_both.tolist()):
            if val:
                streak += 1
            else:
                break

        pullback_declining = self._is_volume_declining_on_pullback(
            close, ema_8, volume
        )

        trend_state = self._classify_trend(
            last_close, last_ema_8, last_ema_21,
            ema_8_slope, ema_21_slope, price_to_8, price_to_21,
            pct_to_50=price_to_50, ema_50=last_ema_50,
            slope_50=ema_50_slope,
        )

        prior_streak_val = 0
        if trend_state in (
            TrendState.PULLBACK_TO_8EMA,
            TrendState.PULLBACK_TO_21EMA,
            TrendState.OVERSOLD_21EMA,
            TrendState.OVERSOLD_50EMA,
        ):
            prior_streak_val = self._compute_prior_streak(above_both)

        raw_conviction = self._assess_conviction(
            trend_state, ema_8_slope, ema_21_slope,
            pullback_declining, streak,
            prior_streak=prior_streak_val,
        )

        as_of = raw_as_of
        if isinstance(as_of, str):
            as_of = date.fromisoformat(as_of)

        iv_metrics = self._derived_cache.get(cache_key, {})

        signal = FeatureSignal(
            ticker=ticker,
            trend_state=trend_state,
            raw_conviction=raw_conviction,
            last_close=last_close,
            ema_8=last_ema_8,
            ema_21=last_ema_21,
            ema_8_slope=ema_8_slope,
            ema_21_slope=ema_21_slope,
            price_to_8ema_pct=price_to_8,
            price_to_21ema_pct=price_to_21,
            volume_declining_on_pullback=pullback_declining,
            avg_volume_20d=avg_vol_20,
            latest_volume=last_volume,
            days_above_both_emas=streak,
            prior_streak=prior_streak_val,
            as_of_date=as_of,
            ema_50=last_ema_50,
            ema_50_slope=ema_50_slope,
            price_to_50ema_pct=price_to_50,
            rsi_14=rsi_14,
            iv_rank=iv_metrics.get("iv_rank"),
            iv_percentile=iv_metrics.get("iv_percentile"),
            atm_iv=iv_metrics.get("atm_iv"),
            vrp=iv_metrics.get("vrp"),
        )
        signal.conviction_score = compute_conviction_score(signal)
        self._cache[cache_key] = signal
        return signal

    def analyze_batch(
        self,
        ticker_data: dict[str, pd.DataFrame],
        requested_tickers: list[str] | None = None,
    ) -> list[FeatureSignal]:
        """Compute features for multiple tickers. Returns UNSORTED list.

        Warms from disk store if the in-memory cache is empty.
        Writes back to disk when new signals are computed.
        Does NOT apply any policy-specific sorting.
        """
        cache_size_before = len(self._cache)

        if not self._cache and self._signal_store is not None and ticker_data:
            sample_df = next(iter(ticker_data.values()), None)
            if sample_df is not None and not sample_df.empty:
                raw_as_of = sample_df["date"].iloc[-1]
                as_of_date = raw_as_of if isinstance(raw_as_of, date) else date.fromisoformat(str(raw_as_of))
                cached_rows = self._signal_store.read_signals(as_of_date)
                if cached_rows:
                    self._warm_from_store(cached_rows)

        self._ensure_derived_cache(ticker_data)

        signals: list[FeatureSignal] = []

        if requested_tickers:
            missing = set(t.upper() for t in requested_tickers) - set(ticker_data.keys())
            for ticker in sorted(missing):
                signals.append(FeatureSignal(
                    ticker=ticker,
                    trend_state=TrendState.INSUFFICIENT_DATA,
                    raw_conviction="none",
                ))

        for ticker, df in ticker_data.items():
            try:
                signal = self.analyze(ticker, df)
                signals.append(signal)
            except Exception:
                logger.warning("feature_analysis_failed", ticker=ticker, exc_info=True)
                signals.append(FeatureSignal(
                    ticker=ticker,
                    trend_state=TrendState.INSUFFICIENT_DATA,
                    raw_conviction="none",
                ))

        if self._signal_store is not None and len(self._cache) > cache_size_before:
            valid = [s for s in self._cache.values() if s.as_of_date is not None]
            if valid:
                self._signal_store.write_signals(valid)

        logger.info(
            "feature_batch_complete",
            total=len(signals),
            cache_size=len(self._cache),
        )
        return signals

    def _ensure_derived_cache(self, ticker_data: dict[str, pd.DataFrame]) -> None:
        """Bulk-load derived IV metrics for all tickers in the batch."""
        if self._derived_store is None or not ticker_data:
            return

        sample_df = next(iter(ticker_data.values()), None)
        if sample_df is None or sample_df.empty:
            return

        raw_as_of = sample_df["date"].iloc[-1]
        as_of_str = raw_as_of if isinstance(raw_as_of, str) else raw_as_of.isoformat()

        if self._derived_cache_date == as_of_str and self._derived_cache:
            return

        as_of_date = raw_as_of if isinstance(raw_as_of, date) else date.fromisoformat(str(raw_as_of))
        tickers = list(ticker_data.keys())
        self._derived_cache = self._derived_store.read_latest_batch(tickers, as_of_date)
        self._derived_cache_date = as_of_str
        if self._derived_cache:
            logger.info("derived_metrics_loaded", tickers=len(self._derived_cache))

    def _warm_from_store(self, cached_rows: list[dict]) -> int:
        """Warm in-memory cache from stored EMA data, recomputing trend/conviction."""
        warmed = 0
        for row in cached_rows:
            as_of = row.get("as_of_date")
            if isinstance(as_of, str):
                as_of = date.fromisoformat(as_of)

            last_close = float(row["last_close"])
            ema_8 = float(row["ema_8"])
            ema_21 = float(row["ema_21"])
            ema_8_slope = float(row["ema_8_slope"])
            ema_21_slope = float(row["ema_21_slope"])
            price_to_8 = float(row["price_to_8ema_pct"])
            price_to_21 = float(row["price_to_21ema_pct"])
            pullback_declining = bool(row["volume_declining_on_pullback"])
            streak = int(row["days_above_both_emas"])
            prior_streak_val = int(row["prior_streak"])

            ema_50_val = float(row.get("ema_50", 0.0))
            price_to_50_val = float(row.get("price_to_50ema_pct", 0.0))

            ema_50_slope_val = float(row.get("ema_50_slope", 0.0))

            trend_state = self._classify_trend(
                last_close, ema_8, ema_21,
                ema_8_slope, ema_21_slope, price_to_8, price_to_21,
                pct_to_50=price_to_50_val, ema_50=ema_50_val,
                slope_50=ema_50_slope_val,
            )
            raw_conviction = self._assess_conviction(
                trend_state, ema_8_slope, ema_21_slope,
                pullback_declining, streak,
                prior_streak=prior_streak_val,
            )

            def _opt_float(val: Any) -> float | None:
                if val is None or (isinstance(val, float) and np.isnan(val)):
                    return None
                return float(val)

            signal = FeatureSignal(
                ticker=row["ticker"],
                trend_state=trend_state,
                raw_conviction=raw_conviction,
                last_close=last_close,
                ema_8=ema_8,
                ema_21=ema_21,
                ema_8_slope=ema_8_slope,
                ema_21_slope=ema_21_slope,
                price_to_8ema_pct=price_to_8,
                price_to_21ema_pct=price_to_21,
                volume_declining_on_pullback=pullback_declining,
                avg_volume_20d=int(row["avg_volume_20d"]),
                latest_volume=int(row["latest_volume"]),
                days_above_both_emas=streak,
                prior_streak=prior_streak_val,
                as_of_date=as_of,
                ema_50=float(row.get("ema_50", 0.0)),
                ema_50_slope=float(row.get("ema_50_slope", 0.0)),
                price_to_50ema_pct=float(row.get("price_to_50ema_pct", 0.0)),
                rsi_14=float(row.get("rsi_14", 0.0)),
                iv_rank=_opt_float(row.get("iv_rank")),
                iv_percentile=_opt_float(row.get("iv_percentile")),
                atm_iv=_opt_float(row.get("atm_iv")),
                vrp=_opt_float(row.get("vrp")),
                csp_safety_prob=_opt_float(row.get("csp_safety_prob")),
            )
            signal.conviction_score = compute_conviction_score(signal)
            self._cache[signal.ticker.upper()] = signal
            warmed += 1

        if warmed:
            as_of = cached_rows[0].get("as_of_date")
            self._cache_date = as_of.isoformat() if isinstance(as_of, date) else str(as_of)
            logger.info(
                "feature_cache_warmed_from_store",
                signals=warmed,
                as_of_date=self._cache_date,
            )
        return warmed

    def _classify_trend(
        self,
        price: float,
        ema_8: float,
        ema_21: float,
        slope_8: float,
        slope_21: float,
        pct_to_8: float,
        pct_to_21: float,
        pct_to_50: float = 0.0,
        ema_50: float = 0.0,
        slope_50: float = 0.0,
    ) -> TrendState:
        """Classify the current trend state based on price vs EMAs.

        Oversold states require the stock to be significantly below EMAs
        (beyond the proximity band), distinguishing recoverable dips from
        shallow pullbacks. The 50-EMA slope guards against chronic declines:
        a sudden dip from uptrend keeps the 50-EMA slope near zero or positive,
        while a chronic decline has a strongly negative 50-EMA slope.
        """
        above_8 = price > ema_8
        above_21 = price > ema_21
        above_50 = price > ema_50 if ema_50 > 0 else True
        both_slopes_up = slope_8 > 0 and slope_21 > 0

        if above_8 and above_21:
            if both_slopes_up and pct_to_8 > 1.0:
                return TrendState.STRONG_UPTREND
            return TrendState.UPTREND

        if above_21 and not above_8:
            if abs(pct_to_8) <= self._proximity_pct:
                return TrendState.PULLBACK_TO_8EMA
            if abs(pct_to_21) <= self._proximity_pct:
                return TrendState.PULLBACK_TO_21EMA
            return TrendState.CONSOLIDATION

        if not above_21 and abs(pct_to_21) <= self._proximity_pct and slope_21 > 0:
            return TrendState.PULLBACK_TO_21EMA

        if not above_8 and not above_21:
            not_chronic = slope_50 > -0.3
            if not above_50 and pct_to_50 <= -self._oversold_dip_50 and not_chronic:
                return TrendState.OVERSOLD_50EMA
            if pct_to_21 <= -self._oversold_dip_21 and not_chronic:
                return TrendState.OVERSOLD_21EMA
            return TrendState.DOWNTREND

        return TrendState.CONSOLIDATION

    def _assess_conviction(
        self,
        state: TrendState,
        slope_8: float,
        slope_21: float,
        vol_declining: bool,
        streak: int,
        prior_streak: int = 0,
    ) -> str:
        """Map trend state to raw conviction level (data-derived, no policy)."""
        match state:
            case TrendState.STRONG_UPTREND:
                return "high" if streak >= 5 else "medium"
            case TrendState.UPTREND:
                return "medium" if slope_21 > 0 else "low"
            case TrendState.PULLBACK_TO_21EMA:
                if slope_21 > 0 and vol_declining:
                    return "high"
                if slope_21 > 0:
                    return "medium"
                return "low"
            case TrendState.PULLBACK_TO_8EMA:
                return "medium" if slope_21 > 0 else "low"
            case TrendState.OVERSOLD_50EMA:
                if prior_streak >= self._oversold_min_prior:
                    return "high"
                if prior_streak >= 5:
                    return "medium"
                return "low"
            case TrendState.OVERSOLD_21EMA:
                if prior_streak >= self._oversold_min_prior:
                    return "medium"
                if prior_streak >= 5:
                    return "low"
                return "none"
            case TrendState.CONSOLIDATION:
                return "low"
            case TrendState.DOWNTREND | TrendState.INSUFFICIENT_DATA:
                return "none"
            case _:
                return "none"

    @staticmethod
    def _compute_prior_streak(above_both: pd.Series) -> int:
        """Count the uptrend streak that ended before the current pullback."""
        vals = above_both.tolist()
        idx = len(vals) - 1

        while idx >= 0 and not vals[idx]:
            idx -= 1

        streak = 0
        while idx >= 0 and vals[idx]:
            streak += 1
            idx -= 1
        return streak

    def _is_volume_declining_on_pullback(
        self,
        close: pd.Series,
        ema_8: pd.Series,
        volume: pd.Series,
        lookback: int = 5,
    ) -> bool:
        """Check if volume is declining during the most recent pullback."""
        if len(close) < lookback + 5:
            return False

        recent_close = close.iloc[-lookback:]
        recent_ema = ema_8.iloc[-lookback:]
        recent_vol = volume.iloc[-lookback:]
        prior_avg_vol = volume.iloc[-(lookback + 10) : -lookback].mean()

        is_pulling_back = any(recent_close < recent_ema)
        vol_below_avg = float(recent_vol.mean()) < float(prior_avg_vol)

        return is_pulling_back and vol_below_avg
