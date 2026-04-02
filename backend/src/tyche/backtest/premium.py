"""Pluggable premium models for CSP backtests.

Three simulation models ship out of the box:

* ``fixed_pct`` — flat percentage of notional (legacy default, backward-compatible).
* ``fixed_pct_by_offset`` — offset-aware fixed premium for pullback CSP backtests.
* ``iv_proxy`` — deterministic estimate based on DTE, moneyness, and a realized
  volatility proxy derived from OHLCV close-to-close returns.

Plus a market-data model:

* ``market`` — uses real options chain snapshots from :class:`OptionsChainStore`.
  Falls back to a configurable simulation model when no snapshot is available.

Every model implements :class:`PremiumModel` and is instantiated via
:func:`get_premium_model`.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Any

import numpy as np
import pandas as pd


class PremiumModel(ABC):
    """Interface every premium model must satisfy."""

    name: str

    @abstractmethod
    def premium_pct(
        self,
        strike: float,
        underlying_price: float,
        dte: int,
        *,
        ohlcv: pd.DataFrame | None = None,
        **kwargs: Any,
    ) -> float:
        """Return estimated premium as a fraction of the strike (e.g. 0.015 = 1.5%).

        Args:
            strike: Option strike price.
            underlying_price: Current underlying price.
            dte: Days to expiration.
            ohlcv: Historical OHLCV DataFrame (required by some models).
        """

    def describe(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary of model parameters."""
        return {"model": self.name}


class FixedPctPremiumModel(PremiumModel):
    """Legacy model: constant premium percentage regardless of market conditions.

    Matches the original ``PREMIUM_PCT = 0.015`` behavior.
    """

    name = "fixed_pct"

    def __init__(self, pct: float = 0.015) -> None:
        self.pct = pct

    def premium_pct(
        self,
        strike: float,
        underlying_price: float,
        dte: int,
        *,
        ohlcv: pd.DataFrame | None = None,
        **kwargs: Any,
    ) -> float:
        return self.pct

    def describe(self) -> dict[str, Any]:
        return {"model": self.name, "pct": self.pct}


class FixedPctByOffsetPremiumModel(PremiumModel):
    """Offset-aware fixed model used by ``backtest_pullback_csp.py``.

    Premium varies by how far the strike sits below the support EMA.
    """

    name = "fixed_pct_by_offset"

    DEFAULT_MAP: dict[float, float] = {
        0.0: 0.025,
        3.0: 0.015,
        5.0: 0.010,
    }

    def __init__(
        self,
        offset_map: dict[float, float] | None = None,
        fallback: float = 0.015,
    ) -> None:
        self.offset_map = offset_map or dict(self.DEFAULT_MAP)
        self.fallback = fallback

    def premium_pct(
        self,
        strike: float,
        underlying_price: float,
        dte: int,
        *,
        ohlcv: pd.DataFrame | None = None,
        strike_offset_pct: float = 0.0,
        **kwargs: Any,
    ) -> float:
        return self.offset_map.get(strike_offset_pct, self.fallback)

    def describe(self) -> dict[str, Any]:
        return {
            "model": self.name,
            "offset_map": self.offset_map,
            "fallback": self.fallback,
        }


class IVProxyPremiumModel(PremiumModel):
    """Deterministic IV-proxy model using OHLCV realised volatility.

    Approximates Black-Scholes put premium using:

    1. Realised vol σ = annualised close-to-close standard deviation
       over a trailing window (default 20 trading days).
    2. Moneyness m = strike / underlying_price.
    3. Time factor τ = sqrt(DTE / 252).
    4. Premium fraction ≈ σ × τ × f(m), where f(m) amplifies near ATM
       and decays for deep OTM.

    The result is *not* exact BS — it's a fast, deterministic proxy that
    respects volatility regime changes without requiring an IV surface.
    """

    name = "iv_proxy"

    def __init__(
        self,
        vol_window: int = 20,
        vol_floor: float = 0.10,
        vol_cap: float = 1.50,
    ) -> None:
        self.vol_window = vol_window
        self.vol_floor = vol_floor
        self.vol_cap = vol_cap

    def premium_pct(
        self,
        strike: float,
        underlying_price: float,
        dte: int,
        *,
        ohlcv: pd.DataFrame | None = None,
        **kwargs: Any,
    ) -> float:
        sigma = self._realised_vol(ohlcv)
        tau = math.sqrt(max(dte, 1) / 252.0)
        moneyness = strike / underlying_price if underlying_price > 0 else 1.0

        moneyness_factor = _moneyness_weight(moneyness)
        raw = sigma * tau * moneyness_factor

        return max(0.001, min(raw, 0.20))

    def _realised_vol(self, ohlcv: pd.DataFrame | None) -> float:
        if ohlcv is None or len(ohlcv) < 3:
            return 0.25  # safe default when no data

        close = ohlcv["close"].astype(float)
        window = min(self.vol_window, len(close) - 1)
        log_returns = np.log(close.iloc[-window - 1 :] / close.iloc[-window - 1 :].shift(1)).dropna()

        if len(log_returns) < 2:
            return 0.25

        sigma = float(log_returns.std()) * math.sqrt(252)
        return max(self.vol_floor, min(sigma, self.vol_cap))

    def describe(self) -> dict[str, Any]:
        return {
            "model": self.name,
            "vol_window": self.vol_window,
            "vol_floor": self.vol_floor,
            "vol_cap": self.vol_cap,
        }


