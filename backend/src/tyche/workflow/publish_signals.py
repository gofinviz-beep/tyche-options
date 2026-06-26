"""Route-level signal publisher (GCP-C).

Reads compact ``signals/`` Parquet (and optional local SQLite snapshots) and
writes ultra-compact ``published/routes/*.json`` artifacts for the UI/API layer.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

import structlog

from tyche.config import TycheSettings, get_settings
from tyche.exceptions import PublishError
from tyche.market_data.alpha_store import AlphaSignalStore
from tyche.market_data.data_store import TickerMetaStore
from tyche.ops.run_manifest import RunManifest, new_run_id
from tyche.persistence.published_route_registry import (
    ALPHA_PEAK_SOURCE_CANDIDATES as _ALPHA_PEAK_CANDIDATES,
    ALPHA_SUSTAINED_SOURCE_CANDIDATES as _ALPHA_SUSTAINED_CANDIDATES,
    ROUTE_FILES,
    ROUTE_PATHS,
    first_existing_path,
)
from tyche.schemas.alpha import (
    AlphaDemandDimensions,
    AlphaFactorScores,
    AlphaScanResponse,
    AlphaSignalResponse,
)
from tyche.schemas.news import NewsSignalResponse
from tyche.schemas.stocks import ConvictionSnapshotResponse
from tyche.storage import exists as storage_exists, read_parquet, write_json
from tyche.storage.json_io import sanitize_json_records
from tyche.storage.paths import StorageContext, join_uri, storage_context_from_settings

logger = structlog.get_logger()

RouteStatus = Literal["ok", "stale", "unavailable"]


def _run_coroutine(coro):
    """Run *coro* from sync code; safe when ``execute_job`` already has a loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()

_STOCKS_CONVICTION_CANDIDATES = ("signals/stocks/conviction.parquet",)
_STOCKS_DEEP_DIPS_CANDIDATES = ("signals/stocks/deep_dips.parquet",)
_STOCKS_HISTORY_CANDIDATES = ("signals/stocks/history_summary.parquet",)
_STOCKS_TRANSITIONS_CANDIDATES = ("signals/stocks/transitions.parquet",)
_INTELLIGENCE_NEWS_CANDIDATES = (
    "signals/intelligence/news.parquet",
    "signals/intelligence/_checkpoints/news.partial.parquet",
)
_INTELLIGENCE_FILINGS_CANDIDATES = (
    "signals/intelligence/filings.parquet",
    "signals/intelligence/_checkpoints/filings.partial.parquet",
)
_INTELLIGENCE_INSIDER_CANDIDATES = (
    "signals/intelligence/insider.parquet",
    "signals/intelligence/_checkpoints/insider.partial.parquet",
)
_OPTIONS_SCANNER_CANDIDATES = ("signals/options/scanner.parquet",)


@dataclass
class PublishConfig:
    """Publisher knobs."""

    data_dir: str = "data"
    ctx: StorageContext | None = None
    run_id: str | None = None
    alpha_row_limit: int = 500
    conviction_row_limit: int = 5000
    intelligence_row_limit: int = 500
    strict: bool = True
    max_stale_minutes: int = 180
    settings: TycheSettings | None = None


@dataclass
class RoutePublishResult:
    """Outcome for a single route artifact."""

    route_key: str
    route: str
    rel_path: str
    as_of: str | None
    row_count: int
    source_paths: list[str]
    status: RouteStatus
    generated_at: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class PublishResult:
    """Aggregate publisher outcome."""

    run_id: str
    generated_at: str
    routes: list[RoutePublishResult]
    manifest_rel: str
    run_manifest_rel: str
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _route_rel(route_key: str) -> str:
    return join_uri("published", "routes", ROUTE_FILES[route_key])


def _route_manifest_rel(route_key: str) -> str:
    return join_uri("published", "route_manifests", ROUTE_FILES[route_key])


def _build_route_envelope(
    *,
    route_key: str,
    run_id: str,
    as_of: str | None,
    row_count: int,
    source_paths: list[str],
    status: RouteStatus,
    data: Any,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "route": ROUTE_PATHS[route_key],
        "as_of": as_of,
        "generated_at": _utc_now_iso(),
        "run_id": run_id,
        "row_count": row_count,
        "source_paths": source_paths,
        "status": status,
        "data": data,
    }
    if warnings:
        envelope["warnings"] = warnings
    return envelope


def _write_route_artifact(
    route_key: str,
    envelope: dict[str, Any],
    *,
    ctx: StorageContext,
) -> str:
    rel = _route_rel(route_key)
    write_json(envelope, rel, atomic=True, ctx=ctx)
    manifest = {
        "route": envelope["route"],
        "artifact": rel,
        "as_of": envelope.get("as_of"),
        "generated_at": envelope["generated_at"],
        "run_id": envelope["run_id"],
        "row_count": envelope["row_count"],
        "source_paths": envelope["source_paths"],
        "status": envelope["status"],
    }
    write_json(manifest, _route_manifest_rel(route_key), atomic=True, ctx=ctx)
    return rel


