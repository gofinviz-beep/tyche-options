"""Per-ticker Parquet store for precomputed Stock Deep Dive payloads.

Storage layout: ``signals/stocks/deep_dive/{TICKER}.parquet`` — ONE file per
ticker, single row. This is intentionally NOT a monolithic universe file
(unlike ``signals/stocks/conviction.parquet``) because the per-ticker payload
is large (price history, volume bars, fundamentals, estimates, catalysts) and
v3's screener index depends on this per-ticker layout.

Each file stores the fully-serialized ``TickerDeepDiveResponse`` as a JSON
string column (``payload_json``) rather than nested-array Parquet columns —
this sidesteps fragile nested-array Parquet schemas and is schema-evolution
safe (adding a field to the response never requires a Parquet migration).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import structlog

from tyche.schemas.deep_dive import TickerDeepDiveResponse
from tyche.storage.paths import StorageContext
from tyche.storage.store_io import StoreBackend

logger = structlog.get_logger()

DEEP_DIVE_REL = "signals/stocks/deep_dive"


class DeepDiveStore:
    """Manages per-ticker Parquet files of precomputed deep-dive payloads.

    Layout: ``signals/stocks/deep_dive/{TICKER}.parquet``. Each file holds a
    single row (overwritten on every write — this is a latest-snapshot store,
    not an append-only history).
    """

    def __init__(
        self,
        data_dir: str = "data",
        ctx: StorageContext | None = None,
    ) -> None:
        self._io = StoreBackend.create(
            DEEP_DIVE_REL, data_dir, ctx, upper_stems=True
        )

    @property
    def ctx(self) -> StorageContext:
        return self._io.ctx

    def write_ticker(
        self,
        ticker: str,
        payload: TickerDeepDiveResponse,
        *,
        computed_at: datetime | None = None,
    ) -> None:
        """Persist a single ticker's precomputed payload (overwrites prior row)."""
        ticker = ticker.upper().strip()
        computed = computed_at or datetime.now(timezone.utc)
        row = {
            "ticker": ticker,
            "as_of_date": payload.as_of_date,
            "computed_at": computed.isoformat(),
            "payload_json": payload.model_dump_json(),
        }
        df = pd.DataFrame([row])
        self._io.write_df(self._io.ticker_rel(ticker), df)

    def write_batch(
        self,
        payloads: dict[str, TickerDeepDiveResponse],
        *,
        computed_at: datetime | None = None,
    ) -> int:
        """Write one Parquet file per ticker. Returns the number of tickers written."""
        if not payloads:
            return 0
        computed = computed_at or datetime.now(timezone.utc)
        written = 0
        for ticker, payload in payloads.items():
            try:
                self.write_ticker(ticker, payload, computed_at=computed)
                written += 1
            except Exception:
                logger.error("deep_dive_store_write_failed", ticker=ticker, exc_info=True)
        logger.info("deep_dive_batch_written", tickers=written)
        return written

    def read_ticker(
        self, ticker: str
    ) -> tuple[TickerDeepDiveResponse, str] | None:
        """Return ``(payload, as_of_date)`` for *ticker*, or ``None`` if missing."""
        ticker = ticker.upper().strip()
        rel = self._io.ticker_rel(ticker)
        df = self._io.read_df(rel)
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        payload_json = row.get("payload_json")
        if not payload_json:
            return None
        try:
            payload = TickerDeepDiveResponse.model_validate_json(payload_json)
        except Exception:
            logger.error("deep_dive_store_parse_failed", ticker=ticker, exc_info=True)
            return None
        as_of_date = str(row.get("as_of_date") or "")
        return payload, as_of_date

    def get_all_tickers(self) -> list[str]:
        return self._io.list_ticker_stems()

    def get_stats(self) -> dict:
        tickers = self.get_all_tickers()
        return {"ticker_count": len(tickers)}
