"""Filing signal builder — computes aggregate per-ticker signals from 8-K and Form 4 data.

Reads classified 8-K filings and parsed insider transactions from their
respective Parquet stores, computes aggregate metrics (including the
high-value insider cluster-sell detection), and writes/updates
FilingSignal rows in news.db.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import structlog
from sqlalchemy import select

from tyche.market_data.filing_store import Filing8KStore, InsiderTxStore
from tyche.models.filing import FilingSignal
from tyche.persistence.database import get_session

logger = structlog.get_logger()

_LOOKBACK_DAYS = 30
_CLUSTER_WINDOW_DAYS = 7
_CLUSTER_MIN_INSIDERS = 3


def compute_filing_signal(
    ticker: str,
    filings_8k: pd.DataFrame,
    insider_tx: pd.DataFrame,
    lookback_days: int = _LOOKBACK_DAYS,
) -> dict:
    """Compute aggregate filing signal from 8-K and insider transaction data.

    Args:
        ticker: Stock symbol.
        filings_8k: DataFrame of classified 8-K filings.
        insider_tx: DataFrame of Form 4 insider transactions.
        lookback_days: Window for aggregate metrics.

    Returns:
        Dict ready to construct/update a FilingSignal row.
    """
    now = datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(days=lookback_days)

    # --- 8-K signals ---
    last_8k_at = None
    last_8k_sentiment = None
    last_8k_impact = None
    eightk_count_30d = 0

    if not filings_8k.empty:
        filings_8k = filings_8k.copy()
        filings_8k["filed_at"] = pd.to_datetime(filings_8k["filed_at"], utc=True)
        recent_8k = filings_8k[filings_8k["filed_at"] >= cutoff]
        eightk_count_30d = len(recent_8k)

        classified = filings_8k[
            filings_8k["event_type"].notna() & (filings_8k["event_type"] != "")
        ]

        if not classified.empty:
            latest = classified.sort_values("filed_at", ascending=False).iloc[0]
            last_8k_at = latest["filed_at"]
            if pd.notna(last_8k_at):
                last_8k_at = last_8k_at.to_pydatetime()
            else:
                last_8k_at = None
            last_8k_sentiment = latest.get("sentiment")
            if pd.isna(last_8k_sentiment):
                last_8k_sentiment = None
            impact = latest.get("impact_score")
            last_8k_impact = float(impact) if pd.notna(impact) else None

    # --- Insider transaction signals ---
    insider_net_shares = 0.0
    buy_count = 0
    sell_count = 0
    cluster_sell = False
    last_insider_at = None

    if not insider_tx.empty:
        insider_tx = insider_tx.copy()
        insider_tx["filed_at"] = pd.to_datetime(insider_tx["filed_at"], utc=True)
        recent_tx = insider_tx[insider_tx["filed_at"] >= cutoff]

        if not recent_tx.empty:
            buys = recent_tx[recent_tx["transaction_type"] == "P"]
            sells = recent_tx[recent_tx["transaction_type"] == "S"]

            buy_shares = buys["shares"].sum() if not buys.empty else 0.0
            sell_shares = sells["shares"].sum() if not sells.empty else 0.0
            insider_net_shares = float(buy_shares - sell_shares)

            buy_count = len(buys)
            sell_count = len(sells)

            last_tx = recent_tx.sort_values("filed_at", ascending=False).iloc[0]
            last_insider_at = last_tx["filed_at"]
            if pd.notna(last_insider_at):
                last_insider_at = last_insider_at.to_pydatetime()
            else:
                last_insider_at = None

            cluster_sell = _detect_cluster_sell(sells)

    return {
        "ticker": ticker.upper(),
        "last_8k_at": last_8k_at,
        "last_8k_sentiment": last_8k_sentiment,
        "last_8k_impact": last_8k_impact,
        "eightk_count_30d": eightk_count_30d,
        "insider_net_shares_30d": round(insider_net_shares, 2),
        "insider_buy_count_30d": buy_count,
        "insider_sell_count_30d": sell_count,
        "insider_cluster_sell": cluster_sell,
        "last_insider_tx_at": last_insider_at,
        "updated_at": now,
    }


def _detect_cluster_sell(sells: pd.DataFrame) -> bool:
    """Detect if 3+ distinct insiders sold within a 7-day window.

    This is the highest-value signal — multiple C-suite insiders selling
    in a tight window is a strong negative predictor.
    """
    if sells.empty or len(sells) < _CLUSTER_MIN_INSIDERS:
        return False

    sells = sells.copy()
    sells["filed_at"] = pd.to_datetime(sells["filed_at"], utc=True)
    sells = sells.sort_values("filed_at")

    window = timedelta(days=_CLUSTER_WINDOW_DAYS)

    for i, row in sells.iterrows():
        window_end = row["filed_at"] + window
        in_window = sells[
            (sells["filed_at"] >= row["filed_at"])
            & (sells["filed_at"] <= window_end)
        ]
        unique_sellers = in_window["insider_name"].nunique()
        if unique_sellers >= _CLUSTER_MIN_INSIDERS:
            return True

    return False


async def rebuild_filing_signals(
    filing_store: Filing8KStore,
    insider_store: InsiderTxStore,
    tickers: list[str] | None = None,
    lookback_days: int = _LOOKBACK_DAYS,
) -> int:
    """Rebuild filing signals for given tickers (or all tickers with data).

    Returns:
        Number of tickers updated.
    """
    all_tickers: set[str] = set()
    if tickers is not None:
        all_tickers = {t.upper() for t in tickers}
    else:
        all_tickers = set(filing_store.list_tickers()) | set(
            insider_store.list_tickers()
        )

    if not all_tickers:
        return 0

    updated = 0
    async with get_session("news") as session:
        for ticker in sorted(all_tickers):
            filings_8k = filing_store.read_recent(ticker, days=lookback_days)
            insider_tx = insider_store.read_recent(ticker, days=lookback_days)

            signal_data = compute_filing_signal(
                ticker, filings_8k, insider_tx, lookback_days
            )

            existing = await session.get(FilingSignal, ticker)
            if existing is not None:
                for key, value in signal_data.items():
                    if key != "ticker":
                        setattr(existing, key, value)
            else:
                session.add(FilingSignal(**signal_data))

            updated += 1

        await session.commit()

    logger.info("filing_signals_rebuilt", tickers_updated=updated)
    return updated


async def get_all_filing_signals() -> list[dict]:
    """Read all filing signals from the database."""
    async with get_session("news") as session:
        result = await session.execute(select(FilingSignal))
        signals = result.scalars().all()
        return [s.to_dict() for s in signals]


async def get_filing_signal(ticker: str) -> dict | None:
    """Read a single ticker's filing signal."""
    async with get_session("news") as session:
        signal = await session.get(FilingSignal, ticker.upper())
        return signal.to_dict() if signal else None