def _parse_iso_age_minutes(iso_ts: str | None) -> float | None:
    if not iso_ts:
        return None
    try:
        parsed = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
        return delta.total_seconds() / 60.0
    except ValueError:
        return None


def _stale_status(
    computed_at: str | None,
    *,
    max_stale_minutes: int,
) -> tuple[RouteStatus, list[str]]:
    age = _parse_iso_age_minutes(computed_at)
    if age is None:
        return "ok", []
    if age > max_stale_minutes:
        return "stale", [f"upstream age {age:.0f}m exceeds {max_stale_minutes}m"]
    return "ok", []


def _alpha_record_to_response(
    r: dict[str, Any],
    *,
    market_cap: float | None = None,
    institutional_pct: float | None = None,
    sector: str | None = None,
    is_watchlist: bool = False,
) -> AlphaSignalResponse:
    factors = r.get("factors") or {}
    demand = r.get("demand") or {}
    return AlphaSignalResponse(
        ticker=r.get("ticker", ""),
        alpha_score=r.get("alpha_score", 0.0) or 0.0,
        signal=r.get("signal", "avoid"),
        horizon=r.get("horizon", "none"),
        factors=AlphaFactorScores(
            momentum=factors.get("momentum", 0.0) or 0.0,
            relative_strength=factors.get("relative_strength", 0.0) or 0.0,
            trend_quality=factors.get("trend_quality", 0.0) or 0.0,
            breakout=factors.get("breakout", 0.0) or 0.0,
            volume_thrust=factors.get("volume_thrust", 0.0) or 0.0,
        ),
        breakout_prob_swing=r.get("breakout_prob_swing"),
        breakout_prob_trend=r.get("breakout_prob_trend"),
        breakout_prob_thematic=r.get("breakout_prob_thematic"),
        last_close=r.get("last_close", 0.0) or 0.0,
        return_63d=r.get("return_63d"),
        return_126d=r.get("return_126d"),
        return_252d=r.get("return_252d"),
        rs_126d=r.get("rs_126d"),
        pct_off_52w_high=r.get("pct_off_52w_high"),
        ema_stack_score=int(r.get("ema_stack_score", 0) or 0),
        volume_thrust_ratio=r.get("volume_thrust_ratio"),
        as_of_date=r.get("as_of_date"),
        regime=r.get("regime", "narrative") or "narrative",
        demand=AlphaDemandDimensions(
            fund=demand.get("fund"),
            est=demand.get("est"),
            catalyst=demand.get("catalyst"),
            policy=demand.get("policy"),
            squeeze=demand.get("squeeze"),
            net=demand.get("net"),
        )
        if demand
        else None,
        demand_multiplier=r.get("demand_multiplier"),
        overextension_score=r.get("overextension_score"),
        overextension_penalty=r.get("overextension_penalty"),
        market_cap=market_cap if market_cap and market_cap > 0 else None,
        institutional_pct=(
            institutional_pct if institutional_pct and institutional_pct > 0 else None
        ),
        sector=sector,
        is_watchlist=is_watchlist,
    )


def _read_alpha_scan(
    *,
    data_dir: str,
    ctx: StorageContext,
    variant: str,
    row_limit: int,
    settings: TycheSettings,
) -> tuple[AlphaScanResponse, str, list[str], RouteStatus, list[str]]:
    """Build alpha scan payload; returns (scan, as_of, sources, status, warnings)."""
    if variant == "sustained":
        source_rel = first_existing_path(_ALPHA_SUSTAINED_CANDIDATES, ctx=ctx)
        store_variant = "sustained"
        if not source_rel:
            source_rel = first_existing_path(_ALPHA_PEAK_CANDIDATES, ctx=ctx)
            store_variant = "peak"
    else:
        source_rel = first_existing_path(_ALPHA_PEAK_CANDIDATES, ctx=ctx)
        store_variant = "peak"

    if not source_rel:
        raise PublishError(
            "Required alpha snapshot missing "
            f"(tried {_ALPHA_SUSTAINED_CANDIDATES + _ALPHA_PEAK_CANDIDATES})"
        )

    store = AlphaSignalStore(
        data_dir=data_dir,
        variant=store_variant,
        ctx=ctx,
        rel_path=source_rel,
    )
    records, as_of, computed_at = store.read_latest()
    status, stale_warnings = _stale_status(
        computed_at,
        max_stale_minutes=settings.published_max_age_minutes,
    )

    meta = TickerMetaStore(data_dir=data_dir, ctx=ctx)
    floor = settings.alpha_min_market_cap_millions * 1_000_000
    watchlist = frozenset(s.upper() for s in (settings.watchlist_symbols or []))

    if meta.exists and records:
        all_tickers = [r["ticker"] for r in records]
        eligible = set(meta.filter_equity_only(all_tickers))
        caps = meta.get_market_caps(all_tickers)
        records = [
            r
            for r in records
            if r["ticker"] in eligible and (caps.get(r["ticker"]) or 0) >= floor
        ]

    records.sort(key=lambda r: r.get("alpha_score") or 0, reverse=True)
    strong_buy = sum(1 for r in records if r.get("signal") == "strong_buy")
    buy = sum(1 for r in records if r.get("signal") == "buy")
    display = records[:row_limit]
    tickers = [r["ticker"] for r in display]
    market_caps = meta.get_market_caps(tickers) if meta.exists else {}
    sectors = meta.get_sectors(tickers) if meta.exists else {}
    inst_pcts = meta.get_institutional_pcts(tickers) if meta.exists else {}

    ml_available = any(r.get("breakout_prob_swing") is not None for r in display)
    signals = [
        _alpha_record_to_response(
            r,
            market_cap=market_caps.get(r["ticker"]),
            institutional_pct=inst_pcts.get(r["ticker"]),
            sector=sectors.get(r["ticker"]),
            is_watchlist=r["ticker"] in watchlist,
        )
        for r in display
    ]
    scan = AlphaScanResponse(
        scanned_at=_utc_now_iso(),
        as_of_date=as_of,
        computed_at=computed_at,
        ml_available=ml_available,
        variant=store.variant,
        total=len(records),
        strong_buy_count=strong_buy,
        buy_count=buy,
        signals=signals,
    )
    return scan, as_of, [source_rel], status, stale_warnings


