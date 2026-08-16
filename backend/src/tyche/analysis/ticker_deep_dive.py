"""Per-ticker deep dive engine.

Computes multi-timeframe RSI (daily/weekly/monthly/quarterly), EMA stack,
MACD, Bollinger Bands, volume profile, period returns, and aggregates
fundamentals/estimates/catalyst data from existing stores. All computation
is local — no network calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
import structlog

from tyche.conviction.features import compute_ema, compute_rsi, compute_slope

logger = structlog.get_logger()

_RSI_PERIOD = 14


@dataclass
class RSIReading:
    """Single RSI data point with date and close price context."""

    date: str
    value: float
    close: float


@dataclass
class MultiTimeframeRSI:
    """RSI computed at four timeframes."""

    daily: float = 50.0
    weekly: float = 50.0
    monthly: float = 50.0
    quarterly: float = 50.0
    weekly_history: list[RSIReading] = field(default_factory=list)
    monthly_history: list[RSIReading] = field(default_factory=list)
    quarterly_history: list[RSIReading] = field(default_factory=list)


@dataclass
class EMAStack:
    """EMA values and distance metrics."""

    ema_8: float = 0.0
    ema_21: float = 0.0
    ema_50: float = 0.0
    sma_200: float = 0.0
    pct_vs_ema_8: float = 0.0
    pct_vs_ema_21: float = 0.0
    pct_vs_ema_50: float = 0.0
    pct_vs_sma_200: float = 0.0
    slope_ema_8: float = 0.0
    slope_ema_21: float = 0.0
    slope_ema_50: float = 0.0
    days_above_ema_8: int = 0
    days_above_ema_21: int = 0
    stack_score: int = 0


@dataclass
class MACDData:
    """MACD indicator values."""

    macd_line: float = 0.0
    signal_line: float = 0.0
    histogram: float = 0.0


@dataclass
class BollingerBands:
    """Bollinger Band values."""

    upper: float = 0.0
    middle: float = 0.0
    lower: float = 0.0
    width_pct: float = 0.0
    pct_b: float = 0.0


@dataclass
class VolumeBar:
    """Single volume bar for chart rendering."""

    date: str
    volume: float
    close: float


@dataclass
class PricePoint:
    """Single price data point for chart rendering."""

    date: str
    close: float


@dataclass
class FundamentalsPeriod:
    """One quarter of fundamental data."""

    period: str
    revenue: float | None = None
    gross_profit: float | None = None
    gross_margin: float | None = None
    operating_income: float | None = None
    operating_margin: float | None = None
    net_income: float | None = None
    net_margin: float | None = None
    eps_diluted: float | None = None
    cash: float | None = None
    operating_cash_flow: float | None = None
    total_debt: float | None = None


@dataclass
class EstimatesSnapshot:
    """Latest analyst consensus estimates."""

    pt_mean: float | None = None
    pt_median: float | None = None
    pt_high: float | None = None
    pt_low: float | None = None
    analyst_count: int | None = None
    rev_growth_q_yoy: float | None = None
    rev_growth_ttm_yoy: float | None = None
    gross_margin_ttm: float | None = None
    op_margin_ttm: float | None = None
    current_ratio: float | None = None
    debt_to_equity: float | None = None
    forward_eps: list[dict] = field(default_factory=list)
    forward_rev: list[dict] = field(default_factory=list)


@dataclass
class CatalystEvent:
    """Single catalyst event from guidance/news."""

    date: str
    tag: str
    impact: float
    source: str


@dataclass
class TickerDeepDive:
    """Full deep-dive analysis for a single ticker."""

    ticker: str
    name: str = ""
    sector: str = ""
    last_close: float = 0.0
    market_cap: float | None = None
    institutional_pct: float | None = None
    high_52w: float = 0.0
    low_52w: float = 0.0
    pct_off_52w_high: float = 0.0
    as_of_date: str = ""

    rsi: MultiTimeframeRSI = field(default_factory=MultiTimeframeRSI)
    ema_stack: EMAStack = field(default_factory=EMAStack)
    macd: MACDData = field(default_factory=MACDData)
    bollinger: BollingerBands = field(default_factory=BollingerBands)

    returns: dict[str, float] = field(default_factory=dict)
    price_history: list[PricePoint] = field(default_factory=list)
    volume_bars: list[VolumeBar] = field(default_factory=list)

    fundamentals: list[FundamentalsPeriod] = field(default_factory=list)
    estimates: EstimatesSnapshot = field(default_factory=EstimatesSnapshot)
    catalysts: list[CatalystEvent] = field(default_factory=list)


class TickerDeepDiveEngine:
    """Stateless deep-dive engine — computes from stores on demand.

    Follows the CCAnalysisEngine pattern: accepts stores at construction,
    performs all computation locally from OHLCV + demand data.
    """

    def __init__(
        self,
        ohlcv_store,
        meta_store=None,
        fundamentals_store=None,
        estimates_store=None,
        catalyst_store=None,
    ) -> None:
        self._ohlcv = ohlcv_store
        self._meta = meta_store
        self._fundamentals = fundamentals_store
        self._estimates = estimates_store
        self._catalysts = catalyst_store

    def analyze(self, ticker: str) -> TickerDeepDive:
        """Run full deep-dive analysis for a single ticker."""
        df = self._ohlcv.read_ticker(ticker)
        if df is None or df.empty or len(df) < 50:
            logger.warning("deep_dive_insufficient_data", ticker=ticker, bars=len(df) if df is not None else 0)
            return TickerDeepDive(ticker=ticker)

        df = df.copy()
        df["date_ts"] = pd.to_datetime(df["date"])
        closes = pd.Series(df["close"].values, dtype=float)

        result = TickerDeepDive(ticker=ticker)
        result.last_close = float(closes.iloc[-1])
        result.as_of_date = str(df["date"].iloc[-1])

        last_252 = df.tail(252)
        result.high_52w = float(last_252["high"].max())
        result.low_52w = float(last_252["low"].min())
        if result.high_52w > 0:
            result.pct_off_52w_high = round((1 - result.last_close / result.high_52w) * 100, 2)

        self._compute_metadata(result, ticker)
        self._compute_rsi(result, df, closes)
        self._compute_ema_stack(result, closes)
        self._compute_macd(result, closes)
        self._compute_bollinger(result, closes)
        self._compute_returns(result, closes)
        self._compute_price_history(result, df)
        self._compute_volume_bars(result, df)
        self._aggregate_fundamentals(result, ticker)
        self._aggregate_estimates(result, ticker)
        self._aggregate_catalysts(result, ticker)

        return result

    def _compute_metadata(self, result: TickerDeepDive, ticker: str) -> None:
        if self._meta is None:
            return
        meta = self._meta.get_meta_batch([ticker]).get(ticker, {})
        result.name = meta.get("name") or ""
        result.sector = meta.get("sector") or ""
        result.market_cap = meta.get("market_cap")
        inst = self._meta.get_institutional_pcts([ticker])
        result.institutional_pct = inst.get(ticker)

    # ── RSI (multi-timeframe) ──────────────────────────────────────────

    def _compute_rsi(self, result: TickerDeepDive, df: pd.DataFrame, closes: pd.Series) -> None:
        result.rsi.daily = round(compute_rsi(closes, _RSI_PERIOD), 1)

        ts_df = df.set_index("date_ts")

        weekly = ts_df["close"].resample("W-FRI").last().dropna()
        result.rsi.weekly = round(self._rsi_series(weekly), 1)
        w_rsi = self._rsi_full_series(weekly)
        for dt, val in w_rsi.tail(12).items():
            if np.isnan(val):
                continue
            result.rsi.weekly_history.append(
                RSIReading(date=str(dt.date()), value=round(val, 1), close=round(weekly.loc[dt], 2))
            )

        monthly = ts_df["close"].resample("ME").last().dropna()
        result.rsi.monthly = round(self._rsi_series(monthly), 1)
        m_rsi = self._rsi_full_series(monthly)
        for dt, val in m_rsi.tail(12).items():
            if np.isnan(val):
                continue
            result.rsi.monthly_history.append(
                RSIReading(date=str(dt.date()), value=round(val, 1), close=round(monthly.loc[dt], 2))
            )

        quarterly = ts_df["close"].resample("QE").last().dropna()
        result.rsi.quarterly = round(self._rsi_series(quarterly), 1)
        q_rsi = self._rsi_full_series(quarterly)
        for dt, val in q_rsi.items():
            if not np.isnan(val):
                result.rsi.quarterly_history.append(
                    RSIReading(date=str(dt.date()), value=round(val, 1), close=round(quarterly.loc[dt], 2))
                )

    @staticmethod
    def _rsi_series(closes: pd.Series) -> float:
        """Compute latest RSI from a resampled close series."""
        if len(closes) < _RSI_PERIOD + 1:
            return 50.0
        delta = closes.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / _RSI_PERIOD, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / _RSI_PERIOD, adjust=False).mean()
        last_loss = float(loss.iloc[-1])
        if last_loss == 0:
            return 100.0
        rs = float(gain.iloc[-1]) / last_loss
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def _rsi_full_series(closes: pd.Series) -> pd.Series:
        """Compute RSI as a full series for history charts."""
        delta = closes.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / _RSI_PERIOD, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / _RSI_PERIOD, adjust=False).mean()
        rs = gain / loss
        return 100.0 - (100.0 / (1.0 + rs))

    # ── EMA Stack ──────────────────────────────────────────────────────

    def _compute_ema_stack(self, result: TickerDeepDive, closes: pd.Series) -> None:
        ema8 = compute_ema(closes, 8)
        ema21 = compute_ema(closes, 21)
        ema50 = compute_ema(closes, 50)
        sma200 = closes.rolling(200).mean()

        price = result.last_close
        result.ema_stack.ema_8 = round(float(ema8.iloc[-1]), 2)
        result.ema_stack.ema_21 = round(float(ema21.iloc[-1]), 2)
        result.ema_stack.ema_50 = round(float(ema50.iloc[-1]), 2)
        result.ema_stack.sma_200 = round(float(sma200.iloc[-1]), 2) if not np.isnan(sma200.iloc[-1]) else 0.0

        for attr, ema_val in [
            ("pct_vs_ema_8", result.ema_stack.ema_8),
            ("pct_vs_ema_21", result.ema_stack.ema_21),
            ("pct_vs_ema_50", result.ema_stack.ema_50),
            ("pct_vs_sma_200", result.ema_stack.sma_200),
        ]:
            if ema_val > 0:
                setattr(result.ema_stack, attr, round((price / ema_val - 1) * 100, 2))

        result.ema_stack.slope_ema_8 = round(self._pct_slope(ema8, 5), 2)
        result.ema_stack.slope_ema_21 = round(self._pct_slope(ema21, 5), 2)
        result.ema_stack.slope_ema_50 = round(self._pct_slope(ema50, 5), 2)

        # Streak: consecutive days above each EMA from the end
        result.ema_stack.days_above_ema_8 = self._consecutive_above(closes, ema8)
        result.ema_stack.days_above_ema_21 = self._consecutive_above(closes, ema21)

        # Stack score: how many EMAs price is above (0-3)
        score = 0
        if price > result.ema_stack.ema_8:
            score += 1
        if price > result.ema_stack.ema_21:
            score += 1
        if price > result.ema_stack.ema_50:
            score += 1
        result.ema_stack.stack_score = score

    @staticmethod
    def _pct_slope(series: pd.Series, n: int = 5) -> float:
        if len(series) < n:
            return 0.0
        old = float(series.iloc[-n])
        if old == 0:
            return 0.0
        return (float(series.iloc[-1]) - old) / old * 100

    @staticmethod
    def _consecutive_above(price: pd.Series, ema: pd.Series) -> int:
        count = 0
        for i in range(len(price) - 1, -1, -1):
            if price.iloc[i] > ema.iloc[i]:
                count += 1
            else:
                break
        return count

    # ── MACD ───────────────────────────────────────────────────────────

    def _compute_macd(self, result: TickerDeepDive, closes: pd.Series) -> None:
        ema12 = closes.ewm(span=12, adjust=False).mean()
        ema26 = closes.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        result.macd.macd_line = round(float(macd_line.iloc[-1]), 4)
        result.macd.signal_line = round(float(signal_line.iloc[-1]), 4)
        result.macd.histogram = round(float((macd_line - signal_line).iloc[-1]), 4)

    # ── Bollinger Bands ────────────────────────────────────────────────

    def _compute_bollinger(self, result: TickerDeepDive, closes: pd.Series) -> None:
        sma20 = closes.rolling(20).mean()
        std20 = closes.rolling(20).std()
        upper = float(sma20.iloc[-1] + 2 * std20.iloc[-1])
        lower = float(sma20.iloc[-1] - 2 * std20.iloc[-1])
        middle = float(sma20.iloc[-1])
        result.bollinger.upper = round(upper, 2)
        result.bollinger.middle = round(middle, 2)
        result.bollinger.lower = round(lower, 2)
        if middle > 0:
            result.bollinger.width_pct = round((upper - lower) / middle * 100, 1)
        if upper != lower:
            result.bollinger.pct_b = round((result.last_close - lower) / (upper - lower) * 100, 1)

    # ── Returns ────────────────────────────────────────────────────────

    def _compute_returns(self, result: TickerDeepDive, closes: pd.Series) -> None:
        for label, days in [("1W", 5), ("2W", 10), ("1M", 21), ("3M", 63), ("6M", 126), ("1Y", 252)]:
            if len(closes) > days:
                ret = (closes.iloc[-1] / closes.iloc[-days - 1] - 1) * 100
                result.returns[label] = round(ret, 1)

    # ── Price history (sampled for chart) ──────────────────────────────

    def _compute_price_history(self, result: TickerDeepDive, df: pd.DataFrame) -> None:
        ts_df = df.set_index("date_ts")
        # Use weekly closes for a manageable chart, show last 2 years
        weekly = ts_df["close"].resample("W-FRI").last().dropna().tail(104)
        for dt, close in weekly.items():
            result.price_history.append(PricePoint(date=str(dt.date()), close=round(close, 2)))

    # ── Volume bars (last 60 days) ─────────────────────────────────────

    def _compute_volume_bars(self, result: TickerDeepDive, df: pd.DataFrame) -> None:
        for _, row in df.tail(60).iterrows():
            result.volume_bars.append(VolumeBar(
                date=str(row["date"]),
                volume=round(float(row["volume"]) / 1e6, 2),
                close=round(float(row["close"]), 2),
            ))

    # ── Fundamentals aggregation ───────────────────────────────────────

    def _aggregate_fundamentals(self, result: TickerDeepDive, ticker: str) -> None:
        if self._fundamentals is None:
            return
        df = self._fundamentals.read_ticker(ticker)
        if df is None or df.empty:
            return

        df = df.sort_values("period_end", ascending=True)
        quarterly = df[df.get("timeframe", pd.Series(dtype=str)).isin(["quarterly", ""])]
        if quarterly.empty:
            quarterly = df

        for _, row in quarterly.tail(6).iterrows():
            period_end = row.get("period_end")
            label = str(period_end) if period_end else "?"
            if hasattr(period_end, "strftime"):
                label = period_end.strftime("Q%q'%y") if hasattr(period_end, "quarter") else str(period_end)
                q = (period_end.month - 1) // 3 + 1 if hasattr(period_end, "month") else 0
                yr = str(period_end.year)[-2:] if hasattr(period_end, "year") else "??"
                label = f"Q{q}'{yr}"

            fp = FundamentalsPeriod(period=label)
            for field_name in [
                "revenue", "gross_profit", "operating_income", "net_income",
                "eps_diluted", "cash_and_equivalents", "operating_cash_flow", "total_debt",
            ]:
                val = row.get(field_name)
                if pd.notna(val):
                    setattr(fp, field_name if field_name != "cash_and_equivalents" else "cash", float(val))

            for margin_field in ["gross_margin", "operating_margin", "net_margin"]:
                val = row.get(margin_field)
                if pd.notna(val):
                    setattr(fp, margin_field, float(val))

            result.fundamentals.append(fp)

    # ── Estimates aggregation ──────────────────────────────────────────

    def _aggregate_estimates(self, result: TickerDeepDive, ticker: str) -> None:
        if self._estimates is None:
            return
        df = self._estimates.read_ticker(ticker)
        if df is None or df.empty:
            return

        latest_date = df["snapshot_date"].max()
        latest = df[df["snapshot_date"] == latest_date]

        def _get(metric: str) -> float | None:
            rows = latest[latest["metric"] == metric]
            if rows.empty:
                return None
            val = rows.iloc[0]["value"]
            return float(val) if pd.notna(val) else None

        result.estimates.pt_mean = _get("price_target_mean")
        result.estimates.pt_median = _get("price_target_median")
        result.estimates.pt_high = _get("price_target_high")
        result.estimates.pt_low = _get("price_target_low")
        result.estimates.rev_growth_q_yoy = _get("fin_revenue_growth_q_yoy")
        result.estimates.rev_growth_ttm_yoy = _get("fin_revenue_growth_ttm_yoy")
        result.estimates.gross_margin_ttm = _get("fin_gross_margin_ttm")
        result.estimates.op_margin_ttm = _get("fin_operating_margin_ttm")
        result.estimates.current_ratio = _get("fin_current_ratio")
        result.estimates.debt_to_equity = _get("fin_debt_to_equity")

        # Analyst count from EPS estimate count
        eps_count_rows = latest[(latest["metric"] == "eps_est_count")]
        if not eps_count_rows.empty:
            result.estimates.analyst_count = int(eps_count_rows.iloc[0]["value"])

        # Forward estimates
        for metric_key, result_list in [("eps_est_avg", result.estimates.forward_eps), ("rev_est_avg", result.estimates.forward_rev)]:
            rows = latest[latest["metric"] == metric_key].sort_values("period")
            for _, row in rows.head(4).iterrows():
                result_list.append({"period": str(row["period"]), "value": round(float(row["value"]), 4)})

    # ── Catalysts ──────────────────────────────────────────────────────

    def _aggregate_catalysts(self, result: TickerDeepDive, ticker: str) -> None:
        if self._catalysts is None:
            return
        df = self._catalysts.read_ticker(ticker)
        if df is None or df.empty:
            return

        df = df.sort_values("event_date", ascending=True)
        for _, row in df.tail(15).iterrows():
            evt_date = row.get("event_date")
            result.catalysts.append(CatalystEvent(
                date=str(evt_date) if evt_date else "",
                tag=str(row.get("tag", "")),
                impact=round(float(row.get("signed_impact", 0)), 3),
                source=str(row.get("source", "")),
            ))
