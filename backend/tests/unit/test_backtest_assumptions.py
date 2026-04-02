"""Tests for tyche.backtest.assumptions — centralised assumptions audit."""

import json

import pytest

from tyche.backtest.assumptions import (
    BacktestAssumptions,
    build_assumptions,
)
from tyche.backtest.execution import get_execution_model
from tyche.backtest.premium import get_premium_model


# ── Helpers ──────────────────────────────────────────────────────────────

def _build_default() -> BacktestAssumptions:
    return build_assumptions(
        "test_script.py",
        min_market_cap=5_000_000_000,
        min_price=15.0,
        min_volume=500_000,
        valid_exchanges=["XNYS", "XNAS"],
        dte=8,
        otm_pct=0.05,
        premium_model=get_premium_model("fixed_pct"),
        execution_model=get_execution_model("none"),
    )


# ── BacktestAssumptions ─────────────────────────────────────────────────

class TestBacktestAssumptions:
    def test_to_dict_structure(self):
        a = _build_default()
        d = a.to_dict()
        assert d["script"] == "test_script.py"
        assert "universe_filters" in d
        assert "trade_parameters" in d
        assert "premium_model" in d
        assert "execution_model" in d
        assert "conviction_filters" in d

    def test_market_cap_label(self):
        a = _build_default()
        d = a.to_dict()
        assert d["universe_filters"]["min_market_cap_label"] == "$5B"

    def test_market_cap_label_millions(self):
        a = build_assumptions(
            "test",
            min_market_cap=500_000_000,
            min_price=15.0,
            min_volume=500_000,
            valid_exchanges=["XNYS"],
            dte=8,
            premium_model=get_premium_model("fixed_pct"),
            execution_model=get_execution_model("none"),
        )
        d = a.to_dict()
        assert d["universe_filters"]["min_market_cap_label"] == "$500M"

    def test_otm_pct_included(self):
        a = _build_default()
        d = a.to_dict()
        assert d["trade_parameters"]["otm_pct"] == 0.05

    def test_strike_offsets(self):
        a = build_assumptions(
            "test",
            min_market_cap=5e9,
            min_price=15.0,
            min_volume=500_000,
            valid_exchanges=["XNYS"],
            dte=8,
            strike_offsets=[0.0, 3.0, 5.0],
            premium_model=get_premium_model("fixed_pct_by_offset"),
            execution_model=get_execution_model("none"),
        )
        d = a.to_dict()
        assert d["trade_parameters"]["strike_offsets"] == [0.0, 3.0, 5.0]

    def test_capital_simulation_optional(self):
        a = _build_default()
        d = a.to_dict()
        assert "capital_simulation" not in d

    def test_capital_simulation_present(self):
        a = build_assumptions(
            "test",
            min_market_cap=5e9,
            min_price=15.0,
            min_volume=500_000,
            valid_exchanges=["XNYS"],
            dte=8,
            premium_model=get_premium_model("fixed_pct"),
            execution_model=get_execution_model("none"),
            starting_capital=100_000.0,
            max_positions=8,
            max_concentration_pct=25.0,
        )
        d = a.to_dict()
        assert d["capital_simulation"]["starting_capital"] == 100_000.0

    def test_walk_forward_optional(self):
        a = _build_default()
        d = a.to_dict()
        assert "walk_forward" not in d

    def test_walk_forward_present(self):
        a = build_assumptions(
            "test",
            min_market_cap=5e9,
            min_price=15.0,
            min_volume=500_000,
            valid_exchanges=["XNYS"],
            dte=8,
            premium_model=get_premium_model("fixed_pct"),
            execution_model=get_execution_model("base"),
            walk_forward_enabled=True,
            train_days=126,
            test_days=63,
        )
        d = a.to_dict()
        assert d["walk_forward"]["enabled"] is True
        assert d["walk_forward"]["train_days"] == 126

    def test_extra_kwargs(self):
        a = build_assumptions(
            "test",
            min_market_cap=5e9,
            min_price=15.0,
            min_volume=500_000,
            valid_exchanges=["XNYS"],
            dte=8,
            premium_model=get_premium_model("fixed_pct"),
            execution_model=get_execution_model("none"),
            top_n_per_day=10,
        )
        d = a.to_dict()
        assert d["extra"]["top_n_per_day"] == 10

    def test_conviction_defaults(self):
        a = _build_default()
        d = a.to_dict()
        cf = d["conviction_filters"]
        assert cf["ema_fast"] == 8
        assert cf["ema_slow"] == 21
        assert cf["max_extension_pct"] == 3.0
        assert cf["min_days_above_emas"] == 5
        assert cf["max_days_above_emas"] == 10

    def test_prior_streak_when_set(self):
        a = build_assumptions(
            "test",
            min_market_cap=5e9,
            min_price=15.0,
            min_volume=500_000,
            valid_exchanges=["XNYS"],
            dte=8,
            premium_model=get_premium_model("fixed_pct"),
            execution_model=get_execution_model("none"),
            min_prior_streak=5,
        )
        d = a.to_dict()
        assert d["conviction_filters"]["min_prior_streak"] == 5