async def _load_conviction_snapshots(
    *,
    row_limit: int,
    data_dir: str,
    ctx: StorageContext,
    settings: TycheSettings,
) -> tuple[list[ConvictionSnapshotResponse], str | None, list[str], RouteStatus]:
    from tyche.market_data.stocks_conviction_store import load_stocks_conviction_parquet

    signal_rel = first_existing_path(_STOCKS_CONVICTION_CANDIDATES, ctx=ctx)
    if signal_rel:
        rows, as_of = load_stocks_conviction_parquet(
            ctx=ctx,
            row_limit=row_limit,
            rel_path=signal_rel,
        )
        if rows:
            return rows, as_of, [signal_rel], "ok"

    if not settings.api_allow_local_db_fallback:
        sources = [signal_rel] if signal_rel else []
        return [], None, sources, "unavailable"

    from tyche.persistence.conviction_repository import (
        get_latest_snapshot_date,
        get_snapshots_for_date,
    )

    try:
        latest = await get_latest_snapshot_date()
    except RuntimeError as exc:
        logger.warning("publish_conviction_db_unavailable", error=str(exc))
        return [], None, [], "unavailable"
    if latest is None:
        return [], None, [], "unavailable"

    snaps = await get_snapshots_for_date(latest)
    if not snaps:
        return [], None, [], "unavailable"

    meta = TickerMetaStore(data_dir=data_dir, ctx=ctx)
    tickers = [s.ticker for s in snaps[:row_limit]]
    market_caps = meta.get_market_caps(tickers) if meta.exists else {}
    inst_pcts = meta.get_institutional_pcts(tickers) if meta.exists else {}
    sectors = meta.get_sectors(tickers) if meta.exists else {}

    rows: list[ConvictionSnapshotResponse] = []
    for snap in snaps[:row_limit]:
        resp = ConvictionSnapshotResponse(**snap.to_dict())
        resp.market_cap = market_caps.get(snap.ticker)
        resp.institutional_pct = inst_pcts.get(snap.ticker)
        resp.sector = sectors.get(snap.ticker)
        rows.append(resp)

    sources = [f"conviction.db:snapshots:{latest.isoformat()}"]
    if signal_rel:
        sources.insert(0, signal_rel)
    return rows, latest.isoformat(), sources, "ok"


def _news_rows_from_parquet(
    *,
    ctx: StorageContext,
    settings: TycheSettings,
    row_limit: int,
) -> tuple[list[dict[str, Any]], list[str], RouteStatus] | None:
    signal_rel = first_existing_path(_INTELLIGENCE_NEWS_CANDIDATES, ctx=ctx)
    if not signal_rel:
        return None

    df = read_parquet(signal_rel, ctx=ctx)
    if df is None or df.empty:
        return [], [signal_rel], "unavailable"

    threshold = settings.news_risk_threshold
    rows = sanitize_json_records(df.to_dict(orient="records"))
    if rows and "has_risk" not in rows[0]:
        for row in rows:
            score = row.get("news_impact_score", 0.0) or 0.0
            row["has_risk"] = score < threshold

    rows.sort(key=lambda x: x.get("news_impact_score", 0.0))
    return rows[:row_limit], [signal_rel], "ok"


def _filing_rows_from_parquet(
    *,
    ctx: StorageContext,
    row_limit: int,
) -> tuple[list[dict[str, Any]], list[str], RouteStatus] | None:
    signal_rel = first_existing_path(_INTELLIGENCE_FILINGS_CANDIDATES, ctx=ctx)
    if not signal_rel:
        return None

    df = read_parquet(signal_rel, ctx=ctx)
    if df is None or df.empty:
        return [], [signal_rel], "unavailable"

    signals = sanitize_json_records(df.to_dict(orient="records"))
    signals.sort(
        key=lambda x: (
            0 if x.get("insider_cluster_sell") else 1,
            x.get("last_8k_impact") or 0.0,
        ),
    )
    return signals[:row_limit], [signal_rel], "ok"


