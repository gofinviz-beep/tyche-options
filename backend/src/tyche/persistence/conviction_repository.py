"""Repository for persisting and querying conviction snapshots and transitions.

Operates against conviction.db via the 'conviction' engine.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import structlog
from sqlalchemy import delete, select, and_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from tyche.conviction.engine import ConvictionSignal
from tyche.models.conviction import ConvictionSnapshot, ConvictionTransition
from tyche.persistence.database import get_session

logger = structlog.get_logger()

DB_NAME = "conviction"


async def upsert_snapshots(
    signals: list[ConvictionSignal],
    as_of_date: date,
) -> int:
    """Bulk upsert conviction signals into conviction_snapshots.

    Uses SQLite INSERT ... ON CONFLICT DO UPDATE to idempotently write
    one row per ticker per date.

    Returns:
        Number of rows upserted.
    """
    if not signals:
        return 0

    now = datetime.now(timezone.utc)
    rows = []
    for sig in signals:
        gate_json = None
        if sig.gate_results:
            gate_json = json.dumps(
                [g.to_dict() for g in sig.gate_results],
                default=str,
            )

        rows.append({
            "ticker": sig.ticker,
            "as_of_date": sig.as_of_date or as_of_date,
            "trend_state": sig.trend_state.value,
            "conviction_level": sig.conviction_level,
            "raw_conviction": sig.raw_conviction,
            "csp_eligible": sig.csp_eligible,
            "last_close": sig.last_close,
            "ema_8": sig.ema_8,
            "ema_21": sig.ema_21,
            "ema_8_slope": sig.ema_8_slope,
            "ema_21_slope": sig.ema_21_slope,
            "price_to_8ema_pct": sig.price_to_8ema_pct,
            "price_to_21ema_pct": sig.price_to_21ema_pct,
            "volume_declining": sig.volume_declining_on_pullback,
            "days_above_both_emas": sig.days_above_both_emas,
            "prior_streak": sig.prior_streak,
            "avg_volume_20d": sig.avg_volume_20d,
            "latest_volume": sig.latest_volume,
            "ema_50": sig.ema_50,
            "ema_50_slope": sig.ema_50_slope,
            "rsi_14": sig.rsi_14,
            "iv_rank": sig.iv_rank,
            "iv_percentile": sig.iv_percentile,
            "atm_iv": sig.atm_iv,
            "vrp": sig.vrp,
            "conviction_score": sig.conviction_score,
            "csp_safety_prob": sig.csp_safety_prob,
            "gate_results_json": gate_json,
            "computed_at": now,
        })

    update_cols = {
        "trend_state", "conviction_level", "raw_conviction", "csp_eligible", "last_close",
        "ema_8", "ema_21", "ema_8_slope", "ema_21_slope",
        "price_to_8ema_pct", "price_to_21ema_pct", "volume_declining",
        "days_above_both_emas", "prior_streak", "avg_volume_20d", "latest_volume",
        "ema_50", "ema_50_slope", "rsi_14",
        "iv_rank", "iv_percentile", "atm_iv", "vrp", "conviction_score",
        "csp_safety_prob", "gate_results_json", "computed_at",
    }

    batch_size = 500
    total = 0
    async with get_session(DB_NAME) as session:
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            stmt = sqlite_insert(ConvictionSnapshot).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ticker", "as_of_date"],
                set_={col: stmt.excluded[col] for col in update_cols},
            )
            await session.execute(stmt)
            total += len(batch)
        await session.commit()

    logger.info("conviction_snapshots_upserted", count=total, as_of_date=str(as_of_date))
    return total


async def detect_and_record_transitions(
    as_of_date: date,
) -> list[ConvictionTransition]:
    """Compare today's snapshots to the previous day's and record state changes.

    Returns:
        List of newly created ConvictionTransition rows.
    """
    prev_date = _previous_trading_day(as_of_date)

    async with get_session(DB_NAME) as session:
        today_stmt = select(ConvictionSnapshot).where(
            ConvictionSnapshot.as_of_date == as_of_date
        )
        today_rows = (await session.execute(today_stmt)).scalars().all()

        prev_stmt = select(ConvictionSnapshot).where(
            ConvictionSnapshot.as_of_date == prev_date
        )
        prev_rows = (await session.execute(prev_stmt)).scalars().all()

    prev_map = {r.ticker: r for r in prev_rows}

    now = datetime.now(timezone.utc)
    transitions: list[ConvictionTransition] = []

    for snap in today_rows:
        prev = prev_map.get(snap.ticker)
        if prev is None:
            continue
        if snap.trend_state == prev.trend_state:
            continue

        transitions.append(ConvictionTransition(
            ticker=snap.ticker,
            from_state=prev.trend_state,
            to_state=snap.trend_state,
            transition_date=as_of_date,
            last_close=snap.last_close,
            ema_8=snap.ema_8,
            ema_21=snap.ema_21,
            ema_8_slope=snap.ema_8_slope,
            ema_21_slope=snap.ema_21_slope,
            conviction_level=snap.conviction_level,
            raw_conviction=snap.raw_conviction,
            detected_at=now,
        ))

    if transitions:
        async with get_session(DB_NAME) as session:
            session.add_all(transitions)
            await session.commit()

    logger.info(
        "conviction_transitions_detected",
        as_of_date=str(as_of_date),
        prev_date=str(prev_date),
        transitions=len(transitions),
    )
    return transitions


async def get_active_pullbacks(
    as_of_date: date,
) -> list[ConvictionSnapshot]:
    """Query snapshots in pullback states with positive slopes for a given date."""
    async with get_session(DB_NAME) as session:
        stmt = select(ConvictionSnapshot).where(
            and_(
                ConvictionSnapshot.as_of_date == as_of_date,
                ConvictionSnapshot.trend_state.in_([
                    "pullback_to_8ema", "pullback_to_21ema"
                ]),
                ConvictionSnapshot.ema_8_slope > 0,
                ConvictionSnapshot.ema_21_slope > 0,
            )
        )
        rows = (await session.execute(stmt)).scalars().all()

    return list(rows)


async def get_ticker_history(
    ticker: str,
    days: int = 30,
) -> list[ConvictionSnapshot]:
    """Get conviction snapshots for a single ticker over the last N days."""
    cutoff = date.today() - timedelta(days=days)
    async with get_session(DB_NAME) as session:
        stmt = (
            select(ConvictionSnapshot)
            .where(
                and_(
                    ConvictionSnapshot.ticker == ticker.upper(),
                    ConvictionSnapshot.as_of_date >= cutoff,
                )
            )
            .order_by(ConvictionSnapshot.as_of_date.asc())
        )
        rows = (await session.execute(stmt)).scalars().all()

    return list(rows)


async def get_transitions(
    from_date: date | None = None,
    to_date: date | None = None,
    to_states: list[str] | None = None,
    ticker: str | None = None,
) -> list[ConvictionTransition]:
    """Query conviction transitions with optional filters."""
    conditions = []
    if from_date:
        conditions.append(ConvictionTransition.transition_date >= from_date)
    if to_date:
        conditions.append(ConvictionTransition.transition_date <= to_date)
    if to_states:
        conditions.append(ConvictionTransition.to_state.in_(to_states))
    if ticker:
        conditions.append(ConvictionTransition.ticker == ticker.upper())

    async with get_session(DB_NAME) as session:
        stmt = (
            select(ConvictionTransition)
            .where(and_(*conditions) if conditions else True)
            .order_by(ConvictionTransition.transition_date.desc())
            .limit(500)
        )
        rows = (await session.execute(stmt)).scalars().all()

    return list(rows)


async def get_snapshots_for_date(
    as_of_date: date,
    tickers: list[str] | None = None,
) -> list[ConvictionSnapshot]:
    """Get all snapshots for a given date, optionally filtered by ticker list."""
    conditions = [ConvictionSnapshot.as_of_date == as_of_date]
    if tickers:
        conditions.append(ConvictionSnapshot.ticker.in_([t.upper() for t in tickers]))

    async with get_session(DB_NAME) as session:
        stmt = (
            select(ConvictionSnapshot)
            .where(and_(*conditions))
            .order_by(ConvictionSnapshot.ticker.asc())
        )
        rows = (await session.execute(stmt)).scalars().all()

    return list(rows)


async def get_latest_snapshot_date() -> date | None:
    """Return the most recent as_of_date in conviction_snapshots, or None if empty."""
    async with get_session(DB_NAME) as session:
        from sqlalchemy import func

        stmt = select(func.max(ConvictionSnapshot.as_of_date))
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()

    if row is None:
        return None
    if isinstance(row, str):
        return date.fromisoformat(row)
    return row


async def get_conviction_version() -> dict[str, str | None]:
    """Return the latest computed_at and as_of_date from conviction_snapshots.

    Single SQL query — extremely cheap (~1ms on SQLite).
    """
    async with get_session(DB_NAME) as session:
        from sqlalchemy import func

        stmt = select(
            func.max(ConvictionSnapshot.computed_at).label("last_computed_at"),
            func.max(ConvictionSnapshot.as_of_date).label("as_of_date"),
        )
        result = await session.execute(stmt)
        row = result.one_or_none()

    if row is None:
        return {"last_computed_at": None, "as_of_date": None}

    computed_at = row.last_computed_at
    as_of = row.as_of_date

    if computed_at is not None and hasattr(computed_at, "isoformat"):
        computed_at = computed_at.isoformat()
    elif computed_at is not None:
        computed_at = str(computed_at)

    if as_of is not None and hasattr(as_of, "isoformat"):
        as_of = as_of.isoformat()
    elif as_of is not None:
        as_of = str(as_of)

    return {"last_computed_at": computed_at, "as_of_date": as_of}


async def cleanup_old_snapshots(retention_days: int = 90) -> int:
    """Delete snapshots and transitions older than the retention period."""
    cutoff = date.today() - timedelta(days=retention_days)
    total_deleted = 0

    async with get_session(DB_NAME) as session:
        snap_result = await session.execute(
            delete(ConvictionSnapshot).where(
                ConvictionSnapshot.as_of_date < cutoff
            )
        )
        total_deleted += snap_result.rowcount or 0

        trans_result = await session.execute(
            delete(ConvictionTransition).where(
                ConvictionTransition.transition_date < cutoff
            )
        )
        total_deleted += trans_result.rowcount or 0

        await session.commit()

    if total_deleted > 0:
        logger.info(
            "conviction_old_data_cleaned",
            cutoff=str(cutoff),
            deleted=total_deleted,
        )
    return total_deleted


def _previous_trading_day(d: date) -> date:
    """Step back to the previous weekday (simple heuristic, no holiday calendar)."""
    prev = d - timedelta(days=1)
    while prev.weekday() >= 5:  # Saturday=5, Sunday=6
        prev -= timedelta(days=1)
    return prev
