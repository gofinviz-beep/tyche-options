"""Per-ticker Parquet store for demand-catalyst / policy signals (D-CAT/D-POL).

Storage layout:
  data/catalyst_signals/{TICKER}.parquet — one file per ticker

Tidy long format — one row per discrete catalyst event observed for a ticker:
``(event_date, kind, tag, signed_impact, source)``. Populated from the news /
8-K classifier output (``demand_catalyst`` / ``policy_tag`` fields) via
``records_from_classification``. Recency-weighted aggregates feed the Demand
Conviction catalyst dimension.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import structlog

from tyche.analysis.catalyst_taxonomy import (
    signed_catalyst_impact,
    signed_policy_impact,
)

logger = structlog.get_logger()

CATALYST_SCHEMA = pa.schema(
    [
        ("ticker", pa.string()),
        ("event_date", pa.date32()),
        ("kind", pa.string()),  # demand | policy
        ("tag", pa.string()),  # catalyst/policy taxonomy key
        ("signed_impact", pa.float64()),  # polarity-adjusted, [-1, 1]
        ("source", pa.string()),  # news | 8k
        ("ref_id", pa.string()),  # article_id / accession_no (dedup key)
    ]
)

_HALF_LIFE_DAYS = 30.0  # recency weight half-life for demand catalysts


def records_from_classification(
    ticker: str,
    event_date: date,
    demand_catalyst: str,
    policy_tag: str,
    impact_score: float,
    source: str,
    ref_id: str,
) -> list[dict]:
    """Convert one classifier output into 0-2 catalyst rows (demand + policy)."""
    rows: list[dict] = []
    di = signed_catalyst_impact(demand_catalyst, impact_score)
    if di != 0.0:
        rows.append(
            {
                "ticker": ticker.upper(),
                "event_date": event_date,
                "kind": "demand",
                "tag": demand_catalyst,
                "signed_impact": di,
                "source": source,
                "ref_id": ref_id,
            }
        )
    pi = signed_policy_impact(policy_tag, impact_score)
    if pi != 0.0:
        rows.append(
            {
                "ticker": ticker.upper(),
                "event_date": event_date,
                "kind": "policy",
                "tag": policy_tag,
                "signed_impact": pi,
                "source": source,
                "ref_id": ref_id,
            }
        )
    return rows


class CatalystSignalStore:
    """Per-ticker demand-catalyst / policy event store."""

    def __init__(self, data_dir: str = "data") -> None:
        self._store_dir = Path(data_dir) / "catalyst_signals"
        self._store_dir.mkdir(parents=True, exist_ok=True)

    @property
    def store_dir(self) -> Path:
        return self._store_dir

    def _ticker_path(self, ticker: str) -> Path:
        safe = ticker.upper().replace("/", "_").replace(" ", "_")
        return self._store_dir / f"{safe}.parquet"

    def write_records(self, ticker: str, df: pd.DataFrame) -> int:
        """Persist catalyst rows for a ticker. Dedupes on (event_date, kind, ref_id)."""
        if df is None or df.empty:
            return 0
        df = df.copy()
        df["event_date"] = pd.to_datetime(df["event_date"]).dt.date
        df["ticker"] = ticker.upper()
        for col in ("kind", "tag", "source", "ref_id"):
            if col not in df.columns:
                df[col] = ""
            df[col] = df[col].fillna("").astype(str)
        df["signed_impact"] = pd.to_numeric(df["signed_impact"], errors="coerce")
        df = df.dropna(subset=["signed_impact"])
        if df.empty:
            return 0

        path = self._ticker_path(ticker)
        if path.exists():
            existing = pd.read_parquet(path)
            existing["event_date"] = pd.to_datetime(existing["event_date"]).dt.date
            combined = pd.concat([existing, df], ignore_index=True)
        else:
            combined = df

        combined = (
            combined.drop_duplicates(subset=["event_date", "kind", "ref_id"], keep="last")
            .sort_values("event_date")
            .reset_index(drop=True)
        )
        ordered = combined[[f.name for f in CATALYST_SCHEMA]]
        table = pa.Table.from_pandas(ordered, schema=CATALYST_SCHEMA, preserve_index=False)
        pq.write_table(table, path, compression="snappy")
        return len(combined)

    def read_ticker(self, ticker: str, as_of: date | None = None) -> pd.DataFrame:
        path = self._ticker_path(ticker)
        if not path.exists():
            return pd.DataFrame(columns=[f.name for f in CATALYST_SCHEMA])
        df = pd.read_parquet(path)
        df["event_date"] = pd.to_datetime(df["event_date"]).dt.date
        if as_of is not None:
            df = df[df["event_date"] <= as_of]
        return df.sort_values("event_date").reset_index(drop=True)

    def aggregate(
        self,
        ticker: str,
        as_of: date,
        lookback_days: int = 180,
    ) -> dict[str, float]:
        """Recency-weighted catalyst aggregates as of *as_of*.

        Returns ``cat_demand_score`` (recency-weighted signed demand impact,
        ~[-1, 1]), ``cat_policy_score`` (news-derived policy), ``cat_count_90d``
        (number of demand catalysts in 90d), and ``cat_recency_days`` (days
        since the most recent demand catalyst).
        """
        df = self.read_ticker(ticker, as_of=as_of)
        out = {
            "cat_demand_score": 0.0,
            "cat_policy_score": 0.0,
            "cat_count_90d": 0.0,
            "cat_recency_days": float("nan"),
        }
        if df.empty:
            return out

        cutoff = as_of - pd.Timedelta(days=lookback_days)
        df = df[pd.to_datetime(df["event_date"]) >= pd.Timestamp(cutoff)]
        if df.empty:
            return out

        df = df.copy()
        df["age"] = (pd.Timestamp(as_of) - pd.to_datetime(df["event_date"])).dt.days
        df["w"] = df["age"].apply(lambda d: math.exp(-math.log(2) * max(d, 0) / _HALF_LIFE_DAYS))

        demand = df[df["kind"] == "demand"]
        policy = df[df["kind"] == "policy"]

        if not demand.empty:
            wsum = demand["w"].sum()
            if wsum > 0:
                out["cat_demand_score"] = float(
                    (demand["signed_impact"] * demand["w"]).sum() / wsum
                )
            recent = demand[demand["age"] <= 90]
            out["cat_count_90d"] = float(len(recent))
            out["cat_recency_days"] = float(demand["age"].min())

        if not policy.empty:
            wsum = policy["w"].sum()
            if wsum > 0:
                out["cat_policy_score"] = float(
                    (policy["signed_impact"] * policy["w"]).sum() / wsum
                )
        return out

    def get_all_tickers(self) -> list[str]:
        return sorted(
            p.stem.upper()
            for p in self._store_dir.glob("*.parquet")
            if not p.name.startswith("_")
        )
