"""8/21 EMA Conviction Engine.

Implements a simple, battle-tested conviction signal:
- Compute 8-day and 21-day Exponential Moving Averages on daily closes
- Classify stock trend state (uptrend, pullback_8, pullback_21, downtrend)
- Assess conviction level for CSP selling

The rule: When price is above both EMAs, it's in a confirmed uptrend.
Pullbacks to EMAs in an uptrend are opportunities (for buying or selling CSPs).
Price below both EMAs = trend broken, do not sell CSPs.
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
    """Stock trend classification based on 8/21 EMA position."""

    STRONG_UPTREND = "strong_uptrend"
    UPTREND = "uptrend"
    PULLBACK_TO_8EMA = "pullback_to_8ema"
    PULLBACK_TO_21EMA = "pullback_to_21ema"
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
class ConvictionSignal:
    """Complete conviction assessment for a single ticker."""

    ticker: str
    trend_state: TrendState
    conviction_level: str  # high, medium, low, none
    csp_eligible: bool

    # Price and EMA values
    last_close: float = 0.0
    ema_8: float = 0.0
    ema_21: float = 0.0

    # EMA dynamics
    ema_8_slope: float = 0.0
    ema_21_slope: float = 0.0
    price_to_8ema_pct: float = 0.0
    price_to_21ema_pct: float = 0.0

    # Volume analysis
    volume_declining_on_pullback: bool = False
    avg_volume_20d: int = 0
    latest_volume: int = 0

    # Additional context
    days_above_both_emas: int = 0
    as_of_date: date | None = None

    # Gate-level eligibility results
    gate_results: list[GateResult] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "trend_state": self.trend_state.value,
            "conviction_level": self.conviction_level,
            "csp_eligible": self.csp_eligible,
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
            "as_of_date": self.as_of_date.isoformat() if self.as_of_date else None,
            "gate_results": [g.to_dict() for g in self.gate_results] if self.gate_results else [],
        }


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Compute EMA using Wilder-style smoothing (adjust=False)."""
    return series.ewm(span=period, adjust=False).mean()


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


