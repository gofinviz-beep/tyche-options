"""Tests for tyche.backtest.walk_forward — rolling walk-forward harness."""

import json
from datetime import date, timedelta

import pytest

from tyche.backtest.walk_forward import (
    WalkForwardRunner,
    WalkForwardSummary,
    WindowResult,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_dates(n: int, start: date = date(2024, 1, 2)) -> list[date]:
    """Generate n weekday-only dates."""
    dates: list[date] = []
    d = start
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return dates


def _dummy_run_fn(
    train_dates: list[date],
    test_dates: list[date],
    **ctx: object,
) -> WindowResult:
    """Deterministic window function for testing."""
    n = len(test_dates)
    return WindowResult(
        window_id=0,
        train_start=train_dates[0],
        train_end=train_dates[-1],
        test_start=test_dates[0],
        test_end=test_dates[-1],
        total_trades=n,
        wins=n - 1,
        win_rate=((n - 1) / n * 100) if n else 0.0,
        avg_pnl_pct=1.5,
        cumulative_pnl_pct=1.5 * n,
        max_drawdown_pct=-2.0,
        sharpe=1.2,
    )


# ── WindowResult ─────────────────────────────────────────────────────────

class TestWindowResult:
    def test_losses_property(self):
        wr = WindowResult(
            window_id=0,
            train_start=date(2024, 1, 1),
            train_end=date(2024, 3, 1),
            test_start=date(2024, 3, 2),
            test_end=date(2024, 5, 1),
            total_trades=10,
            wins=7,
        )
        assert wr.losses == 3

    def test_extra_dict(self):
        wr = WindowResult(
            window_id=0,
            train_start=date(2024, 1, 1),
            train_end=date(2024, 3, 1),
            test_start=date(2024, 3, 2),
            test_end=date(2024, 5, 1),
            extra={"note": "test"},
        )
        assert wr.extra["note"] == "test"


# ── WalkForwardRunner ────────────────────────────────────────────────────

class TestWalkForwardRunner:
    def test_basic_run(self):
        dates = _make_dates(300)
        runner = WalkForwardRunner(train_days=126, test_days=63)
        summary = runner.run(dates, run_fn=_dummy_run_fn)
        assert summary.n_windows >= 1
        assert all(isinstance(w, WindowResult) for w in summary.windows)

    def test_window_count(self):
        dates = _make_dates(252)  # ~1 year
        runner = WalkForwardRunner(train_days=126, test_days=63)
        summary = runner.run(dates, run_fn=_dummy_run_fn)
        assert summary.n_windows == 2

    def test_insufficient_data_raises(self):
        dates = _make_dates(50)
        runner = WalkForwardRunner(train_days=126, test_days=63)
        with pytest.raises(ValueError, match="Need at least"):
            runner.run(dates, run_fn=_dummy_run_fn)

    def test_exact_minimum_dates(self):
        dates = _make_dates(189)  # 126 + 63
        runner = WalkForwardRunner(train_days=126, test_days=63)
        summary = runner.run(dates, run_fn=_dummy_run_fn)
        assert summary.n_windows == 1

    def test_step_days(self):
        dates = _make_dates(400)
        runner_no_overlap = WalkForwardRunner(train_days=100, test_days=50)
        runner_overlap = WalkForwardRunner(train_days=100, test_days=50, step_days=25)
        summary_no = runner_no_overlap.run(dates, run_fn=_dummy_run_fn)
        summary_yes = runner_overlap.run(dates, run_fn=_dummy_run_fn)
        assert summary_yes.n_windows > summary_no.n_windows

    def test_context_forwarded(self):
        dates = _make_dates(200)
        received = {}

        def capture_fn(train_dates, test_dates, **ctx):
            received.update(ctx)
            return _dummy_run_fn(train_dates, test_dates)

        runner = WalkForwardRunner(train_days=100, test_days=50)
        runner.run(dates, run_fn=capture_fn, foo="bar", baz=42)
        assert received["foo"] == "bar"
        assert received["baz"] == 42

    def test_window_ids_sequential(self):
        dates = _make_dates(400)
        runner = WalkForwardRunner(train_days=100, test_days=50)
        summary = runner.run(dates, run_fn=_dummy_run_fn)
        ids = [w.window_id for w in summary.windows]
        assert ids == list(range(len(ids)))

    def test_train_end_before_test_start(self):
        dates = _make_dates(300)
        runner = WalkForwardRunner(train_days=100, test_days=50)
        summary = runner.run(dates, run_fn=_dummy_run_fn)
        for w in summary.windows:
            assert w.train_end < w.test_start

    def test_invalid_train_days(self):
        with pytest.raises(ValueError, match="train_days must be >= 1"):
            WalkForwardRunner(train_days=0, test_days=50)

    def test_invalid_test_days(self):
        with pytest.raises(ValueError, match="test_days must be >= 1"):
            WalkForwardRunner(train_days=100, test_days=0)


# ── WalkForwardSummary ───────────────────────────────────────────────────

class TestWalkForwardSummary:
    def _make_summary(self) -> WalkForwardSummary:
        dates = _make_dates(400)
        runner = WalkForwardRunner(train_days=100, test_days=50)
        return runner.run(dates, run_fn=_dummy_run_fn)

    def test_mean_win_rate(self):
        s = self._make_summary()
        assert 0 < s.mean_win_rate <= 100

    def test_std_win_rate(self):
        s = self._make_summary()
        assert s.std_win_rate >= 0

    def test_mean_pnl(self):
        s = self._make_summary()
        assert s.mean_pnl_pct > 0

    def test_worst_drawdown(self):
        s = self._make_summary()
        assert s.worst_drawdown <= 0

    def test_mean_sharpe(self):
        s = self._make_summary()
        assert s.mean_sharpe > 0

    def test_empty_summary(self):
        s = WalkForwardSummary(windows=[], train_days=100, test_days=50)
        assert s.n_windows == 0
        assert s.mean_win_rate == 0.0
        assert s.std_win_rate == 0.0
        assert s.mean_pnl_pct == 0.0
        assert s.worst_drawdown == 0.0
        assert s.mean_sharpe == 0.0

    def test_single_window_std(self):
        dates = _make_dates(200)
        runner = WalkForwardRunner(train_days=100, test_days=50)
        s = runner.run(dates, run_fn=_dummy_run_fn)
        assert s.std_win_rate == 0.0

    def test_print_report(self, capsys):
        s = self._make_summary()
        s.print_report()
        captured = capsys.readouterr()
        assert "WALK-FORWARD ANALYSIS" in captured.out
        assert "Train window:" in captured.out
        assert "Win%" in captured.out
        assert "AGG" in captured.out

    def test_save(self, tmp_path):
        s = self._make_summary()
        out = s.save(str(tmp_path))
        assert (out / "summary.json").exists()
        assert (out / "windows.csv").exists()

        summary_data = json.loads((out / "summary.json").read_text())
        assert "mean_win_rate" in summary_data
        assert "n_windows" in summary_data
        assert summary_data["n_windows"] == s.n_windows

    def test_save_creates_dirs(self, tmp_path):
        s = self._make_summary()
        out = s.save(str(tmp_path / "nested" / "output"))
        assert out.exists()
