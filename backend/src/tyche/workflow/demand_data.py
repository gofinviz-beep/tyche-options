"""Demand-data ingestion: fundamentals, estimates/revisions, short interest.

Foundation for the Demand Conviction engine (Directional Alpha v2). Pulls
point-in-time financials (Finnhub ``/stock/financials`` standardized, with
as-reported fallback; dual-class symbols resolve to the voting share class),
analyst estimates/revisions/surprises (Finnhub Estimates-1), short interest
(Polygon), and company-issued corporate guidance (Benzinga via Massive/Polygon)
for the equity universe and persists them to their respective Parquet stores.

Every external call is isolated per-ticker so one failure never halts the
batch, and each source degrades gracefully when its credentials/subscription
are absent (the corresponding store simply receives no new rows).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import date

import pandas as pd
import structlog

from tyche.config import TycheSettings
from tyche.market_data.catalyst_store import (
    CatalystSignalStore,
    records_from_classification,
)
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.market_data.estimates_store import EstimatesStore
from tyche.market_data.dual_class import finnhub_symbol_candidates
from tyche.market_data.fundamentals_store import FundamentalsStore
from tyche.market_data.polygon import PolygonClient
from tyche.market_data.short_interest_store import ShortInterestStore

logger = structlog.get_logger()


def build_demand_universe(
    settings: TycheSettings,
    ohlcv: OHLCVStore,
    meta: TickerMetaStore,
) -> list[str]:
    """Equity-only universe at/above the demand-data market-cap floor."""
    tickers = ohlcv.get_all_tickers()
    if not tickers:
        return []

    equity = meta.filter_equity_only(tickers)
    floor = settings.demand_data_min_market_cap_millions * 1_000_000
    caps = meta.get_market_caps(equity)
    # Keep tickers at/above the floor; tickers with no cap data are kept
    # (same permissive policy as the scanner — missing data isn't a reject).
    universe = [
        t for t in equity if caps.get(t, 0.0) >= floor or t not in caps or caps.get(t, 0.0) == 0.0
    ]
    return sorted(set(universe))


def _build_consensus_by_period(
    est_store: EstimatesStore | None,
    ticker: str,
    as_of: date | None = None,
) -> list[tuple[date, float | None, float | None]]:
    """Build ``[(period_end, rev_consensus, eps_consensus)]`` sorted ascending.

    Uses the latest snapshot per period for ``rev_est_avg`` / ``eps_est_avg``
    (Finnhub keys these by calendar period-end date). Returns ``[]`` when no
    consensus is available so guidance falls back to revision/YoY comparators.
    """
    if est_store is None:
        return []
    try:
        df = est_store.read_ticker(ticker, as_of=as_of)
    except Exception:
        return []
    if df.empty:
        return []

    wanted = df[df["metric"].isin(("rev_est_avg", "eps_est_avg"))]
    if wanted.empty:
        return []

    # Latest snapshot per (metric, period).
    latest = (
        wanted.sort_values("snapshot_date")
        .groupby(["metric", "period"], as_index=False)
        .tail(1)
    )

    rev: dict[date, float] = {}
    eps: dict[date, float] = {}
    for _, row in latest.iterrows():
        period_end = _parse_period_end(row["period"])
        if period_end is None:
            continue
        if row["metric"] == "rev_est_avg":
            rev[period_end] = float(row["value"])
        else:
            eps[period_end] = float(row["value"])

    periods = sorted(set(rev) | set(eps))
    return [(pe, rev.get(pe), eps.get(pe)) for pe in periods]


def _parse_period_end(value: object) -> date | None:
    """Parse a Finnhub estimate period (``YYYY-MM-DD``) to a ``date``."""
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value).split("T")[0].split(" ")[0]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _infer_fye_month(
    fund_store: FundamentalsStore | None,
    ticker: str,
    as_of: date | None = None,
) -> int | None:
    """Infer a company's fiscal-year-end month (1–12) from its fundamentals.

    Each fundamentals row carries a fiscal-period label and a true period-end
    date (e.g. NVDA ``Q1 FY2027`` ended ``2026-04-26``). Annual rows give the
    FYE month directly; quarterly rows imply it (``Qn`` end + ``3*(4-n)``
    months). Returns the modal vote across all rows, or ``None`` when no
    fundamentals exist — in which case the consensus comparator is skipped.
    """
    if fund_store is None:
        return None
    try:
        df = fund_store.read_ticker(ticker, timeframe=None, as_of=as_of)
    except Exception:
        return None
    if df.empty:
        return None

    votes: Counter[int] = Counter()
    for _, row in df.iterrows():
        period_end = row.get("period_end")
        if period_end is None or pd.isna(period_end):
            continue
        timeframe = str(row.get("timeframe", ""))
        fperiod = str(row.get("fiscal_period", "")).upper()
        if timeframe == "annual" or fperiod == "FY":
            votes[period_end.month] += 1
            continue
        q = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}.get(fperiod)
        if q is None:
            continue
        # FYE month is 3*(4-q) months *after* this quarter's end.
        votes[(period_end.month - 1 + 3 * (4 - q)) % 12 + 1] += 1

    return votes.most_common(1)[0][0] if votes else None


async def _guidance_catalyst_records(
    benzinga,
    ticker: str,
    est_store: EstimatesStore | None = None,
    fund_store: FundamentalsStore | None = None,
    as_of: date | None = None,
) -> pd.DataFrame | None:
    """Fetch corporate guidance and map raises/cuts to catalyst rows.

    Uses ``derive_guidance_catalysts`` which compares each forward guide to
    (0) analyst consensus for the *same fiscal quarter* (aligned via the
    company's fiscal-year-end month), (1) a same-period revision, or (2) the
    year-ago guide for the same fiscal period — capturing both beat-and-raise
    surprises and demand ramps.

    Returns a DataFrame ready for ``CatalystSignalStore.write_records`` or
    ``None`` when there's nothing directional to record.
    """
    from tyche.market_data.benzinga import derive_guidance_catalysts

    recs = await benzinga.get_corporate_guidance(ticker)
    if not recs:
        return None

    consensus = _build_consensus_by_period(est_store, ticker, as_of=as_of)
    fye_month = _infer_fye_month(fund_store, ticker, as_of=as_of)

    rows: list[dict] = []
    for rec, catalyst, impact in derive_guidance_catalysts(
        recs, consensus_by_period=consensus, fye_month=fye_month
    ):
        try:
            event_date = pd.to_datetime(rec["date"]).date()
        except Exception:
            continue
        ref = rec.get("benzinga_id") or (
            f"guidance:{ticker.upper()}:{rec['date']}:{rec.get('fiscal_period', '')}"
        )
        rows.extend(
            records_from_classification(
                ticker=ticker,
                event_date=event_date,
                demand_catalyst=catalyst,
                policy_tag="none",
                impact_score=impact,
                source="guidance",
                ref_id=ref,
            )
        )

    return pd.DataFrame(rows) if rows else None


async def ingest_demand_data(
    settings: TycheSettings,
    *,
    tickers: list[str] | None = None,
    do_fundamentals: bool = True,
    do_estimates: bool = True,
    do_short_interest: bool = True,
    do_guidance: bool = True,
    concurrency: int | None = None,
    limit_periods: int = 20,
    as_of: date | None = None,
) -> dict[str, int]:
    """Ingest demand data for the universe (or an explicit ticker list).

    Returns a summary dict of per-source ticker counts written.
    """
    as_of = as_of or date.today()
    concurrency = concurrency or settings.demand_data_concurrency

    ohlcv = OHLCVStore(data_dir=settings.data_dir)
    meta = TickerMetaStore(data_dir=settings.data_dir)

    universe = [t.upper() for t in tickers] if tickers else build_demand_universe(
        settings, ohlcv, meta
    )
    if not universe:
        logger.warning("demand_data_empty_universe")
        return {
            "tickers": 0,
            "fundamentals": 0,
            "estimates": 0,
            "short_interest": 0,
            "guidance": 0,
        }

    # Fundamentals source: Finnhub Fundamental-1 (preferred) or Polygon.
    fund_source = (settings.fundamentals_source or "finnhub").lower()

    finnhub = None
    if (do_estimates or (do_fundamentals and fund_source == "finnhub")) and settings.finnhub_api_key:
        from tyche.market_data.finnhub import FinnhubClient

        finnhub = FinnhubClient(
            api_key=settings.finnhub_api_key,
            rate_limit_rpm=settings.finnhub_rate_limit_rpm,
        )

    # Polygon needed for: short interest, guidance (Benzinga key), and
    # fundamentals only when the source is polygon or Finnhub is unavailable.
    poly_for_fund = do_fundamentals and (fund_source == "polygon" or finnhub is None)
    polygon: PolygonClient | None = None
    if (poly_for_fund or do_short_interest) and settings.polygon_api_key:
        polygon = PolygonClient(
            api_key=settings.polygon_api_key,
            base_url=settings.polygon_base_url,
            rate_limit_rpm=settings.polygon_rate_limit_rpm,
        )

    # Benzinga Corporate Guidance (via the Massive/Polygon key).
    benzinga = None
    if do_guidance and settings.polygon_api_key:
        from tyche.market_data.benzinga import BenzingaClient

        benzinga = BenzingaClient(
            api_key=settings.polygon_api_key,
            base_url=settings.polygon_base_url,
            rate_limit_rpm=settings.polygon_rate_limit_rpm,
        )

    fund_store = FundamentalsStore(data_dir=settings.data_dir)
    est_store = EstimatesStore(data_dir=settings.data_dir)
    si_store = ShortInterestStore(data_dir=settings.data_dir)
    cat_store = CatalystSignalStore(data_dir=settings.data_dir) if benzinga else None

    counts = {
        "tickers": len(universe),
        "fundamentals": 0,
        "estimates": 0,
        "short_interest": 0,
        "guidance": 0,
    }

    # Cross-ticker cache: canonical Finnhub symbol → fetched rows (dual-class).
    _fund_rows_cache: dict[str, list[dict]] = {}
    _est_rows_cache: dict[str, list[dict]] = {}

    async def _fetch_fundamental_rows(ticker: str) -> list[dict]:
        for sym in finnhub_symbol_candidates(ticker):
            if sym in _fund_rows_cache:
                cached = _fund_rows_cache[sym]
                if cached:
                    return cached
                continue
            rows = await finnhub.get_standardized_financials(
                sym, freq="quarterly", limit=limit_periods, preliminary=True
            )
            if not rows:
                rows = await finnhub.get_financials_statements(
                    sym, freq="quarterly", limit=limit_periods
                )
            if not rows:
                rows = await finnhub.get_financials_statements(
                    sym, freq="annual", limit=min(limit_periods, 8)
                )
            _fund_rows_cache[sym] = rows
            if rows:
                if sym != ticker.upper():
                    logger.debug(
                        "dual_class_fundamentals",
                        ticker=ticker,
                        fetch_symbol=sym,
                    )
                return rows
        return []

    async def _fetch_estimate_rows(ticker: str) -> list[dict]:
        for sym in finnhub_symbol_candidates(ticker):
            if sym in _est_rows_cache:
                cached = _est_rows_cache[sym]
                if cached:
                    return cached
                continue
            est_rows: list[dict] = []
            est_rows += await finnhub.get_recommendation_trends(sym)
            est_rows += await finnhub.get_earnings_surprises(sym)
            est_rows += await finnhub.get_estimates(sym, as_of=as_of)
            est_rows += await finnhub.get_price_target(sym, as_of=as_of)
            est_rows += await finnhub.get_basic_financials(sym, as_of=as_of)
            _est_rows_cache[sym] = est_rows
            if est_rows:
                if sym != ticker.upper():
                    logger.debug(
                        "dual_class_estimates",
                        ticker=ticker,
                        fetch_symbol=sym,
                    )
                return est_rows
        return []

    # ── Per-source workers (one ticker each) ───────────────────────────
    # Each source hits a different API (Finnhub / Polygon / Benzinga) with its
    # own client-side rate budget, so they're run as independent pipelines that
    # execute fully in parallel — no source waits on another's per-ticker work.

    async def _fundamentals(ticker: str) -> None:
        try:
            rows: list[dict] = []
            if finnhub is not None and fund_source != "polygon":
                rows = await _fetch_fundamental_rows(ticker)
            if not rows and polygon is not None:
                rows = await polygon.get_financials(
                    ticker, timeframe="quarterly", limit=limit_periods
                )
            if rows:
                await asyncio.to_thread(
                    fund_store.write_financials, ticker, pd.DataFrame(rows)
                )
                counts["fundamentals"] += 1
        except Exception:
            logger.warning("demand_fundamentals_failed", ticker=ticker, exc_info=True)

    async def _short_interest(ticker: str) -> None:
        try:
            rows = await polygon.get_short_interest(ticker)
            if rows:
                await asyncio.to_thread(si_store.write_records, ticker, pd.DataFrame(rows))
                counts["short_interest"] += 1
        except Exception:
            logger.warning("demand_short_interest_failed", ticker=ticker, exc_info=True)

    async def _estimates(ticker: str) -> None:
        try:
            est_rows = await _fetch_estimate_rows(ticker)
            if est_rows:
                await asyncio.to_thread(est_store.write_records, ticker, pd.DataFrame(est_rows))
                counts["estimates"] += 1
        except Exception:
            logger.warning("demand_estimates_failed", ticker=ticker, exc_info=True)

    async def _guidance(ticker: str) -> None:
        try:
            recs = await _guidance_catalyst_records(
                benzinga,
                ticker,
                est_store=est_store,
                fund_store=fund_store,
                as_of=as_of,
            )
            if recs is not None and not recs.empty:
                await asyncio.to_thread(cat_store.write_records, ticker, recs)
                counts["guidance"] += 1
        except Exception:
            logger.warning("demand_guidance_failed", ticker=ticker, exc_info=True)

    async def _drain(label: str, worker, conc: int) -> None:
        """Run *worker* over the whole universe with a bounded semaphore."""
        sem = asyncio.Semaphore(conc)

        async def _one(t: str) -> None:
            async with sem:
                await worker(t)

        tasks = [asyncio.create_task(_one(t)) for t in universe]
        done = 0
        for coro in asyncio.as_completed(tasks):
            await coro
            done += 1
            if done % 250 == 0:
                logger.info(
                    "demand_source_progress", source=label, done=done, total=len(universe)
                )
        logger.info("demand_source_complete", source=label, total=len(universe))

    fund_available = (finnhub is not None and fund_source != "polygon") or polygon is not None
    pipelines = []
    if do_fundamentals and fund_available:
        pipelines.append(_drain("fundamentals", _fundamentals, concurrency))
    if do_estimates and finnhub is not None:
        pipelines.append(_drain("estimates", _estimates, concurrency))
    if do_short_interest and polygon is not None:
        pipelines.append(_drain("short_interest", _short_interest, concurrency))
    if do_guidance and benzinga is not None and cat_store is not None:
        pipelines.append(_drain("guidance", _guidance, concurrency))

    logger.info(
        "demand_data_ingest_start",
        tickers=len(universe),
        concurrency=concurrency,
        parallel_sources=len(pipelines),
        fundamentals_source=fund_source,
        finnhub_rpm=settings.finnhub_rate_limit_rpm if finnhub is not None else None,
    )

    # All source pipelines run concurrently; each self-throttles to its API's
    # rate limit, so the three budgets saturate independently.
    await asyncio.gather(*pipelines)

    logger.info("demand_data_ingest_complete", **counts)
    return counts
