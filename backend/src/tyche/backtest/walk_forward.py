"""Walk-forward analysis harness for backtest stability testing.

Splits the full backtest date range into rolling *train + test* windows
and runs the supplied backtest function on each window independently.
Collects per-window metrics and computes aggregate stability statistics.

Usage from a backtest script::

    from tyche.backtest.walk_forward import WalkForwardRunner, WindowResult

    def run_window(dates, ticker_frames, **ctx) -> WindowResult:
        # ... run backtest on dates, return WindowResult ...

    runner = WalkForwardRunner(
        train_days=126,  # 6 months
        test_days=63,    # 3 months
    )
    summary = runner.run(all_dates, run_fn=run_window, ticker_frames=frames)
    summary.print_report()
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable


@dataclass
class WindowResult:
    """Metrics produced by one train+test window."""

    window_id: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    total_trades: int = 0
    wins: int = 0
    win_rate: float = 0.0
    avg_pnl_pct: float = 0.0
    cumulative_pnl_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def losses(self) -> int:
        return self.total_trades - self.wins


@dataclass
class WalkForwardSummary:
    """Aggregate results across all walk-forward windows."""

    windows: list[WindowResult]
    train_days: int
    test_days: int

    @property
    def n_windows(self) -> int:
        return len(self.windows)

    @property
    def mean_win_rate(self) -> float:
        if not self.windows:
            return 0.0
        rates = [w.win_rate for w in self.windows if w.total_trades > 0]
        return sum(rates) / len(rates) if rates else 0.0

    @property
    def std_win_rate(self) -> float:
        if not self.windows:
            return 0.0
        rates = [w.win_rate for w in self.windows if w.total_trades > 0]
        if len(rates) < 2:
            return 0.0
        mean = self.mean_win_rate
        return math.sqrt(sum((r - mean) ** 2 for r in rates) / (len(rates) - 1))

    @property
    def mean_pnl_pct(self) -> float:
        if not self.windows:
            return 0.0
        vals = [w.avg_pnl_pct for w in self.windows if w.total_trades > 0]
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def worst_drawdown(self) -> float:
        if not self.windows:
            return 0.0
        return min(w.max_drawdown_pct for w in self.windows)

    @property
    def mean_sharpe(self) -> float:
        if not self.windows:
            return 0.0
        vals = [w.sharpe for w in self.windows if w.total_trades > 0]
        return sum(vals) / len(vals) if vals else 0.0

    def print_report(self) -> None:
        """Print a human-readable walk-forward summary to stdout."""
        print("\n" + "=" * 90)
        print("WALK-FORWARD ANALYSIS")
        print(f"Train window: {self.train_days} days | Test window: {self.test_days} days")
        print(f"Windows: {self.n_windows}")
        print("=" * 90)

        header = (
            f"{'#':>3s}  {'Test Start':>11s}  {'Test End':>11s}  "
            f"{'Trades':>7s}  {'Win%':>6s}  {'AvgP&L':>8s}  "
            f"{'MaxDD':>8s}  {'Sharpe':>7s}"
        )
        print(header)
        print("-" * 90)

        for w in self.windows:
            print(
                f"{w.window_id:>3d}  {w.test_start.isoformat():>11s}  "
                f"{w.test_end.isoformat():>11s}  "
                f"{w.total_trades:>7d}  {w.win_rate:>5.1f}%  "
                f"{w.avg_pnl_pct:>+7.2f}%  "
                f"{w.max_drawdown_pct:>+7.2f}%  "
                f"{w.sharpe:>7.2f}"
            )

        print("-" * 90)
        print(
            f"{'AGG':>3s}  {'':>11s}  {'':>11s}  "
            f"{'':>7s}  {self.mean_win_rate:>5.1f}%  "
            f"{self.mean_pnl_pct:>+7.2f}%  "
            f"{self.worst_drawdown:>+7.2f}%  "
            f"{self.mean_sharpe:>7.2f}"
        )
        print(f"\nWin-rate stability (σ): {self.std_win_rate:.2f}%")

    def save(self, output_dir: str | Path) -> Path:
        """Persist walk-forward results to a structured output folder.

        Creates ``output_dir/walk_forward/`` with:
        - ``summary.json`` — aggregate stats
        - ``windows.csv``  — per-window row data

        Returns:
            Path to the created directory.
        """
        out = Path(output_dir) / "walk_forward"
        out.mkdir(parents=True, exist_ok=True)

        summary_data = {
            "train_days": self.train_days,
            "test_days": self.test_days,
            "n_windows": self.n_windows,
            "mean_win_rate": round(self.mean_win_rate, 4),
            "std_win_rate": round(self.std_win_rate, 4),
            "mean_pnl_pct": round(self.mean_pnl_pct, 4),
            "worst_drawdown": round(self.worst_drawdown, 4),
            "mean_sharpe": round(self.mean_sharpe, 4),
        }
        (out / "summary.json").write_text(json.dumps(summary_data, indent=2, default=str))

        import csv

        csv_path = out / "windows.csv"
        if self.windows:
            fieldnames = [
                "window_id", "train_start", "train_end", "test_start", "test_end",
                "total_trades", "wins", "win_rate", "avg_pnl_pct",
                "cumulative_pnl_pct", "max_drawdown_pct", "sharpe",
            ]
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for w in self.windows:
                    row = {k: getattr(w, k) for k in fieldnames}
                    writer.writerows([row])

        return out


class WalkForwardRunner:
    """Splits dates into rolling train+test windows and invokes a callback.

    The callback signature must be::

        def run_fn(
            train_dates: list[date],
            test_dates: list[date],
            **context,
        ) -> WindowResult

    Any additional keyword arguments passed to :meth:`run` are forwarded to
    the callback as ``context``.
    """

    def __init__(
        self,
        train_days: int = 126,
        test_days: int = 63,
        step_days: int | None = None,
    ) -> None:
        if train_days < 1:
            raise ValueError(f"train_days must be >= 1, got {train_days}")
        if test_days < 1:
            raise ValueError(f"test_days must be >= 1, got {test_days}")

        self.train_days = train_days
        self.test_days = test_days
        self.step_days = step_days or test_days  # non-overlapping by default

    def run(
        self,
        all_dates: list[date],
        run_fn: Callable[..., WindowResult],
        **context: Any,
    ) -> WalkForwardSummary:
        """Execute the walk-forward analysis.

        Args:
            all_dates: Sorted list of all trading dates.
            run_fn: Callback that runs a backtest on one window.
            **context: Extra kwargs forwarded to *run_fn*.

        Returns:
            :class:`WalkForwardSummary` with per-window and aggregate metrics.

        Raises:
            ValueError: Not enough dates for even one window.
        """
        min_required = self.train_days + self.test_days
        if len(all_dates) < min_required:
            raise ValueError(
                f"Need at least {min_required} dates for walk-forward "
                f"(train={self.train_days} + test={self.test_days}), "
                f"got {len(all_dates)}"
            )

        windows: list[WindowResult] = []
        window_id = 0
        start = 0

        while start + self.train_days + self.test_days <= len(all_dates):
            train_start_idx = start
            train_end_idx = start + self.train_days
            test_start_idx = train_end_idx
            test_end_idx = min(test_start_idx + self.test_days, len(all_dates))

            train_dates = all_dates[train_start_idx:train_end_idx]
            test_dates = all_dates[test_start_idx:test_end_idx]

            result = run_fn(
                train_dates=train_dates,
                test_dates=test_dates,
                **context,
            )
            result.window_id = window_id
            result.train_start = train_dates[0]
            result.train_end = train_dates[-1]
            result.test_start = test_dates[0]
            result.test_end = test_dates[-1]

            windows.append(result)
            window_id += 1
            start += self.step_days

        return WalkForwardSummary(
            windows=windows,
            train_days=self.train_days,
            test_days=self.test_days,
        )
