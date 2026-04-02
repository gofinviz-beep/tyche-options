"""Tests for tyche.backtest.premium — pluggable premium models."""

import numpy as np
import pandas as pd
import pytest

from tyche.backtest.premium import (
    FixedPctByOffsetPremiumModel,
    FixedPctPremiumModel,
    IVProxyPremiumModel,
    PremiumModel,
    available_models,
    get_premium_model,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_ohlcv(n: int = 60, base: float = 100.0, vol: float = 0.02) -> pd.DataFrame:
    np.random.seed(42)
    returns = np.random.normal(0.001, vol, n)
    prices = base * np.cumprod(1 + returns)
    return pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=n),
        "open": prices * 0.999,
        "high": prices * 1.005,
        "low": prices * 0.995,
        "close": prices,
        "volume": np.random.randint(500_000, 2_000_000, n),
    })


# ── FixedPctPremiumModel ─────────────────────────────────────────────────

class TestFixedPctPremiumModel:
    def test_default_pct(self):
        m = FixedPctPremiumModel()
        assert m.premium_pct(strike=95, underlying_price=100, dte=8) == 0.015

    def test_custom_pct(self):
        m = FixedPctPremiumModel(pct=0.025)
        assert m.premium_pct(strike=95, underlying_price=100, dte=8) == 0.025

    def test_ignores_ohlcv(self):
        m = FixedPctPremiumModel()
        df = _make_ohlcv()
        assert m.premium_pct(strike=95, underlying_price=100, dte=8, ohlcv=df) == 0.015

    def test_name(self):
        assert FixedPctPremiumModel().name == "fixed_pct"

    def test_describe(self):
        m = FixedPctPremiumModel(pct=0.02)
        d = m.describe()
        assert d["model"] == "fixed_pct"
        assert d["pct"] == 0.02


# ── FixedPctByOffsetPremiumModel ─────────────────────────────────────────

class TestFixedPctByOffsetPremiumModel:
    def test_default_offsets(self):
        m = FixedPctByOffsetPremiumModel()
        assert m.premium_pct(95, 100, 8, strike_offset_pct=0.0) == 0.025
        assert m.premium_pct(95, 100, 8, strike_offset_pct=3.0) == 0.015
        assert m.premium_pct(95, 100, 8, strike_offset_pct=5.0) == 0.010

    def test_unknown_offset_uses_fallback(self):
        m = FixedPctByOffsetPremiumModel(fallback=0.012)
        assert m.premium_pct(95, 100, 8, strike_offset_pct=7.0) == 0.012

    def test_custom_map(self):
        m = FixedPctByOffsetPremiumModel(offset_map={10.0: 0.005})
        assert m.premium_pct(90, 100, 8, strike_offset_pct=10.0) == 0.005

    def test_name(self):
        assert FixedPctByOffsetPremiumModel().name == "fixed_pct_by_offset"

    def test_describe(self):
        m = FixedPctByOffsetPremiumModel()
        d = m.describe()
        assert "offset_map" in d
        assert d["model"] == "fixed_pct_by_offset"


# ── IVProxyPremiumModel ──────────────────────────────────────────────────

