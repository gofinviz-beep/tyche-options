"""IntradayStore init after _MetadataCache ctx migration."""

from __future__ import annotations

from tyche.market_data.data_store import IntradayStore


def test_intraday_store_init_local(tmp_path) -> None:
    store = IntradayStore(data_dir=str(tmp_path))
    assert store.store_dir.exists()
    assert store.get_ticker_count() == 0
