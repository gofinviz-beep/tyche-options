"""Covered Call analysis engine.

Computes extension-based signals, EMA reversion timing, day-of-week
theta efficiency, earnings proximity, and Go/Wait recommendations
from OHLCV history.  All computations are local — no network calls
for the core analysis path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd
import structlog

from tyche.conviction.features import compute_slope

logger = structlog.get_logger()

_DOW_NAMES = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
_EXTENSION_THRESHOLD = 10.0  # % above 8-EMA to count as "extended"
_EPISODE_GAP_DAYS = 5
_FORWARD_HORIZON = 21  # trading days to look forward
_EARNINGS_VOL_RATIO = 2.5
_EARNINGS_MOVE_PCT = 0.05


@dataclass
class CCSignal:
    """Summary signal for a covered call recommendation."""

    ticker: str
    signal: str  # GO, WAIT, CAUTION
    signal_reason: str
    last_close: float
    ema_8: float
    ema_21: float
    ema_50: float
    ema_21_slope: float
    extension_pct_8: float
    extension_pct_21: float
    rsi_14: float
    iv_rank: float | None = None
    vrp: float | None = None
    rv_20d: float | None = None
    suggested_strike: float = 0.0
    suggested_otm_pct: float = 0.0
    suggested_expiry_dte: int = 8
    suggested_premium_est: float | None = None
    optimal_entry_day: str = "Wed"
    assignment_prob_1w: float = 0.0
    assignment_prob_2w: float = 0.0
    estimated_next_earnings: str | None = None
    earnings_in_window: bool = False
    price_source: str = "ohlcv_close"  # "ohlcv_close" or "live_tradier"
    live_price: float | None = None
    prev_close: float | None = None  # original OHLCV close when live differs


@dataclass
class CCDeepDive:
    """Full deep-dive analysis for a single ticker."""

    signal: CCSignal
    total_episodes: int = 0
    episode_table: list[dict] = field(default_factory=list)
    days_to_8ema: dict = field(default_factory=dict)
    days_to_21ema: dict = field(default_factory=dict)
    days_to_50ema: dict = field(default_factory=dict)
    drawdown_at_8ema: dict = field(default_factory=dict)
    drawdown_at_21ema: dict = field(default_factory=dict)
    forward_returns: list[dict] = field(default_factory=list)
    dow_analysis: list[dict] = field(default_factory=list)
    rally_peak_day_distribution: dict = field(default_factory=dict)
    call_candidates: list[dict] | None = None
    pnl_scenarios: dict = field(default_factory=dict)
    recommended_action: dict = field(default_factory=dict)


@dataclass
class CCPortfolioAnalysis:
    """Batch analysis result for multiple positions."""

    analyses: list[CCDeepDive] = field(default_factory=list)
    portfolio_summary: dict = field(default_factory=dict)


class CCAnalysisEngine:
    """Stateless covered call analysis engine.

    All computations derive from OHLCV history.  Derived metrics
    (IV Rank, VRP) and options history are optional overlays.
    """

    def __init__(
        self,
        ohlcv_store,
        derived_store=None,
        options_history_store=None,
    ) -> None:
        self._ohlcv = ohlcv_store
        self._derived = derived_store
        self._options_hist = options_history_store

    def analyze(
        self,
        ticker: str,
        shares: int = 100,
        cost_basis: float = 0.0,
        target_dte: int = 8,
        as_of: date | None = None,
    ) -> CCDeepDive:
        """Run full CC analysis for a single ticker."""
        df = self._ohlcv.read_ticker(ticker)
        if df.empty or len(df) < 50:
            return self._empty_result(ticker)

        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()

        if as_of is not None:
            df = df.loc[:str(as_of)]
            if df.empty:
                return self._empty_result(ticker)

        close = df["close"]
        high = df["high"]
        low = df["low"]

        ema_8 = close.ewm(span=8, adjust=False).mean()
        ema_21 = close.ewm(span=21, adjust=False).mean()
        ema_50 = close.ewm(span=50, adjust=False).mean()

        last = close.iloc[-1]
        last_ema8 = ema_8.iloc[-1]
        last_ema21 = ema_21.iloc[-1]
        last_ema50 = ema_50.iloc[-1]
        ema_21_slope = compute_slope(ema_21)
        ext_8 = ((last / last_ema8) - 1) * 100 if last_ema8 else 0
        ext_21 = ((last / last_ema21) - 1) * 100 if last_ema21 else 0

        rsi = self._compute_rsi(close)

        # Derived metrics
        iv_rank, vrp, rv_20d = self._load_derived(ticker)

        today = as_of or date.today()

        # Extension episode analysis
        ext_series = (close / ema_8 - 1) * 100
        episodes = self._find_episodes(ext_series, _EXTENSION_THRESHOLD)
        episode_table, ema_reversion, fwd_returns, rally_dist = (
            self._analyze_episodes(
                episodes, close, high, low, ema_8, ema_21, ema_50, df, target_dte,
            )
        )

        # Map forward returns to actual calendar dates
        fwd_returns = self._map_forward_dates(fwd_returns, today)

        # Day-of-week analysis
        dow_analysis = self._analyze_dow(close, high, target_otm_pct=13)

        # Earnings detection
        earnings_dates = self._detect_earnings(df)
        est_next = self._estimate_next_earnings(earnings_dates)
        earnings_in_window = (
            est_next is not None
            and 0 <= (est_next - today).days <= target_dte
        )

        # Assignment probabilities from episodes
        prob_1w = self._assignment_prob(episodes, close, otm_pct=13, window=5)
        prob_2w = self._assignment_prob(episodes, close, otm_pct=13, window=10)

        # Determine optimal entry day
        best_dow = self._best_entry_day(dow_analysis)

        # Suggested strike
        suggested_otm = self._suggest_otm(episodes, close, target_dte)
        suggested_strike = round(last * (1 + suggested_otm / 100), 1)

        # Premium estimate from options history
        premium_est = self._estimate_premium(
            ticker, suggested_strike, target_dte, today,
        )

        # Go/Wait signal
        signal, reason = self._compute_signal(
            ext_8, rsi, iv_rank, vrp, earnings_in_window, today,
        )

        cc_signal = CCSignal(
            ticker=ticker,
            signal=signal,
            signal_reason=reason,
            last_close=round(last, 2),
            ema_8=round(last_ema8, 2),
            ema_21=round(last_ema21, 2),
            ema_50=round(last_ema50, 2),
            ema_21_slope=round(ema_21_slope, 4),
            extension_pct_8=round(ext_8, 1),
            extension_pct_21=round(ext_21, 1),
            rsi_14=round(rsi, 1),
            iv_rank=round(iv_rank, 1) if iv_rank is not None else None,
            vrp=round(vrp, 1) if vrp is not None else None,
            rv_20d=round(rv_20d, 1) if rv_20d is not None else None,
            suggested_strike=suggested_strike,
            suggested_otm_pct=round(suggested_otm, 1),
            suggested_expiry_dte=target_dte,
            suggested_premium_est=(
                round(premium_est, 2) if premium_est is not None else None
            ),
            optimal_entry_day=best_dow,
            assignment_prob_1w=round(prob_1w, 1),
            assignment_prob_2w=round(prob_2w, 1),
            estimated_next_earnings=(
                est_next.isoformat() if est_next else None
            ),
            earnings_in_window=earnings_in_window,
        )

        # P&L scenarios
        pnl = self._compute_pnl(
            last, suggested_strike, premium_est, shares, cost_basis,
        )

        # Call candidates from options history
        call_cands = self._load_call_candidates(ticker, last, target_dte, today)

        # Recommended action — the concrete trade instruction
        rec = self._build_recommendation(
            ticker=ticker,
            signal=signal,
            last=last,
            ext_8=ext_8,
            suggested_strike=suggested_strike,
            suggested_otm=suggested_otm,
            target_dte=target_dte,
            premium_est=premium_est,
            shares=shares,
            today=today,
            best_dow=best_dow,
            prob_1w=prob_1w,
            prob_2w=prob_2w,
            episodes=episodes,
            close=close,
            ema_reversion=ema_reversion,
            earnings_in_window=earnings_in_window,
            fwd_returns=fwd_returns,
        )

        return CCDeepDive(
            signal=cc_signal,
            total_episodes=len(episodes),
            episode_table=episode_table,
            days_to_8ema=ema_reversion.get("8ema", {}),
            days_to_21ema=ema_reversion.get("21ema", {}),
            days_to_50ema=ema_reversion.get("50ema", {}),
            drawdown_at_8ema=ema_reversion.get("dd_8ema", {}),
            drawdown_at_21ema=ema_reversion.get("dd_21ema", {}),
            forward_returns=fwd_returns,
            dow_analysis=dow_analysis,
            rally_peak_day_distribution=rally_dist,
            call_candidates=call_cands,
            pnl_scenarios=pnl,
            recommended_action=rec,
        )

    def analyze_batch(
        self,
        positions: list[dict],
        target_dte: int = 8,
    ) -> CCPortfolioAnalysis:
        """Analyze multiple positions and produce a portfolio summary."""
        analyses = []
        total_premium = 0.0
        go_count = 0
        wait_count = 0
        caution_count = 0

        for pos in positions:
            result = self.analyze(
                ticker=pos["ticker"],
                shares=pos.get("shares", 100),
                cost_basis=pos.get("cost_basis", 0.0),
                target_dte=target_dte,
            )
            analyses.append(result)

            sig = result.signal
            if sig.signal == "GO":
                go_count += 1
            elif sig.signal == "WAIT":
                wait_count += 1
            else:
                caution_count += 1

            if sig.suggested_premium_est is not None:
                contracts = pos.get("shares", 100) // 100
                total_premium += sig.suggested_premium_est * contracts * 100

        return CCPortfolioAnalysis(
            analyses=analyses,
            portfolio_summary={
                "total_premium_est": round(total_premium, 2),
                "positions_go": go_count,
                "positions_wait": wait_count,
                "positions_caution": caution_count,
                "total_positions": len(analyses),
            },
        )

    # ── Private helpers ────────────────────────────────────────────

    def _empty_result(self, ticker: str) -> CCDeepDive:
        return CCDeepDive(
            signal=CCSignal(
                ticker=ticker,
                signal="WAIT",
                signal_reason="Insufficient OHLCV data",
                last_close=0, ema_8=0, ema_21=0, ema_50=0, ema_21_slope=0,
                extension_pct_8=0, extension_pct_21=0, rsi_14=0,
            ),
        )

    @staticmethod
    def _compute_rsi(close: pd.Series, period: int = 14) -> float:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1])

    def _load_derived(self, ticker: str) -> tuple[float | None, float | None, float | None]:
        if self._derived is None:
            return None, None, None
        try:
            df = self._derived.read_ticker(ticker)
            if df is None or df.empty:
                return None, None, None
            row = df.iloc[-1]
            iv_rank = float(row.get("iv_rank")) if pd.notna(row.get("iv_rank")) else None
            vrp_val = float(row.get("vrp")) if pd.notna(row.get("vrp")) else None
            rv = float(row.get("rv_20d")) if pd.notna(row.get("rv_20d")) else None
            if vrp_val is not None:
                vrp_val = vrp_val * 100
            if rv is not None:
                rv = rv * 100
            return iv_rank, vrp_val, rv
        except Exception:
            return None, None, None

    @staticmethod
    def _find_episodes(
        ext_series: pd.Series, threshold: float,
    ) -> list[dict]:
        extended_mask = ext_series >= threshold
        extended_dates = ext_series[extended_mask].index
        if len(extended_dates) == 0:
            return []

        episodes: list[dict] = []
        peak_ext = float(ext_series[extended_dates[0]])
        peak_date = extended_dates[0]
        prev = extended_dates[0]

        for d in extended_dates[1:]:
            if (d - prev).days > _EPISODE_GAP_DAYS:
                episodes.append({"peak_date": peak_date, "peak_ext": peak_ext})
                peak_ext = float(ext_series[d])
                peak_date = d
            else:
                val = float(ext_series[d])
                if val > peak_ext:
                    peak_ext = val
                    peak_date = d
            prev = d

        episodes.append({"peak_date": peak_date, "peak_ext": peak_ext})
        return episodes

    @staticmethod
    def _analyze_episodes(
        episodes: list[dict],
        close: pd.Series,
        high: pd.Series,
        low: pd.Series,
        ema_8: pd.Series,
        ema_21: pd.Series,
        ema_50: pd.Series,
        df: pd.DataFrame,
        target_dte: int,
    ) -> tuple[list[dict], dict, list[dict], dict]:
        """Compute per-episode stats, EMA reversion timing, forward returns."""
        episode_rows: list[dict] = []
        d8_list, d21_list, d50_list = [], [], []
        dd8_list, dd21_list = [], []
        fwd_data: dict[int, list[float]] = {d: [] for d in range(1, _FORWARD_HORIZON + 1)}
        peak_d13, peak_d46, peak_d78 = 0, 0, 0

        for ep in episodes:
            idx = df.index.get_loc(ep["peak_date"])
            if idx + 1 >= len(df):
                continue

            peak_price = float(close.iloc[idx])
            rally_peak = peak_price
            rally_idx = idx
            touch_8 = touch_21 = touch_50 = None
            dd_8 = dd_21 = None

            max_fwd = min(idx + 120, len(df))
            for i in range(idx + 1, max_fwd):
                c = float(close.iloc[i])
                if c > rally_peak:
                    rally_peak = c
                    rally_idx = i
                if touch_8 is None and c <= float(ema_8.iloc[i]):
                    touch_8 = i - idx
                    dd_8 = (c / rally_peak - 1) * 100
                if touch_21 is None and c <= float(ema_21.iloc[i]):
                    touch_21 = i - idx
                    dd_21 = (c / rally_peak - 1) * 100
                if touch_50 is None and c <= float(ema_50.iloc[i]):
                    touch_50 = i - idx

            add_rally = (rally_peak / peak_price - 1) * 100
            rally_dur = rally_idx - idx

            episode_rows.append({
                "peak_date": ep["peak_date"].date().isoformat()
                if hasattr(ep["peak_date"], "date") else str(ep["peak_date"]),
                "peak_price": round(peak_price, 2),
                "extension_pct": round(ep["peak_ext"], 1),
                "additional_rally_pct": round(add_rally, 1),
                "rally_days": rally_dur,
                "days_to_8ema": touch_8,
                "days_to_21ema": touch_21,
                "days_to_50ema": touch_50,
            })

            if touch_8 is not None:
                d8_list.append(touch_8)
            if touch_21 is not None:
                d21_list.append(touch_21)
            if touch_50 is not None:
                d50_list.append(touch_50)
            if dd_8 is not None:
                dd8_list.append(dd_8)
            if dd_21 is not None:
                dd21_list.append(dd_21)

            # Forward returns
            for d in range(1, _FORWARD_HORIZON + 1):
                if idx + d < len(df):
                    ret = (float(close.iloc[idx + d]) / peak_price - 1) * 100
                    fwd_data[d].append(ret)

            # Rally peak distribution within target DTE
            window = min(target_dte, len(df) - idx - 1)
            if window > 0:
                prices = [float(close.iloc[idx + d]) for d in range(1, window + 1)]
                if prices:
                    pk = int(np.argmax(prices)) + 1
                    if pk <= 3:
                        peak_d13 += 1
                    elif pk <= 6:
                        peak_d46 += 1
                    else:
                        peak_d78 += 1

        # Summarize EMA reversion
        ema_reversion: dict[str, dict] = {}
        for label, lst in [("8ema", d8_list), ("21ema", d21_list), ("50ema", d50_list)]:
            if lst:
                arr = np.array(lst)
                ema_reversion[label] = {
                    "mean": round(float(np.mean(arr)), 1),
                    "median": round(float(np.median(arr)), 1),
                    "p25": round(float(np.percentile(arr, 25)), 1),
                    "p75": round(float(np.percentile(arr, 75)), 1),
                    "p90": round(float(np.percentile(arr, 90)), 1),
                    "count": len(lst),
                }
        for label, lst in [("dd_8ema", dd8_list), ("dd_21ema", dd21_list)]:
            if lst:
                arr = np.array(lst)
                ema_reversion[label] = {
                    "mean": round(float(np.mean(arr)), 1),
                    "median": round(float(np.median(arr)), 1),
                }

        # Forward returns summary
        fwd_returns = []
        for d in range(1, _FORWARD_HORIZON + 1):
            vals = fwd_data[d]
            if vals:
                arr = np.array(vals)
                fwd_returns.append({
                    "day": d,
                    "pct_above_entry": round(float((arr > 0).mean()) * 100, 1),
                    "avg_ret": round(float(np.mean(arr)), 1),
                    "med_ret": round(float(np.median(arr)), 1),
                })

        total_peaks = peak_d13 + peak_d46 + peak_d78
        rally_dist = {
            "days_1_3": peak_d13,
            "days_4_6": peak_d46,
            "days_7_plus": peak_d78,
            "total": total_peaks,
        }

        return episode_rows, ema_reversion, fwd_returns, rally_dist

    @staticmethod
    def _map_forward_dates(
        fwd_returns: list[dict],
        as_of: date,
    ) -> list[dict]:
        """Add actual calendar dates to forward return entries.

        Maps trading day offsets (D1, D2, ...) to real business dates
        starting from the day after as_of, so the user sees
        "Apr 17 (Thu)" instead of just "D1".
        """
        if not fwd_returns:
            return fwd_returns

        max_day = max(fr["day"] for fr in fwd_returns)
        bdays = pd.bdate_range(
            start=as_of + timedelta(days=1),
            periods=max_day,
        )
        day_to_date = {i + 1: bdays[i] for i in range(len(bdays))}

        _SHORT_DOW = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}

        result = []
        for fr in fwd_returns:
            d = fr["day"]
            cal = day_to_date.get(d)
            enriched = {**fr}
            if cal is not None:
                enriched["calendar_date"] = cal.strftime("%Y-%m-%d")
                enriched["day_label"] = cal.strftime("%b %d") + f" ({_SHORT_DOW.get(cal.dayofweek, '')})"
            result.append(enriched)
        return result

    @staticmethod
    def _analyze_dow(
        close: pd.Series,
        high: pd.Series,
        target_otm_pct: int = 13,
    ) -> list[dict]:
        """Day-of-week CC entry analysis."""
        cal_days_to_fri = {0: 5, 1: 4, 2: 3, 3: 2, 4: 7}
        results = []

        for dow in range(5):
            wins, total, called = 0, 0, 0
            rets: list[float] = []

            for i in range(50, len(close) - 10):
                if close.index[i].dayofweek != dow:
                    continue
                entry = float(close.iloc[i])
                strike = entry * (1 + target_otm_pct / 100)

                days_to_fri = (4 - dow) % 7
                if days_to_fri == 0:
                    days_to_fri = 7
                exp_target = close.index[i] + pd.Timedelta(days=days_to_fri)

                exp_idx = None
                for j in range(i + 1, min(i + 10, len(close))):
                    if close.index[j] >= exp_target:
                        exp_idx = j
                        break
                if exp_idx is None:
                    continue

                exp_close = float(close.iloc[exp_idx])
                is_called = exp_close > strike

                rv = float(close.iloc[max(0, i - 20):i].pct_change().std()) * (252 ** 0.5) if i >= 20 else 0.5
                cal_d = days_to_fri
                prem = entry * rv * np.sqrt(cal_d / 365) * np.exp(
                    -(target_otm_pct / 100) ** 2
                    / (2 * (rv * np.sqrt(cal_d / 365)) ** 2)
                ) * 0.4
                prem = max(prem, 0.01)

                if is_called:
                    ret = (prem + (strike - entry)) / entry * 100
                    called += 1
                else:
                    ret = prem / entry * 100
                    wins += 1
                rets.append(ret)
                total += 1

            if total == 0:
                continue

            cal_days = cal_days_to_fri[dow]
            avg_ret = float(np.mean(rets))
            results.append({
                "day": _DOW_NAMES[dow],
                "trades": total,
                "win_pct": round(wins / total * 100, 1),
                "called_pct": round(called / total * 100, 1),
                "avg_return": round(avg_ret, 2),
                "cal_days": cal_days,
                "ret_per_calday": round(avg_ret / cal_days, 4),
            })

        return results

    @staticmethod
    def _detect_earnings(df: pd.DataFrame) -> list[pd.Timestamp]:
        """Detect earnings-like events from volume spikes + big moves."""
        close = df["close"]
        avg_vol = df["volume"].rolling(20).mean()
        vol_ratio = df["volume"] / avg_vol
        abs_change = close.pct_change().abs()

        mask = (vol_ratio > _EARNINGS_VOL_RATIO) & (abs_change > _EARNINGS_MOVE_PCT)
        events_idx = df.index[mask]

        # Cluster into distinct events
        clustered: list[pd.Timestamp] = []
        prev = None
        for d in events_idx:
            if prev is None or (d - prev).days > 10:
                clustered.append(d)
            prev = d
        return clustered

    @staticmethod
    def _estimate_next_earnings(
        events: list[pd.Timestamp],
    ) -> date | None:
        if len(events) < 2:
            return None
        recent = [e for e in events if e.year >= 2024]
        if len(recent) < 2:
            return None
        gaps = [(recent[i + 1] - recent[i]).days for i in range(len(recent) - 1)]
        avg_gap = np.mean(gaps)
        last = recent[-1]
        est = last + pd.Timedelta(days=int(avg_gap))
        return est.date() if hasattr(est, "date") else est

    @staticmethod
    def _assignment_prob(
        episodes: list[dict],
        close: pd.Series,
        otm_pct: float,
        window: int,
    ) -> float:
        """Close-based assignment probability from extension episodes."""
        total = 0
        breached = 0
        df_idx = close.index
        for ep in episodes:
            idx = df_idx.get_loc(ep["peak_date"])
            if idx + window >= len(close):
                continue
            entry = float(close.iloc[idx])
            strike = entry * (1 + otm_pct / 100)
            end_close = float(close.iloc[idx + window])
            total += 1
            if end_close >= strike:
                breached += 1
        return (breached / total * 100) if total > 0 else 0.0

    @staticmethod
    def _best_entry_day(dow_analysis: list[dict]) -> str:
        if not dow_analysis:
            return "Wed"
        best = max(dow_analysis, key=lambda x: x.get("ret_per_calday", 0))
        return best["day"]

    @staticmethod
    def _suggest_otm(
        episodes: list[dict],
        close: pd.Series,
        target_dte: int,
    ) -> float:
        """Find the OTM% where close-based assignment is <= 15%."""
        if not episodes:
            return 13.0

        df_idx = close.index
        for otm in [10, 13, 15, 20, 25]:
            total = 0
            called = 0
            for ep in episodes:
                idx = df_idx.get_loc(ep["peak_date"])
                if idx + target_dte >= len(close):
                    continue
                entry = float(close.iloc[idx])
                strike = entry * (1 + otm / 100)
                end_close = float(close.iloc[idx + target_dte])
                total += 1
                if end_close >= strike:
                    called += 1
            if total > 0 and (called / total) <= 0.15:
                return float(otm)
        return 20.0

    def _estimate_premium(
        self,
        ticker: str,
        strike: float,
        dte: int,
        as_of: date,
    ) -> float | None:
        """Estimate premium from options history if available.

        Matches by both strike AND expiration proximity to avoid using
        stale premium data from a different expiration (which has completely
        different time value).
        """
        if self._options_hist is None:
            return None
        try:
            path = self._options_hist._ticker_path(ticker)
            if not path.exists():
                return None
            df = pd.read_parquet(path)
            if df.empty:
                return None

            df["date"] = pd.to_datetime(df["date"]).dt.date
            calls = df[df["option_type"] == "C"].copy()
            if calls.empty:
                return None

            # Use today's data, or fall back to most recent date
            today_calls = calls[calls["date"] == as_of]
            if today_calls.empty:
                latest = calls["date"].max()
                today_calls = calls[calls["date"] == latest]
            if today_calls.empty:
                return None

            # Filter to expirations within the DTE range (±3 days)
            today_calls["expiration_dt"] = pd.to_datetime(today_calls["expiration"])
            target_exp = pd.Timestamp(as_of) + pd.Timedelta(days=dte)
            today_calls["exp_diff"] = (today_calls["expiration_dt"] - target_exp).abs()
            within_range = today_calls[today_calls["exp_diff"] <= pd.Timedelta(days=3)]

            if within_range.empty:
                # No matching expiration — fall back to nearest available
                # but this is unreliable, so flag it
                within_range = today_calls.nsmallest(5, "exp_diff")

            # Find nearest strike within the filtered expirations
            nearest = within_range.iloc[
                (within_range["strike"] - strike).abs().argsort()[:1]
            ]
            if nearest.empty:
                return None

            close_price = float(nearest.iloc[0]["close"])

            # Sanity check: premium should not exceed ~20% of strike for OTM calls
            if close_price > strike * 0.20:
                logger.debug(
                    "cc_premium_sanity_fail",
                    ticker=ticker,
                    strike=strike,
                    premium=close_price,
                    msg="Premium too high relative to strike — likely wrong expiration",
                )
                return None

            return close_price
        except Exception:
            return None

    @staticmethod
    def _compute_pnl(
        current_price: float,
        strike: float,
        premium: float | None,
        shares: int,
        cost_basis: float,
    ) -> dict:
        contracts = shares // 100
        prem = premium or 0.0
        commission = contracts * 0.65
        total_premium = prem * contracts * 100
        net_premium = total_premium - commission

        if_not_called = {
            "premium_income": round(net_premium, 2),
            "shares_kept": shares,
        }

        stock_gain = (strike - current_price) * shares
        if_called = {
            "stock_gain": round(stock_gain, 2),
            "premium_income": round(net_premium, 2),
            "total_gain": round(stock_gain + net_premium, 2),
            "effective_sell_price": round(strike + prem, 2),
        }

        if cost_basis > 0:
            total_cost = cost_basis * shares
            if_called["total_return_pct"] = round(
                ((strike + prem) / cost_basis - 1) * 100, 1,
            )
            if_not_called["unrealized_gain"] = round(
                (current_price - cost_basis) * shares, 2,
            )

        return {
            "if_not_called": if_not_called,
            "if_called": if_called,
            "contracts": contracts,
            "commission": round(commission, 2),
        }

    @staticmethod
    def _build_recommendation(
        *,
        ticker: str,
        signal: str,
        last: float,
        ext_8: float,
        suggested_strike: float,
        suggested_otm: float,
        target_dte: int,
        premium_est: float | None,
        shares: int,
        today: date,
        best_dow: str,
        prob_1w: float,
        prob_2w: float,
        episodes: list[dict],
        close: pd.Series,
        ema_reversion: dict,
        earnings_in_window: bool,
        fwd_returns: list[dict],
    ) -> dict:
        """Build a concrete, actionable trade recommendation."""
        contracts = shares // 100
        if contracts < 1:
            return {"action": "SKIP", "reason": "Need at least 100 shares to sell a covered call"}

        # Find the best entry date from forward returns
        # The ideal entry is when % above entry starts declining
        # (meaning stock is most likely to be lower = CC most likely to expire OTM)
        best_entry_date = None
        best_entry_label = None
        if fwd_returns:
            # The earliest date with lowest pct_above (stock most likely below entry)
            # within the first 5 trading days — we want to act soon
            near_term = [fr for fr in fwd_returns if fr["day"] <= 5]
            if near_term:
                worst_for_stock = min(near_term, key=lambda fr: fr["pct_above_entry"])
                best_entry_date = worst_for_stock.get("calendar_date")
                best_entry_label = worst_for_stock.get("day_label")

        # Compute the recommended expiration date
        exp_bdays = pd.bdate_range(start=today + timedelta(days=1), periods=target_dte)
        # Prefer Friday expirations for standard weeklies
        fridays = [d for d in exp_bdays if d.dayofweek == 4]
        if fridays:
            exp_date = fridays[-1]
        else:
            exp_date = exp_bdays[-1] if len(exp_bdays) > 0 else None

        exp_str = exp_date.strftime("%Y-%m-%d") if exp_date else None
        exp_label = exp_date.strftime("%b %d (%a)") if exp_date else None
        actual_dte = (exp_date.date() - today).days if exp_date else target_dte

        # Pullback probability — what % of time the stock is BELOW entry by expiration
        pullback_prob = 0.0
        if fwd_returns:
            # Find the forward return closest to the actual DTE
            nearest_fr = min(fwd_returns, key=lambda fr: abs(fr["day"] - actual_dte))
            pullback_prob = 100 - nearest_fr.get("pct_above_entry", 50)

        # Median days to 8-EMA reversion
        days_to_pullback = ema_reversion.get("8ema", {}).get("median")

        # Compute premium income
        prem_per_share = premium_est or 0.0
        total_premium = prem_per_share * contracts * 100
        commission = contracts * 0.65
        net_premium = total_premium - commission

        # Safety reasoning
        safety_reasons: list[str] = []
        if prob_1w == 0:
            safety_reasons.append(f"0% historical assignment at {suggested_otm:.0f}% OTM (1-week)")
        elif prob_1w <= 5:
            safety_reasons.append(f"Only {prob_1w:.0f}% assignment risk at {suggested_otm:.0f}% OTM (1-week)")
        if pullback_prob >= 70:
            safety_reasons.append(f"{pullback_prob:.0f}% probability stock pulls back by expiration")
        if days_to_pullback and days_to_pullback <= target_dte:
            safety_reasons.append(f"Median {days_to_pullback:.0f}d to 8-EMA reversion (within DTE)")

        # Premium quality check
        # $0.10/share minimum for meaningful CC premium — below this the
        # net income doesn't justify the assignment risk or the effort.
        _MIN_PREMIUM_PER_SHARE = 0.10
        premium_too_thin = (
            prem_per_share > 0 and prem_per_share < _MIN_PREMIUM_PER_SHARE
        )

        # Risk warnings
        warnings: list[str] = []
        if premium_too_thin:
            warnings.append(
                f"Premium only ${prem_per_share:.2f}/share "
                f"(${net_premium:.0f} net for {contracts} contracts) — "
                f"too thin to justify assignment risk"
            )
        if earnings_in_window:
            warnings.append("Earnings within DTE — elevated gap risk, consider skipping")
        if prob_1w > 10:
            warnings.append(f"Assignment risk elevated at {prob_1w:.0f}% — consider wider OTM")
        if ext_8 >= 15:
            warnings.append(f"Extreme extension (+{ext_8:.0f}%) — possible blow-off top")

        # Build the entry timing advice
        # CC backtest shows Mon/Tue/Wed are optimal (more theta to Friday)
        entry_timing = "today"
        if today.weekday() >= 3:  # Thu/Fri
            entry_timing = f"Monday (or {best_dow} for optimal theta)"
        elif today.weekday() not in (0, 1, 2):  # not Mon/Tue/Wed
            entry_timing = best_dow

        # Downgrade action if premium is too thin
        if signal == "GO" and premium_too_thin:
            action = "SKIP"
        elif signal == "GO":
            action = "SELL"
        elif signal == "CAUTION":
            action = "CONSIDER"
        else:
            action = "WAIT"

        if action == "SKIP" and premium_too_thin:
            instruction = (
                f"SKIP — {ticker} ${suggested_strike:.1f} Call premium is only "
                f"${prem_per_share:.2f}/share (${net_premium:.0f} net) — not worth the assignment risk"
            )
        elif action in ("SELL", "CONSIDER"):
            instruction = (
                f"SELL {contracts} × {ticker} ${suggested_strike:.1f} CALL "
                f"exp {exp_label or exp_str or 'TBD'}"
            )
        elif action == "SKIP":
            instruction = f"SKIP — {ticker} has fewer than 100 shares"
        else:
            instruction = f"WAIT — {ticker} not extended enough for meaningful premium"

        rec: dict = {
            "action": action,
            "instruction": instruction,
            "ticker": ticker,
            "contracts": contracts,
            "strike": suggested_strike,
            "otm_pct": round(suggested_otm, 1),
            "expiration_date": exp_str,
            "expiration_label": exp_label,
            "actual_dte": actual_dte,
            "entry_timing": entry_timing,
            "premium_est_per_share": round(prem_per_share, 2) if prem_per_share else None,
            "total_premium_est": round(total_premium, 2) if total_premium else None,
            "net_premium_est": round(net_premium, 2) if net_premium else None,
            "assignment_prob": round(prob_1w, 1),
            "pullback_prob_by_expiry": round(pullback_prob, 1),
            "safety_reasons": safety_reasons,
            "warnings": warnings,
        }

        return rec

    def _load_call_candidates(
        self,
        ticker: str,
        current_price: float,
        target_dte: int,
        as_of: date,
    ) -> list[dict] | None:
        """Load call options from options history for display."""
        if self._options_hist is None:
            return None
        try:
            path = self._options_hist._ticker_path(ticker)
            if not path.exists():
                return None
            df = pd.read_parquet(path)
            if df.empty:
                return None
            df["date"] = pd.to_datetime(df["date"]).dt.date
            latest = df["date"].max()
            calls = df[(df["option_type"] == "C") & (df["date"] == latest)]
            if calls.empty:
                return None

            # Filter to OTM calls within relevant expirations
            otm = calls[calls["strike"] >= current_price * 0.95].copy()
            otm["otm_pct"] = ((otm["strike"] / current_price) - 1) * 100

            # Only expirations within ~45 days
            otm["exp_date"] = pd.to_datetime(otm["expiration"])
            cutoff = pd.Timestamp(latest) + pd.Timedelta(days=45)
            otm = otm[otm["exp_date"] <= cutoff]
            otm = otm.sort_values(["expiration", "strike"])

            results = []
            for _, r in otm.iterrows():
                exp_d = r["expiration"]
                dte_val = (exp_d - latest).days if hasattr(exp_d, "__sub__") else 0
                if dte_val <= 0:
                    continue
                ann = (r["close"] / current_price) * (365 / dte_val) * 100 if r["close"] > 0 else 0
                results.append({
                    "strike": float(r["strike"]),
                    "expiration": str(r["expiration"]),
                    "dte": dte_val,
                    "premium": float(r["close"]),
                    "volume": int(r.get("volume", 0) or 0),
                    "otm_pct": round(float(r["otm_pct"]), 1),
                    "annualized_return": round(ann, 0),
                    "per_100_shares": round(float(r["close"]) * 100, 0),
                })
            return results if results else None
        except Exception:
            logger.debug("cc_call_candidates_failed", ticker=ticker, exc_info=True)
            return None

    @staticmethod
    def _compute_signal(
        ext_8: float,
        rsi: float,
        iv_rank: float | None,
        vrp: float | None,
        earnings_in_window: bool,
        today: date,
    ) -> tuple[str, str]:
        if earnings_in_window:
            return "WAIT", "Earnings within DTE window — elevated gap risk"

        if ext_8 >= _EXTENSION_THRESHOLD:
            if vrp is not None and vrp < -20:
                return (
                    "CAUTION",
                    f"Extended +{ext_8:.0f}% but VRP is {vrp:.0f}% — options underpriced vs realized moves",
                )
            return "GO", f"Extended +{ext_8:.0f}% above 8-EMA — sell into strength"

        if ext_8 >= 5:
            dow = today.weekday()
            if dow in (0, 1, 2):  # Mon/Tue/Wed — early-week captures more theta to Friday
                return "GO", f"Moderate extension +{ext_8:.0f}% on optimal CC entry day ({_DOW_NAMES.get(dow, '')})"
            return "CAUTION", f"Moderate extension +{ext_8:.0f}% — consider waiting for Mon/Tue/Wed"

        if rsi > 70:
            return "CAUTION", f"RSI overbought ({rsi:.0f}) but extension low — thin premiums"

        return "WAIT", f"Extension only +{ext_8:.0f}% — premiums likely thin, wait for a rally"

    # ------------------------------------------------------------------
    # Live broker integration — fetches real Tradier chain for premiums
    # ------------------------------------------------------------------

    async def analyze_with_live_chain(
        self,
        ticker: str,
        shares: int = 100,
        cost_basis: float = 0.0,
        target_dte: int = 8,
        broker=None,
    ) -> CCDeepDive:
        """Run full analysis, then overlay live quote + premium from broker.

        The base analysis uses OHLCV parquet (last close). During market
        hours, the live quote can differ materially — this method fetches
        the real-time price and recalculates extension, signal, and strike
        before overlaying the live options chain premium.
        """
        result = self.analyze(
            ticker=ticker,
            shares=shares,
            cost_basis=cost_basis,
            target_dte=target_dte,
        )

        if broker is None:
            result.recommended_action["premium_source"] = "historical_estimate"
            return result

        # Overlay live stock price first (recalculates extension & signal)
        await self._overlay_live_quote(result, broker, target_dte)

        # Then fetch live options chain for the (possibly updated) strike
        live = await self._fetch_live_premium(
            broker=broker,
            ticker=ticker,
            target_strike=result.signal.suggested_strike,
            target_dte=target_dte,
        )

        if live is None:
            result.recommended_action["premium_source"] = "historical_estimate"
            return result

        self._overlay_live_premium(result, live, shares)
        return result

    async def analyze_batch_with_live_chain(
        self,
        positions: list[dict],
        target_dte: int = 8,
        broker=None,
    ) -> CCPortfolioAnalysis:
        """Analyze multiple positions with live premium data."""
        import asyncio

        tasks = [
            self.analyze_with_live_chain(
                ticker=pos["ticker"],
                shares=pos.get("shares", 100),
                cost_basis=pos.get("cost_basis", 0.0),
                target_dte=target_dte,
                broker=broker,
            )
            for pos in positions
        ]
        analyses = await asyncio.gather(*tasks, return_exceptions=True)

        valid_analyses = []
        total_premium = 0.0
        go_count = wait_count = caution_count = 0

        for i, result in enumerate(analyses):
            if isinstance(result, Exception):
                logger.warning(
                    "cc_live_analysis_failed",
                    ticker=positions[i]["ticker"],
                    error=str(result),
                )
                result = self.analyze(
                    ticker=positions[i]["ticker"],
                    shares=positions[i].get("shares", 100),
                    cost_basis=positions[i].get("cost_basis", 0.0),
                    target_dte=target_dte,
                )
                result.recommended_action["premium_source"] = "historical_estimate"

            valid_analyses.append(result)

            sig = result.signal
            if sig.signal == "GO":
                go_count += 1
            elif sig.signal == "WAIT":
                wait_count += 1
            else:
                caution_count += 1

            if sig.suggested_premium_est is not None:
                contracts = positions[i].get("shares", 100) // 100
                total_premium += sig.suggested_premium_est * contracts * 100

        return CCPortfolioAnalysis(
            analyses=valid_analyses,
            portfolio_summary={
                "total_premium_est": round(total_premium, 2),
                "positions_go": go_count,
                "positions_wait": wait_count,
                "positions_caution": caution_count,
                "total_positions": len(valid_analyses),
            },
        )

    @staticmethod
    async def _fetch_live_premium(
        *,
        broker,
        ticker: str,
        target_strike: float,
        target_dte: int,
    ) -> dict | None:
        """Fetch live call option premium from broker for the target strike/DTE.

        Returns a dict with bid, ask, mid, strike, expiration, iv, volume,
        open_interest, and option_symbol — or None if unavailable.
        """
        try:
            expirations = await broker.get_options_expirations(ticker)
            if not expirations:
                logger.debug("cc_no_expirations", ticker=ticker)
                return None

            # Find the expiration closest to target DTE
            today = date.today()
            best_exp = None
            best_diff = 999
            for exp_str in expirations:
                exp_date = date.fromisoformat(exp_str)
                diff = abs((exp_date - today).days - target_dte)
                if diff < best_diff and (exp_date - today).days >= 1:
                    best_diff = diff
                    best_exp = exp_str

            if best_exp is None:
                return None

            chain = await broker.get_options_chain(ticker, best_exp, greeks=True)
            if not chain or not chain.calls:
                logger.debug("cc_empty_chain", ticker=ticker, exp=best_exp)
                return None

            # Find the call closest to target strike
            calls = chain.calls
            best_call = min(calls, key=lambda c: abs(c.strike - target_strike))

            # Also grab the exact strike if it exists
            exact_calls = [c for c in calls if c.strike == target_strike]
            if exact_calls:
                best_call = exact_calls[0]

            return {
                "bid": best_call.bid,
                "ask": best_call.ask,
                "mid": best_call.mid,
                "strike": best_call.strike,
                "expiration": best_call.expiration.isoformat(),
                "iv": best_call.implied_volatility,
                "volume": best_call.volume,
                "open_interest": best_call.open_interest,
                "delta": best_call.delta,
                "theta": best_call.theta,
                "option_symbol": best_call.option_symbol,
            }
        except Exception as exc:
            logger.warning("cc_live_chain_error", ticker=ticker, error=str(exc))
            return None

    async def _overlay_live_quote(
        self,
        result: CCDeepDive,
        broker,
        target_dte: int,
    ) -> None:
        """Fetch live stock quote and recalculate price-dependent fields.

        During market hours the live price can differ significantly from
        the last OHLCV close.  This updates: last_close, extension %,
        signal/reason, suggested strike, and OTM%.  EMAs are unchanged
        (they're computed from historical data — one intraday bar won't
        meaningfully shift them).
        """
        try:
            quote = await broker.get_quote(result.signal.ticker)
            live_price = quote.last
            if live_price <= 0:
                live_price = quote.close if quote.close > 0 else quote.bid
            if live_price <= 0:
                return

            prev_close = result.signal.last_close
            if abs(live_price - prev_close) / prev_close < 0.001:
                # Less than 0.1% change — not worth recalculating
                return

            sig = result.signal
            ema_8 = sig.ema_8
            ema_21 = sig.ema_21

            # Recalculate extension
            new_ext_8 = ((live_price / ema_8) - 1) * 100 if ema_8 else 0
            new_ext_21 = ((live_price / ema_21) - 1) * 100 if ema_21 else 0

            # Recalculate signal
            new_signal, new_reason = self._compute_signal(
                new_ext_8,
                sig.rsi_14,
                sig.iv_rank,
                sig.vrp,
                sig.earnings_in_window,
                date.today(),
            )

            # Recalculate strike from episodes
            df = self._ohlcv.read_ticker(sig.ticker)
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date").sort_index()
                close = df["close"]
                ema_8_series = close.ewm(span=8, adjust=False).mean()
                ext_series = (close / ema_8_series - 1) * 100
                episodes = self._find_episodes(ext_series, _EXTENSION_THRESHOLD)
                new_otm = self._suggest_otm(episodes, close, target_dte)
            else:
                new_otm = sig.suggested_otm_pct

            new_strike = round(live_price * (1 + new_otm / 100), 1)

            # Apply updates
            sig.prev_close = round(prev_close, 2)
            sig.live_price = round(live_price, 2)
            sig.price_source = "live_tradier"
            sig.last_close = round(live_price, 2)
            sig.extension_pct_8 = round(new_ext_8, 1)
            sig.extension_pct_21 = round(new_ext_21, 1)
            sig.signal = new_signal
            sig.signal_reason = new_reason
            sig.suggested_strike = new_strike
            sig.suggested_otm_pct = round(new_otm, 1)

            # Update recommendation action/instruction with recalculated signal
            rec = result.recommended_action
            if rec:
                contracts = rec.get("contracts", 1)
                prem = rec.get("premium_est_per_share") or 0.0
                _MIN_PREM = 0.10
                premium_too_thin = 0 < prem < _MIN_PREM

                if new_signal == "GO" and premium_too_thin:
                    action = "SKIP"
                elif new_signal == "GO":
                    action = "SELL"
                elif new_signal == "CAUTION":
                    action = "CONSIDER"
                else:
                    action = "WAIT"

                exp_label = rec.get("expiration_label") or rec.get("expiration_date") or "TBD"
                if action in ("SELL", "CONSIDER"):
                    instruction = (
                        f"SELL {contracts} × {sig.ticker} ${new_strike:.1f} CALL "
                        f"exp {exp_label}"
                    )
                elif action == "SKIP" and premium_too_thin:
                    net = prem * contracts * 100 - contracts * 0.65
                    instruction = (
                        f"SKIP — {sig.ticker} ${new_strike:.1f} Call premium is only "
                        f"${prem:.2f}/share (${net:.0f} net) — not worth the assignment risk"
                    )
                elif action == "WAIT":
                    instruction = f"WAIT — {sig.ticker} not extended enough for meaningful premium"
                else:
                    instruction = rec.get("instruction", "")

                rec["action"] = action
                rec["instruction"] = instruction
                rec["strike"] = new_strike
                rec["otm_pct"] = round(new_otm, 1)
                rec["live_price"] = round(live_price, 2)
                rec["prev_close"] = round(prev_close, 2)
                rec["price_source"] = "live_tradier"

            logger.info(
                "cc_live_quote_overlay",
                ticker=sig.ticker,
                prev_close=round(prev_close, 2),
                live_price=round(live_price, 2),
                ext_8=round(new_ext_8, 1),
                signal=new_signal,
                strike=new_strike,
            )
        except Exception as exc:
            logger.debug(
                "cc_live_quote_failed",
                ticker=result.signal.ticker,
                error=str(exc),
            )

    @staticmethod
    def _overlay_live_premium(
        result: CCDeepDive,
        live: dict,
        shares: int,
    ) -> None:
        """Replace estimated premium with live broker data in-place."""
        contracts = shares // 100
        bid = live["bid"]
        ask = live["ask"]
        mid = live["mid"]
        commission = contracts * 0.65

        # Use bid for conservative estimate (what we'd actually receive)
        prem_per_share = bid
        total_premium = prem_per_share * contracts * 100
        net_premium = total_premium - commission

        # Update the signal-level estimate
        result.signal.suggested_premium_est = round(prem_per_share, 2)
        result.signal.suggested_strike = live["strike"]

        # Update the recommendation
        rec = result.recommended_action
        rec["premium_est_per_share"] = round(prem_per_share, 2)
        rec["total_premium_est"] = round(total_premium, 2)
        rec["net_premium_est"] = round(net_premium, 2)
        rec["strike"] = live["strike"]
        rec["expiration_date"] = live["expiration"]
        rec["premium_source"] = "live_tradier"
        rec["live_bid"] = bid
        rec["live_ask"] = ask
        rec["live_mid"] = round(mid, 2)
        rec["live_iv"] = round(live.get("iv", 0), 4)
        rec["live_volume"] = live.get("volume", 0)
        rec["live_oi"] = live.get("open_interest", 0)
        rec["live_delta"] = round(live.get("delta", 0), 4)
        rec["live_theta"] = round(live.get("theta", 0), 4)
        rec["option_symbol"] = live.get("option_symbol")

        # Re-evaluate premium quality with live data
        _MIN_PREMIUM_PER_SHARE = 0.10
        premium_too_thin = 0 < prem_per_share < _MIN_PREMIUM_PER_SHARE

        if premium_too_thin:
            rec["action"] = "SKIP"
            rec["instruction"] = (
                f"SKIP — {result.signal.ticker} ${live['strike']:.1f} Call bid is only "
                f"${prem_per_share:.2f}/share (${net_premium:.0f} net) — not worth the assignment risk"
            )
            if not any("too thin" in w for w in rec.get("warnings", [])):
                rec.setdefault("warnings", []).append(
                    f"Live bid ${bid:.2f}/share "
                    f"(${net_premium:.0f} net for {contracts} contracts) — "
                    f"too thin to justify assignment risk"
                )
        elif rec["action"] == "SKIP" and prem_per_share >= _MIN_PREMIUM_PER_SHARE:
            # Historical estimate triggered SKIP, but live data shows
            # premium is actually viable — upgrade back
            original_signal = result.signal.signal
            if original_signal == "GO":
                rec["action"] = "SELL"
                rec["instruction"] = (
                    f"SELL {contracts} × {result.signal.ticker} "
                    f"${live['strike']:.1f} CALL exp {live['expiration']}"
                )
            elif original_signal == "CAUTION":
                rec["action"] = "CONSIDER"
                rec["instruction"] = (
                    f"SELL {contracts} × {result.signal.ticker} "
                    f"${live['strike']:.1f} CALL exp {live['expiration']}"
                )
            rec["warnings"] = [
                w for w in rec.get("warnings", []) if "too thin" not in w
            ]

        # Update P&L scenarios with live data
        if result.pnl_scenarios:
            pnl = result.pnl_scenarios
            if "if_not_called" in pnl:
                pnl["if_not_called"]["premium_income"] = round(net_premium, 2)
            if "if_called" in pnl:
                pnl["if_called"]["premium_income"] = round(net_premium, 2)
                pnl["if_called"]["effective_sell_price"] = round(
                    live["strike"] + prem_per_share, 2,
                )
                stock_gain = (live["strike"] - result.signal.last_close) * shares
                pnl["if_called"]["stock_gain"] = round(stock_gain, 2)
                pnl["if_called"]["total_gain"] = round(
                    stock_gain + net_premium, 2,
                )
