"""Unit tests for the per-ticker Stock Deep Dive Parquet store."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from tyche.analysis.ticker_deep_dive import TickerDeepDiveEngine
from tyche.market_data.deep_dive_store import DEEP_DIVE_REL, DeepDiveStore
from tyche.schemas.deep_dive import TickerDeepDiveResponse, to_response


def _make_ohlcv(n: int = 300, base_price: float = 100.0) -> pd.DataFrame:
    np.random.seed(7)
    dates = pd.bdate_range(end=date.today(), periods=n)
    n = len(dates)  # bdate_range can return fewer periods depending on weekday alignment
    trend = np.linspace(base_price * 0.7, base_price, n)
    noise = np.random.normal(0, base_price * 0.01, n)
    closes = trend + noise
    highs = closes * (1 + np.abs(np.random.normal(0, 0.005, n)))
    lows = closes * (1 - np.abs(np.random.normal(0, 0.005, n)))
    opens = closes * (1 + np.random.normal(0, 0.003, n))
    volumes = np.random.randint(500_000, 5_000_000, n).astype(float)
    return pd.DataFrame(
        {
            "date": dates.date,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )


def _make_payload(ticker: str = "TEST") -> TickerDeepDiveResponse:
    """Build a realistic response via the real engine + shared serializer."""
    ohlcv_store = MagicMock()
    ohlcv_store.read_ticker.return_value = _make_ohlcv()

    meta_store = MagicMock()
    meta_store.get_meta_batch.return_value = {
        ticker: {"name": "Test Inc.", "sector": "Technology", "market_cap": 50e9}
    }
    meta_store.get_institutional_pcts.return_value = {ticker: 0.65}

    fundamentals_store = MagicMock()
    today = date.today()
    fundamentals_store.read_ticker.return_value = pd.DataFrame(
        [
            {
                "ticker": ticker,
                "period_end": today - timedelta(days=90),
                "filing_date": today - timedelta(days=60),
                "fiscal_year": 2026,
                "fiscal_period": "Q1",
                "timeframe": "quarterly",
                "revenue": 5e9,
                "gross_profit": 3e9,
                "gross_margin": 46.88,
                "operating_income": 1.5e9,
                "operating_margin": 30.0,
                "net_income": 1e9,
                "net_margin": 20.0,
                "eps_diluted": 2.50,
                "cash_and_equivalents": 10e9,
                "operating_cash_flow": 2e9,
                "total_debt": 3e9,
            }
        ]
    )

    estimates_store = MagicMock()
    estimates_store.read_ticker.return_value = pd.DataFrame(
        [
            {
                "ticker": ticker,
                "snapshot_date": today,
                "metric": "price_target_mean",
                "period": "",
                "value": 120.0,
            },
            {
                "ticker": ticker,
                "snapshot_date": today,
                "metric": "fin_gross_margin_ttm",
                "period": "",
                "value": 46.88,
            },
        ]
    )

    catalyst_store = MagicMock()
    catalyst_store.read_ticker.return_value = pd.DataFrame(
        [
            {
                "ticker": ticker,
                "event_date": today - timedelta(days=5),
                "kind": "demand",
                "tag": "revenue_beat",
                "signed_impact": 0.8,
                "source": "news",
                "ref_id": "a1",
            }
        ]
    )

    engine = TickerDeepDiveEngine(
        ohlcv_store=ohlcv_store,
        meta_store=meta_store,
        fundamentals_store=fundamentals_store,
        estimates_store=estimates_store,
        catalyst_store=catalyst_store,
    )
    result = engine.analyze(ticker)
    return to_response(result)


class TestWriteReadRoundTrip:
    def test_write_then_read_ticker(self, tmp_path):
        store = DeepDiveStore(data_dir=str(tmp_path))
        payload = _make_payload("AAPL")

        store.write_ticker("AAPL", payload)
        loaded = store.read_ticker("AAPL")

        assert loaded is not None
        loaded_payload, as_of = loaded
        assert as_of == payload.as_of_date
        assert loaded_payload.ticker == "AAPL"

    def test_round_trip_is_model_fidelity(self, tmp_path):
        """model_dump_json <-> model_validate_json preserves exact field values."""
        store = DeepDiveStore(data_dir=str(tmp_path))
        payload = _make_payload("MSFT")

        store.write_ticker("MSFT", payload)
        loaded_payload, _ = store.read_ticker("MSFT")

        assert loaded_payload.model_dump(mode="json") == payload.model_dump(mode="json")

    def test_percent_scale_margins_survive_round_trip(self, tmp_path):
        """Margins must stay percent-scale (e.g. 46.88, not 0.4688 or 4688)."""
        store = DeepDiveStore(data_dir=str(tmp_path))
        payload = _make_payload("RKLB")

        store.write_ticker("RKLB", payload)
        loaded_payload, _ = store.read_ticker("RKLB")

        assert loaded_payload.fundamentals[0].gross_margin == pytest.approx(46.88)
        assert loaded_payload.estimates.gross_margin_ttm == pytest.approx(46.88)

    def test_write_batch_writes_one_file_per_ticker(self, tmp_path):
        store = DeepDiveStore(data_dir=str(tmp_path))
        payloads = {
            "AAPL": _make_payload("AAPL"),
            "MSFT": _make_payload("MSFT"),
            "RKLB": _make_payload("RKLB"),
        }

        written = store.write_batch(payloads)

        assert written == 3
        deep_dive_dir = tmp_path / DEEP_DIVE_REL
        files = sorted(p.name for p in deep_dive_dir.glob("*.parquet"))
        assert files == ["AAPL.parquet", "MSFT.parquet", "RKLB.parquet"]

    def test_write_batch_empty_dict_returns_zero(self, tmp_path):
        store = DeepDiveStore(data_dir=str(tmp_path))
        assert store.write_batch({}) == 0


class TestMissingTicker:
    def test_read_missing_ticker_returns_none(self, tmp_path):
        store = DeepDiveStore(data_dir=str(tmp_path))
        assert store.read_ticker("NOPE") is None

    def test_read_ticker_with_empty_payload_json_returns_none(self, tmp_path):
        store = DeepDiveStore(data_dir=str(tmp_path))
        df = pd.DataFrame(
            [
                {
                    "ticker": "EMPTY",
                    "as_of_date": "2026-01-01",
                    "computed_at": "2026-01-01T00:00:00+00:00",
                    "payload_json": "",
                }
            ]
        )
        store._io.write_df(store._io.ticker_rel("EMPTY"), df)
        assert store.read_ticker("EMPTY") is None


class TestOneFilePerTickerLayout:
    def test_ticker_rel_is_scoped_under_deep_dive_dir(self, tmp_path):
        store = DeepDiveStore(data_dir=str(tmp_path))
        rel = store._io.ticker_rel("AAPL")
        assert rel == f"{DEEP_DIVE_REL}/AAPL.parquet"

    def test_no_monolithic_file_written(self, tmp_path):
        """Batch write must never produce a single universe-wide Parquet file."""
        store = DeepDiveStore(data_dir=str(tmp_path))
        store.write_batch({"AAPL": _make_payload("AAPL"), "MSFT": _make_payload("MSFT")})

        deep_dive_dir = tmp_path / DEEP_DIVE_REL
        monolithic = deep_dive_dir / "deep_dive.parquet"
        assert not monolithic.exists()
        # Every file under the dir is a per-ticker file (2 tickers -> 2 files).
        assert len(list(deep_dive_dir.glob("*.parquet"))) == 2

    def test_get_all_tickers(self, tmp_path):
        store = DeepDiveStore(data_dir=str(tmp_path))
        store.write_batch({"AAPL": _make_payload("AAPL"), "MSFT": _make_payload("MSFT")})
        tickers = store.get_all_tickers()
        assert set(tickers) == {"AAPL", "MSFT"}

    def test_get_stats(self, tmp_path):
        store = DeepDiveStore(data_dir=str(tmp_path))
        store.write_batch({"AAPL": _make_payload("AAPL")})
        stats = store.get_stats()
        assert stats["ticker_count"] == 1