def _insider_rows_from_parquet(
    *,
    ctx: StorageContext,
    row_limit: int,
) -> tuple[list[dict[str, Any]], list[str], RouteStatus] | None:
    signal_rel = first_existing_path(_INTELLIGENCE_INSIDER_CANDIDATES, ctx=ctx)
    if not signal_rel:
        return None

    df = read_parquet(signal_rel, ctx=ctx)
    if df is None or df.empty:
        return [], [signal_rel], "unavailable"

    rows = sanitize_json_records(df.to_dict(orient="records"))
    rows.sort(
        key=lambda x: (
            0 if x.get("insider_cluster_sell") else 1,
            -(x.get("insider_sell_count_30d") or 0),
        ),
    )
    return rows[:row_limit], [signal_rel], "ok"


async def _load_news_signals(
    *,
    settings: TycheSettings,
    row_limit: int,
    ctx: StorageContext | None = None,
) -> tuple[list[dict[str, Any]], list[str], RouteStatus]:
    ctx = ctx or storage_context_from_settings(settings)
    if settings.data_backend == "gcs":
        parquet_rows = _news_rows_from_parquet(
            ctx=ctx, settings=settings, row_limit=row_limit
        )
        if parquet_rows is not None:
            return parquet_rows

    try:
        from tyche.market_data.news_signals import get_all_signals

        raw = await get_all_signals()
    except Exception as exc:
        logger.warning("publish_news_signals_unavailable", error=str(exc))
        parquet_rows = _news_rows_from_parquet(
            ctx=ctx, settings=settings, row_limit=row_limit
        )
        if parquet_rows is not None:
            return parquet_rows
        return [], [], "unavailable"

    threshold = settings.news_risk_threshold
    signals = [
        NewsSignalResponse(
            ticker=s["ticker"],
            news_impact_score=s["news_impact_score"],
            negative_count_24h=s["negative_count_24h"],
            positive_count_24h=s["positive_count_24h"],
            total_count_24h=s["total_count_24h"],
            dominant_event_type=s.get("dominant_event_type"),
            last_negative_at=s.get("last_negative_at"),
            last_positive_at=s.get("last_positive_at"),
            has_risk=s["news_impact_score"] < threshold,
            updated_at=s.get("updated_at"),
        ).model_dump(mode="json")
        for s in raw
    ]
    signals.sort(key=lambda x: x.get("news_impact_score", 0.0))
    sources = ["news.db:news_signals"]
    parquet_rel = first_existing_path(_INTELLIGENCE_NEWS_CANDIDATES, ctx=ctx)
    if parquet_rel:
        sources.insert(0, parquet_rel)
    return signals[:row_limit], sources, "ok"


async def _load_filing_signals(
    *,
    row_limit: int,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
) -> tuple[list[dict[str, Any]], list[str], RouteStatus]:
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    if settings.data_backend == "gcs":
        parquet_rows = _filing_rows_from_parquet(ctx=ctx, row_limit=row_limit)
        if parquet_rows is not None:
            return parquet_rows

    try:
        from tyche.market_data.filing_signals import get_all_filing_signals

        raw = await get_all_filing_signals()
    except Exception as exc:
        logger.warning("publish_filing_signals_unavailable", error=str(exc))
        parquet_rows = _filing_rows_from_parquet(ctx=ctx, row_limit=row_limit)
        if parquet_rows is not None:
            return parquet_rows
        return [], [], "unavailable"

    signals = list(raw)
    signals.sort(
        key=lambda x: (
            0 if x.get("insider_cluster_sell") else 1,
            x.get("last_8k_impact") or 0.0,
        ),
    )
    sources = ["news.db:filing_signals"]
    parquet_rel = first_existing_path(_INTELLIGENCE_FILINGS_CANDIDATES, ctx=ctx)
    if parquet_rel:
        sources.insert(0, parquet_rel)
    return signals[:row_limit], sources, "ok"


async def _load_insider_signals(
    *,
    row_limit: int,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
) -> tuple[list[dict[str, Any]], list[str], RouteStatus]:
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    parquet_rows = _insider_rows_from_parquet(ctx=ctx, row_limit=row_limit)
    if parquet_rows is not None:
        return parquet_rows
    return [], [], "unavailable"


def _unavailable_data(message: str) -> dict[str, Any]:
    return {"message": message, "rows": []}


def publish_stocks_alpha(
    *,
    config: PublishConfig,
    run_id: str,
    settings: TycheSettings,
) -> RoutePublishResult:
    ctx = config.ctx or storage_context_from_settings(settings)
    scan, as_of, sources, status, warnings = _read_alpha_scan(
        data_dir=config.data_dir,
        ctx=ctx,
        variant="sustained",
        row_limit=config.alpha_row_limit,
        settings=settings,
    )
    envelope = _build_route_envelope(
        route_key="stocks_alpha",
        run_id=run_id,
        as_of=as_of,
        row_count=len(scan.signals),
        source_paths=sources,
        status=status,
        data=scan.model_dump(mode="json"),
        warnings=warnings or None,
    )
    rel = _write_route_artifact("stocks_alpha", envelope, ctx=ctx)
    return RoutePublishResult(
        route_key="stocks_alpha",
        route=ROUTE_PATHS["stocks_alpha"],
        rel_path=rel,
        as_of=as_of,
        row_count=len(scan.signals),
        source_paths=sources,
        status=status,
        generated_at=envelope["generated_at"],
        warnings=warnings,
    )


