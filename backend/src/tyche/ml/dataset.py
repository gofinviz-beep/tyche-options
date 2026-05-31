"""Dataset assembly: combines features + labels into ML-ready DataFrames.

Orchestrates loading from OHLCVStore, DerivedMetricsStore, and TickerMetaStore,
then calls feature extraction and label construction for each ticker.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import structlog

from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.market_data.derived_store import DerivedMetricsStore
from tyche.ml.features import (
    FEATURE_COLS,
    add_catalyst_features,
    add_correlation_features,
    add_estimate_features,
    add_etf_features,
    add_fundamental_features,
    add_graph_features,
    add_market_context_features,
    add_neighbor_features,
    add_relative_strength_features,
    add_short_interest_features,
    build_sector_map,
    extract_ticker_features,
)
from tyche.ml.labels import compute_labels_vectorized

logger = structlog.get_logger()

MIN_BARS = 60
MIN_MARKET_CAP = 4e9


def build_dataset(
    data_dir: str = "data",
    start_date: date | None = None,
    end_date: date | None = None,
    min_market_cap: float = MIN_MARKET_CAP,
    include_neighbors: bool = True,
    include_etf: bool = True,
    include_correlation: bool = True,
    include_market_context: bool = True,
    include_momentum: bool = True,
    include_demand: bool = True,
    max_tickers: int | None = None,
) -> pd.DataFrame:
    """Build the full tabular dataset from on-disk stores.

    Args:
        data_dir: Root data directory containing OHLCV, derived, ticker_meta.
        start_date: Earliest date to include in the dataset.
        end_date: Latest date to include.
        min_market_cap: Minimum market cap filter.
        include_neighbors: Whether to compute sector-aggregated features.
        max_tickers: Limit number of tickers (for debugging).

    Returns:
        DataFrame with feature columns, label columns, plus ``ticker`` and
        ``date`` identifiers.  Rows with NaN in key label columns are
        retained (they're dropped at training time per target).
    """
    t0 = time.time()

    ohlcv_store = OHLCVStore(data_dir=data_dir)
    derived_store = DerivedMetricsStore(data_dir=data_dir)
    meta_store = TickerMetaStore(data_dir=data_dir)

    all_tickers = ohlcv_store.get_all_tickers()
    logger.info("dataset_tickers_found", count=len(all_tickers))

    market_caps = meta_store.get_market_caps()
    sectors = meta_store.get_sectors()
    inst_pcts = meta_store.get_institutional_pcts()
    sector_map = build_sector_map(sectors)

    equity_tickers = _filter_equity(
        all_tickers, market_caps, min_market_cap,
    )
    if max_tickers:
        equity_tickers = equity_tickers[:max_tickers]

    logger.info(
        "dataset_universe",
        total=len(all_tickers),
        after_filter=len(equity_tickers),
    )

    frames: list[pd.DataFrame] = []
    skipped = 0

    for i, ticker in enumerate(equity_tickers):
        if (i + 1) % 100 == 0:
            logger.info("dataset_progress", processed=i + 1, total=len(equity_tickers))

        try:
            ohlcv = ohlcv_store.read_ticker(ticker, start_date=start_date, end_date=end_date)
            if len(ohlcv) < MIN_BARS:
                skipped += 1
                continue

            derived = derived_store.read_ticker(ticker, start_date=start_date, end_date=end_date)

            features = extract_ticker_features(
                ohlcv=ohlcv,
                derived=derived,
                market_cap=market_caps.get(ticker),
                institutional_pct=inst_pcts.get(ticker),
                sector=sectors.get(ticker),
                sector_map=sector_map,
                min_bars=MIN_BARS,
            )
            if features.empty:
                skipped += 1
                continue

            support_ema = features["ema_21"]
            labels = compute_labels_vectorized(
                ohlcv.iloc[MIN_BARS:].reset_index(drop=True),
                support_ema=support_ema,
            )

            combined = pd.concat([features.reset_index(drop=True), labels], axis=1)
            combined["ticker"] = ticker

            frames.append(combined)

        except Exception:
            logger.warning("dataset_ticker_failed", ticker=ticker, exc_info=True)
            skipped += 1

    if not frames:
        logger.error("dataset_empty", skipped=skipped)
        return pd.DataFrame()

    dataset = pd.concat(frames, ignore_index=True)

    dataset = apply_relational_features(
        dataset,
        ohlcv_store=ohlcv_store,
        data_dir=data_dir,
        start_date=start_date,
        end_date=end_date,
        include_neighbors=include_neighbors,
        include_etf=include_etf,
        include_correlation=include_correlation,
        include_market_context=include_market_context,
        include_momentum=include_momentum,
        include_demand=include_demand,
    )

    elapsed = time.time() - t0
    logger.info(
        "dataset_built",
        rows=len(dataset),
        tickers=dataset["ticker"].nunique(),
        skipped=skipped,
        elapsed_s=round(elapsed, 1),
    )

    return dataset


def apply_relational_features(
    dataset: pd.DataFrame,
    *,
    ohlcv_store: OHLCVStore,
    data_dir: str,
    start_date: date | None = None,
    end_date: date | None = None,
    include_neighbors: bool = True,
    include_etf: bool = True,
    include_correlation: bool = True,
    include_market_context: bool = True,
    include_momentum: bool = True,
    include_demand: bool = True,
) -> pd.DataFrame:
    """Apply cross-sectional / relational feature augmentations in place.

    Shared by ``build_dataset`` (training) and the live alpha batch
    (inference) so feature definitions stay identical across train/serve.
    """
    if dataset.empty:
        return dataset

    if include_neighbors and "sector_encoded" in dataset.columns:
        dataset = add_neighbor_features(dataset)

    if include_etf:
        try:
            from tyche.market_data.etf_store import ETFConstituentStore

            etf_store = ETFConstituentStore(data_dir=data_dir)
            if etf_store.exists:
                dataset = add_etf_features(dataset, etf_store=etf_store)
                logger.info("etf_features_added")
            else:
                logger.info("etf_features_skipped", reason="no etf data file")
                dataset = add_etf_features(dataset, etf_store=None)
        except Exception:
            logger.warning("etf_features_failed", exc_info=True)
            dataset = add_etf_features(dataset, etf_store=None)

    if include_correlation:
        try:
            from tyche.market_data.correlation_store import CorrelationStore

            corr_store = CorrelationStore(data_dir=data_dir)
            if corr_store.exists:
                dataset = add_correlation_features(dataset, correlation_store=corr_store)
                logger.info("correlation_features_added")
            else:
                logger.info("correlation_features_skipped", reason="no correlation data file")
                dataset = add_correlation_features(dataset, correlation_store=None)
        except Exception:
            logger.warning("correlation_features_failed", exc_info=True)
            dataset = add_correlation_features(dataset, correlation_store=None)

    if include_market_context:
        try:
            spy_ohlcv = ohlcv_store.read_ticker(
                "SPY", start_date=start_date, end_date=end_date,
            )
            dataset = add_market_context_features(dataset, spy_ohlcv=spy_ohlcv)
            logger.info("market_context_features_added")
        except Exception:
            logger.warning("market_context_features_failed", exc_info=True)
            dataset = add_market_context_features(dataset, spy_ohlcv=None)

    if include_momentum:
        try:
            spy_ohlcv = ohlcv_store.read_ticker(
                "SPY", start_date=start_date, end_date=end_date,
            )
            dataset = add_relative_strength_features(dataset, spy_ohlcv=spy_ohlcv)
            logger.info("relative_strength_features_added")
        except Exception:
            logger.warning("relative_strength_features_failed", exc_info=True)
            dataset = add_relative_strength_features(dataset, spy_ohlcv=None)

    if include_demand:
        dataset = _apply_demand_features(dataset, data_dir=data_dir)

    return dataset


def _apply_demand_features(dataset: pd.DataFrame, *, data_dir: str) -> pd.DataFrame:
    """Apply fundamentals / estimates / short-interest augmentations (D-FUND,
    D-EST, D-TECH). Each degrades to NaN defaults when its store is absent."""
    try:
        from tyche.market_data.fundamentals_store import FundamentalsStore

        store = FundamentalsStore(data_dir=data_dir)
        dataset = add_fundamental_features(dataset, fundamentals_store=store)
        logger.info("fundamental_features_added")
    except Exception:
        logger.warning("fundamental_features_failed", exc_info=True)
        dataset = add_fundamental_features(dataset, fundamentals_store=None)

    try:
        from tyche.market_data.estimates_store import EstimatesStore

        store = EstimatesStore(data_dir=data_dir)
        dataset = add_estimate_features(dataset, estimates_store=store)
        logger.info("estimate_features_added")
    except Exception:
        logger.warning("estimate_features_failed", exc_info=True)
        dataset = add_estimate_features(dataset, estimates_store=None)

    try:
        from tyche.market_data.short_interest_store import ShortInterestStore

        store = ShortInterestStore(data_dir=data_dir)
        dataset = add_short_interest_features(dataset, short_interest_store=store)
        logger.info("short_interest_features_added")
    except Exception:
        logger.warning("short_interest_features_failed", exc_info=True)
        dataset = add_short_interest_features(dataset, short_interest_store=None)

    try:
        from tyche.market_data.catalyst_store import CatalystSignalStore
        from tyche.market_data.policy_calendar import PolicyEventCalendar

        cat_store = CatalystSignalStore(data_dir=data_dir)
        meta = TickerMetaStore(data_dir=data_dir)
        dataset = add_catalyst_features(
            dataset,
            catalyst_store=cat_store if cat_store.get_all_tickers() else None,
            policy_calendar=PolicyEventCalendar(),
            sectors=meta.get_sectors(),
        )
        logger.info("catalyst_features_added")
    except Exception:
        logger.warning("catalyst_features_failed", exc_info=True)
        dataset = add_catalyst_features(dataset, catalyst_store=None, policy_calendar=None)

    try:
        from tyche.market_data.supply_chain_graph import SupplyChainGraph

        dataset = add_graph_features(dataset, graph=SupplyChainGraph())
        logger.info("graph_features_added")
    except Exception:
        logger.warning("graph_features_failed", exc_info=True)
        dataset = add_graph_features(dataset, graph=None)

    return dataset


def build_latest_features(
    data_dir: str = "data",
    min_market_cap: float = MIN_MARKET_CAP,
    tickers: list[str] | None = None,
    max_tickers: int | None = None,
) -> pd.DataFrame:
    """Build the latest-date feature row per ticker for live alpha inference.

    Mirrors ``build_dataset`` feature extraction (relational + demand
    augmentations) but keeps only the most recent row per ticker and skips
    label construction. Used by the nightly alpha batch and on-demand scans.
    """
    ohlcv_store = OHLCVStore(data_dir=data_dir)
    derived_store = DerivedMetricsStore(data_dir=data_dir)
    meta_store = TickerMetaStore(data_dir=data_dir)

    market_caps = meta_store.get_market_caps()
    sectors = meta_store.get_sectors()
    inst_pcts = meta_store.get_institutional_pcts()
    sector_map = build_sector_map(sectors)

    if tickers is None:
        all_tickers = ohlcv_store.get_all_tickers()
        candidates = _filter_equity(
            all_tickers,
            market_caps,
            min_market_cap,
            meta_store=meta_store,
            equity_only=True,
            require_cap=True,
        )
    else:
        candidates = [t.upper() for t in tickers]
    if max_tickers:
        candidates = candidates[:max_tickers]

    frames: list[pd.DataFrame] = []
    total = len(candidates)
    for i, ticker in enumerate(candidates, start=1):
        try:
            ohlcv = ohlcv_store.read_ticker(ticker)
            if len(ohlcv) < MIN_BARS:
                continue
            derived = derived_store.read_ticker(ticker)
            features = extract_ticker_features(
                ohlcv=ohlcv,
                derived=derived,
                market_cap=market_caps.get(ticker),
                institutional_pct=inst_pcts.get(ticker),
                sector=sectors.get(ticker),
                sector_map=sector_map,
                min_bars=MIN_BARS,
            )
            if features.empty:
                continue
            last = features.iloc[[-1]].copy()
            last["ticker"] = ticker
            frames.append(last)
        except Exception:
            logger.warning("latest_features_ticker_failed", ticker=ticker, exc_info=True)
        if i % 500 == 0:
            logger.info("latest_features_progress", done=i, total=total, rows=len(frames))

    if not frames:
        return pd.DataFrame()

    latest = pd.concat(frames, ignore_index=True)
    latest = apply_relational_features(
        latest,
        ohlcv_store=ohlcv_store,
        data_dir=data_dir,
    )
    latest = _apply_demand_features(latest, data_dir=data_dir)
    logger.info("latest_features_complete", tickers=len(latest))
    return latest


def _filter_equity(
    tickers: list[str],
    market_caps: dict[str, float],
    min_market_cap: float,
    *,
    meta_store: TickerMetaStore | None = None,
    equity_only: bool = False,
    require_cap: bool = False,
) -> list[str]:
    """Filter tickers by market cap and (optionally) security type.

    Args:
        min_market_cap: Minimum market cap floor (USD).
        meta_store: Required when ``equity_only`` is set, to resolve types.
        equity_only: Keep only common stock (type 'CS') — drops warrants,
            units, ADRs, ETFs and anything missing from the meta store.
        require_cap: Exclude tickers with no market-cap data (so unknown-cap
            names cannot slip past the floor). When False, missing-cap tickers
            pass (legacy training behavior).
    """
    eligible: set[str] | None = None
    if equity_only and meta_store is not None:
        eligible = set(meta_store.filter_equity_only(tickers))

    result = []
    for t in tickers:
        if eligible is not None and t not in eligible:
            continue
        cap = market_caps.get(t)
        if cap is None:
            if require_cap:
                continue
        elif cap < min_market_cap:
            continue
        result.append(t)
    return result


def save_dataset(df: pd.DataFrame, path: str | Path) -> Path:
    """Persist dataset to Parquet for reproducible experiments."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False, compression="snappy")
    logger.info("dataset_saved", path=str(p), rows=len(df))
    return p


def load_dataset(path: str | Path) -> pd.DataFrame:
    """Load a previously saved dataset."""
    return pd.read_parquet(path)
