"""Purged walk-forward split tests."""

from __future__ import annotations

from datetime import date, timedelta

from tyche.ml.validation import parse_label_horizon_days, purged_walk_forward_splits


def test_parse_label_horizon_days() -> None:
    assert parse_label_horizon_days("big_move_sustained_60pct_120d") == 120
    assert parse_label_horizon_days("csp_win_5d") == 5
    assert parse_label_horizon_days("direction_5d") == 5


def test_purged_splits_respect_embargo() -> None:
    start = date(2024, 1, 1)
    all_dates = [start + timedelta(days=i) for i in range(400)]
    splits = purged_walk_forward_splits(
        all_dates,
        train_days=60,
        test_days=20,
        embargo_days=10,
        step_days=20,
    )
    assert splits
    for train_dates, test_dates in splits:
        assert max(train_dates) < min(test_dates)
        train_end_idx = all_dates.index(max(train_dates))
        test_start_idx = all_dates.index(min(test_dates))
        assert test_start_idx - train_end_idx - 1 >= 10