def publish_stocks_summary(
    *,
    alpha_result: RoutePublishResult,
    conviction_result: RoutePublishResult | None,
    config: PublishConfig,
    run_id: str,
) -> RoutePublishResult:
    ctx = config.ctx or storage_context_from_settings(config.settings or get_settings())
    data = {
        "alpha": {
            "as_of": alpha_result.as_of,
            "row_count": alpha_result.row_count,
            "status": alpha_result.status,
            "source_paths": alpha_result.source_paths,
        },
        "conviction": (
            {
                "as_of": conviction_result.as_of,
                "row_count": conviction_result.row_count,
                "status": conviction_result.status,
                "source_paths": conviction_result.source_paths,
            }
            if conviction_result
            else {"status": "unavailable", "row_count": 0}
        ),
    }
    envelope = _build_route_envelope(
        route_key="stocks",
        run_id=run_id,
        as_of=alpha_result.as_of,
        row_count=alpha_result.row_count,
        source_paths=alpha_result.source_paths,
        status=alpha_result.status,
        data=data,
    )
    rel = _write_route_artifact("stocks", envelope, ctx=ctx)
    return RoutePublishResult(
        route_key="stocks",
        route=ROUTE_PATHS["stocks"],
        rel_path=rel,
        as_of=alpha_result.as_of,
        row_count=alpha_result.row_count,
        source_paths=alpha_result.source_paths,
        status=alpha_result.status,
        generated_at=envelope["generated_at"],
    )


def publish_stocks_conviction(
    *,
    config: PublishConfig,
    run_id: str,
    settings: TycheSettings,
) -> RoutePublishResult:
    ctx = config.ctx or storage_context_from_settings(settings)
    rows, as_of, sources, status = _run_coroutine(
        _load_conviction_snapshots(
            row_limit=config.conviction_row_limit,
            data_dir=config.data_dir,
            ctx=ctx,
            settings=settings,
        )
    )
    data = {
        "snapshots": [r.model_dump(mode="json") for r in rows],
        "total": len(rows),
    }
    envelope = _build_route_envelope(
        route_key="stocks_conviction",
        run_id=run_id,
        as_of=as_of,
        row_count=len(rows),
        source_paths=sources,
        status=status,
        data=data if rows else _unavailable_data("No conviction snapshots available"),
    )
    rel = _write_route_artifact("stocks_conviction", envelope, ctx=ctx)
    return RoutePublishResult(
        route_key="stocks_conviction",
        route=ROUTE_PATHS["stocks_conviction"],
        rel_path=rel,
        as_of=as_of,
        row_count=len(rows),
        source_paths=sources,
        status=status,
        generated_at=envelope["generated_at"],
    )


def publish_stocks_deep_dips(
    *,
    config: PublishConfig,
    run_id: str,
    settings: TycheSettings,
) -> RoutePublishResult:
    from tyche.market_data.stocks_deep_dips_store import load_deep_dips_scan

    ctx = config.ctx or storage_context_from_settings(settings)
    signal_rel = first_existing_path(_STOCKS_DEEP_DIPS_CANDIDATES, ctx=ctx)
    sources = [signal_rel] if signal_rel else []
    scan = load_deep_dips_scan(ctx=ctx, rel_path=signal_rel) if signal_rel else None
    status: RouteStatus = "ok" if scan is not None else "unavailable"
    row_count = len(scan.alerts) if scan else 0
    data = (
        scan.model_dump(mode="json")
        if scan
        else _unavailable_data("No deep dip scan available")
    )
    envelope = _build_route_envelope(
        route_key="stocks_deep_dips",
        run_id=run_id,
        as_of=scan.as_of_date if scan else None,
        row_count=row_count,
        source_paths=sources,
        status=status,
        data=data,
    )
    rel = _write_route_artifact("stocks_deep_dips", envelope, ctx=ctx)
    return RoutePublishResult(
        route_key="stocks_deep_dips",
        route=ROUTE_PATHS["stocks_deep_dips"],
        rel_path=rel,
        as_of=scan.as_of_date if scan else None,
        row_count=row_count,
        source_paths=sources,
        status=status,
        generated_at=envelope["generated_at"],
    )


