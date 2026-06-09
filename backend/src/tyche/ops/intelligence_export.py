"""Export intelligence aggregate signals to GCS Parquet (GCP-C).

Cloud jobs compute rollups in memory from per-ticker article/filing Parquet
stores — no SQLite. Checkpoints flush every *batch_size* tickers so a pod
crash retains partial progress in ``signals/intelligence/_checkpoints/``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pandas as pd
import structlog

from tyche.config import TycheSettings, get_settings
from tyche.market_data.filing_signals import compute_filing_signal
from tyche.market_data.filing_store import Filing8KStore, InsiderTxStore
from tyche.market_data.news_signals import compute_signal_from_articles
from tyche.market_data.news_store import NewsArticleStore
from tyche.schemas.news import NewsSignalResponse
from tyche.storage import exists as storage_exists, read_parquet, write_parquet
from tyche.storage.paths import StorageContext, storage_context_from_settings

logger = structlog.get_logger()

NEWS_SIGNALS_REL = "signals/intelligence/news.parquet"
FILINGS_SIGNALS_REL = "signals/intelligence/filings.parquet"
INSIDER_SIGNALS_REL = "signals/intelligence/insider.parquet"

NEWS_CHECKPOINT_REL = "signals/intelligence/_checkpoints/news.partial.parquet"
FILINGS_CHECKPOINT_REL = "signals/intelligence/_checkpoints/filings.partial.parquet"
INSIDER_CHECKPOINT_REL = "signals/intelligence/_checkpoints/insider.partial.parquet"

DEFAULT_SIGNAL_BATCH_SIZE = 100


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if pd.isna(value):
        return None
    return str(value)


def _news_row_from_signal(
    signal_data: dict[str, Any],
    *,
    threshold: float,
) -> dict[str, Any]:
    return NewsSignalResponse(
        ticker=signal_data["ticker"],
        news_impact_score=signal_data["news_impact_score"],
        negative_count_24h=signal_data["negative_count_24h"],
        positive_count_24h=signal_data["positive_count_24h"],
        total_count_24h=signal_data["total_count_24h"],
        dominant_event_type=signal_data.get("dominant_event_type"),
        last_negative_at=_iso_or_none(signal_data.get("last_negative_at")),
        last_positive_at=_iso_or_none(signal_data.get("last_positive_at")),
        has_risk=signal_data["news_impact_score"] < threshold,
        updated_at=_iso_or_none(signal_data.get("updated_at")),
    ).model_dump(mode="json")


def _filing_row_from_signal(signal_data: dict[str, Any]) -> dict[str, Any]:
    impact = signal_data.get("last_8k_impact")
    return {
        "ticker": signal_data["ticker"],
        "last_8k_at": _iso_or_none(signal_data.get("last_8k_at")),
        "last_8k_sentiment": signal_data.get("last_8k_sentiment"),
        "last_8k_impact": round(impact, 3) if impact is not None else None,
        "eightk_count_30d": signal_data.get("eightk_count_30d", 0),
        "insider_net_shares_30d": signal_data.get("insider_net_shares_30d", 0.0),
        "insider_buy_count_30d": signal_data.get("insider_buy_count_30d", 0),
        "insider_sell_count_30d": signal_data.get("insider_sell_count_30d", 0),
        "insider_cluster_sell": bool(signal_data.get("insider_cluster_sell", False)),
        "last_insider_tx_at": _iso_or_none(signal_data.get("last_insider_tx_at")),
        "updated_at": _iso_or_none(signal_data.get("updated_at")),
    }


def _insider_row_from_filing(filing_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": filing_row.get("ticker"),
        "insider_net_shares_30d": filing_row.get("insider_net_shares_30d", 0.0),
        "insider_buy_count_30d": filing_row.get("insider_buy_count_30d", 0),
        "insider_sell_count_30d": filing_row.get("insider_sell_count_30d", 0),
        "insider_cluster_sell": bool(filing_row.get("insider_cluster_sell", False)),
        "last_insider_tx_at": filing_row.get("last_insider_tx_at"),
        "updated_at": filing_row.get("updated_at"),
    }


def _load_checkpoint_rows(rel: str, *, ctx: StorageContext) -> list[dict[str, Any]]:
    if not storage_exists(rel, ctx=ctx):
        return []
    df = read_parquet(rel, ctx=ctx)
    if df is None or df.empty:
        return []
    return df.to_dict(orient="records")


def _write_checkpoint_rows(
    rel: str,
    rows: list[dict[str, Any]],
    *,
    ctx: StorageContext,
) -> int:
    df = pd.DataFrame(rows) if rows else pd.DataFrame()
    write_parquet(df, rel, atomic=True, ctx=ctx)
    return len(df)


def _dedupe_rows(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get(key, "")).upper()
        if ticker:
            by_key[ticker] = row
    return list(by_key.values())


async def _export_batched(
    *,
    tickers: list[str],
    batch_size: int,
    checkpoint_rel: str,
    final_rel: str,
    ctx: StorageContext,
    build_batch: Any,
) -> dict[str, Any]:
    """Process tickers in batches, checkpointing after each batch."""
    accumulated = _load_checkpoint_rows(checkpoint_rel, ctx=ctx)
    done = {str(r.get("ticker", "")).upper() for r in accumulated}
    pending = [t for t in tickers if t.upper() not in done]

    batches_written = 0
    for offset in range(0, len(pending), batch_size):
        batch_tickers = pending[offset : offset + batch_size]
        batch_rows = await build_batch(batch_tickers)
        accumulated = _dedupe_rows([*accumulated, *batch_rows], "ticker")
        await asyncio.to_thread(
            _write_checkpoint_rows, checkpoint_rel, accumulated, ctx=ctx
        )
        batches_written += 1
        logger.info(
            "intelligence_checkpoint_written",
            checkpoint=checkpoint_rel,
            batch_size=len(batch_tickers),
            total_rows=len(accumulated),
            batches_written=batches_written,
        )

    await asyncio.to_thread(
        _write_checkpoint_rows, final_rel, accumulated, ctx=ctx
    )
    return {
        "rows": len(accumulated),
        "batches_written": batches_written,
        "checkpoint": checkpoint_rel,
        "final": final_rel,
        "resumed_from_checkpoint": len(done),
    }


async def export_news_signals_from_parquet(
    *,
    store: NewsArticleStore,
    tickers: list[str],
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    batch_size: int = DEFAULT_SIGNAL_BATCH_SIZE,
    lookback_hours: int | None = None,
) -> dict[str, Any]:
    """Roll up classified articles → news signal Parquet (no SQLite)."""
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    hours = lookback_hours if lookback_hours is not None else settings.news_lookback_hours
    threshold = settings.news_risk_threshold

    async def build_batch(batch_tickers: list[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for ticker in batch_tickers:
            articles = store.read_recent(ticker, hours=hours)
            signal_data = compute_signal_from_articles(ticker, articles, hours)
            rows.append(_news_row_from_signal(signal_data, threshold=threshold))
        return rows

    summary = await _export_batched(
        tickers=tickers,
        batch_size=batch_size,
        checkpoint_rel=NEWS_CHECKPOINT_REL,
        final_rel=NEWS_SIGNALS_REL,
        ctx=ctx,
        build_batch=build_batch,
    )
    logger.info("news_signals_exported_from_parquet", **summary)
    return summary


async def export_filing_signals_from_parquet(
    *,
    filing_store: Filing8KStore,
    insider_store: InsiderTxStore,
    tickers: list[str],
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    batch_size: int = DEFAULT_SIGNAL_BATCH_SIZE,
    lookback_days: int | None = None,
) -> dict[str, Any]:
    """Roll up 8-K + Form 4 Parquet → filings + insider signal Parquet."""
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    days = lookback_days if lookback_days is not None else settings.edgar_lookback_days

    async def build_batch(batch_tickers: list[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for ticker in batch_tickers:
            filings = filing_store.read_recent(ticker, days=days)
            insider = insider_store.read_recent(ticker, days=days)
            signal_data = compute_filing_signal(ticker, filings, insider, days)
            rows.append(_filing_row_from_signal(signal_data))
        return rows

    filings_summary = await _export_batched(
        tickers=tickers,
        batch_size=batch_size,
        checkpoint_rel=FILINGS_CHECKPOINT_REL,
        final_rel=FILINGS_SIGNALS_REL,
        ctx=ctx,
        build_batch=build_batch,
    )

    filing_rows = _load_checkpoint_rows(FILINGS_CHECKPOINT_REL, ctx=ctx)
    insider_rows = [_insider_row_from_filing(r) for r in filing_rows]
    insider_count = await asyncio.to_thread(
        _write_checkpoint_rows,
        INSIDER_CHECKPOINT_REL,
        insider_rows,
        ctx=ctx,
    )
    await asyncio.to_thread(
        _write_checkpoint_rows,
        INSIDER_SIGNALS_REL,
        insider_rows,
        ctx=ctx,
    )

    summary = {
        "filings": filings_summary,
        "insider_rows": insider_count,
        "output_paths": [
            FILINGS_SIGNALS_REL,
            INSIDER_SIGNALS_REL,
            FILINGS_CHECKPOINT_REL,
            INSIDER_CHECKPOINT_REL,
        ],
    }
    logger.info("filing_signals_exported_from_parquet", **summary)
    return summary


async def export_intelligence_signals(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    include: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Local-mode fallback: rebuild from Parquet stores (no SQLite on GCS)."""
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    targets = include or frozenset({"news", "filings", "insider"})
    summary: dict[str, Any] = {"output_paths": []}

    if "news" in targets:
        from tyche.workflow.news_pipeline import _get_universe as news_universe

        store = NewsArticleStore(data_dir=settings.data_dir)
        tickers = news_universe(settings)
        news_summary = await export_news_signals_from_parquet(
            store=store,
            tickers=tickers,
            settings=settings,
            ctx=ctx,
        )
        summary["news"] = news_summary
        summary["output_paths"].append(NEWS_SIGNALS_REL)

    if "filings" in targets or "insider" in targets:
        from tyche.workflow.edgar_pipeline import _get_universe as edgar_universe

        filing_store = Filing8KStore(data_dir=settings.data_dir)
        insider_store = InsiderTxStore(data_dir=settings.data_dir)
        tickers = edgar_universe(settings)
        filing_summary = await export_filing_signals_from_parquet(
            filing_store=filing_store,
            insider_store=insider_store,
            tickers=tickers,
            settings=settings,
            ctx=ctx,
        )
        summary["filings"] = filing_summary
        summary["output_paths"].extend(
            [FILINGS_SIGNALS_REL, INSIDER_SIGNALS_REL]
        )

    logger.info("intelligence_signals_exported", **summary)
    return summary
