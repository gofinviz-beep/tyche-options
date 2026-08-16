"""Pydantic response schemas for the Ticker Deep Dive API."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from tyche.analysis.ticker_deep_dive import TickerDeepDive


def _rsi_history(readings: list) -> list["RSIReadingResponse"]:
    """Serialize RSI history, dropping points whose value is undefined.

    RSI is undefined during its warmup window; short-history tickers (recent
    IPOs) can produce ``NaN``/``None`` values that serialize to ``null`` and
    break the read schema. Drop them so payloads stay clean and round-trip.
    """
    out: list[RSIReadingResponse] = []
    for r in readings:
        value = getattr(r, "value", None)
        if value is None or (isinstance(value, float) and math.isnan(value)):
            continue
        out.append(RSIReadingResponse(**r.__dict__))
    return out


class RSIReadingResponse(BaseModel):
    date: str
    # Nullable: RSI is undefined during the warmup window (e.g. recent IPOs
    # without enough weekly/monthly bars). Tolerate legacy payloads that
    # persisted a null value; fresh payloads drop these points entirely.
    value: float | None = None
    close: float


class MultiTimeframeRSIResponse(BaseModel):
    daily: float = 50.0
    weekly: float = 50.0
    monthly: float = 50.0
    quarterly: float = 50.0
    weekly_history: list[RSIReadingResponse] = []
    monthly_history: list[RSIReadingResponse] = []
    quarterly_history: list[RSIReadingResponse] = []


class EMAStackResponse(BaseModel):
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


class MACDResponse(BaseModel):
    macd_line: float = 0.0
    signal_line: float = 0.0
    histogram: float = 0.0


class BollingerResponse(BaseModel):
    upper: float = 0.0
    middle: float = 0.0
    lower: float = 0.0
    width_pct: float = 0.0
    pct_b: float = 0.0


class VolumeBarResponse(BaseModel):
    date: str
    volume: float
    close: float


class PricePointResponse(BaseModel):
    date: str
    close: float


class FundamentalsPeriodResponse(BaseModel):
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


class EstimatesResponse(BaseModel):
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
    forward_eps: list[dict] = []
    forward_rev: list[dict] = []


class CatalystResponse(BaseModel):
    date: str
    tag: str
    impact: float
    source: str


class TickerDeepDiveResponse(BaseModel):
    """Full deep-dive analysis response for a single ticker."""

    ticker: str
    name: str = ""
    sector: str = ""
    last_close: float = 0.0
    market_cap: float | None = None
    institutional_pct: float | None = Field(
        default=None,
        description="Institutional ownership as a 0-1 fraction (0.82 = 82% held).",
    )
    high_52w: float = 0.0
    low_52w: float = 0.0
    pct_off_52w_high: float = 0.0
    as_of_date: str = ""

    rsi: MultiTimeframeRSIResponse = MultiTimeframeRSIResponse()
    ema_stack: EMAStackResponse = EMAStackResponse()
    macd: MACDResponse = MACDResponse()
    bollinger: BollingerResponse = BollingerResponse()

    returns: dict[str, float] = {}
    price_history: list[PricePointResponse] = []
    volume_bars: list[VolumeBarResponse] = []

    fundamentals: list[FundamentalsPeriodResponse] = []
    estimates: EstimatesResponse = EstimatesResponse()
    catalysts: list[CatalystResponse] = []


def to_response(result: "TickerDeepDive") -> TickerDeepDiveResponse:
    """Map the ``TickerDeepDiveEngine`` dataclass to the Pydantic response.

    Single shared serialization path used by BOTH the on-demand route and
    the nightly precompute batch, so cached and live payloads are always
    byte-for-byte the same shape. Margin/growth fields stay percent-scale
    (e.g. ``46.88``) — the frontend formats them without ``x100``.
    """
    return TickerDeepDiveResponse(
        ticker=result.ticker,
        name=result.name,
        sector=result.sector,
        last_close=result.last_close,
        market_cap=result.market_cap,
        institutional_pct=result.institutional_pct,
        high_52w=result.high_52w,
        low_52w=result.low_52w,
        pct_off_52w_high=result.pct_off_52w_high,
        as_of_date=result.as_of_date,
        rsi=MultiTimeframeRSIResponse(
            daily=result.rsi.daily,
            weekly=result.rsi.weekly,
            monthly=result.rsi.monthly,
            quarterly=result.rsi.quarterly,
            weekly_history=_rsi_history(result.rsi.weekly_history),
            monthly_history=_rsi_history(result.rsi.monthly_history),
            quarterly_history=_rsi_history(result.rsi.quarterly_history),
        ),
        ema_stack=EMAStackResponse(**result.ema_stack.__dict__),
        macd=MACDResponse(**result.macd.__dict__),
        bollinger=BollingerResponse(**result.bollinger.__dict__),
        returns=result.returns,
        price_history=[PricePointResponse(**p.__dict__) for p in result.price_history],
        volume_bars=[VolumeBarResponse(**v.__dict__) for v in result.volume_bars],
        fundamentals=[
            FundamentalsPeriodResponse(**f.__dict__) for f in result.fundamentals
        ],
        estimates=EstimatesResponse(
            pt_mean=result.estimates.pt_mean,
            pt_median=result.estimates.pt_median,
            pt_high=result.estimates.pt_high,
            pt_low=result.estimates.pt_low,
            analyst_count=result.estimates.analyst_count,
            rev_growth_q_yoy=result.estimates.rev_growth_q_yoy,
            rev_growth_ttm_yoy=result.estimates.rev_growth_ttm_yoy,
            gross_margin_ttm=result.estimates.gross_margin_ttm,
            op_margin_ttm=result.estimates.op_margin_ttm,
            current_ratio=result.estimates.current_ratio,
            debt_to_equity=result.estimates.debt_to_equity,
            forward_eps=result.estimates.forward_eps,
            forward_rev=result.estimates.forward_rev,
        ),
        catalysts=[CatalystResponse(**c.__dict__) for c in result.catalysts],
    )
