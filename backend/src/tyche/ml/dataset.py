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
    add_correlation_features,
    add_etf_features,
    add_neighbor_features,
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

    elapsed = time.time() - t0
    logger.info(
        "dataset_built",
        rows=len(dataset),
        tickers=dataset["ticker"].nunique(),
        skipped=skipped,
        elapsed_s=round(elapsed, 1),
    )

    return dataset


def _filter_equity(
    tickers: list[str],
    market_caps: dict[str, float],
    min_market_cap: float,
) -> list[str]:
    """Filter tickers by market cap, passing those with no data."""
    result = []
    for t in tickers:
        cap = market_caps.get(t)
        if cap is not None and cap < min_market_cap:
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
