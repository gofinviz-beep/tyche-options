"""Audit local Finnhub consensus snapshot cadence and revision availability."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, "src")

import pandas as pd

from tyche.market_data.estimate_snapshot_store import EstimateSnapshotStore

_HORIZONS = (7, 14, 30, 90)


def _audit_ticker(store: EstimateSnapshotStore, ticker: str, as_of: date) -> dict:
    df = store.read_ticker(ticker, as_of=as_of)
    if df.empty:
        return {
            "ticker": ticker,
            "snapshot_dates": 0,
            "root_cause": "metric_missing",
        }

    out: dict = {
        "ticker": ticker,
        "snapshot_dates": int(df["snapshot_date"].nunique()),
        "metrics": sorted(df["metric"].unique().tolist()),
        "periods_with_multiple_snapshots": 0,
        "same_period_prior_at_horizons": {str(h): 0 for h in _HORIZONS},
    }

    for metric in df["metric"].unique():
        mdf = df[df["metric"] == metric]
        for period, grp in mdf.groupby("period"):
            snaps = sorted(grp["snapshot_date"].unique())
            if len(snaps) > 1:
                out["periods_with_multiple_snapshots"] += 1
            latest = grp[grp["snapshot_date"] == snaps[-1]].iloc[0]
            est_now = latest.get("estimate_avg")
            for h in _HORIZONS:
                prior_cut = as_of - timedelta(days=h)
                prior_rows = grp[grp["snapshot_date"] <= prior_cut]
                if prior_rows.empty or est_now is None or pd.isna(est_now):
                    continue
                prior_val = prior_rows.iloc[-1].get("estimate_avg")
                if prior_val is not None and not pd.isna(prior_val):
                    out["same_period_prior_at_horizons"][str(h)] += 1

    if out["snapshot_dates"] < 2:
        out["root_cause"] = "too_few_snapshots_for_revision"
    elif out["periods_with_multiple_snapshots"] == 0:
        out["root_cause"] = "same_period_prior_missing"
    elif any(v > 0 for v in out["same_period_prior_at_horizons"].values()):
        out["root_cause"] = "revision_populated"
    else:
        out["root_cause"] = "same_period_prior_missing"

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Estimate snapshot cadence audit")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--tickers", nargs="+", default=["MU", "AVGO", "STX"])
    parser.add_argument(
        "--output",
        default="data/ml/alpha_results/estimate_snapshot_cadence_v1.json",
    )
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD (default today)")
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    store = EstimateSnapshotStore(data_dir=args.data_dir)
    rows = [_audit_ticker(store, t.upper(), as_of) for t in args.tickers]

    report = {"as_of": as_of.isoformat(), "tickers": rows}
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
