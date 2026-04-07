"""Black-Scholes implied volatility calculator.

Computes IV from observed option prices using the analytical Black-Scholes
put formula and ``scipy.optimize.brentq`` root-finding.  Designed for batch
processing of historical option bars where IV is not directly available
from the data provider.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm


def bs_put_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
) -> float:
    """European put price under Black-Scholes.

    Args:
        S: Underlying price.
        K: Strike price.
        T: Time to expiration in years (DTE / 365).
        r: Risk-free interest rate (annualised, e.g. 0.05).
        sigma: Volatility (annualised, e.g. 0.30).

    Returns:
        Theoretical put price.  Returns intrinsic value when
        *T* or *sigma* are effectively zero.
    """
    if T <= 0 or sigma <= 0:
        return max(K * math.exp(-r * max(T, 0)) - S, 0.0)

    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def compute_iv(
    option_price: float,
    underlying_price: float,
    strike: float,
    dte: int,
    risk_free_rate: float = 0.05,
    *,
    sigma_low: float = 0.01,
    sigma_high: float = 5.0,
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float:
    """Back-solve implied volatility from an observed put price.

    Args:
        option_price: Market put price (per share).
        underlying_price: Underlying close on the observation date.
        strike: Contract strike price.
        dte: Days to expiration on the observation date.
        risk_free_rate: Annualised risk-free rate.
        sigma_low: Lower bracket for brentq search.
        sigma_high: Upper bracket for brentq search.
        tol: Convergence tolerance for brentq.
        max_iter: Maximum iterations for brentq.

    Returns:
        Annualised implied volatility (e.g. 0.30 = 30%).
        Returns ``NaN`` when IV cannot be determined (zero price,
        arbitrage violation, or solver failure).
    """
    if option_price <= 0 or underlying_price <= 0 or strike <= 0 or dte <= 0:
        return float("nan")

    T = dte / 365.0

    intrinsic = max(strike * math.exp(-risk_free_rate * T) - underlying_price, 0.0)
    if option_price < intrinsic - tol:
        return float("nan")

    def objective(sigma: float) -> float:
        return bs_put_price(underlying_price, strike, T, risk_free_rate, sigma) - option_price

    try:
        lo_val = objective(sigma_low)
        hi_val = objective(sigma_high)

        if lo_val * hi_val > 0:
            if abs(lo_val) < tol:
                return sigma_low
            if abs(hi_val) < tol:
                return sigma_high
            return float("nan")

        iv: float = brentq(objective, sigma_low, sigma_high, xtol=tol, maxiter=max_iter)
        return iv
    except (ValueError, RuntimeError):
        return float("nan")


def compute_iv_batch(
    records: list[dict],
    risk_free_rate: float = 0.05,
) -> np.ndarray:
    """Compute IV for a batch of option observations.

    Each record must contain keys: ``option_close``, ``underlying_close``,
    ``strike``, ``dte``.

    Returns:
        1-D numpy array of implied volatilities, same length as *records*.
    """
    ivs = np.empty(len(records), dtype=np.float64)
    for i, rec in enumerate(records):
        ivs[i] = compute_iv(
            option_price=rec["option_close"],
            underlying_price=rec["underlying_close"],
            strike=rec["strike"],
            dte=rec["dte"],
            risk_free_rate=risk_free_rate,
        )
    return ivs