# ── JSON serialisation ───────────────────────────────────────────────────

class TestSerialization:
    def test_to_json_is_valid(self):
        a = _build_default()
        j = a.to_json()
        parsed = json.loads(j)
        assert parsed["script"] == "test_script.py"

    def test_to_json_roundtrip(self):
        a = _build_default()
        d1 = a.to_dict()
        d2 = json.loads(a.to_json())
        assert d1 == d2


# ── Print output ─────────────────────────────────────────────────────────

class TestPrintSummary:
    def test_prints_all_sections(self, capsys):
        a = _build_default()
        a.print_summary()
        out = capsys.readouterr().out
        assert "BACKTEST ASSUMPTIONS" in out
        assert "test_script.py" in out
        assert "Universe Filters" in out
        assert "Trade Parameters" in out
        assert "Premium Model" in out
        assert "Execution Model" in out
        assert "Conviction Filters" in out

    def test_prints_capital_when_set(self, capsys):
        a = build_assumptions(
            "test",
            min_market_cap=5e9,
            min_price=15.0,
            min_volume=500_000,
            valid_exchanges=["XNYS"],
            dte=8,
            premium_model=get_premium_model("fixed_pct"),
            execution_model=get_execution_model("none"),
            starting_capital=100_000.0,
            max_positions=8,
        )
        a.print_summary()
        out = capsys.readouterr().out
        assert "Capital Simulation" in out

    def test_prints_walk_forward_when_enabled(self, capsys):
        a = build_assumptions(
            "test",
            min_market_cap=5e9,
            min_price=15.0,
            min_volume=500_000,
            valid_exchanges=["XNYS"],
            dte=8,
            premium_model=get_premium_model("fixed_pct"),
            execution_model=get_execution_model("none"),
            walk_forward_enabled=True,
            train_days=126,
            test_days=63,
        )
        a.print_summary()
        out = capsys.readouterr().out
        assert "Walk-Forward" in out


# ── Snapshot: assumptions match runtime constants ────────────────────────

class TestAssumptionsMatchConstants:
    """Verify that the assumptions block produced by the builder accurately
    reflects the constants used in the actual backtest scripts."""

    def test_ema_backtest_assumptions(self):
        pm = get_premium_model("fixed_pct")
        em = get_execution_model("none")
        a = build_assumptions(
            "backtest_ema.py",
            min_market_cap=5_000_000_000,
            min_price=15.0,
            min_volume=500_000,
            valid_exchanges=["ARCX", "BATS", "XASE", "XNAS", "XNMS", "XNYS"],
            dte=8,
            otm_pct=0.05,
            premium_model=pm,
            execution_model=em,
        )
        d = a.to_dict()
        assert d["universe_filters"]["min_market_cap"] == 5_000_000_000
        assert d["universe_filters"]["min_market_cap_label"] == "$5B"
        assert d["trade_parameters"]["dte"] == 8
        assert d["trade_parameters"]["otm_pct"] == 0.05
        assert d["premium_model"]["model"] == "fixed_pct"
        assert d["premium_model"]["pct"] == 0.015
        assert d["execution_model"]["mode"] == "none"

    def test_pullback_backtest_assumptions(self):
        pm = get_premium_model("fixed_pct_by_offset")
        em = get_execution_model("none")
        a = build_assumptions(
            "backtest_pullback_csp.py",
            min_market_cap=5_000_000_000,
            min_price=15.0,
            min_volume=500_000,
            valid_exchanges=["ARCX", "BATS", "XASE", "XNAS", "XNMS", "XNYS"],
            dte=8,
            strike_offsets=[0.0, 3.0, 5.0],
            premium_model=pm,
            execution_model=em,
            ema_fast=8,
            ema_slow=21,
            min_prior_streak=5,
        )
        d = a.to_dict()
        assert d["premium_model"]["model"] == "fixed_pct_by_offset"
        assert d["trade_parameters"]["strike_offsets"] == [0.0, 3.0, 5.0]
        assert d["conviction_filters"]["min_prior_streak"] == 5
