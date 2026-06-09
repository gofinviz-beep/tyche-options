"""Tests for estimate snapshot audit job."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from tyche.config import TycheSettings
from tyche.market_data.estimate_snapshot_store import EstimateSnapshotStore
from tyche.ops.audit_snapshots import _audit_ticker, run_audit_snapshots
from tyche.storage import read_json
from tyche.storage.paths import StorageContext


def _local_ctx(tmp_path: Path) -> StorageContext:
    return StorageContext(backend="local", local_root=tmp_path)


def _write_snapshots(tmp_path: Path, ticker: str) -> None:
    store = EstimateSnapshotStore(data_dir=str(tmp_path), ctx=_local_ctx(tmp_path))
    df = pd.DataFrame(
        [
            {
                "ticker": ticker,
                "vendor_symbol": ticker,
                "vendor": "finnhub",
                "metric": "eps",
                "snapshot_date": date(2026, 5, 20),
                "freq": "quarterly",
                "period": "2026-Q2",
                "fiscal_year": 2026,
                "fiscal_quarter": 2,
                "estimate_avg": 0.9,
                "estimate_high": 1.0,
                "estimate_low": 0.8,
                "number_analysts": 9,
                "raw_payload_hash": "old",
                "source_endpoint": "test",
            },
            {
                "ticker": ticker,
                "vendor_symbol": ticker,
                "vendor": "finnhub",
                "metric": "eps",
                "snapshot_date": date(2026, 6, 1),
                "freq": "quarterly",
                "period": "2026-Q2",
                "fiscal_year": 2026,
                "fiscal_quarter": 2,
                "estimate_avg": 1.0,
                "estimate_high": 1.1,
                "estimate_low": 0.9,
                "number_analysts": 10,
                "raw_payload_hash": "abc",
                "source_endpoint": "test",
            },
            {
                "ticker": ticker,
                "vendor_symbol": ticker,
                "vendor": "finnhub",
                "metric": "eps",
                "snapshot_date": date(2026, 6, 5),
                "freq": "quarterly",
                "period": "2026-Q2",
                "fiscal_year": 2026,
                "fiscal_quarter": 2,
                "estimate_avg": 1.2,
                "estimate_high": 1.3,
                "estimate_low": 1.1,
                "number_analysts": 11,
                "raw_payload_hash": "def",
                "source_endpoint": "test",
            },
        ]
    )
    store.write_snapshots(ticker, df)


class TestAuditTicker:
    def test_revision_populated(self, tmp_path: Path) -> None:
        _write_snapshots(tmp_path, "MU")
        ctx = _local_ctx(tmp_path)
        store = EstimateSnapshotStore(data_dir=str(tmp_path), ctx=ctx)
        result = _audit_ticker(store, "MU", date(2026, 6, 7))
        assert result["snapshot_dates"] == 3
        assert result["root_cause"] == "revision_populated"

    def test_metric_missing(self, tmp_path: Path) -> None:
        store = EstimateSnapshotStore(data_dir=str(tmp_path), ctx=_local_ctx(tmp_path))
        result = _audit_ticker(store, "MISSING", date.today())
        assert result["root_cause"] == "metric_missing"


class TestRunAuditSnapshots:
    def test_writes_reports_and_manifest(self, tmp_path: Path) -> None:
        _write_snapshots(tmp_path, "MU")
        ctx = _local_ctx(tmp_path)
        settings = TycheSettings(
            tradier_api_token="x",
            tradier_account_id="x",
            gemini_api_key="x",
            data_dir=str(tmp_path),
        )
        summary = run_audit_snapshots(
            settings=settings,
            ctx=ctx,
            tickers=["MU"],
            as_of=date(2026, 6, 7),
        )
        health = read_json(summary["health_report"], ctx=ctx)
        assert health["estimate_snapshot_files"] == 1
        assert summary["status"] == "success"
        cadence = read_json(summary["cadence_report"], ctx=ctx)
        assert cadence["tickers_audited"] == 1
        assert cadence["revision_populated"] == 1
