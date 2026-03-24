"""End-of-day journal workflow — snapshots, summaries, and continuity."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from tyche.analysis.agent import AnalysisAgent
from tyche.broker.base import BrokerClient

logger = structlog.get_logger()


class EODJournalResult:
    """Results of an end-of-day journal run."""

    def __init__(self) -> None:
        self.captured_at: datetime = datetime.now(timezone.utc)
        self.account_snapshot: dict[str, Any] = {}
        self.positions_snapshot: list[dict[str, Any]] = []
        self.summary: str = ""
        self.errors: list[str] = []


async def run_eod_journal(
    broker: BrokerClient,
    analysis_agent: AnalysisAgent | None = None,
    trades_today_summary: str = "No trades recorded today.",
    recommendations_summary: str = "No recommendations generated.",
) -> EODJournalResult:
    """Execute the end-of-day journal pipeline.

    Steps:
    1. Snapshot account state
    2. Snapshot positions
    3. Generate LLM journal summary
    """
    result = EODJournalResult()

    try:
        balance = await broker.get_account_balances()
        result.account_snapshot = {
            "cash": balance.cash,
            "buying_power": balance.buying_power,
            "net_liquidation_value": balance.net_liquidation_value,
            "open_pl": balance.open_pl,
            "close_pl": balance.close_pl,
        }
    except Exception as exc:
        result.errors.append(f"Account snapshot failed: {exc}")

    try:
        positions = await broker.get_positions()
        result.positions_snapshot = [
            {
                "symbol": p.symbol,
                "quantity": p.quantity,
                "market_value": p.market_value,
                "unrealized_pl": p.unrealized_pl,
            }
            for p in positions
        ]
    except Exception as exc:
        result.errors.append(f"Positions snapshot failed: {exc}")

    if analysis_agent:
        try:
            import json

            result.summary = await analysis_agent.generate_journal_summary(
                account_summary=json.dumps(result.account_snapshot, indent=2),
                positions_summary=json.dumps(result.positions_snapshot, indent=2),
                trades_today=trades_today_summary,
                recommendations_summary=recommendations_summary,
            )
        except Exception as exc:
            result.errors.append(f"Journal summary failed: {exc}")
            result.summary = "Summary unavailable."

    logger.info(
        "eod_journal_complete",
        positions=len(result.positions_snapshot),
        has_summary=bool(result.summary),
    )
    return result