def publish_stocks_history(
    *,
    config: PublishConfig,
    run_id: str,
    settings: TycheSettings,
) -> RoutePublishResult:
    from tyche.market_data.stocks_history_store import (
        load_history_summary_rows,
        load_transition_responses,
    )

    ctx = config.ctx or storage_context_from_settings(settings)
    summary_rel = first_existing_path(_STOCKS_HISTORY_CANDIDATES, ctx=ctx)
    transitions_rel = first_existing_path(_STOCKS_TRANSITIONS_CANDIDATES, ctx=ctx)
    sources = [p for p in (summary_rel, transitions_rel) if p]

    summaries = (
        load_history_summary_rows(ctx=ctx, rel_path=summary_rel)
        if summary_rel
        else []
    )
    transitions = load_transition_responses(ctx=ctx) if transitions_rel else []
    as_of = summaries[0].get("as_of") if summaries else None
    status: RouteStatus = "ok" if summaries or transitions else "unavailable"
    data = {
        "summaries": summaries,
        "transitions": [t.model_dump(mode="json") for t in transitions],
        "total_summaries": len(summaries),
        "total_transitions": len(transitions),
    }
    envelope = _build_route_envelope(
        route_key="stocks_history",
        run_id=run_id,
        as_of=as_of,
        row_count=len(summaries),
        source_paths=sources,
        status=status,
        data=data if (summaries or transitions) else _unavailable_data("No history summary available"),
    )
    rel = _write_route_artifact("stocks_history", envelope, ctx=ctx)
    return RoutePublishResult(
        route_key="stocks_history",
        route=ROUTE_PATHS["stocks_history"],
        rel_path=rel,
        as_of=as_of,
        row_count=len(summaries),
        source_paths=sources,
        status=status,
        generated_at=envelope["generated_at"],
    )


def publish_intelligence_news(
    *,
    config: PublishConfig,
    run_id: str,
    settings: TycheSettings,
) -> RoutePublishResult:
    ctx = config.ctx or storage_context_from_settings(settings)
    rows, sources, status = _run_coroutine(
        _load_news_signals(
            settings=settings,
            row_limit=config.intelligence_row_limit,
            ctx=ctx,
        )
    )
    data = {"signals": rows, "total": len(rows)}
    envelope = _build_route_envelope(
        route_key="intelligence_news",
        run_id=run_id,
        as_of=None,
        row_count=len(rows),
        source_paths=sources,
        status=status,
        data=data if rows else _unavailable_data("No news signals available"),
    )
    rel = _write_route_artifact("intelligence_news", envelope, ctx=ctx)
    return RoutePublishResult(
        route_key="intelligence_news",
        route=ROUTE_PATHS["intelligence_news"],
        rel_path=rel,
        as_of=None,
        row_count=len(rows),
        source_paths=sources,
        status=status,
        generated_at=envelope["generated_at"],
    )


def publish_intelligence_filings(
    *,
    config: PublishConfig,
    run_id: str,
) -> RoutePublishResult:
    settings = config.settings or get_settings()
    ctx = config.ctx or storage_context_from_settings(settings)
    rows, sources, status = _run_coroutine(
        _load_filing_signals(
            row_limit=config.intelligence_row_limit,
            settings=settings,
            ctx=ctx,
        )
    )
    data = {"signals": rows, "total": len(rows)}
    envelope = _build_route_envelope(
        route_key="intelligence_filings",
        run_id=run_id,
        as_of=None,
        row_count=len(rows),
        source_paths=sources,
        status=status,
        data=data if rows else _unavailable_data("No filing signals available"),
    )
    rel = _write_route_artifact("intelligence_filings", envelope, ctx=ctx)
    return RoutePublishResult(
        route_key="intelligence_filings",
        route=ROUTE_PATHS["intelligence_filings"],
        rel_path=rel,
        as_of=None,
        row_count=len(rows),
        source_paths=sources,
        status=status,
        generated_at=envelope["generated_at"],
    )


def publish_intelligence_insider(
    *,
    config: PublishConfig,
    run_id: str,
) -> RoutePublishResult:
    settings = config.settings or get_settings()
    ctx = config.ctx or storage_context_from_settings(settings)
    rows, sources, status = _run_coroutine(
        _load_insider_signals(
            row_limit=config.intelligence_row_limit,
            settings=settings,
            ctx=ctx,
        )
    )
    data = {"signals": rows, "total": len(rows)}
    envelope = _build_route_envelope(
        route_key="intelligence_insider",
        run_id=run_id,
        as_of=None,
        row_count=len(rows),
        source_paths=sources,
        status=status,
        data=data if rows else _unavailable_data("No insider signals available"),
    )
    rel = _write_route_artifact("intelligence_insider", envelope, ctx=ctx)
    return RoutePublishResult(
        route_key="intelligence_insider",
        route=ROUTE_PATHS["intelligence_insider"],
        rel_path=rel,
        as_of=None,
        row_count=len(rows),
        source_paths=sources,
        status=status,
        generated_at=envelope["generated_at"],
    )