class MarketPremiumModel(PremiumModel):
    """Premium model backed by real options chain snapshots.

    Looks up the nearest put contract in the OptionsChainStore for the
    given ticker/date/strike.  When no matching snapshot exists, falls
    back to a configurable simulation model (default: iv_proxy).

    Required kwargs at call site:
        ticker (str): Underlying symbol.
        snapshot_date (date): Date of the backtest entry.

    Optional kwargs:
        use_bid (bool): If True, use bid price (conservative). Default True.
    """

    name = "market"

    def __init__(
        self,
        options_store: Any,
        fallback: PremiumModel | None = None,
        max_date_gap_days: int = 7,
        strike_tolerance_pct: float = 2.0,
    ) -> None:
        self._store = options_store
        self._fallback = fallback or IVProxyPremiumModel()
        self._max_gap = max_date_gap_days
        self._strike_tol = strike_tolerance_pct
        self._hits = 0
        self._misses = 0

    def premium_pct(
        self,
        strike: float,
        underlying_price: float,
        dte: int,
        *,
        ohlcv: pd.DataFrame | None = None,
        ticker: str = "",
        snapshot_date: Any = None,
        use_bid: bool = True,
        **kwargs: Any,
    ) -> float:
        if not ticker or snapshot_date is None:
            self._misses += 1
            return self._fallback.premium_pct(
                strike, underlying_price, dte, ohlcv=ohlcv, **kwargs
            )

        nearest = self._store.get_nearest_snapshot_date(ticker, snapshot_date)
        if nearest is None or abs((nearest - snapshot_date).days) > self._max_gap:
            self._misses += 1
            return self._fallback.premium_pct(
                strike, underlying_price, dte, ohlcv=ohlcv, **kwargs
            )

        from datetime import timedelta
        target_exp = snapshot_date + timedelta(days=dte)

        match = self._store.get_put_premium(
            ticker,
            nearest,
            target_strike=strike,
            target_expiration=target_exp,
            tolerance_pct=self._strike_tol,
        )

        if match is None:
            self._misses += 1
            return self._fallback.premium_pct(
                strike, underlying_price, dte, ohlcv=ohlcv, **kwargs
            )

        self._hits += 1
        price = match["bid"] if use_bid else match["mid"]
        if strike > 0:
            return max(0.001, price / strike)
        return self._fallback.premium_pct(
            strike, underlying_price, dte, ohlcv=ohlcv, **kwargs
        )

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return (self._hits / total * 100) if total > 0 else 0.0

    def describe(self) -> dict[str, Any]:
        return {
            "model": self.name,
            "fallback": self._fallback.name,
            "max_date_gap_days": self._max_gap,
            "strike_tolerance_pct": self._strike_tol,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate_pct": round(self.hit_rate, 1),
        }


def _moneyness_weight(moneyness: float) -> float:
    """Weighting function that peaks near ATM (m ≈ 1.0) and decays OTM.

    For puts, m < 1.0 is OTM.  The function is a smoothed Gaussian-like
    bump centred on m=1.0 with moderate tails so that 5% OTM still gets
    meaningful premium.
    """
    return float(np.exp(-5.0 * (moneyness - 1.0) ** 2) * 0.8 + 0.2)


# ── Factory ──────────────────────────────────────────────────────────────


_REGISTRY: dict[str, type[PremiumModel]] = {
    "fixed_pct": FixedPctPremiumModel,
    "fixed_pct_by_offset": FixedPctByOffsetPremiumModel,
    "iv_proxy": IVProxyPremiumModel,
}


def get_premium_model(name: str, **kwargs: Any) -> PremiumModel:
    """Instantiate a premium model by name.

    The ``market`` model requires an ``options_store`` kwarg and is not
    in the auto-registry.  Use :func:`get_market_premium_model` instead.

    Args:
        name: One of ``fixed_pct``, ``fixed_pct_by_offset``, ``iv_proxy``.
        **kwargs: Forwarded to the model constructor.

    Raises:
        ValueError: Unknown model name.
    """
    if name == "market":
        return MarketPremiumModel(**kwargs)
    cls = _REGISTRY.get(name)
    if cls is None:
        available = ", ".join(sorted(_REGISTRY)) + ", market"
        raise ValueError(f"Unknown premium model '{name}'. Available: {available}")
    return cls(**kwargs)


def get_market_premium_model(
    options_store: Any,
    fallback_name: str = "iv_proxy",
    **kwargs: Any,
) -> MarketPremiumModel:
    """Convenience factory for the market premium model.

    Args:
        options_store: An :class:`OptionsChainStore` instance.
        fallback_name: Simulation model to use when no market data exists.
        **kwargs: Forwarded to :class:`MarketPremiumModel`.
    """
    fallback = get_premium_model(fallback_name)
    return MarketPremiumModel(options_store=options_store, fallback=fallback, **kwargs)


def available_models() -> list[str]:
    """Return sorted list of registered model names."""
    return sorted(_REGISTRY)
