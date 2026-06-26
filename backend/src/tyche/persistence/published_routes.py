"""Read compact route artifacts from ``published/`` and ``signals/`` (GCP-D).

Normal UI routes should prefer precomputed JSON under ``published/routes/``,
then fall back to signal Parquet stores. Curated/raw scans are gated behind
``api_allow_curated_fallback`` (default false).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

import structlog

from tyche.config import TycheSettings, get_settings
from tyche.market_data.alpha_store import AlphaSignalStore
from tyche.market_data.data_store import TickerMetaStore
from tyche.persistence.published_route_registry import (
    ALPHA_PEAK_SOURCE_CANDIDATES,
    ALPHA_SUSTAINED_SOURCE_CANDIDATES,
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
from tyche.storage import exists as storage_exists, read_json
from tyche.storage.json_io import sanitize_json_records
from tyche.storage.paths import StorageContext, join_uri, storage_context_from_settings
logger = structlog.get_logger()

RouteLayer = Literal["published", "signals"]
_VALID_SIGNALS = frozenset({"strong_buy", "buy", "watch", "avoid"})
_VALID_VARIANTS = frozenset({"peak", "sustained"})


@dataclass(frozen=True)
class PublishedRouteEnvelope:
    """Parsed ``published/routes/*.json`` artifact."""

    route_key: str
    route: str
    rel_path: str
    as_of: str | None
    generated_at: str
    run_id: str
    row_count: int
    source_paths: list[str]
    status: str
    data: Any
    layer: RouteLayer = "published"

    @property
    def is_available(self) -> bool:
        return self.status in ("ok", "stale")

    def age_minutes(self) -> float | None:
        try:
            parsed = datetime.fromisoformat(
                self.generated_at.replace("Z", "+00:00"),
            )
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
            return delta.total_seconds() / 60.0
        except ValueError:
            return None

    def is_fresh(self, max_age_minutes: int) -> bool:
        age = self.age_minutes()
        if age is None:
            return True
        return age <= max_age_minutes


def route_rel_path(route_key: str) -> str:
    return join_uri("published", "routes", ROUTE_FILES[route_key])


def load_published_route(
    route_key: str,
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
) -> PublishedRouteEnvelope | None:
    """Load a route artifact when present; return ``None`` if missing."""
    if route_key not in ROUTE_FILES:
        raise ValueError(f"Unknown route key: {route_key}")

    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    rel = route_rel_path(route_key)
    if not storage_exists(rel, ctx=ctx):
        return None

    raw = read_json(rel, ctx=ctx)
    return PublishedRouteEnvelope(
        route_key=route_key,
        route=str(raw.get("route") or ROUTE_PATHS[route_key]),
        rel_path=rel,
        as_of=raw.get("as_of"),
        generated_at=str(raw.get("generated_at") or ""),
        run_id=str(raw.get("run_id") or ""),
        row_count=int(raw.get("row_count") or 0),
        source_paths=list(raw.get("source_paths") or []),
        status=str(raw.get("status") or "unavailable"),
        data=raw.get("data"),
    )


def _prefer_published(settings: TycheSettings) -> bool:
    return bool(settings.api_prefer_published_signals)


def _usable_published(
    envelope: PublishedRouteEnvelope | None,
    *,
    settings: TycheSettings,
) -> PublishedRouteEnvelope | None:
    if envelope is None or not envelope.is_available:
        return None
    if not envelope.is_fresh(settings.published_max_age_minutes):
        # Serve stale published JSON — better than falling back to a full
        # signal-store scan (especially in GCS mode without local OHLCV).
        logger.info(
            "published_route_stale_serving",
            route=envelope.route,
            age_minutes=envelope.age_minutes(),
            max_age=settings.published_max_age_minutes,
        )
    return envelope


def _record_to_response(
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


def get_stock_alpha_scan(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    variant: str = "sustained",
    prefer_published: bool = True,
) -> tuple[AlphaScanResponse, RouteLayer] | None:
    """Return alpha scan from published JSON or signal Parquet."""
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)

    if prefer_published and _prefer_published(settings):
        env = _usable_published(
            load_published_route("stocks_alpha", settings=settings, ctx=ctx),
            settings=settings,
        )
        if env is not None and isinstance(env.data, dict):
            try:
                scan = AlphaScanResponse.model_validate(env.data)
                logger.debug("alpha_scan_source", layer="published", route=env.rel_path)
                return scan, "published"
            except Exception as exc:
                logger.warning("published_alpha_parse_failed", error=str(exc))

    scan = _load_alpha_from_signals(
        settings=settings,
        ctx=ctx,
        variant=variant,
    )
    if scan is not None:
        return scan, "signals"
    return None


def _load_alpha_from_signals(
    *,
    settings: TycheSettings,
    ctx: StorageContext,
    variant: str,
) -> AlphaScanResponse | None:
    requested = variant if variant in _VALID_VARIANTS else "peak"
    if requested == "sustained":
        source_rel = first_existing_path(ALPHA_SUSTAINED_SOURCE_CANDIDATES, ctx=ctx)
        store_variant = "sustained"
        if not source_rel:
            source_rel = first_existing_path(ALPHA_PEAK_SOURCE_CANDIDATES, ctx=ctx)
            store_variant = "peak"
    else:
        source_rel = first_existing_path(ALPHA_PEAK_SOURCE_CANDIDATES, ctx=ctx)
        store_variant = "peak"

    if not source_rel:
        return None

    store = AlphaSignalStore(
        data_dir=settings.data_dir,
        variant=store_variant,
        ctx=ctx,
        rel_path=source_rel,
    )
    records, as_of, computed_at = store.read_latest()
    if not records:
        return None

    meta_store = TickerMetaStore(data_dir=settings.data_dir, ctx=ctx)
    floor = settings.alpha_min_market_cap_millions * 1_000_000
    watchlist = frozenset(s.upper() for s in (settings.watchlist_symbols or []))

    if meta_store.exists:
        all_tickers = [r["ticker"] for r in records]
        eligible = set(meta_store.filter_equity_only(all_tickers))
        caps = meta_store.get_market_caps(all_tickers)
        records = [
            r
            for r in records
            if r["ticker"] in eligible and (caps.get(r["ticker"]) or 0) >= floor
        ]

    records.sort(key=lambda r: r.get("alpha_score") or 0, reverse=True)
    strong_buy = sum(1 for r in records if r.get("signal") == "strong_buy")
    buy = sum(1 for r in records if r.get("signal") == "buy")
    ml_available = any(r.get("breakout_prob_swing") is not None for r in records)

    tickers = [r["ticker"] for r in records]
    market_caps = meta_store.get_market_caps(tickers) if meta_store.exists else {}
    sectors = meta_store.get_sectors(tickers) if meta_store.exists else {}
    inst_pcts = meta_store.get_institutional_pcts(tickers) if meta_store.exists else {}

    signals = [
        _record_to_response(
            r,
            market_cap=market_caps.get(r["ticker"]),
            institutional_pct=inst_pcts.get(r["ticker"]),
            sector=sectors.get(r["ticker"]),
            is_watchlist=r["ticker"] in watchlist,
        )
        for r in records
    ]
    return AlphaScanResponse(
        scanned_at=datetime.now(timezone.utc).isoformat(),
        as_of_date=as_of,
        computed_at=computed_at,
        ml_available=ml_available,
        variant=store.variant,
        total=len(records),
        strong_buy_count=strong_buy,
        buy_count=buy,
        signals=signals,
    )


def apply_alpha_scan_filters(
    scan: AlphaScanResponse,
    *,
    signal: str | None = None,
    horizon: str | None = None,
    min_score: float = 0.0,
    min_market_cap_millions: float | None = None,
    limit: int = 200,
) -> AlphaScanResponse:
    """Apply read-time query filters to an alpha scan payload."""
    records = list(scan.signals)
    if min_market_cap_millions is not None and min_market_cap_millions > 0:
        floor = min_market_cap_millions * 1_000_000
        records = [
            r
            for r in records
            if r.market_cap is not None and r.market_cap >= floor
        ]
    if signal and signal in _VALID_SIGNALS:
        records = [r for r in records if r.signal == signal]
    if horizon:
        records = [r for r in records if r.horizon == horizon]
    if min_score > 0:
        records = [r for r in records if r.alpha_score >= min_score]

    records.sort(key=lambda r: r.alpha_score, reverse=True)
    strong_buy = sum(1 for r in records if r.signal == "strong_buy")
    buy = sum(1 for r in records if r.signal == "buy")
    display = records[:limit]

    return AlphaScanResponse(
        scanned_at=datetime.now(timezone.utc).isoformat(),
        as_of_date=scan.as_of_date,
        computed_at=scan.computed_at,
        ml_available=scan.ml_available,
        variant=scan.variant,
        total=len(records),
        strong_buy_count=strong_buy,
        buy_count=buy,
        signals=display,
    )


def alpha_needs_signals_fallback(
    *,
    limit: int,
    published_row_count: int | None,
) -> bool:
    """Return True when the request needs more rows than the published snapshot."""
    return published_row_count is not None and limit > published_row_count


def get_stocks_conviction_rows(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
) -> tuple[list[ConvictionSnapshotResponse], RouteLayer] | None:
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)

    if _prefer_published(settings):
        env = _usable_published(
            load_published_route("stocks_conviction", settings=settings, ctx=ctx),
            settings=settings,
        )
        if env is not None and isinstance(env.data, dict):
            rows = env.data.get("snapshots") or []
            if rows:
                parsed = [
                    ConvictionSnapshotResponse.model_validate(r) for r in rows
                ]
                logger.debug(
                    "conviction_source",
                    layer="published",
                    rows=len(parsed),
                )
                return parsed, "published"

    from tyche.market_data.stocks_conviction_store import (
        STOCKS_CONVICTION_REL,
        load_stocks_conviction_parquet,
    )

    signal_rel = first_existing_path((STOCKS_CONVICTION_REL,), ctx=ctx)
    if signal_rel:
        rows, _as_of = load_stocks_conviction_parquet(ctx=ctx, rel_path=signal_rel)
        if rows:
            logger.debug(
                "conviction_source",
                layer="signals",
                rows=len(rows),
                rel=signal_rel,
            )
            return rows, "signals"

    return None


def get_stocks_deep_dips_scan(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
):
    """Return deep dip scan from published JSON or signal Parquet."""
    from tyche.market_data.stocks_deep_dips_store import load_deep_dips_scan
    from tyche.schemas.alerts import DeepDipScanResponse

    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)

    if _prefer_published(settings):
        env = _usable_published(
            load_published_route("stocks_deep_dips", settings=settings, ctx=ctx),
            settings=settings,
        )
        if env is not None and isinstance(env.data, dict):
            try:
                scan = DeepDipScanResponse.model_validate(env.data)
                return scan, "published"
            except Exception as exc:
                logger.warning("published_deep_dips_parse_failed", error=str(exc))

    signal_rel = first_existing_path(("signals/stocks/deep_dips.parquet",), ctx=ctx)
    if signal_rel:
        scan = load_deep_dips_scan(ctx=ctx, rel_path=signal_rel)
        if scan is not None:
            return scan, "signals"
    return None


def get_stocks_history_payload(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
) -> tuple[dict[str, Any], RouteLayer] | None:
    """Return history summaries + transitions from published JSON or Parquet."""
    from tyche.market_data.stocks_history_store import (
        load_history_summary_rows,
        load_transition_responses,
    )

    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)

    if _prefer_published(settings):
        env = _usable_published(
            load_published_route("stocks_history", settings=settings, ctx=ctx),
            settings=settings,
        )
        if env is not None and isinstance(env.data, dict):
            if env.data.get("summaries") or env.data.get("transitions"):
                return env.data, "published"

    summaries = load_history_summary_rows(ctx=ctx)
    transitions = load_transition_responses(ctx=ctx)
    if summaries or transitions:
        return {
            "summaries": summaries,
            "transitions": [t.model_dump(mode="json") for t in transitions],
            "total_summaries": len(summaries),
            "total_transitions": len(transitions),
        }, "signals"
    return None


def get_intelligence_news_rows(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
) -> tuple[list[NewsSignalResponse], RouteLayer] | None:
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)

    if _prefer_published(settings):
        env = _usable_published(
            load_published_route("intelligence_news", settings=settings, ctx=ctx),
            settings=settings,
        )
        if env is not None and isinstance(env.data, dict):
            rows = env.data.get("signals") or []
            if rows:
                parsed = [
                    NewsSignalResponse.model_validate(r)
                    for r in sanitize_json_records(rows)
                ]
                return parsed, "published"
    return None


def get_intelligence_filing_rows(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
) -> tuple[list[dict[str, Any]], RouteLayer] | None:
    """Return raw filing signal dicts (route builds ``FilingSignalResponse``)."""
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)

    if _prefer_published(settings):
        env = _usable_published(
            load_published_route("intelligence_filings", settings=settings, ctx=ctx),
            settings=settings,
        )
        if env is not None and isinstance(env.data, dict):
            rows = env.data.get("signals") or []
            if rows:
                return sanitize_json_records(rows), "published"
    return None


def get_options_scanner_payload(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
) -> tuple[dict[str, Any], RouteLayer] | None:
    """Return scanner page payload from published JSON or signal Parquet."""
    from tyche.market_data.options_scanner_store import (
        OPTIONS_SCANNER_REL,
        build_scan_payload,
        load_scanner_parquet,
        load_scanner_report,
    )

    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)

    if _prefer_published(settings):
        env = _usable_published(
            load_published_route("options_scanner", settings=settings, ctx=ctx),
            settings=settings,
        )
        if env is not None and isinstance(env.data, dict):
            if env.status == "ok" and (
                env.data.get("csp_candidates") or env.data.get("pipeline_stages")
            ):
                logger.debug(
                    "options_scanner_source",
                    layer="published",
                    route=env.rel_path,
                )
                return env.data, "published"

    report = load_scanner_report(ctx=ctx)
    candidate_rows, _as_of = load_scanner_parquet(ctx=ctx)
    if candidate_rows or report:
        payload = build_scan_payload(report=report, candidate_rows=candidate_rows)
        logger.debug(
            "options_scanner_source",
            layer="signals",
            rel=OPTIONS_SCANNER_REL,
        )
        return payload, "signals"
    return None


def get_options_conviction_scan(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    limit_per_path: int = 100,
    watchlist_set: frozenset[str] | None = None,
    specific_tickers: frozenset[str] | None = None,
):
    """Return options conviction scan from published JSON or stocks conviction Parquet."""
    from tyche.persistence.conviction_scan_builder import build_conviction_scan_response
    from tyche.schemas.conviction import ConvictionScanResponse

    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    watchlist = watchlist_set or frozenset()

    if _prefer_published(settings):
        env = _usable_published(
            load_published_route("options_conviction", settings=settings, ctx=ctx),
            settings=settings,
        )
        if env is not None and isinstance(env.data, dict):
            if env.status == "ok" and env.data.get("signals") is not None:
                try:
                    scan = ConvictionScanResponse.model_validate(env.data)
                    if specific_tickers:
                        filtered = [
                            s for s in scan.signals if s.ticker in specific_tickers
                        ]
                        scan = scan.model_copy(update={"signals": filtered})
                    logger.debug(
                        "options_conviction_source",
                        layer="published",
                        route=env.rel_path,
                    )
                    return scan, "published"
                except Exception as exc:
                    logger.warning(
                        "published_options_conviction_parse_failed",
                        error=str(exc),
                    )

    loaded = get_stocks_conviction_rows(settings=settings, ctx=ctx)
    if loaded is None:
        return None
    rows, layer = loaded
    if not rows:
        return None
    scan = build_conviction_scan_response(
        rows,
        limit_per_path=limit_per_path,
        watchlist_set=watchlist,
        specific_tickers=specific_tickers,
    )
    logger.debug("options_conviction_source", layer=layer, rows=len(rows))
    return scan, layer
