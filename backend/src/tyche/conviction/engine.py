"""8/21 EMA Conviction Engine — backward-compatible wrapper.

This module preserves the original ``ConvictionEngine`` API so all
existing consumers (routes, workflows, tests) continue to work.
Internally it delegates to:

- ``ConvictionFeatureEngine`` (features.py) — pure EMA computation + caching
- ``CSPEligibilityPolicy`` (csp_policy.py) — stateless CSP gate evaluation

Direct consumers that only need features (stocks pipeline, alerts)
can import from ``features`` or ``csp_policy`` directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd
import structlog

# Re-export shared types so existing ``from tyche.conviction.engine import ...``
# statements continue to resolve.
from tyche.conviction.features import (  # noqa: F401
    TrendState,
    GateResult,
    FeatureSignal,
    compute_ema,
    compute_rsi,
    compute_slope,
    ConvictionFeatureEngine,
)
from tyche.conviction.csp_policy import CSPEligibilityPolicy  # noqa: F401

logger = structlog.get_logger()


@dataclass
class ConvictionSignal:
    """Complete conviction assessment for a single ticker."""

    ticker: str
    trend_state: TrendState
    conviction_level: str  # high, medium, low, none — CSP-adjusted
    raw_conviction: str = "none"  # genuine EMA quality assessment before CSP override
    csp_eligible: bool = False

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
    rsi_14: float = 0.0

    gate_results: list[GateResult] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "trend_state": self.trend_state.value,
            "conviction_level": self.conviction_level,
            "raw_conviction": self.raw_conviction,
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
            "prior_streak": self.prior_streak,
            "as_of_date": self.as_of_date.isoformat() if self.as_of_date else None,
            "ema_50": round(self.ema_50, 4),
            "ema_50_slope": round(self.ema_50_slope, 6),
            "rsi_14": round(self.rsi_14, 2),
            "gate_results": [g.to_dict() for g in self.gate_results] if self.gate_results else [],
        }


def _feature_to_signal(feature: FeatureSignal, policy_result: dict) -> ConvictionSignal:
    """Combine a FeatureSignal with CSP policy results into a ConvictionSignal."""
    return ConvictionSignal(
        ticker=feature.ticker,
        trend_state=feature.trend_state,
        conviction_level=policy_result["conviction_level"],
        raw_conviction=feature.raw_conviction,
        csp_eligible=policy_result["csp_eligible"],
        last_close=feature.last_close,
        ema_8=feature.ema_8,
        ema_21=feature.ema_21,
        ema_8_slope=feature.ema_8_slope,
        ema_21_slope=feature.ema_21_slope,
        price_to_8ema_pct=feature.price_to_8ema_pct,
        price_to_21ema_pct=feature.price_to_21ema_pct,
        volume_declining_on_pullback=feature.volume_declining_on_pullback,
        avg_volume_20d=feature.avg_volume_20d,
        latest_volume=feature.latest_volume,
        days_above_both_emas=feature.days_above_both_emas,
        prior_streak=feature.prior_streak,
        as_of_date=feature.as_of_date,
        ema_50=feature.ema_50,
        ema_50_slope=feature.ema_50_slope,
        rsi_14=feature.rsi_14,
        gate_results=policy_result["gate_results"],
    )


class ConvictionEngine:
    """Backward-compatible conviction engine.

    Delegates feature computation to ``ConvictionFeatureEngine`` and
    CSP gate evaluation to ``CSPEligibilityPolicy``.  Produces
    ``ConvictionSignal`` objects identical to the original monolithic
    engine for seamless migration.

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
        pullback_csp_enabled: bool = True,
        min_prior_streak: int = 5,
        signal_store: Any | None = None,
    ) -> None:
        self._feature_engine = ConvictionFeatureEngine(
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            pullback_proximity_pct=pullback_proximity_pct,
            min_bars=min_bars,
            signal_store=signal_store,
        )
        self._csp_policy = CSPEligibilityPolicy(
            max_extension_pct=max_extension_pct,
            min_days_above_emas=min_days_above_emas,
            max_days_above_emas=max_days_above_emas,
            pullback_csp_enabled=pullback_csp_enabled,
            min_prior_streak=min_prior_streak,
        )

    @property
    def feature_engine(self) -> ConvictionFeatureEngine:
        """Direct access to the underlying feature engine."""
        return self._feature_engine

    @property
    def csp_policy(self) -> CSPEligibilityPolicy:
        """Direct access to the CSP eligibility policy."""
        return self._csp_policy

    def invalidate_cache(self) -> None:
        """Clear the feature engine's per-ticker cache and disk store."""
        self._feature_engine.invalidate_cache()

    @property
    def cache_size(self) -> int:
        return self._feature_engine.cache_size

    def analyze(self, ticker: str, df: pd.DataFrame) -> ConvictionSignal:
        """Analyze a single ticker's OHLCV data and return a ConvictionSignal.

        Delegates to the feature engine for EMA computation (cached)
        and the CSP policy for gate evaluation (stateless).
        """
        feature = self._feature_engine.analyze(ticker, df)
        policy_result = self._csp_policy.evaluate(feature)
        return _feature_to_signal(feature, policy_result)

    def analyze_batch(
        self,
        ticker_data: dict[str, pd.DataFrame],
        requested_tickers: list[str] | None = None,
    ) -> list[ConvictionSignal]:
        """Analyze multiple tickers and return signals sorted by conviction.

        Delegates feature computation to the feature engine (handles
        disk store warming/writing) and gate evaluation to the CSP policy.
        """
        features = self._feature_engine.analyze_batch(
            ticker_data, requested_tickers=requested_tickers,
        )

        signals: list[ConvictionSignal] = []
        for feature in features:
            policy_result = self._csp_policy.evaluate(feature)
            signals.append(_feature_to_signal(feature, policy_result))

        conviction_order = {"high": 0, "medium": 1, "low": 2, "none": 3}
        pullback_priority = {
            TrendState.PULLBACK_TO_21EMA: 0,
            TrendState.PULLBACK_TO_8EMA: 1,
        }
        signals.sort(key=lambda s: (
            conviction_order.get(s.conviction_level, 99),
            pullback_priority.get(s.trend_state, 2),
            -s.prior_streak,
        ))

        logger.info(
            "conviction_batch_complete",
            total=len(signals),
            eligible=sum(1 for s in signals if s.csp_eligible),
            cache_size=self._feature_engine.cache_size,
        )
        return signals
