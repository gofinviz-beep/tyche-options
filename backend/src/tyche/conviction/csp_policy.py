"""CSP eligibility policy — evaluates gates on precomputed feature signals.

Stateless policy layer that takes FeatureSignal objects and determines
Cash-Secured Put eligibility using configurable gate thresholds.
Produces ConvictionSignal objects (with csp_eligible, conviction_level,
gate_results) for backward compatibility with the options pipeline.

This module has NO cache and NO disk I/O.  All expensive computation
has already been done by ConvictionFeatureEngine; gate evaluation is
microseconds per ticker.
"""

from __future__ import annotations

from tyche.conviction.features import FeatureSignal, GateResult, TrendState


class CSPEligibilityPolicy:
    """Evaluates CSP eligibility gates on precomputed feature signals.

    Config knobs:
        max_extension_pct: Max % above 8-EMA for uptrend path
        min_days_above_emas / max_days_above_emas: Streak sweet spot
        pullback_csp_enabled: Whether pullback path (Path B) is active
        min_prior_streak: Min prior uptrend days for pullback path
        max_rsi: Optional RSI ceiling (0 = disabled). Blocks overbought tickers.
    """

    def __init__(
        self,
        max_extension_pct: float = 3.0,
        min_days_above_emas: int = 5,
        max_days_above_emas: int = 10,
        pullback_csp_enabled: bool = True,
        min_prior_streak: int = 5,
        max_rsi: float = 0.0,
    ) -> None:
        self._max_extension_pct = max_extension_pct
        self._min_days_above = min_days_above_emas
        self._max_days_above = max_days_above_emas
        self._pullback_csp_enabled = pullback_csp_enabled
        self._min_prior_streak = min_prior_streak
        self._max_rsi = max_rsi

    def evaluate(self, feature: FeatureSignal) -> dict:
        """Evaluate CSP gates and return policy fields.

        Returns a dict with keys: csp_eligible, conviction_level, gate_results.
        conviction_level starts as feature.raw_conviction but is downgraded
        to "low" when the trend is eligible but gates fail.
        """
        if feature.trend_state == TrendState.INSUFFICIENT_DATA:
            return {
                "csp_eligible": False,
                "conviction_level": "none",
                "gate_results": self._insufficient_data_gates(feature),
            }

        is_pullback = feature.trend_state in (
            TrendState.PULLBACK_TO_8EMA,
            TrendState.PULLBACK_TO_21EMA,
        )

        eligible_trends = (
            TrendState.STRONG_UPTREND,
            TrendState.UPTREND,
            TrendState.PULLBACK_TO_8EMA,
            TrendState.PULLBACK_TO_21EMA,
        )
        gates: list[GateResult] = []

        # Gate 1: Trend State
        trend_passed = feature.trend_state in eligible_trends
        trend_label = feature.trend_state.value.replace("_", " ")
        gates.append(GateResult(
            gate="Trend State",
            passed=trend_passed,
            actual=trend_label,
            threshold="uptrend, strong uptrend, pullback to 8ema, or pullback to 21ema",
            reason=f"Trend is {trend_label}" if trend_passed else f"{trend_label} is not an eligible trend state",
        ))

        # Gate 2: Extension Cap
        if trend_passed and not is_pullback:
            ext_passed = feature.price_to_8ema_pct <= self._max_extension_pct
            gates.append(GateResult(
                gate="Extension Cap",
                passed=ext_passed,
                actual=f"{feature.price_to_8ema_pct:.2f}%",
                threshold=f"≤{self._max_extension_pct}%",
                reason=f"Price is {feature.price_to_8ema_pct:.2f}% above 8-EMA (limit {self._max_extension_pct}%)"
                if ext_passed
                else f"Over-extended at {feature.price_to_8ema_pct:.2f}% above 8-EMA (max {self._max_extension_pct}%)",
            ))
        elif trend_passed and is_pullback:
            ext_passed = True
            gates.append(GateResult(
                gate="Extension Cap",
                passed=True,
                actual="n/a (pullback)",
                threshold=f"≤{self._max_extension_pct}%",
                reason="Extension cap not applied on pullback path",
            ))
        else:
            ext_passed = False
            gates.append(GateResult(
                gate="Extension Cap", passed=False, actual="—",
                threshold=f"≤{self._max_extension_pct}%",
                reason="Skipped — failed prior gate",
            ))

        # Gate 3a: Days Above EMAs (uptrend path)
        # Gate 3b: Pullback Prior Streak (pullback path)
        if trend_passed and ext_passed and not is_pullback:
            streak_passed = self._min_days_above <= feature.days_above_both_emas <= self._max_days_above
            gates.append(GateResult(
                gate="Days Above EMAs",
                passed=streak_passed,
                actual=f"{feature.days_above_both_emas}d",
                threshold=f"{self._min_days_above}–{self._max_days_above}d",
                reason=f"{feature.days_above_both_emas} consecutive days above both EMAs (sweet spot {self._min_days_above}–{self._max_days_above})"
                if streak_passed
                else (
                    f"Only {feature.days_above_both_emas}d above both EMAs — trend not yet confirmed (need ≥{self._min_days_above})"
                    if feature.days_above_both_emas < self._min_days_above
                    else f"{feature.days_above_both_emas}d above both EMAs — overdue for reversal (max {self._max_days_above})"
                ),
            ))
        elif trend_passed and ext_passed and is_pullback:
            if self._pullback_csp_enabled:
                pullback_passed = (
                    feature.prior_streak >= self._min_prior_streak
                    and feature.ema_21_slope > 0
                )
                streak_passed = pullback_passed
                reason_parts = []
                if feature.prior_streak < self._min_prior_streak:
                    reason_parts.append(
                        f"Prior streak {feature.prior_streak}d < min {self._min_prior_streak}d"
                    )
                if feature.ema_21_slope <= 0:
                    reason_parts.append("21-EMA slope is flat/declining")
                gates.append(GateResult(
                    gate="Pullback Prior Streak",
                    passed=pullback_passed,
                    actual=f"{feature.prior_streak}d prior streak, 21-EMA slope={feature.ema_21_slope:.4f}",
                    threshold=f"≥{self._min_prior_streak}d + rising 21-EMA",
                    reason=(
                        f"Pullback CSP eligible: {feature.prior_streak}d prior uptrend, rising 21-EMA"
                        if pullback_passed
                        else f"Pullback CSP ineligible: {'; '.join(reason_parts)}"
                    ),
                ))
            else:
                streak_passed = False
                gates.append(GateResult(
                    gate="Pullback Prior Streak",
                    passed=False,
                    actual="disabled",
                    threshold="pullback_csp_enabled=true",
                    reason="Pullback CSP path is disabled",
                ))
        else:
            streak_passed = False
            gate_name = "Pullback Prior Streak" if is_pullback else "Days Above EMAs"
            threshold = (
                f"≥{self._min_prior_streak}d + rising 21-EMA" if is_pullback
                else f"{self._min_days_above}–{self._max_days_above}d"
            )
            gates.append(GateResult(
                gate=gate_name, passed=False, actual="—",
                threshold=threshold, reason="Skipped — failed prior gate",
            ))

        # Gate 4 (optional): RSI Overbought
        rsi_passed = True
        if self._max_rsi > 0 and trend_passed and ext_passed and streak_passed:
            rsi_val = feature.rsi_14
            if rsi_val is not None:
                rsi_passed = rsi_val <= self._max_rsi
                gates.append(GateResult(
                    gate="RSI Overbought",
                    passed=rsi_passed,
                    actual=f"{rsi_val:.1f}",
                    threshold=f"≤{self._max_rsi:.0f}",
                    reason=(
                        f"RSI {rsi_val:.1f} within limit"
                        if rsi_passed
                        else f"RSI {rsi_val:.1f} exceeds overbought threshold {self._max_rsi:.0f} — mean-reversion risk"
                    ),
                ))
            else:
                gates.append(GateResult(
                    gate="RSI Overbought",
                    passed=True,
                    actual="n/a",
                    threshold=f"≤{self._max_rsi:.0f}",
                    reason="RSI data not available — gate skipped",
                ))
        elif self._max_rsi > 0 and not (trend_passed and ext_passed and streak_passed):
            rsi_passed = True  # don't double-fail
            gates.append(GateResult(
                gate="RSI Overbought", passed=False, actual="—",
                threshold=f"≤{self._max_rsi:.0f}",
                reason="Skipped — failed prior gate",
            ))

        csp_eligible = trend_passed and ext_passed and streak_passed and rsi_passed
        conviction_level = feature.raw_conviction
        if not csp_eligible and trend_passed:
            conviction_level = "low"

        return {
            "csp_eligible": csp_eligible,
            "conviction_level": conviction_level,
            "gate_results": gates,
        }

    def evaluate_batch(
        self, features: list[FeatureSignal],
    ) -> list[dict]:
        """Evaluate CSP gates for a batch, returned in CSP-priority sort order."""
        results = [self.evaluate(f) for f in features]

        conviction_order = {"high": 0, "medium": 1, "low": 2, "none": 3}
        pullback_priority = {
            TrendState.PULLBACK_TO_21EMA: 0,
            TrendState.PULLBACK_TO_8EMA: 1,
        }

        paired = list(zip(features, results))
        paired.sort(key=lambda pair: (
            conviction_order.get(pair[1]["conviction_level"], 99),
            pullback_priority.get(pair[0].trend_state, 2),
            -pair[0].prior_streak,
        ))

        return [r for _, r in paired]

    def _insufficient_data_gates(self, feature: FeatureSignal) -> list[GateResult]:
        has_data = feature.last_close > 0
        if has_data:
            actual = f"insufficient_data"
            reason = "Insufficient bars for EMA computation"
        else:
            actual = "no data"
            reason = "No OHLCV data in store — bootstrap or check ticker symbol"

        skip_reason = "Skipped — failed prior gate" if has_data else "Skipped — no data"
        gates = [
            GateResult(
                gate="Trend State", passed=False, actual=actual,
                threshold="OHLCV data required" if not has_data else "≥50 bars required",
                reason=reason,
            ),
            GateResult(
                gate="Extension Cap", passed=False, actual="—",
                threshold=f"≤{self._max_extension_pct}%",
                reason=skip_reason,
            ),
            GateResult(
                gate="Days Above EMAs", passed=False, actual="—",
                threshold=f"{self._min_days_above}–{self._max_days_above}d",
                reason=skip_reason,
            ),
        ]
        if self._max_rsi > 0:
            gates.append(GateResult(
                gate="RSI Overbought", passed=False, actual="—",
                threshold=f"≤{self._max_rsi:.0f}",
                reason=skip_reason,
            ))
        return gates