def publish_options_scanner(
    *,
    config: PublishConfig,
    run_id: str,
    settings: TycheSettings,
) -> RoutePublishResult:
    from tyche.market_data.options_scanner_store import (
        OPTIONS_SCANNER_REL,
        build_scan_payload,
        load_scanner_parquet,
        load_scanner_report,
    )

    ctx = config.ctx or storage_context_from_settings(settings)
    source_rel = first_existing_path(_OPTIONS_SCANNER_CANDIDATES, ctx=ctx)
    sources = [source_rel] if source_rel else []
    report = load_scanner_report(ctx=ctx)
    candidate_rows, as_of = load_scanner_parquet(ctx=ctx)
    status: RouteStatus = "ok" if candidate_rows or report else "unavailable"
    row_count = len(candidate_rows)
    data = build_scan_payload(report=report, candidate_rows=candidate_rows)
    if status == "unavailable":
        data = _unavailable_data("No scanner results available")
    envelope = _build_route_envelope(
        route_key="options_scanner",
        run_id=run_id,
        as_of=as_of or (report or {}).get("as_of_date"),
        row_count=row_count,
        source_paths=sources,
        status=status,
        data=data,
    )
    rel = _write_route_artifact("options_scanner", envelope, ctx=ctx)
    return RoutePublishResult(
        route_key="options_scanner",
        route=ROUTE_PATHS["options_scanner"],
        rel_path=rel,
        as_of=as_of or (report or {}).get("as_of_date"),
        row_count=row_count,
        source_paths=sources,
        status=status,
        generated_at=envelope["generated_at"],
    )


def publish_placeholder_route(
    *,
    route_key: str,
    config: PublishConfig,
    run_id: str,
    candidates: tuple[str, ...] = (),
    message: str = "Upstream signal not yet exported for this route",
) -> RoutePublishResult:
    settings = config.settings or get_settings()
    ctx = config.ctx or storage_context_from_settings(settings)
    source_rel = first_existing_path(candidates, ctx=ctx) if candidates else None
    status: RouteStatus = "ok" if source_rel else "unavailable"
    sources = [source_rel] if source_rel else []
    envelope = _build_route_envelope(
        route_key=route_key,
        run_id=run_id,
        as_of=None,
        row_count=0,
        source_paths=sources,
        status=status,
        data=_unavailable_data(message),
    )
    rel = _write_route_artifact(route_key, envelope, ctx=ctx)
    return RoutePublishResult(
        route_key=route_key,
        route=ROUTE_PATHS[route_key],
        rel_path=rel,
        as_of=None,
        row_count=0,
        source_paths=sources,
        status=status,
        generated_at=envelope["generated_at"],
    )


def publish_intelligence_summary(
    *,
    news: RoutePublishResult,
    filings: RoutePublishResult,
    insider: RoutePublishResult,
    config: PublishConfig,
    run_id: str,
) -> RoutePublishResult:
    ctx = config.ctx or storage_context_from_settings(config.settings or get_settings())
    data = {
        "news": {
            "row_count": news.row_count,
            "status": news.status,
            "source_paths": news.source_paths,
        },
        "filings": {
            "row_count": filings.row_count,
            "status": filings.status,
            "source_paths": filings.source_paths,
        },
        "insider": {
            "row_count": insider.row_count,
            "status": insider.status,
            "source_paths": insider.source_paths,
        },
    }
    row_count = news.row_count + filings.row_count + insider.row_count
    status: RouteStatus = (
        "ok"
        if any(r.status == "ok" for r in (news, filings, insider))
        else "unavailable"
    )
    envelope = _build_route_envelope(
        route_key="intelligence",
        run_id=run_id,
        as_of=None,
        row_count=row_count,
        source_paths=news.source_paths + filings.source_paths + insider.source_paths,
        status=status,
        data=data,
    )
    rel = _write_route_artifact("intelligence", envelope, ctx=ctx)
    return RoutePublishResult(
        route_key="intelligence",
        route=ROUTE_PATHS["intelligence"],
        rel_path=rel,
        as_of=None,
        row_count=row_count,
        source_paths=envelope["source_paths"],
        status=status,
        generated_at=envelope["generated_at"],
    )


def publish_options_summary(
    *,
    scanner: RoutePublishResult,
    config: PublishConfig,
    run_id: str,
) -> RoutePublishResult:
    ctx = config.ctx or storage_context_from_settings(config.settings or get_settings())
    data = {
        "scanner": {
            "row_count": scanner.row_count,
            "status": scanner.status,
            "source_paths": scanner.source_paths,
        },
    }
    envelope = _build_route_envelope(
        route_key="options",
        run_id=run_id,
        as_of=scanner.as_of,
        row_count=scanner.row_count,
        source_paths=scanner.source_paths,
        status=scanner.status,
        data=data,
    )
    rel = _write_route_artifact("options", envelope, ctx=ctx)
    return RoutePublishResult(
        route_key="options",
        route=ROUTE_PATHS["options"],
        rel_path=rel,
        as_of=scanner.as_of,
        row_count=scanner.row_count,
        source_paths=scanner.source_paths,
        status=scanner.status,
        generated_at=envelope["generated_at"],
    )


def _write_master_manifest(
    *,
    run_id: str,
    routes: list[RoutePublishResult],
    ctx: StorageContext,
    warnings: list[str],
    errors: list[str],
) -> str:
    manifest = {
        "run_id": run_id,
        "generated_at": _utc_now_iso(),
        "routes": [
            {
                "route": r.route,
                "path": r.rel_path,
                "status": r.status,
                "row_count": r.row_count,
                "as_of": r.as_of,
                "source_paths": r.source_paths,
            }
            for r in routes
        ],
        "warnings": warnings,
        "errors": errors,
    }
    rel = join_uri("published", "manifest.json")
    write_json(manifest, rel, atomic=True, ctx=ctx)
    return rel