class ConvictionEngine:
    """Computes 8/21 EMA conviction signals for stock screening.

    Usage:
        engine = ConvictionEngine(ema_fast=8, ema_slow=21)
        signal = engine.analyze(ticker="AAPL", df=ohlcv_dataframe)
    """

    def __init__(
        self,
        ema_fast: int = 8,
        ema_slow: int = 21,
        pullback_proximity_pct: float = 2.0,
        min_bars: int = 50,
        max_extension_pct: float = 3.0,
        min_days_above_emas: int = 5,
        max_days_above_emas: int = 10,
    ) -> None:
        self._fast = ema_fast
        self._slow = ema_slow
        self._proximity_pct = pullback_proximity_pct
        self._min_bars = min_bars
        self._max_extension_pct = max_extension_pct
        self._min_days_above = min_days_above_emas
        self._max_days_above = max_days_above_emas

    def analyze(self, ticker: str, df: pd.DataFrame) -> ConvictionSignal:
        """Analyze a single ticker's OHLCV DataFrame and return a conviction signal.

        Args:
            ticker: Stock ticker symbol.
            df: DataFrame with columns: date, open, high, low, close, volume.
                Must be sorted by date ascending with at least `min_bars` rows.

        Returns:
            ConvictionSignal with trend state and conviction level.
        """
        if len(df) < self._min_bars:
            return ConvictionSignal(
                ticker=ticker,
                trend_state=TrendState.INSUFFICIENT_DATA,
                conviction_level="none",
                csp_eligible=False,
                gate_results=[
                    GateResult(
                        gate="Trend State",
                        passed=False,
                        actual=f"insufficient_data ({len(df)} bars)",
                        threshold=f"≥{self._min_bars} bars required",
                        reason=f"Only {len(df)} bars available, need at least {self._min_bars}",
                    ),
                    GateResult(gate="Extension Cap", passed=False, actual="—", threshold=f"≤{self._max_extension_pct}%", reason="Skipped — failed prior gate"),
                    GateResult(gate="Days Above EMAs", passed=False, actual="—", threshold=f"{self._min_days_above}–{self._max_days_above}d", reason="Skipped — failed prior gate"),
                ],
            )

        close = df["close"].astype(float)
        volume = df["volume"].astype(int)

        ema_8 = compute_ema(close, self._fast)
        ema_21 = compute_ema(close, self._slow)

        last_close = float(close.iloc[-1])
        last_ema_8 = float(ema_8.iloc[-1])
        last_ema_21 = float(ema_21.iloc[-1])
        last_volume = int(volume.iloc[-1])

        ema_8_slope = compute_slope(ema_8)
        ema_21_slope = compute_slope(ema_21)

        price_to_8 = ((last_close - last_ema_8) / last_ema_8 * 100) if last_ema_8 else 0
        price_to_21 = ((last_close - last_ema_21) / last_ema_21 * 100) if last_ema_21 else 0

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
        )

        conviction = self._assess_conviction(
            trend_state, ema_8_slope, ema_21_slope,
            pullback_declining, streak,
        )

        eligible_trends = (
            TrendState.STRONG_UPTREND,
            TrendState.UPTREND,
            TrendState.PULLBACK_TO_8EMA,
            TrendState.PULLBACK_TO_21EMA,
        )
        gates: list[GateResult] = []

        # Gate 1: Trend State
        trend_passed = trend_state in eligible_trends
        trend_label = trend_state.value.replace("_", " ")
        gates.append(GateResult(
            gate="Trend State",
            passed=trend_passed,
            actual=trend_label,
            threshold="uptrend, strong uptrend, pullback to 8ema, or pullback to 21ema",
            reason=f"Trend is {trend_label}" if trend_passed else f"{trend_label} is not an eligible trend state",
        ))

        # Gate 2: Extension Cap
        if trend_passed:
            ext_passed = price_to_8 <= self._max_extension_pct
            gates.append(GateResult(
                gate="Extension Cap",
                passed=ext_passed,
                actual=f"{price_to_8:.2f}%",
                threshold=f"≤{self._max_extension_pct}%",
                reason=f"Price is {price_to_8:.2f}% above 8-EMA (limit {self._max_extension_pct}%)"
                if ext_passed
                else f"Over-extended at {price_to_8:.2f}% above 8-EMA (max {self._max_extension_pct}%)",
            ))
        else:
            ext_passed = False
            gates.append(GateResult(gate="Extension Cap", passed=False, actual="—", threshold=f"≤{self._max_extension_pct}%", reason="Skipped — failed prior gate"))

        # Gate 3: Days Above EMAs
        if trend_passed and ext_passed:
            streak_passed = self._min_days_above <= streak <= self._max_days_above
            gates.append(GateResult(
                gate="Days Above EMAs",
                passed=streak_passed,
                actual=f"{streak}d",
                threshold=f"{self._min_days_above}–{self._max_days_above}d",
                reason=f"{streak} consecutive days above both EMAs (sweet spot {self._min_days_above}–{self._max_days_above})"
                if streak_passed
                else (
                    f"Only {streak}d above both EMAs — trend not yet confirmed (need ≥{self._min_days_above})"
                    if streak < self._min_days_above
                    else f"{streak}d above both EMAs — overdue for reversal (max {self._max_days_above})"
                ),
            ))
        else:
            streak_passed = False
            gates.append(GateResult(gate="Days Above EMAs", passed=False, actual="—", threshold=f"{self._min_days_above}–{self._max_days_above}d", reason="Skipped — failed prior gate"))

        csp_eligible = trend_passed and ext_passed and streak_passed
        if not csp_eligible and trend_passed:
            conviction = "low"

        as_of = df["date"].iloc[-1]
        if isinstance(as_of, str):
            as_of = date.fromisoformat(as_of)

        return ConvictionSignal(
            ticker=ticker,
            trend_state=trend_state,
            conviction_level=conviction,
            csp_eligible=csp_eligible,
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
            as_of_date=as_of,
            gate_results=gates,
        )

    def analyze_batch(
        self,
        ticker_data: dict[str, pd.DataFrame],
        requested_tickers: list[str] | None = None,
    ) -> list[ConvictionSignal]:
        """Analyze multiple tickers and return signals sorted by conviction.

        Args:
            ticker_data: Dict of ticker -> OHLCV DataFrame.
            requested_tickers: Original list of requested tickers. Tickers
                present here but missing from ticker_data are included with
                a ``no_data`` status so the caller can see them.
        """
        signals: list[ConvictionSignal] = []

        if requested_tickers:
            missing = set(t.upper() for t in requested_tickers) - set(ticker_data.keys())
            for ticker in sorted(missing):
                signals.append(ConvictionSignal(
                    ticker=ticker,
                    trend_state=TrendState.INSUFFICIENT_DATA,
                    conviction_level="none",
                    csp_eligible=False,
                    gate_results=[
                        GateResult(gate="Trend State", passed=False, actual="no data", threshold="OHLCV data required", reason="No OHLCV data in store — bootstrap or check ticker symbol"),
                        GateResult(gate="Extension Cap", passed=False, actual="—", threshold="—", reason="Skipped — no data"),
                        GateResult(gate="Days Above EMAs", passed=False, actual="—", threshold="—", reason="Skipped — no data"),
                    ],
                ))

        for ticker, df in ticker_data.items():
            try:
                signal = self.analyze(ticker, df)
                signals.append(signal)
            except Exception:
                logger.warning("conviction_analysis_failed", ticker=ticker, exc_info=True)
                signals.append(ConvictionSignal(
                    ticker=ticker,
                    trend_state=TrendState.INSUFFICIENT_DATA,
                    conviction_level="none",
                    csp_eligible=False,
                    gate_results=[
                        GateResult(gate="Trend State", passed=False, actual="error", threshold="—", reason="Analysis failed — data quality issue"),
                        GateResult(gate="Extension Cap", passed=False, actual="—", threshold="—", reason="Skipped — analysis error"),
                        GateResult(gate="Days Above EMAs", passed=False, actual="—", threshold="—", reason="Skipped — analysis error"),
                    ],
                ))

        conviction_order = {"high": 0, "medium": 1, "low": 2, "none": 3}
        signals.sort(key=lambda s: conviction_order.get(s.conviction_level, 99))

        logger.info(
            "conviction_batch_complete",
            total=len(signals),
            eligible=sum(1 for s in signals if s.csp_eligible),
        )
        return signals

    def _classify_trend(
        self,
        price: float,
        ema_8: float,
        ema_21: float,
        slope_8: float,
        slope_21: float,
        pct_to_8: float,
        pct_to_21: float,
    ) -> TrendState:
        """Classify the current trend state based on price vs EMAs."""
        above_8 = price > ema_8
        above_21 = price > ema_21
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
            return TrendState.DOWNTREND

        return TrendState.CONSOLIDATION

    def _assess_conviction(
        self,
        state: TrendState,
        slope_8: float,
        slope_21: float,
        vol_declining: bool,
        streak: int,
    ) -> str:
        """Map trend state to conviction level for CSP selling."""
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
            case TrendState.CONSOLIDATION:
                return "low"
            case TrendState.DOWNTREND | TrendState.INSUFFICIENT_DATA:
                return "none"
            case _:
                return "none"

    def _is_volume_declining_on_pullback(
        self,
        close: pd.Series,
        ema_8: pd.Series,
        volume: pd.Series,
        lookback: int = 5,
    ) -> bool:
        """Check if volume is declining during the most recent pullback.

        Low volume on a pullback suggests sellers are exhausted —
        a bullish confirmation signal.
        """
        if len(close) < lookback + 5:
            return False

        recent_close = close.iloc[-lookback:]
        recent_ema = ema_8.iloc[-lookback:]
        recent_vol = volume.iloc[-lookback:]
        prior_avg_vol = volume.iloc[-(lookback + 10) : -lookback].mean()

        is_pulling_back = any(recent_close < recent_ema)
        vol_below_avg = float(recent_vol.mean()) < float(prior_avg_vol)

        return is_pulling_back and vol_below_avg
