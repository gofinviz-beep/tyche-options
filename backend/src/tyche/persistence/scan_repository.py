"""Repository for persisting and retrieving scan results across distributed SQLite DBs.

Coordinates writes/reads across three engines:
- scans.db      → ScanRun
- candidates.db → ScanCandidate
- analyses.db   → LLMAnalysisRecord
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import delete, select, func

from tyche.models.scan import LLMAnalysisRecord, ScanCandidate, ScanRun
from tyche.persistence.database import get_session
from tyche.schemas.analysis import CSPAnalysis
from tyche.strategy.strategies.base import ScoredCandidate
from tyche.workflow.morning_scan import MorningScanResult

logger = structlog.get_logger()


async def save_scan(
    result: MorningScanResult,
    *,
    intents_created: int = 0,
    trigger: str = "manual",
    config_snapshot: dict[str, Any] | None = None,
) -> str:
    """Persist a full scan result across all three databases.

    Returns:
        The scan_id of the persisted run.
    """
    scan_id = result.scan_id

    # -- 1. Save ScanRun to scans.db --
    allocation = result.allocation
    alloc_trades_json: str | None = None
    if allocation and allocation.trades:
        alloc_trades_json = json.dumps([
            {
                "symbol": t.symbol,
                "option_type": t.option_type,
                "strike": t.strike,
                "expiration": t.expiration.isoformat(),
                "dte": t.dte,
                "contracts": t.contracts,
                "bid": t.bid,
                "total_premium": t.total_premium,
                "collateral": t.collateral,
                "annualized_return_pct": t.annualized_return_pct,
                "conviction": t.conviction,
                "strategy": t.strategy,
            }
            for t in allocation.trades
        ])

    conviction_json = json.dumps({
        ticker: sig.to_dict()
        for ticker, sig in result.conviction_signals.items()
    })

    earnings_json = json.dumps({
        k: {**v, "earnings_date": str(v.get("earnings_date", ""))}
        for k, v in result.earnings_context.items()
    })

    inst_json = json.dumps({
        ticker: round(pct * 100, 1)
        for ticker, pct in result.institutional_ownership.items()
    })

    run = ScanRun(
        id=scan_id,
        scanned_at=result.scanned_at,
        trigger=trigger,
        symbols_input="[]",
        symbols_scanned=result.symbols_scanned,
        pipeline_stages=json.dumps([s.to_dict() for s in result.pipeline_stages]),
        errors=json.dumps(result.errors),
        config_snapshot=json.dumps(config_snapshot or {}),
        csp_candidate_count=len(result.csp_candidates),
        cc_candidate_count=len(result.cc_candidates),
        llm_analysis_count=len(result.csp_analyses),
        intents_created=intents_created,
        conviction_signals=conviction_json,
        earnings_context=earnings_json,
        institutional_ownership=inst_json,
        allocation_total_premium=(
            allocation.total_premium if allocation else None
        ),
        allocation_utilization_pct=(
            allocation.capital_utilization_pct if allocation else None
        ),
        allocation_solver_status=(
            allocation.solver_status if allocation else None
        ),
        allocation_trades=alloc_trades_json,
    )

    async with get_session("scans") as session:
        session.add(run)
        await session.commit()

    # -- 2. Save ScanCandidates to candidates.db --
    candidate_rows: list[ScanCandidate] = []

    for c in result.csp_candidates:
        candidate_rows.append(_scored_to_row(c, scan_id, "csp"))

    for c in result.cc_candidates:
        candidate_rows.append(_scored_to_row(c, scan_id, "cc"))

    if candidate_rows:
        async with get_session("candidates") as session:
            session.add_all(candidate_rows)
            await session.commit()

    # -- 3. Save LLM analyses to analyses.db --
    analysis_rows: list[LLMAnalysisRecord] = []
    now = datetime.now(timezone.utc)

    for a in result.csp_analyses:
        sig = result.conviction_signals.get(a.ticker)
        analysis_rows.append(LLMAnalysisRecord(
            scan_id=scan_id,
            ticker=a.ticker,
            thesis=a.thesis,
            risks=json.dumps(a.risks) if a.risks else None,
            invalidation=a.invalidation,
            confidence=a.confidence,
            assignment_comfort=a.assignment_comfort,
            assignment_comfort_reasoning=a.assignment_comfort_reasoning,
            recommended_strike=a.recommended_strike,
            recommended_expiration=a.recommended_expiration,
            target_premium=a.target_premium,
            annualized_return_pct=a.annualized_return_pct,
            suggested_contracts=a.suggested_contracts,
            collateral_required=a.collateral_required,
            allocation_mode=a.allocation_mode,
            conviction_level=sig.conviction_level if sig else None,
            trend_state=sig.trend_state if sig else None,
            would_you_hold_if_assigned=a.would_you_hold_if_assigned,
            earnings_proximity=a.earnings_proximity,
            earnings_risk_assessment=a.earnings_risk_assessment,
            status="success",
            created_at=now,
        ))

    if analysis_rows:
        async with get_session("analyses") as session:
            session.add_all(analysis_rows)
            await session.commit()

    logger.info(
        "scan_persisted",
        scan_id=scan_id,
        candidates=len(candidate_rows),
        analyses=len(analysis_rows),
    )
    return scan_id


async def load_latest() -> dict[str, Any] | None:
    """Load the most recent scan run with all its candidates and analyses."""
    async with get_session("scans") as session:
        stmt = select(ScanRun).order_by(ScanRun.scanned_at.desc()).limit(1)
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return await _hydrate_scan(row)


async def load_scan(scan_id: str) -> dict[str, Any] | None:
    """Load a specific scan by ID."""
    async with get_session("scans") as session:
        row = await session.get(ScanRun, scan_id)
        if row is None:
            return None
        return await _hydrate_scan(row)


async def load_history(limit: int = 5) -> list[dict[str, Any]]:
    """Load summary info for the last N scans (no candidates/analyses)."""
    async with get_session("scans") as session:
        stmt = select(ScanRun).order_by(ScanRun.scanned_at.desc()).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()

    return [
        {
            "scan_id": r.id,
            "scanned_at": r.scanned_at.isoformat() if r.scanned_at else None,
            "trigger": r.trigger,
            "symbols_scanned": r.symbols_scanned,
            "csp_candidate_count": r.csp_candidate_count,
            "cc_candidate_count": r.cc_candidate_count,
            "llm_analysis_count": r.llm_analysis_count,
            "intents_created": r.intents_created,
            "errors_count": len(json.loads(r.errors or "[]")),
        }
        for r in rows
    ]


async def cleanup_old_scans(retention_count: int = 5) -> int:
    """Delete scans beyond the retention limit, oldest first.

    Returns the number of scans deleted.
    """
    async with get_session("scans") as session:
        count_stmt = select(func.count()).select_from(ScanRun)
        total = (await session.execute(count_stmt)).scalar() or 0

        if total <= retention_count:
            return 0

        to_delete = total - retention_count
        oldest_stmt = (
            select(ScanRun.id)
            .order_by(ScanRun.scanned_at.asc())
            .limit(to_delete)
        )
        old_ids = list((await session.execute(oldest_stmt)).scalars().all())

    if not old_ids:
        return 0

    async with get_session("scans") as session:
        await session.execute(delete(ScanRun).where(ScanRun.id.in_(old_ids)))
        await session.commit()

    async with get_session("candidates") as session:
        await session.execute(
            delete(ScanCandidate).where(ScanCandidate.scan_id.in_(old_ids))
        )
        await session.commit()

    async with get_session("analyses") as session:
        await session.execute(
            delete(LLMAnalysisRecord).where(LLMAnalysisRecord.scan_id.in_(old_ids))
        )
        await session.commit()

    logger.info("old_scans_cleaned", deleted=len(old_ids), retained=retention_count)
    return len(old_ids)


# ── Private helpers ──────────────────────────────────────────────────


def _scored_to_row(c: ScoredCandidate, scan_id: str, strategy: str) -> ScanCandidate:
    return ScanCandidate(
        scan_id=scan_id,
        strategy=strategy,
        symbol=c.symbol,
        option_symbol=c.option_symbol,
        strike=c.strike,
        expiration=c.expiration.isoformat(),
        dte=c.dte,
        bid=c.bid,
        ask=c.ask,
        premium_per_contract=c.premium_per_contract,
        collateral_required=c.collateral_required,
        annualized_return_pct=c.annualized_return_pct,
        score=c.score,
        delta=c.delta,
        theta=c.theta,
        implied_volatility=c.implied_volatility,
        volume=c.volume,
        open_interest=c.open_interest,
        earnings_within_dte=c.earnings_within_dte,
        earnings_date=c.earnings_date.isoformat() if c.earnings_date else None,
    )


async def _hydrate_scan(run: ScanRun) -> dict[str, Any]:
    """Reconstitute a full scan result dict from the DB rows."""
    scan_id = run.id

    # Load candidates
    async with get_session("candidates") as session:
        cand_stmt = select(ScanCandidate).where(ScanCandidate.scan_id == scan_id)
        cand_rows = (await session.execute(cand_stmt)).scalars().all()

    csp_candidates = []
    cc_candidates = []
    for c in cand_rows:
        cand_dict = {
            "symbol": c.symbol,
            "option_symbol": c.option_symbol,
            "strike": c.strike,
            "expiration": c.expiration,
            "dte": c.dte,
            "bid": c.bid,
            "ask": c.ask,
            "premium_per_contract": c.premium_per_contract,
            "collateral_required": c.collateral_required,
            "annualized_return_pct": c.annualized_return_pct,
            "score": c.score,
            "delta": c.delta,
            "theta": c.theta,
            "implied_volatility": c.implied_volatility,
            "volume": c.volume,
            "open_interest": c.open_interest,
            "earnings_within_dte": bool(c.earnings_within_dte),
            "earnings_date": c.earnings_date,
        }
        if c.strategy == "csp":
            csp_candidates.append(cand_dict)
        else:
            cc_candidates.append(cand_dict)

    # Load analyses
    async with get_session("analyses") as session:
        anal_stmt = select(LLMAnalysisRecord).where(
            LLMAnalysisRecord.scan_id == scan_id
        )
        anal_rows = (await session.execute(anal_stmt)).scalars().all()

    llm_analyses = []
    for a in anal_rows:
        llm_analyses.append({
            "ticker": a.ticker,
            "assignment_comfort": a.assignment_comfort,
            "assignment_comfort_reasoning": a.assignment_comfort_reasoning,
            "thesis": a.thesis,
            "recommended_strike": a.recommended_strike,
            "recommended_expiration": a.recommended_expiration,
            "target_premium": a.target_premium,
            "annualized_return_pct": a.annualized_return_pct,
            "earnings_proximity": a.earnings_proximity,
            "earnings_risk_assessment": a.earnings_risk_assessment,
            "invalidation": a.invalidation,
            "confidence": a.confidence,
            "risks": json.loads(a.risks) if a.risks else [],
            "would_you_hold_if_assigned": a.would_you_hold_if_assigned,
            "suggested_contracts": a.suggested_contracts,
            "collateral_required": a.collateral_required,
            "allocation_mode": a.allocation_mode,
        })

    allocation_summary = None
    if run.allocation_solver_status:
        allocation_summary = {
            "total_premium": run.allocation_total_premium,
            "capital_utilization_pct": run.allocation_utilization_pct,
            "solver_status": run.allocation_solver_status,
        }

    allocated_trades = json.loads(run.allocation_trades) if run.allocation_trades else []

    return {
        "scan_id": run.id,
        "scanned_at": run.scanned_at.isoformat() if run.scanned_at else None,
        "symbols_scanned": run.symbols_scanned,
        "pipeline_stages": json.loads(run.pipeline_stages or "[]"),
        "conviction_signals": json.loads(run.conviction_signals or "{}"),
        "csp_candidates": csp_candidates,
        "cc_candidates": cc_candidates,
        "llm_analyses": llm_analyses,
        "earnings_context": json.loads(run.earnings_context or "{}"),
        "institutional_ownership": json.loads(run.institutional_ownership or "{}"),
        "allocation": allocation_summary,
        "allocated_trades": allocated_trades,
        "intents_created": run.intents_created,
        "errors": json.loads(run.errors or "[]"),
    }