def run_publish_signals(config: PublishConfig | None = None) -> PublishResult:
    """Publish all route-level JSON artifacts and job manifests."""
    cfg = config or PublishConfig()
    settings = cfg.settings or get_settings()
    ctx = cfg.ctx or storage_context_from_settings(settings)
    run_id = cfg.run_id or new_run_id()
    generated_at = _utc_now_iso()

    job_manifest = RunManifest.start(
        job_name="publish_signals",
        run_id=run_id,
        data_backend=ctx.backend,
    )

    routes: list[RoutePublishResult] = []
    warnings: list[str] = []
    errors: list[str] = []

    from tyche.ops.job_progress import log_job_phase

    try:
        log_job_phase("publish-signals", "stocks_alpha")
        alpha = publish_stocks_alpha(config=cfg, run_id=run_id, settings=settings)
        routes.append(alpha)
        log_job_phase(
            "publish-signals",
            "stocks_alpha",
            status="complete",
            rows=alpha.row_count,
        )

        log_job_phase("publish-signals", "stocks_conviction")
        job_manifest.input_paths.extend(alpha.source_paths)
        warnings.extend(alpha.warnings)

        conviction = publish_stocks_conviction(
            config=cfg, run_id=run_id, settings=settings
        )
        routes.append(conviction)
        log_job_phase(
            "publish-signals",
            "stocks_conviction",
            status="complete",
            rows=conviction.row_count,
        )
        if conviction.source_paths:
            job_manifest.input_paths.extend(conviction.source_paths)

        log_job_phase("publish-signals", "stocks_summary")
        routes.append(
            publish_stocks_summary(
                alpha_result=alpha,
                conviction_result=conviction,
                config=cfg,
                run_id=run_id,
            )
        )

        log_job_phase("publish-signals", "stocks_deep_dips")
        deep_dips = publish_stocks_deep_dips(
            config=cfg, run_id=run_id, settings=settings
        )
        routes.append(deep_dips)
        if deep_dips.source_paths:
            job_manifest.input_paths.extend(deep_dips.source_paths)

        log_job_phase("publish-signals", "stocks_history")
        history = publish_stocks_history(
            config=cfg, run_id=run_id, settings=settings
        )
        routes.append(history)
        if history.source_paths:
            job_manifest.input_paths.extend(history.source_paths)

        log_job_phase("publish-signals", "intelligence")
        news = publish_intelligence_news(
            config=cfg, run_id=run_id, settings=settings
        )
        filings = publish_intelligence_filings(config=cfg, run_id=run_id)
        insider = publish_intelligence_insider(config=cfg, run_id=run_id)
        log_job_phase(
            "publish-signals",
            "intelligence",
            status="complete",
            news_rows=news.row_count,
            filings_rows=filings.row_count,
            insider_rows=insider.row_count,
        )
        routes.extend([news, filings, insider])
        routes.append(
            publish_intelligence_summary(
                news=news,
                filings=filings,
                insider=insider,
                config=cfg,
                run_id=run_id,
            )
        )

        scanner = publish_options_scanner(
            config=cfg, run_id=run_id, settings=settings
        )
        routes.append(scanner)
        for key in (
            "options_conviction",
            "options_explore",
            "options_monitor",
            "options_covered_calls",
        ):
            routes.append(
                publish_placeholder_route(
                    route_key=key,
                    config=cfg,
                    run_id=run_id,
                    message=f"{key} not yet exported to signals/options/",
                )
            )
        routes.append(
            publish_options_summary(scanner=scanner, config=cfg, run_id=run_id)
        )

        if alpha.status == "stale":
            warnings.append(f"stocks_alpha stale: {alpha.warnings}")

        if cfg.strict and alpha.status == "unavailable":
            raise PublishError("Required route stocks_alpha is unavailable")

        manifest_rel = _write_master_manifest(
            run_id=run_id,
            routes=routes,
            ctx=ctx,
            warnings=warnings,
            errors=errors,
        )
        job_manifest.output_paths.append(manifest_rel)
        job_manifest.published_paths = [r.rel_path for r in routes]
        job_manifest.warnings = warnings
        job_manifest.finish(status="success")
        run_manifest_rel = job_manifest.write(ctx=ctx)

        logger.info(
            "publish_signals_complete",
            run_id=run_id,
            routes=len(routes),
            warnings=len(warnings),
        )
        return PublishResult(
            run_id=run_id,
            generated_at=generated_at,
            routes=routes,
            manifest_rel=manifest_rel,
            run_manifest_rel=run_manifest_rel,
            warnings=warnings,
            errors=errors,
        )
    except Exception as exc:
        job_manifest.errors.append(str(exc))
        job_manifest.finish(status="failed")
        try:
            run_manifest_rel = job_manifest.write(ctx=ctx)
        except Exception:
            run_manifest_rel = ""
        logger.error("publish_signals_failed", run_id=run_id, error=str(exc))
        if isinstance(exc, PublishError):
            raise
        raise PublishError(str(exc)) from exc
