"""API routes for the EDGAR filing intelligence module."""

from __future__ import annotations

import asyncio
import math

import structlog
from fastapi import APIRouter, Depends, Query

from tyche.api.deps import (
    get_filing_8k_store,
    get_insider_tx_store,
    get_settings as dep_settings,
)
from tyche.config import TycheSettings, get_settings
from tyche.market_data.filing_signals import get_all_filing_signals, get_filing_signal
from tyche.persistence.published_routes import get_intelligence_filing_rows
from tyche.market_data.filing_store import Filing8KStore, InsiderTxStore
from tyche.schemas.filing import (
    EdgarIngestResponse,
    Filing8KResponse,
    FilingSignalResponse,
    InsiderTransactionResponse,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/filings", tags=["filings"])


@router.get("/signals", response_model=list[FilingSignalResponse])
async def list_filing_signals(
    settings: TycheSettings = Depends(get_settings),
) -> list[FilingSignalResponse]:
    """Get all tickers with active filing signals."""
    published = await asyncio.to_thread(
        get_intelligence_filing_rows, settings=settings
    )
    if published is not None:
        rows, _layer = published
        return [
            FilingSignalResponse(
                ticker=s["ticker"],
                last_8k_at=s.get("last_8k_at"),
                last_8k_sentiment=s.get("last_8k_sentiment"),
                last_8k_impact=s.get("last_8k_impact"),
                eightk_count_30d=s.get("eightk_count_30d", 0),
                insider_net_shares_30d=s.get("insider_net_shares_30d", 0.0),
                insider_buy_count_30d=s.get("insider_buy_count_30d", 0),
                insider_sell_count_30d=s.get("insider_sell_count_30d", 0),
                insider_cluster_sell=s.get("insider_cluster_sell", False),
                last_insider_tx_at=s.get("last_insider_tx_at"),
                has_risk=_has_filing_risk(s),
                updated_at=s.get("updated_at"),
            )
            for s in rows
        ]

    signals = await get_all_filing_signals()
    return [
        FilingSignalResponse(
            ticker=s["ticker"],
            last_8k_at=s["last_8k_at"],
            last_8k_sentiment=s["last_8k_sentiment"],
            last_8k_impact=s["last_8k_impact"],
            eightk_count_30d=s["eightk_count_30d"],
            insider_net_shares_30d=s["insider_net_shares_30d"],
            insider_buy_count_30d=s["insider_buy_count_30d"],
            insider_sell_count_30d=s["insider_sell_count_30d"],
            insider_cluster_sell=s["insider_cluster_sell"],
            last_insider_tx_at=s["last_insider_tx_at"],
            has_risk=_has_filing_risk(s),
            updated_at=s["updated_at"],
        )
        for s in signals
    ]


@router.get("/signals/{ticker}", response_model=FilingSignalResponse | None)
async def get_ticker_filing_signal(ticker: str) -> FilingSignalResponse | None:
    """Get the filing signal for a single ticker."""
    s = await get_filing_signal(ticker)
    if s is None:
        return None
    return FilingSignalResponse(
        ticker=s["ticker"],
        last_8k_at=s["last_8k_at"],
        last_8k_sentiment=s["last_8k_sentiment"],
        last_8k_impact=s["last_8k_impact"],
        eightk_count_30d=s["eightk_count_30d"],
        insider_net_shares_30d=s["insider_net_shares_30d"],
        insider_buy_count_30d=s["insider_buy_count_30d"],
        insider_sell_count_30d=s["insider_sell_count_30d"],
        insider_cluster_sell=s["insider_cluster_sell"],
        last_insider_tx_at=s["last_insider_tx_at"],
        has_risk=_has_filing_risk(s),
        updated_at=s["updated_at"],
    )


@router.get("/8k/{ticker}", response_model=list[Filing8KResponse])
async def get_ticker_8k_filings(
    ticker: str,
    days: int = Query(default=30, ge=1, le=365),
    store: Filing8KStore = Depends(get_filing_8k_store),
) -> list[Filing8KResponse]:
    """Get recent 8-K filings for a ticker."""
    df = store.read_recent(ticker, days=days)
    if df.empty:
        return []

    filings: list[Filing8KResponse] = []
    for _, row in df.iterrows():
        filings.append(
            Filing8KResponse(
                accession_no=row.get("accession_no", ""),
                form_type=row.get("form_type", "8-K"),
                filed_at=row.get("filed_at"),
                description=_safe_str(row.get("description")),
                filing_url=_safe_str(row.get("filing_url")),
                items_reported=_safe_str(row.get("items_reported")),
                content_summary=_safe_str(row.get("content_summary")),
                event_type=_safe_str(row.get("event_type")),
                sentiment=_safe_str(row.get("sentiment")),
                impact_score=(
                    float(row["impact_score"])
                    if _notna(row.get("impact_score"))
                    else None
                ),
            )
        )
    return filings


@router.get("/insider/{ticker}", response_model=list[InsiderTransactionResponse])
async def get_ticker_insider_transactions(
    ticker: str,
    days: int = Query(default=30, ge=1, le=365),
    store: InsiderTxStore = Depends(get_insider_tx_store),
) -> list[InsiderTransactionResponse]:
    """Get recent insider transactions for a ticker."""
    df = store.read_recent(ticker, days=days)
    if df.empty:
        return []

    transactions: list[InsiderTransactionResponse] = []
    for _, row in df.iterrows():
        transactions.append(
            InsiderTransactionResponse(
                accession_no=row.get("accession_no", ""),
                filed_at=row.get("filed_at"),
                period_of_report=row.get("period_of_report"),
                insider_name=row.get("insider_name", ""),
                insider_title=_safe_str(row.get("insider_title")),
                is_officer=bool(row.get("is_officer", False)),
                is_director=bool(row.get("is_director", False)),
                is_ten_pct_owner=bool(row.get("is_ten_pct_owner", False)),
                transaction_type=row.get("transaction_type", ""),
                shares=float(row.get("shares", 0)),
                price_per_share=float(row.get("price_per_share", 0)),
                total_value=float(row.get("total_value", 0)),
                shares_owned_after=float(row.get("shares_owned_after", 0)),
                acquisition_or_disposition=row.get("acquisition_or_disposition", ""),
            )
        )
    return transactions


@router.post("/ingest")
async def trigger_edgar_ingest(
    settings: TycheSettings = Depends(get_settings),
) -> dict[str, str]:
    """Trigger EDGAR ingestion in the background.

    Returns immediately; the pipeline runs asynchronously.
    Check /filings/signals or backend logs for results.
    """
    from tyche.workflow.edgar_pipeline import run_edgar_pipeline

    async def _run() -> None:
        result = await run_edgar_pipeline(settings)
        logger.info(
            "manual_edgar_ingest_done",
            tickers_resolved=result.tickers_resolved,
            eightk=result.eightk_fetched,
            form4=result.form4_fetched,
            signals=result.signals_rebuilt,
            errors=len(result.errors),
            duration_ms=result.duration_ms,
        )

    asyncio.create_task(_run())
    return {"status": "started", "message": "EDGAR ingestion running in background"}


def _has_filing_risk(signal: dict) -> bool:
    """Determine if a filing signal indicates risk."""
    if signal.get("insider_cluster_sell"):
        return True
    impact = signal.get("last_8k_impact")
    if impact is not None and impact < -0.5:
        return True
    return False


def _notna(val: object) -> bool:
    if val is None:
        return False
    if isinstance(val, float):
        return not math.isnan(val)
    if isinstance(val, str):
        return val != ""
    return True


def _safe_str(val: object) -> str | None:
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    s = str(val)
    return s if s else None
