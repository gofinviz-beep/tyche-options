"""Unit tests for the v3 Stock Screener index store."""

from __future__ import annotations

from tyche.market_data.screener_index_store import (
    SCREENER_INDEX_REL,
    ScreenerIndexStore,
    load_screener_rows,
)
from tyche.storage.paths import StorageContext

_COLUMNS = [
    "ticker",
    "name",
    "sector",
    "as_of_date",
    "last_close",
    "market_cap",
    "institutional_pct",
    "pct_off_52w_high",
    "rsi_daily",
    "rsi_weekly",
    "rsi_monthly",
    "rsi_quarterly",
    "ema_8",
    "ema_21",
    "ema_50",
    "sma_200",
    "pct_vs_ema_8",
    "pct_vs_ema_21",
    "pct_vs_sma_200",
    "slope_ema_8",
    "slope_ema_21",
    "slope_ema_50",
    "days_above_ema_8",
    "days_above_ema_21",
    "stack_score",
    "above_sma_200",
    "ret_1m",
    "ret_3m",
    "ret_6m",
    "ret_1y",
    "macd_histogram",
    "setup_score",
    "setup_label",
]


def _ctx(tmp_path) -> StorageContext:
    return StorageContext(backend="local", local_root=tmp_path)


def _row(ticker: str, **overrides) -> dict:
    row = {col: 0.0 for col in _COLUMNS}
    row["ticker"] = ticker
    row["name"] = f"{ticker} Inc"
    row["sector"] = "Technology"
    row["as_of_date"] = "2026-07-10"
    row["above_sma_200"] = True
    row["setup_label"] = "Prime Pullback"
    row.update(overrides)
    return row


class TestWriteReadRoundTrip:
    def test_write_then_read_round_trip(self, tmp_path):
        store = ScreenerIndexStore(ctx=_ctx(tmp_path))
        rows = [_row("AAPL"), _row("MSFT")]

        written = store.write(rows)
        assert written == 2

        df = store.read()
        assert df is not None
        assert len(df) == 2
        assert set(df["ticker"]) == {"AAPL", "MSFT"}

    def test_single_file_layout(self, tmp_path):
        """Storage layout is exactly one Parquet file, never per-ticker."""
        store = ScreenerIndexStore(ctx=_ctx(tmp_path))
        store.write([_row("AAPL"), _row("MSFT"), _row("NVDA")])

        parquet_files = list(tmp_path.rglob("*.parquet"))
        assert len(parquet_files) == 1
        assert parquet_files[0] == tmp_path / SCREENER_INDEX_REL

    def test_column_presence(self, tmp_path):
        store = ScreenerIndexStore(ctx=_ctx(tmp_path))
        store.write([_row("AAPL")])

        df = store.read()
        assert df is not None
        for col in _COLUMNS:
            assert col in df.columns
        assert "computed_at" in df.columns

    def test_write_overwrites_previous_snapshot(self, tmp_path):
        store = ScreenerIndexStore(ctx=_ctx(tmp_path))
        store.write([_row("AAPL"), _row("MSFT")])
        store.write([_row("NVDA")])

        df = store.read()
        assert df is not None
        assert list(df["ticker"]) == ["NVDA"]


class TestEmptyAndMissing:
    def test_read_returns_none_when_missing(self, tmp_path):
        store = ScreenerIndexStore(ctx=_ctx(tmp_path))
        assert store.read() is None
        assert store.exists is False

    def test_write_empty_rows_returns_zero(self, tmp_path):
        store = ScreenerIndexStore(ctx=_ctx(tmp_path))
        written = store.write([])
        assert written == 0
        assert store.read() is None


class TestLoadScreenerRows:
    def test_load_screener_rows_round_trip(self, tmp_path):
        ctx = _ctx(tmp_path)
        store = ScreenerIndexStore(ctx=ctx)
        store.write([_row("AAPL"), _row("MSFT")])

        records, as_of_date, computed_at = load_screener_rows(ctx=ctx)
        assert len(records) == 2
        assert as_of_date == "2026-07-10"
        assert computed_at is not None
        assert {r["ticker"] for r in records} == {"AAPL", "MSFT"}

    def test_load_screener_rows_missing_returns_empty(self, tmp_path):
        ctx = _ctx(tmp_path)
        records, as_of_date, computed_at = load_screener_rows(ctx=ctx)
        assert records == []
        assert as_of_date is None
        assert computed_at is None
