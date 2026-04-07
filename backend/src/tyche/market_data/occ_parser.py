"""OCC option ticker parser for Polygon / Massive flat files.

Polygon encodes options tickers in a modified OCC format::

    O:SPY230327P00390000
    │ │        │ │       │
    │ │        │ │       └─ strike × 1000, zero-padded to 8 digits
    │ │        │ └─ option type: P (put) or C (call)
    │ │        └─ expiration YYMMDD
    │ └─ underlying symbol (1–6 chars, variable length)
    └─ prefix ``O:``

The last 15 characters after the ``O:`` prefix are always fixed-width:
YYMMDD (6) + P/C (1) + strike (8).  Everything before that is the
underlying symbol.

Provides both a scalar function for one-off parsing and vectorized
pandas helpers for processing flat-file DataFrames at bulk speed.
"""

from __future__ import annotations

from datetime import datetime, date

import pandas as pd


def parse_occ_ticker(ticker: str) -> tuple[str, date, str, float]:
    """Parse a single Polygon OCC ticker string.

    Args:
        ticker: Full OCC ticker, e.g. ``O:SPY230327P00390000``.

    Returns:
        Tuple of (underlying, expiration, option_type, strike).

    Raises:
        ValueError: If the ticker is too short or has an invalid format.
    """
    raw = ticker[2:] if ticker.startswith("O:") else ticker

    if len(raw) < 16:
        raise ValueError(f"OCC ticker too short: {ticker!r}")

    underlying = raw[:-15]
    exp_str = raw[-15:-9]
    option_type = raw[-9]
    strike_raw = raw[-8:]

    if option_type not in ("P", "C"):
        raise ValueError(f"Invalid option type {option_type!r} in {ticker!r}")

    try:
        expiration = datetime.strptime(exp_str, "%y%m%d").date()
    except ValueError as exc:
        raise ValueError(f"Invalid expiration {exp_str!r} in {ticker!r}") from exc

    strike = int(strike_raw) / 1000.0
    return underlying, expiration, option_type, strike


def extract_underlying(tickers: pd.Series) -> pd.Series:
    """Vectorized extraction of the underlying symbol from OCC tickers.

    Strips the ``O:`` prefix and the fixed 15-char suffix to isolate
    the variable-length underlying symbol.  ~10x faster than row-wise
    ``parse_occ_ticker`` on large DataFrames.
    """
    stripped = tickers.str[2:]
    return stripped.str[:-15]


def parse_occ_columns(df: pd.DataFrame, ticker_col: str = "ticker") -> pd.DataFrame:
    """Add parsed OCC columns to a DataFrame in-place (returns same df).

    Adds: ``underlying``, ``expiration``, ``option_type``, ``strike``.
    Uses vectorized string slicing for speed.
    """
    stripped = df[ticker_col].str[2:]

    df["underlying"] = stripped.str[:-15]
    exp_str = stripped.str[-15:-9]
    df["option_type"] = stripped.str[-9]
    strike_raw = stripped.str[-8:]

    df["expiration"] = pd.to_datetime(exp_str, format="%y%m%d").dt.date
    df["strike"] = strike_raw.astype(int) / 1000.0

    return df
