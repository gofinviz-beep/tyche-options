"""Walk-forward split utilities with label-horizon embargo (purged CV)."""

from __future__ import annotations

import re
from datetime import date


def parse_label_horizon_days(target: str) -> int | None:
    """Parse trailing ``_{N}d`` suffix from a label column name."""
    m = re.search(r"_(\d+)d$", target)
    if not m:
        return None
    return int(m.group(1))


def purged_walk_forward_splits(
    all_dates: list[date],
    train_days: int,
    test_days: int,
    embargo_days: int,
    step_days: int | None = None,
) -> list[tuple[list[date], list[date]]]:
    """Yield ``(train_dates, test_dates)`` windows with an embargo gap.

    Invariant: ``max(train_dates)`` is strictly before ``min(test_dates)`` by at
    least ``embargo_days`` trading days (gap counted as dates between train end
    index and test start index).
    """
    if train_days < 1 or test_days < 1 or embargo_days < 0:
        return []

    step_days = step_days or test_days
    splits: list[tuple[list[date], list[date]]] = []
    start = 0

    while True:
        train_end_idx = start + train_days - 1
        test_start_idx = train_end_idx + 1 + embargo_days
        test_end_idx = test_start_idx + test_days - 1
        if test_end_idx >= len(all_dates):
            break

        train_dates = all_dates[start : start + train_days]
        test_dates = all_dates[test_start_idx : test_start_idx + test_days]
        if train_dates and test_dates:
            splits.append((train_dates, test_dates))

        start += step_days

    return splits
