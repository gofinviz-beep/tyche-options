"""EstimateSnapshotStore point-in-time persistence."""

from __future__ import annotations

from datetime import date

import pandas as pd

from tyche.market_data.estimate_snapshot_store import EstimateSnapshotStore


def test_preserves_distinct_snapshot_dates(tmp_path) -> None:
    store = EstimateSnapshotStore(data_dir=str(tmp_path))
    row = {
        "vendor_symbol": "MU",
        "vendor": "finnhub",
        "metric": "eps",
        "snapshot_date": date(2026, 5, 1),
        "freq": "quarterly",
        "period": "2026-06-30",
        "fiscal_year": 2026,
        "fiscal_quarter": 2,
        "estimate_avg": 5.0,
        "estimate_high": 5.5,
        "estimate_low": 4.5,
        "number_analysts": 10,
        "raw_payload_hash": "abc",
        "source_endpoint": "/stock/eps-estimate",
    }
    store.write_snapshots("MU", pd.DataFrame([row]))
    row2 = {**row, "snapshot_date": date(2026, 6, 1), "estimate_avg": 5.6}
    store.write_snapshots("MU", pd.DataFrame([row2]))

    df = store.read_ticker("MU")
    assert df["snapshot_date"].nunique() == 2
    latest = df[df["snapshot_date"] == date(2026, 6, 1)].iloc[0]
    assert latest["estimate_avg"] == 5.6
