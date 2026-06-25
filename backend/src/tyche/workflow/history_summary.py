"""Build compact per-ticker history summaries from OHLCV (+ optional conviction)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import structlog

from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.market_data.stocks_conviction_store import load_stocks_conviction_parquet
from tyche.storage.paths import StorageContext

logger = structlog.get_logger()

STOCKS_HISTORY_SUMMARY_REL = "signals/stocks/history_summary.parquet"

_TRADING_DAYS = {
    "return_1d": 1,
    "return_5d": 5,
    "return_1m": 21,
    "return_3m": 63,
    "return_6m": 126,
    "return_1y": 252,
}


def _atr_14(high: pd.Series, low: pd.Series, close: pd.Series) -> float | None:
    if len(close) < 15:
        return None
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(14, min_periods=14).mean().iloc[-1]
    if pd.isna(atr):
        return None
    return float(atr)


def _summary_row_from_ohlcv(
    ticker: str,
    df: pd.DataFrame,
    *,
    as_of: date,
    computed_at: datetime,
    trend_state: str | None,
    source_run_id: str | None,
) -> dict[str, Any] | None:
    if df is None or df.empty or len(df) < 2:
        return None

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)
    last_price = float(close.iloc[-1])

    row: dict[str, Any] = {
        "ticker": ticker,
        "as_of": as_of.isoformat(),
        "last_price": round(last_price, 2),
        "generated_at": computed_at.isoformat(),
        "source_run_id": source_run_id,
        "trend_state": trend_state,
    }

    for col, offset in _TRADING_DAYS.items():
        if len(close) > offset:
            prior = float(close.iloc[-1 - offset])
            row[col] = round((last_price / prior - 1) * 100, 2) if prior else None
        else:
            row[col] = None

    window = min(252, len(close))
    recent_high = float(high.iloc[-window:].max())
    recent_low = float(low.iloc[-window:].min())
    row["high_52w"] = round(recent_high, 2)
    row["low_52w"] = round(recent_low, 2)
    row["drawdown_52w_pct"] = (
        round((last_price - recent_high) / recent_high * 100, 2)
        if recent_high
        else None
    )

    vol_window = min(30, len(volume))
    row["avg_volume_30d"] = int(volume.iloc[-vol_window:].mean())

    atr = _atr_14(high, low, close)
    row["atr_14"] = round(atr, 4) if atr is not None else None

    return row


def build_history_summary_rows(
    *,
    data_store: OHLCVStore,
    ticker_meta_store: TickerMetaStore,
    tickers: list[str],
    ctx: StorageContext | None,
    as_of: date,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Compute compact history summaries for *tickers* (metadata-filtered universe)."""
    computed_at = datetime.now(timezone.utc)
    trend_by_ticker: dict[str, str | None] = {}
    if ctx is not None:
        conviction_rows, _ = load_stocks_conviction_parquet(ctx=ctx)
        trend_by_ticker = {r.ticker: r.trend_state for r in conviction_rows}

    rows: list[dict[str, Any]] = []
    ticker_data = data_store.read_tickers(tickers)
    for ticker in tickers:
        df = ticker_data.get(ticker)
        summary = _summary_row_from_ohlcv(
            ticker,
            df,
            as_of=as_of,
            computed_at=computed_at,
            trend_state=trend_by_ticker.get(ticker),
            source_run_id=run_id,
        )
        if summary:
            rows.append(summary)

    rows.sort(key=lambda r: r.get("last_price") or 0.0, reverse=True)
    logger.info("history_summary_built", rows=len(rows), as_of=as_of.isoformat())
    return rows


def select_history_universe(
    data_store: OHLCVStore,
    ticker_meta_store: TickerMetaStore,
    *,
    min_market_cap: float,
) -> list[str]:
    """Metadata-first universe selection before per-ticker OHLCV reads."""
    all_tickers = data_store.get_all_tickers()
    equity = (
        ticker_meta_store.filter_equity_only(all_tickers)
        if ticker_meta_store.exists
        else all_tickers
    )
    if not ticker_meta_store.exists or min_market_cap <= 0:
        return equity

    caps = ticker_meta_store.get_market_caps(equity)
    return [
        t
        for t in equity
        if caps.get(t, 0) >= min_market_cap or caps.get(t, 0) == 0
    ]
