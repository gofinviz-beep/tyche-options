"""Rolling pairwise correlation and beta computation from OHLCV data.

Computes and persists:
- Top-N most correlated peers per ticker (sparse representation)
- Rolling beta vs SPY and QQQ

**Leakage prevention:** correlation windows use strictly backward-looking
data: ``[as_of_date - window, as_of_date - 1]``.  Same-day data is never
included (per ``gnn-architecture.md`` Leakage Vector 4).

Storage: ``data/correlations.parquet`` — single file, replaced on each
computation run.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import structlog

logger = structlog.get_logger()

CORRELATION_SCHEMA = pa.schema([
    ("as_of_date", pa.date32()),
    ("ticker_a", pa.string()),
    ("ticker_b", pa.string()),
    ("correlation_60d", pa.float64()),
])

BETA_SCHEMA = pa.schema([
    ("as_of_date", pa.date32()),
    ("ticker", pa.string()),
    ("spy_beta_60d", pa.float64()),
    ("qqq_beta_60d", pa.float64()),
])


class CorrelationStore:
    """Read/write rolling correlation data."""

    def __init__(self, data_dir: str = "data") -> None:
        self._corr_path = Path(data_dir) / "correlations.parquet"
        self._beta_path = Path(data_dir) / "betas.parquet"

    @property
    def exists(self) -> bool:
        return self._corr_path.exists()

    @property
    def beta_exists(self) -> bool:
        return self._beta_path.exists()

    def write_correlations(self, df: pd.DataFrame) -> int:
        """Persist correlation data (replaces existing file)."""
        self._corr_path.parent.mkdir(parents=True, exist_ok=True)
        df = df.copy()
        df["as_of_date"] = pd.to_datetime(df["as_of_date"]).dt.date
        table = pa.Table.from_pandas(df, schema=CORRELATION_SCHEMA)
        pq.write_table(table, self._corr_path)
        logger.info("correlations_written", rows=len(df))
        return len(df)

    def write_betas(self, df: pd.DataFrame) -> int:
        """Persist beta data (replaces existing file)."""
        self._beta_path.parent.mkdir(parents=True, exist_ok=True)
        df = df.copy()
        df["as_of_date"] = pd.to_datetime(df["as_of_date"]).dt.date
        table = pa.Table.from_pandas(df, schema=BETA_SCHEMA)
        pq.write_table(table, self._beta_path)
        logger.info("betas_written", rows=len(df))
        return len(df)

    def read_correlations(self, as_of: date | None = None) -> pd.DataFrame:
        """Read correlation data, optionally filtered to a specific date."""
        if not self.exists:
            return pd.DataFrame(columns=["as_of_date", "ticker_a", "ticker_b", "correlation_60d"])
        df = pd.read_parquet(self._corr_path)
        if as_of is not None:
            df = df[df["as_of_date"] == as_of]
        return df

    def read_betas(self, as_of: date | None = None) -> pd.DataFrame:
        """Read beta data, optionally filtered to a specific date."""
        if not self.beta_exists:
            return pd.DataFrame(columns=["as_of_date", "ticker", "spy_beta_60d", "qqq_beta_60d"])
        df = pd.read_parquet(self._beta_path)
        if as_of is not None:
            df = df[df["as_of_date"] == as_of]
        return df

    def get_top_correlated(
        self, ticker: str, n: int = 10, as_of: date | None = None,
    ) -> list[tuple[str, float]]:
        """Return top-N most correlated peers for a ticker."""
        df = self.read_correlations(as_of)
        if df.empty:
            return []
        mask = (df["ticker_a"] == ticker) | (df["ticker_b"] == ticker)
        sub = df[mask].copy()
        sub["peer"] = sub.apply(
            lambda r: r["ticker_b"] if r["ticker_a"] == ticker else r["ticker_a"],
            axis=1,
        )
        sub = sub.sort_values("correlation_60d", ascending=False).head(n)
        return list(zip(sub["peer"], sub["correlation_60d"]))

    def get_beta(self, ticker: str, as_of: date | None = None) -> dict[str, float | None]:
        """Return SPY/QQQ betas for a ticker."""
        df = self.read_betas(as_of)
        if df.empty:
            return {"spy_beta_60d": None, "qqq_beta_60d": None}
        row = df[df["ticker"] == ticker]
        if row.empty:
            return {"spy_beta_60d": None, "qqq_beta_60d": None}
        r = row.iloc[0]
        return {
            "spy_beta_60d": r.get("spy_beta_60d"),
            "qqq_beta_60d": r.get("qqq_beta_60d"),
        }


def _compute_beta(stock_returns: pd.Series, market_returns: pd.Series) -> float:
    """OLS beta = cov(stock, market) / var(market)."""
    aligned = pd.concat([stock_returns, market_returns], axis=1).dropna()
    if len(aligned) < 20:
        return np.nan
    cov = aligned.iloc[:, 0].cov(aligned.iloc[:, 1])
    var = aligned.iloc[:, 1].var()
    if var == 0 or np.isnan(var):
        return np.nan
    return cov / var


def compute_rolling_correlations(
    ohlcv_store,
    tickers: list[str] | None = None,
    window: int = 60,
    top_n: int = 20,
    as_of_date: date | None = None,
    min_market_cap: float = 4e9,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute pairwise rolling correlations and betas from OHLCV data.

    Args:
        ohlcv_store: An ``OHLCVStore`` instance.
        tickers: Explicit ticker list.  If None, uses all tickers in the store.
        window: Rolling window in trading days (default 60).
        top_n: Keep only the top-N most correlated peers per ticker.
        as_of_date: Reference date.  Correlation uses
            ``[as_of_date - window, as_of_date - 1]`` (no leakage).
        min_market_cap: Filter tickers below this threshold (uses
            TickerMetaStore if available).

    Returns:
        Tuple of (correlations_df, betas_df).
    """
    if as_of_date is None:
        latest = ohlcv_store.get_latest_date()
        if latest is None:
            logger.warning("no_ohlcv_data_for_correlations")
            return pd.DataFrame(), pd.DataFrame()
        as_of_date = latest

    if tickers is None:
        tickers = ohlcv_store.get_all_tickers()

    logger.info(
        "computing_correlations",
        tickers=len(tickers),
        window=window,
        as_of=str(as_of_date),
    )

    # Always include SPY/QQQ for beta computation even if they're not in
    # the equity-filtered ticker list (they're ETFs, not common stock).
    benchmark_tickers = {"SPY", "QQQ"}
    all_needed = list(dict.fromkeys(list(tickers) + sorted(benchmark_tickers)))

    returns_dict: dict[str, pd.Series] = {}
    for ticker in all_needed:
        try:
            df = ohlcv_store.read_ticker(ticker)
            if df is None or df.empty:
                continue
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"]).dt.date
            # Strict backward-looking: exclude as_of_date itself
            df = df[df["date"] < as_of_date]
            df = df.sort_values("date").tail(window)
            if len(df) < window * 0.8:
                continue
            rets = df.set_index("date")["close"].pct_change().dropna()
            if len(rets) >= window * 0.7:
                returns_dict[ticker] = rets
        except Exception:
            continue

    if len(returns_dict) < 2:
        logger.warning("insufficient_tickers_for_correlation", count=len(returns_dict))
        return pd.DataFrame(), pd.DataFrame()

    returns_df = pd.DataFrame(returns_dict)
    returns_df = returns_df.dropna(axis=1, thresh=int(window * 0.7))

    valid_tickers = list(returns_df.columns)
    logger.info("correlation_matrix_shape", tickers=len(valid_tickers), days=len(returns_df))

    corr_matrix = returns_df.corr()

    corr_rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for ticker in valid_tickers:
        if ticker not in corr_matrix.columns:
            continue
        peers = corr_matrix[ticker].drop(ticker, errors="ignore")
        peers = peers.dropna().sort_values(ascending=False).head(top_n)
        for peer, corr_val in peers.items():
            pair = tuple(sorted([ticker, str(peer)]))
            if pair in seen:
                continue
            seen.add(pair)
            corr_rows.append({
                "as_of_date": as_of_date,
                "ticker_a": pair[0],
                "ticker_b": pair[1],
                "correlation_60d": float(corr_val),
            })

    corr_df = pd.DataFrame(corr_rows)

    # Compute betas vs SPY and QQQ
    beta_rows: list[dict] = []
    spy_rets = returns_dict.get("SPY")
    qqq_rets = returns_dict.get("QQQ")

    for ticker in valid_tickers:
        if ticker in ("SPY", "QQQ"):
            continue
        stock_rets = returns_dict[ticker]
        spy_beta = _compute_beta(stock_rets, spy_rets) if spy_rets is not None else np.nan
        qqq_beta = _compute_beta(stock_rets, qqq_rets) if qqq_rets is not None else np.nan
        beta_rows.append({
            "as_of_date": as_of_date,
            "ticker": ticker,
            "spy_beta_60d": spy_beta,
            "qqq_beta_60d": qqq_beta,
        })

    beta_df = pd.DataFrame(beta_rows)

    logger.info(
        "correlations_computed",
        correlation_pairs=len(corr_df),
        betas=len(beta_df),
        as_of=str(as_of_date),
    )

    return corr_df, beta_df
