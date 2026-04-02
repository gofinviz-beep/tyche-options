"""Tests for tyche.backtest.execution — execution friction models."""

import pytest

from tyche.backtest.execution import (
    ExecutionModel,
    FrictionParams,
    SensitivityRow,
    available_modes,
    build_sensitivity_table,
    format_sensitivity_table,
    get_execution_model,
)


# ── FrictionParams ───────────────────────────────────────────────────────

class TestFrictionParams:
    def test_frozen(self):
        fp = FrictionParams(fill_ratio=0.95)
        with pytest.raises(AttributeError):
            fp.fill_ratio = 0.90  # type: ignore[misc]

    def test_default_commission(self):
        fp = FrictionParams(fill_ratio=1.0)
        assert fp.commission_per_contract == 0.65


# ── ExecutionModel ───────────────────────────────────────────────────────

class TestExecutionModel:
    def test_none_mode_no_friction(self):
        m = get_execution_model("none")
        assert m.adjust_premium(100.0, contracts=1) == 100.0

    def test_optimistic_subtracts_commission(self):
        m = get_execution_model("optimistic")
        net = m.adjust_premium(100.0, contracts=1)
        assert net == 100.0 - 0.65

    def test_base_applies_slippage_and_commission(self):
        m = get_execution_model("base")
        net = m.adjust_premium(100.0, contracts=1)
        expected = 100.0 * 0.92 - 0.65
        assert abs(net - expected) < 0.01

    def test_conservative_most_friction(self):
        m = get_execution_model("conservative")
        net = m.adjust_premium(100.0, contracts=1)
        expected = 100.0 * 0.82 - 0.65
        assert abs(net - expected) < 0.01

    def test_multi_contract_commission(self):
        m = get_execution_model("optimistic")
        net = m.adjust_premium(500.0, contracts=5)
        assert net == 500.0 - 0.65 * 5

    def test_premium_never_negative(self):
        m = get_execution_model("conservative")
        assert m.adjust_premium(0.50, contracts=1) == 0.0

    def test_zero_premium(self):
        m = get_execution_model("base")
        assert m.adjust_premium(0.0, contracts=1) == 0.0

    def test_describe(self):
        m = get_execution_model("base")
        d = m.describe()
        assert d["mode"] == "base"
        assert d["fill_ratio"] == 0.92
        assert d["commission_per_contract"] == 0.65


# ── Monotonicity invariant ───────────────────────────────────────────────

class TestMonotonicity:
    """Key acceptance criterion: conservative <= base <= optimistic for any input."""

    @pytest.mark.parametrize("raw_premium,contracts", [
        (50.0, 1),
        (150.0, 1),
        (500.0, 3),
        (1000.0, 10),
        (10.0, 1),
    ])
    def test_premium_ordering(self, raw_premium: float, contracts: int):
        opt = get_execution_model("optimistic").adjust_premium(raw_premium, contracts)
        base = get_execution_model("base").adjust_premium(raw_premium, contracts)
        con = get_execution_model("conservative").adjust_premium(raw_premium, contracts)
        assert con <= base <= opt, (
            f"Monotonicity violated: con={con}, base={base}, opt={opt} "
            f"for premium={raw_premium}, contracts={contracts}"
        )


# ── Sensitivity table ────────────────────────────────────────────────────

class TestSensitivityTable:
    def test_three_rows(self):
        rows = build_sensitivity_table(sample_premium=150.0)
        assert len(rows) == 3
        modes = [r.mode for r in rows]
        assert "optimistic" in modes
        assert "base" in modes
        assert "conservative" in modes

    def test_row_fields(self):
        rows = build_sensitivity_table(sample_premium=200.0, contracts=2)
        for r in rows:
            assert isinstance(r, SensitivityRow)
            assert r.sample_premium_raw == 200.0
            assert r.sample_premium_net <= 200.0
            assert 0 <= r.pct_retained <= 100

    def test_monotonicity_in_table(self):
        rows = build_sensitivity_table(sample_premium=150.0)
        by_mode = {r.mode: r for r in rows}
        assert by_mode["conservative"].sample_premium_net <= by_mode["base"].sample_premium_net
        assert by_mode["base"].sample_premium_net <= by_mode["optimistic"].sample_premium_net

    def test_format_table_string(self):
        rows = build_sensitivity_table(sample_premium=100.0)
        text = format_sensitivity_table(rows)
        assert "optimistic" in text
        assert "base" in text
        assert "conservative" in text
        assert "$" in text

    def test_zero_premium_table(self):
        rows = build_sensitivity_table(sample_premium=0.0)
        for r in rows:
            assert r.pct_retained == 0.0


# ── Factory ──────────────────────────────────────────────────────────────

class TestGetExecutionModel:
    def test_valid_modes(self):
        for mode in ("none", "optimistic", "base", "conservative"):
            m = get_execution_model(mode)
            assert m.mode == mode

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown execution mode"):
            get_execution_model("aggressive")

    def test_available_modes(self):
        modes = available_modes()
        assert "none" in modes
        assert "base" in modes
        assert "conservative" in modes
        assert "optimistic" in modes


# ── Backward compatibility ───────────────────────────────────────────────

class TestBackwardCompat:
    """The 'none' mode must produce identical output to the legacy code
    which had no friction at all."""

    def test_none_is_identity(self):
        m = get_execution_model("none")
        for prem in [50.0, 100.0, 150.0, 250.0]:
            assert m.adjust_premium(prem, contracts=1) == prem
            assert m.adjust_premium(prem, contracts=5) == prem
