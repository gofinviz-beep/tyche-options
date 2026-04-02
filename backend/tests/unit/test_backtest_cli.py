"""Tests for CLI flag parsing and behaviour in backtest scripts.

These tests import the ``_parse_args`` functions from the scripts and verify
that flag combinations produce correct model selections and defaults.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))


# ── backtest_ema.py CLI ──────────────────────────────────────────────────

class TestEmaParseArgs:
    def _parse(self, argv: list[str]):
        from backtest_ema import _parse_args
        with patch("sys.argv", ["backtest_ema.py"] + argv):
            return _parse_args()

    def test_defaults(self):
        args = self._parse([])
        assert args.premium_model == "fixed_pct"
        assert args.execution_model == "none"
        assert args.walk_forward is False
        assert args.train_days == 126
        assert args.test_days == 63
        assert args.print_assumptions is False

    def test_premium_model_iv_proxy(self):
        args = self._parse(["--premium-model", "iv_proxy"])
        assert args.premium_model == "iv_proxy"

    def test_execution_model_conservative(self):
        args = self._parse(["--execution-model", "conservative"])
        assert args.execution_model == "conservative"

    def test_walk_forward_flag(self):
        args = self._parse(["--walk-forward", "--train-days", "100", "--test-days", "50"])
        assert args.walk_forward is True
        assert args.train_days == 100
        assert args.test_days == 50

    def test_print_assumptions_flag(self):
        args = self._parse(["--print-assumptions"])
        assert args.print_assumptions is True

    def test_invalid_premium_model(self):
        with pytest.raises(SystemExit):
            self._parse(["--premium-model", "black_scholes"])

    def test_invalid_execution_model(self):
        with pytest.raises(SystemExit):
            self._parse(["--execution-model", "aggressive"])


# ── backtest_pullback_csp.py CLI ─────────────────────────────────────────

class TestPullbackParseArgs:
    def _parse(self, argv: list[str]):
        from backtest_pullback_csp import _parse_args
        with patch("sys.argv", ["backtest_pullback_csp.py"] + argv):
            return _parse_args()

    def test_defaults(self):
        args = self._parse([])
        assert args.dte == 8
        assert args.dte_alt == 5
        assert args.min_prior_streak == 5
        assert args.premium_model == "fixed_pct_by_offset"
        assert args.execution_model == "none"
        assert args.walk_forward is False
        assert args.print_assumptions is False

    def test_dte_override(self):
        args = self._parse(["--dte", "5"])
        assert args.dte == 5

    def test_premium_model_fixed_pct(self):
        args = self._parse(["--premium-model", "fixed_pct"])
        assert args.premium_model == "fixed_pct"

    def test_premium_model_iv_proxy(self):
        args = self._parse(["--premium-model", "iv_proxy"])
        assert args.premium_model == "iv_proxy"

    def test_execution_model_base(self):
        args = self._parse(["--execution-model", "base"])
        assert args.execution_model == "base"

    def test_walk_forward_flag(self):
        args = self._parse(["--walk-forward"])
        assert args.walk_forward is True

    def test_print_assumptions_flag(self):
        args = self._parse(["--print-assumptions"])
        assert args.print_assumptions is True

    def test_combined_flags(self):
        args = self._parse([
            "--dte", "5",
            "--premium-model", "iv_proxy",
            "--execution-model", "conservative",
            "--walk-forward",
            "--train-days", "200",
            "--test-days", "100",
        ])
        assert args.dte == 5
        assert args.premium_model == "iv_proxy"
        assert args.execution_model == "conservative"
        assert args.walk_forward is True
        assert args.train_days == 200
        assert args.test_days == 100


# ── Model integration ────────────────────────────────────────────────────

class TestModelIntegration:
    """Verify that CLI flag values correctly produce model instances."""

    def test_ema_default_models(self):
        from tyche.backtest.execution import get_execution_model
        from tyche.backtest.premium import get_premium_model

        pm = get_premium_model("fixed_pct")
        em = get_execution_model("none")
        assert pm.name == "fixed_pct"
        assert em.mode == "none"
        assert em.adjust_premium(100.0) == 100.0

    def test_pullback_default_models(self):
        from tyche.backtest.execution import get_execution_model
        from tyche.backtest.premium import get_premium_model

        pm = get_premium_model("fixed_pct_by_offset")
        em = get_execution_model("none")
        assert pm.name == "fixed_pct_by_offset"
        pct_3 = pm.premium_pct(97, 100, 8, strike_offset_pct=3.0)
        assert pct_3 == 0.015