class TestIVProxyPremiumModel:
    def test_returns_positive(self):
        m = IVProxyPremiumModel()
        df = _make_ohlcv()
        pct = m.premium_pct(strike=95, underlying_price=100, dte=8, ohlcv=df)
        assert pct > 0

    def test_atm_higher_than_deep_otm(self):
        m = IVProxyPremiumModel()
        df = _make_ohlcv()
        atm = m.premium_pct(strike=100, underlying_price=100, dte=8, ohlcv=df)
        otm = m.premium_pct(strike=85, underlying_price=100, dte=8, ohlcv=df)
        assert atm > otm

    def test_longer_dte_higher_premium(self):
        m = IVProxyPremiumModel()
        df = _make_ohlcv()
        short = m.premium_pct(strike=95, underlying_price=100, dte=3, ohlcv=df)
        long = m.premium_pct(strike=95, underlying_price=100, dte=30, ohlcv=df)
        assert long > short

    def test_no_ohlcv_uses_default_vol(self):
        m = IVProxyPremiumModel()
        pct = m.premium_pct(strike=95, underlying_price=100, dte=8, ohlcv=None)
        assert pct > 0

    def test_short_ohlcv_uses_default_vol(self):
        m = IVProxyPremiumModel()
        df = pd.DataFrame({"close": [100.0, 101.0]})
        pct = m.premium_pct(strike=95, underlying_price=100, dte=8, ohlcv=df)
        assert pct > 0

    def test_vol_floor(self):
        m = IVProxyPremiumModel(vol_floor=0.10)
        df = pd.DataFrame({"close": [100.0] * 30})
        vol = m._realised_vol(df)
        assert vol >= 0.10

    def test_vol_cap(self):
        m = IVProxyPremiumModel(vol_cap=0.80)
        np.random.seed(0)
        wild = pd.DataFrame({"close": np.exp(np.cumsum(np.random.normal(0, 0.2, 60)))})
        vol = m._realised_vol(wild)
        assert vol <= 0.80

    def test_premium_capped_at_20pct(self):
        m = IVProxyPremiumModel(vol_cap=5.0)
        pct = m.premium_pct(strike=100, underlying_price=100, dte=252, ohlcv=None)
        assert pct <= 0.20

    def test_premium_floored_at_0_1pct(self):
        m = IVProxyPremiumModel(vol_floor=0.001)
        pct = m.premium_pct(strike=50, underlying_price=100, dte=1, ohlcv=None)
        assert pct >= 0.001

    def test_name(self):
        assert IVProxyPremiumModel().name == "iv_proxy"

    def test_describe(self):
        m = IVProxyPremiumModel(vol_window=30, vol_floor=0.15, vol_cap=1.0)
        d = m.describe()
        assert d["vol_window"] == 30
        assert d["vol_floor"] == 0.15

    def test_zero_underlying_price(self):
        m = IVProxyPremiumModel()
        pct = m.premium_pct(strike=5, underlying_price=0.0, dte=8)
        assert pct > 0


# ── Factory ──────────────────────────────────────────────────────────────

class TestGetPremiumModel:
    def test_fixed_pct(self):
        m = get_premium_model("fixed_pct")
        assert isinstance(m, FixedPctPremiumModel)

    def test_fixed_pct_by_offset(self):
        m = get_premium_model("fixed_pct_by_offset")
        assert isinstance(m, FixedPctByOffsetPremiumModel)

    def test_iv_proxy(self):
        m = get_premium_model("iv_proxy")
        assert isinstance(m, IVProxyPremiumModel)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown premium model"):
            get_premium_model("black_scholes")

    def test_kwargs_forwarded(self):
        m = get_premium_model("fixed_pct", pct=0.03)
        assert m.premium_pct(95, 100, 8) == 0.03

    def test_available_models(self):
        models = available_models()
        assert "fixed_pct" in models
        assert "iv_proxy" in models
        assert "fixed_pct_by_offset" in models


# ── Backward compatibility ───────────────────────────────────────────────

class TestBackwardCompatibility:
    """Verify that fixed_pct with default args produces the same 0.015 as the
    legacy PREMIUM_PCT constant."""

    LEGACY_PREMIUM_PCT = 0.015

    def test_ema_backtest_compat(self):
        m = get_premium_model("fixed_pct")
        for strike, price in [(95.0, 100.0), (23.0, 24.5), (180.5, 190.0)]:
            assert m.premium_pct(strike, price, 8) == self.LEGACY_PREMIUM_PCT

    def test_pullback_backtest_compat(self):
        m = get_premium_model("fixed_pct_by_offset")
        for offset, expected in [(0.0, 0.025), (3.0, 0.015), (5.0, 0.010)]:
            assert m.premium_pct(95, 100, 8, strike_offset_pct=offset) == expected
