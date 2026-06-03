"""Estimate store vs feature revision columns — read-only coverage probe.

Diagnoses why ``e_eps_revision_90d`` / ``e_rev_revision_90d`` are empty using the
same point-in-time logic as ``add_estimate_features()`` in ``ml/features.py``.

Run from ``backend/``:
    .venv/bin/python scripts/audit_estimates_coverage.py --tickers MU AVGO
    .venv/bin/python scripts/audit_estimates_coverage.py --sample-size 250
    .venv/bin/python scripts/audit_estimates_coverage.py --inspect-finnhub-api
    .venv/bin/python scripts/audit_estimates_coverage.py --inspect-finnhub-api \\
        --tickers MU AVGO STX SNDK VRT
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tyche.config import get_settings  # noqa: E402
from tyche.market_data.alpha_store import AlphaSignalStore  # noqa: E402
from tyche.market_data.dual_class import finnhub_symbol_candidates  # noqa: E402
from tyche.market_data.estimates_store import EstimatesStore  # noqa: E402
from tyche.market_data.finnhub import FinnhubClient, _BASE_URL  # noqa: E402
from tyche.ml.dataset import build_latest_features  # noqa: E402
from tyche.ml.features import (  # noqa: E402
    _asof_col,
    _front_period_series,
    _pct_change_arr,
)

logger = structlog.get_logger()

KNOWN_WINNERS = ["MU", "AVGO", "SNDK", "STX", "ARM", "WDC", "CIEN", "LITE", "VRT", "FIX"]
FINNHUB_API_PROBE_TICKERS = ["MU", "AVGO", "STX", "SNDK", "VRT"]

_FINNHUB_DATE_ROW_KEYS = (
    "snapshot_date",
    "asOfDate",
    "as_of",
    "estimateDate",
    "estimate_date",
    "updatedAt",
    "updated",
    "lastUpdated",
    "last_updated",
    "revisionDate",
    "revision_date",
)

_FINNHUB_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("/stock/eps-estimate", "eps_estimate"),
    ("/stock/revenue-estimate", "revenue_estimate"),
)

_CLIENT_WRAPPER_DOC = {
    "method": "FinnhubClient.get_estimates(ticker, as_of=None, freq='quarterly')",
    "implementation": (
        "Calls FinnhubClient._safe_get(path, params) twice: "
        "/stock/eps-estimate and /stock/revenue-estimate."
    ),
    "params_sent_to_api": ["symbol", "freq"],
    "params_not_sent_to_api": [
        "as_of (ingest-time only; stamped as snapshot_date in EstimatesStore rows)",
    ],
    "official_optional_params_per_finnhub_docs": ["freq (quarterly|annual)"],
    "date_asof_from_to_in_client_wrapper": False,
}

# Priority when multiple paths disagree (most fundamental first).
_CAUSE_PRIORITY: tuple[str, ...] = (
    "revision_columns_populated",
    "revision_feature_assignment_bug",
    "no_estimate_store",
    "empty_estimate_store",
    "store_has_only_latest_snapshot",
    "missing_eps_rev_estimate_metrics",
    "missing_eps_est_avg_metrics",
    "missing_rev_est_avg_metrics",
    "front_period_selection_failure",
    "missing_current_estimate_value",
    "missing_90d_prior_estimate_value",
    "prior_value_zero_or_invalid",
    "feature_row_missing",
    "revision_population_unknown",
)


def _load_snapshot_tickers(data_dir: str) -> tuple[set[str], dict[str, float]]:
    for variant in ("sustained", "peak"):
        store = AlphaSignalStore(data_dir=data_dir, variant=variant)
        if store.exists:
            signals, _, _ = store.read_latest()
            if signals:
                scores: dict[str, float] = {}
                tickers: set[str] = set()
                for s in signals:
                    t = str(s.get("ticker", "")).upper()
                    if t:
                        tickers.add(t)
                        if s.get("alpha_score") is not None:
                            scores[t] = float(s["alpha_score"])
                return tickers, scores
    return set(), {}


def _select_tickers(
    *,
    explicit: list[str] | None,
    sample_size: int,
    snap_tickers: set[str],
    scores: dict[str, float],
) -> list[str]:
    winners = {t.upper() for t in KNOWN_WINNERS}
    if explicit:
        return sorted(winners | {t.upper() for t in explicit})

    if len(snap_tickers) <= sample_size:
        return sorted(winners | snap_tickers)

    ranked = sorted(
        (t for t in snap_tickers if t in scores),
        key=lambda t: scores[t],
        reverse=True,
    )
    top50 = set(ranked[:50])
    pool = list(snap_tickers - top50 - winners)
    random.seed(42)
    need = max(0, sample_size - len(top50) - len(winners))
    extra = set(random.sample(pool, min(need, len(pool)))) if pool and need else set()
    return sorted(winners | top50 | extra)


def _feature_row(features: pd.DataFrame, ticker: str) -> pd.Series | None:
    if features.empty or "ticker" not in features.columns:
        return None
    sub = features[features["ticker"].astype(str).str.upper() == ticker.upper()]
    if sub.empty:
        return None
    return sub.iloc[-1]


def _feature_val(row: pd.Series | None, col: str) -> float | None:
    if row is None or col not in row.index:
        return None
    val = row[col]
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    return float(val)


def _asof_scalar(series_df: pd.DataFrame, when: pd.Timestamp) -> float:
    if series_df is None or series_df.empty:
        return np.nan
    arr = _asof_col(series_df, pd.Series([when]))
    v = float(arr[0])
    return v if not np.isnan(v) else np.nan


def _manual_revision(now: float, prior: float) -> float:
    arr = _pct_change_arr(np.array([now]), np.array([prior]))
    v = float(arr[0])
    return v if not np.isnan(v) else np.nan


def _metric_count(raw: pd.DataFrame, metric: str) -> int:
    if raw.empty:
        return 0
    return int((raw["metric"] == metric).sum())


def _rec_metric_count(raw: pd.DataFrame) -> int:
    if raw.empty:
        return 0
    rec = {"rec_strong_buy", "rec_buy", "rec_hold", "rec_sell", "rec_strong_sell"}
    return int(raw["metric"].isin(rec).sum())


def _unique_snapshot_dates(raw: pd.DataFrame, metric: str) -> int:
    sub = raw[raw["metric"] == metric]
    if sub.empty or "snapshot_date" not in sub.columns:
        return 0
    return int(pd.to_datetime(sub["snapshot_date"]).nunique())


def _diagnose_revision_path(
    *,
    metric: str,
    metric_label: str,
    metric_count: int,
    front_rows: int,
    unique_snapshots: int,
    asof_now: float,
    asof_90: float,
    manual_rev: float,
    feature_rev: float | None,
    store_exists: bool,
    estimate_rows: int,
) -> str:
    """Per-metric (eps or rev) failure reason."""
    if not store_exists:
        return "no_estimate_store"
    if estimate_rows == 0:
        return "empty_estimate_store"
    if metric_count == 0:
        return f"missing_{metric_label}_metrics"
    if unique_snapshots <= 1:
        return "store_has_only_latest_snapshot"
    if front_rows == 0:
        return "front_period_selection_failure"
    if np.isnan(asof_now):
        return "missing_current_estimate_value"
    if np.isnan(asof_90):
        return "missing_90d_prior_estimate_value"
    if not np.isnan(manual_rev):
        if feature_rev is None:
            return "revision_feature_assignment_bug"
        return "revision_columns_populated"
    if not np.isnan(asof_now) and not np.isnan(asof_90):
        if asof_90 == 0:
            return "prior_value_zero_or_invalid"
        return "prior_value_zero_or_invalid"
    return "revision_population_unknown"


def _pick_probable_cause(*statuses: str) -> str:
    present = {s for s in statuses if s}
    for cause in _CAUSE_PRIORITY:
        if cause in present:
            return cause
    if present:
        return sorted(present)[0]
    return "revision_population_unknown"


def _revision_quantiles(features: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col in ("e_eps_revision_90d", "e_rev_revision_90d"):
        if col not in features.columns:
            out[col] = {"non_null": 0, "p05": None, "p50": None, "p95": None, "p99": None}
            continue
        s = pd.to_numeric(features[col], errors="coerce").dropna()
        if s.empty:
            out[col] = {"non_null": 0, "p05": None, "p50": None, "p95": None, "p99": None}
        else:
            out[col] = {
                "non_null": int(s.notna().sum()),
                "p05": round(float(s.quantile(0.05)), 4),
                "p50": round(float(s.quantile(0.50)), 4),
                "p95": round(float(s.quantile(0.95)), 4),
                "p99": round(float(s.quantile(0.99)), 4),
            }
    return out


def audit_ticker(
    ticker: str,
    *,
    estimates_store: EstimatesStore,
    features: pd.DataFrame,
    in_snapshot: bool,
) -> dict[str, Any]:
    path = estimates_store._ticker_path(ticker)
    store_exists = path.exists()

    estimate_rows = 0
    min_d: date | None = None
    max_d: date | None = None
    raw = pd.DataFrame()
    metric_sample = ""
    metric_count_eps = 0
    metric_count_rev = 0
    metric_count_pt = 0
    metric_count_surp = 0
    metric_count_rec = 0
    unique_eps_snaps = 0
    unique_rev_snaps = 0
    front_eps_rows = 0
    front_rev_rows = 0
    front_eps = pd.DataFrame(columns=["snap_dt", "value"])
    front_rev = pd.DataFrame(columns=["snap_dt", "value"])

    if store_exists:
        raw = estimates_store.read_ticker(ticker)
        estimate_rows = len(raw)
        if not raw.empty:
            raw = raw.copy()
            raw["snap_dt"] = pd.to_datetime(raw["snapshot_date"])
            dates = raw["snap_dt"].dt.date
            min_d = dates.min()
            max_d = dates.max()
            metrics = sorted(raw["metric"].dropna().unique().tolist())
            metric_sample = ",".join(metrics[:12])
            if len(metrics) > 12:
                metric_sample += ",..."
            metric_count_eps = _metric_count(raw, "eps_est_avg")
            metric_count_rev = _metric_count(raw, "rev_est_avg")
            metric_count_pt = _metric_count(raw, "price_target_mean")
            metric_count_surp = _metric_count(raw, "eps_surprise_pct")
            metric_count_rec = _rec_metric_count(raw)
            unique_eps_snaps = _unique_snapshot_dates(raw, "eps_est_avg")
            unique_rev_snaps = _unique_snapshot_dates(raw, "rev_est_avg")
            front_eps = _front_period_series(raw, "eps_est_avg")
            front_rev = _front_period_series(raw, "rev_est_avg")
            front_eps_rows = len(front_eps)
            front_rev_rows = len(front_rev)

    feat_row = _feature_row(features, ticker)
    feat_exists = feat_row is not None
    feature_date: str | None = None
    asof_eps_now = np.nan
    asof_eps_90d = np.nan
    asof_rev_now = np.nan
    asof_rev_90d = np.nan
    eps_manual = np.nan
    rev_manual = np.nan

    if feat_exists and feat_row is not None:
        feature_date = str(pd.to_datetime(feat_row.get("date")).date())
        when = pd.to_datetime(feat_row.get("date"))
        when_90 = when - pd.Timedelta(days=90)
        asof_eps_now = _asof_scalar(front_eps, when)
        asof_eps_90d = _asof_scalar(front_eps, when_90)
        asof_rev_now = _asof_scalar(front_rev, when)
        asof_rev_90d = _asof_scalar(front_rev, when_90)
        eps_manual = _manual_revision(asof_eps_now, asof_eps_90d)
        rev_manual = _manual_revision(asof_rev_now, asof_rev_90d)

    feat_eps = _feature_val(feat_row, "e_eps_revision_90d")
    feat_rev = _feature_val(feat_row, "e_rev_revision_90d")

    if not store_exists:
        eps_status = rev_status = "no_estimate_store"
    elif not feat_exists:
        eps_status = rev_status = "feature_row_missing"
    elif metric_count_eps == 0 and metric_count_rev == 0:
        eps_status = rev_status = "missing_eps_rev_estimate_metrics"
    else:
        eps_status = _diagnose_revision_path(
            metric="eps_est_avg",
            metric_label="eps_est_avg",
            metric_count=metric_count_eps,
            front_rows=front_eps_rows,
            unique_snapshots=unique_eps_snaps,
            asof_now=asof_eps_now,
            asof_90=asof_eps_90d,
            manual_rev=eps_manual,
            feature_rev=feat_eps,
            store_exists=store_exists,
            estimate_rows=estimate_rows,
        )
        rev_status = _diagnose_revision_path(
            metric="rev_est_avg",
            metric_label="rev_est_avg",
            metric_count=metric_count_rev,
            front_rows=front_rev_rows,
            unique_snapshots=unique_rev_snaps,
            asof_now=asof_rev_now,
            asof_90=asof_rev_90d,
            manual_rev=rev_manual,
            feature_rev=feat_rev,
            store_exists=store_exists,
            estimate_rows=estimate_rows,
        )

    manual_revision_status = f"eps:{eps_status};rev:{rev_status}"
    probable_root_cause = _pick_probable_cause(eps_status, rev_status)

    def _fmt(v: float) -> float | None:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        return round(float(v), 6)

    return {
        "ticker": ticker,
        "in_alpha_snapshot": in_snapshot,
        "estimate_store_exists": store_exists,
        "estimate_rows": estimate_rows,
        "estimate_min_date": str(min_d) if min_d else None,
        "estimate_max_date": str(max_d) if max_d else None,
        "metric_names_sample": metric_sample or None,
        "metric_count_eps_est_avg": metric_count_eps,
        "metric_count_rev_est_avg": metric_count_rev,
        "metric_count_price_target_mean": metric_count_pt,
        "metric_count_eps_surprise_pct": metric_count_surp,
        "metric_count_rec_total": metric_count_rec,
        "unique_eps_est_snapshot_dates": unique_eps_snaps,
        "unique_rev_est_snapshot_dates": unique_rev_snaps,
        "front_eps_rows": front_eps_rows,
        "front_rev_rows": front_rev_rows,
        "feature_row_exists": feat_exists,
        "feature_date": feature_date,
        "asof_eps_now": _fmt(asof_eps_now),
        "asof_eps_90d": _fmt(asof_eps_90d),
        "asof_rev_now": _fmt(asof_rev_now),
        "asof_rev_90d": _fmt(asof_rev_90d),
        "eps_revision_manual_90d": _fmt(eps_manual),
        "rev_revision_manual_90d": _fmt(rev_manual),
        "feature_e_eps_revision_90d": feat_eps,
        "feature_e_rev_revision_90d": feat_rev,
        "feature_e_rec_score": _feature_val(feat_row, "e_rec_score"),
        "feature_e_price_target_upside": _feature_val(feat_row, "e_price_target_upside"),
        "feature_e_eps_surprise_avg4": _feature_val(feat_row, "e_eps_surprise_avg4"),
        "manual_revision_status": manual_revision_status,
        "probable_root_cause": probable_root_cause,
    }


def _finnhub_request_url(path: str, params: dict[str, Any]) -> str:
    safe = {k: v for k, v in params.items() if k != "token"}
    qs = urlencode(safe)
    return f"GET {_BASE_URL}{path}?{qs}&token=<redacted>"


def _finnhub_date_fields_in_rows(rows: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in _FINNHUB_DATE_ROW_KEYS:
            if key in row and row[key] not in (None, ""):
                counts[key] = counts.get(key, 0) + 1
    return counts


def _finnhub_period_asof_duplicates(rows: list[Any]) -> dict[str, Any]:
    """Detect same fiscal period appearing multiple times with different as-of fields."""
    by_period: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        period = str(row.get("period", "")).strip()
        if not period:
            continue
        by_period.setdefault(period, []).append(row)

    multi_row_periods = {p: rs for p, rs in by_period.items() if len(rs) > 1}
    periods_multi_asof: list[dict[str, Any]] = []
    for period, period_rows in multi_row_periods.items():
        signatures: set[tuple[str, str]] = set()
        for row in period_rows:
            for key in _FINNHUB_DATE_ROW_KEYS:
                if key in row and row[key] not in (None, ""):
                    signatures.add((key, str(row[key])))
        if len(signatures) > 1:
            periods_multi_asof.append(
                {
                    "period": period,
                    "row_count": len(period_rows),
                    "asof_signatures": sorted(signatures),
                    "rows": period_rows,
                }
            )

    return {
        "data_row_count": len(rows),
        "unique_periods": len(by_period),
        "periods_with_multiple_rows_in_response": sorted(multi_row_periods.keys())[:20],
        "periods_with_multiple_rows_count": len(multi_row_periods),
        "same_fiscal_period_multiple_asof_in_one_response": bool(periods_multi_asof),
        "examples_same_period_multiple_asof": periods_multi_asof[:5],
    }


def _analyze_finnhub_response(
    *,
    path: str,
    label: str,
    params: dict[str, Any],
    body: list | dict | None,
) -> dict[str, Any]:
    top_level_keys: list[str] | str
    if body is None:
        top_level_keys = []
        data_rows: list[Any] = []
    elif isinstance(body, dict):
        top_level_keys = sorted(body.keys())
        raw_data = body.get("data", [])
        data_rows = raw_data if isinstance(raw_data, list) else []
    elif isinstance(body, list):
        top_level_keys = "<list>"
        data_rows = body
    else:
        top_level_keys = [type(body).__name__]
        data_rows = []

    date_fields = _finnhub_date_fields_in_rows(data_rows)
    period_analysis = _finnhub_period_asof_duplicates(data_rows)

    consensus_shape = "unknown"
    if data_rows and not date_fields and period_analysis["unique_periods"] > 0:
        if period_analysis["periods_with_multiple_rows_count"] == 0:
            consensus_shape = "one_row_per_fiscal_period_no_asof_in_api_rows"
        else:
            consensus_shape = "multiple_rows_per_period_without_distinct_asof_fields"
    elif data_rows and date_fields:
        consensus_shape = "rows_include_asof_like_fields"

    return {
        "endpoint": path,
        "label": label,
        "client_method": "FinnhubClient._safe_get via get_estimates()",
        "request_url": _finnhub_request_url(path, params),
        "request_params": params,
        "top_level_keys": top_level_keys,
        "first_five_data_rows": data_rows[:5],
        "date_fields_present_in_rows": date_fields,
        "checked_date_field_names": list(_FINNHUB_DATE_ROW_KEYS),
        "period_duplicate_analysis": period_analysis,
        "inferred_consensus_shape": consensus_shape,
    }


def _print_finnhub_section(title: str, analysis: dict[str, Any]) -> None:
    print(f"\n--- {title} ---")
    print(f"  request: {analysis['request_url']}")
    print(f"  client:  {analysis['client_method']}")
    print(f"  top-level keys: {analysis['top_level_keys']}")
    print(f"  date fields in rows: {analysis['date_fields_present_in_rows'] or '(none)'}")
    dup = analysis["period_duplicate_analysis"]
    print(
        f"  rows={dup['data_row_count']} unique_periods={dup['unique_periods']} "
        f"multi_row_periods={dup['periods_with_multiple_rows_count']} "
        f"same_period_multiple_asof={dup['same_fiscal_period_multiple_asof_in_one_response']}"
    )
    print(f"  inferred: {analysis['inferred_consensus_shape']}")
    print("  first 5 data rows:")
    for i, row in enumerate(analysis["first_five_data_rows"], start=1):
        print(f"    [{i}] {json.dumps(row, default=str)}")


async def _probe_optional_date_params(
    client: FinnhubClient,
    *,
    symbol: str,
    path: str,
    baseline_params: dict[str, Any],
    baseline_body: list | dict | None,
) -> dict[str, Any]:
    """Try common date/as-of query params; report whether the response changes."""
    baseline_len = 0
    if isinstance(baseline_body, dict):
        data = baseline_body.get("data", [])
        baseline_len = len(data) if isinstance(data, list) else 0

    probes: dict[str, Any] = {}
    optional = {
        "from": "2024-01-01",
        "to": "2026-12-31",
        "asOf": "2025-06-01",
        "asOfDate": "2025-06-01",
        "date": "2025-06-01",
    }
    for key, val in optional.items():
        params = {**baseline_params, key: val}
        try:
            body = await client._safe_get(path, params)
        except Exception as exc:
            probes[key] = {"accepted": False, "error": str(exc)}
            continue
        if isinstance(body, dict):
            data = body.get("data", [])
            row_len = len(data) if isinstance(data, list) else 0
        elif isinstance(body, list):
            row_len = len(body)
        else:
            row_len = 0
        probes[key] = {
            "request_url": _finnhub_request_url(path, params),
            "row_count": row_len,
            "differs_from_baseline_row_count": row_len != baseline_len,
            "top_level_keys": sorted(body.keys()) if isinstance(body, dict) else "<list>",
        }
    return {
        "baseline_row_count": baseline_len,
        "optional_param_probes": probes,
        "client_wrapper_accepts_date_params": False,
        "note": (
            "FinnhubClient.get_estimates() only passes symbol+freq. "
            "Probes above are diagnostic GETs with extra query params."
        ),
    }


async def run_finnhub_api_inspect(
    *,
    tickers: list[str],
    output_json: Path,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.finnhub_api_key:
        raise SystemExit(
            "TYCHE_FINNHUB_API_KEY is not set — required for --inspect-finnhub-api"
        )

    client = FinnhubClient(api_key=settings.finnhub_api_key)
    report: dict[str, Any] = {
        "purpose": (
            "Confirm whether Finnhub /stock/eps-estimate and /stock/revenue-estimate "
            "return point-in-time consensus snapshots or only current consensus by fiscal period."
        ),
        "client_wrapper": _CLIENT_WRAPPER_DOC,
        "tickers": {},
        "optional_param_probes_sample": {},
    }

    print(f"\n{'=' * 60}")
    print("FINNHUB ESTIMATE API INSPECT (raw responses)")
    print(f"{'=' * 60}")
    print("\nClient wrapper (tyche/market_data/finnhub.py):")
    print(f"  method: {_CLIENT_WRAPPER_DOC['method']}")
    print(f"  params sent to API: {_CLIENT_WRAPPER_DOC['params_sent_to_api']}")
    print(f"  NOT sent to API: {_CLIENT_WRAPPER_DOC['params_not_sent_to_api']}")
    print(
        "  date/as-of/from/to in client wrapper: "
        f"{_CLIENT_WRAPPER_DOC['date_asof_from_to_in_client_wrapper']}"
    )

    for ticker in tickers:
        sym = ticker.upper()
        symbols = finnhub_symbol_candidates(ticker)
        ticker_report: dict[str, Any] = {
            "finnhub_symbol_candidates": symbols,
            "endpoints": {},
        }
        print(f"\n{'=' * 60}")
        print(f"TICKER {ticker} (symbols tried: {', '.join(symbols)})")
        print(f"{'=' * 60}")

        for path, label in _FINNHUB_ENDPOINTS:
            params = {"symbol": sym, "freq": "quarterly"}
            body = await client._safe_get(path, params)
            analysis = _analyze_finnhub_response(
                path=path, label=label, params=params, body=body
            )
            ticker_report["endpoints"][label] = analysis
            _print_finnhub_section(f"{ticker} {label}", analysis)

        if ticker.upper() == "MU":
            for path, label in _FINNHUB_ENDPOINTS:
                params = {"symbol": sym, "freq": "quarterly"}
                body = await client._safe_get(path, params)
                report["optional_param_probes_sample"][label] = (
                    await _probe_optional_date_params(
                        client,
                        symbol=sym,
                        path=path,
                        baseline_params=params,
                        baseline_body=body,
                    )
                )
            print(f"\n--- MU optional date/as-of param probes (diagnostic) ---")
            print(json.dumps(report["optional_param_probes_sample"], indent=2, default=str))

        report["tickers"][ticker] = ticker_report

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nWrote {output_json}")
    return report


def _run_store_coverage_audit(args: argparse.Namespace, settings: Any) -> None:
    min_cap = settings.alpha_min_market_cap_millions * 1e6

    snap_tickers, scores = _load_snapshot_tickers(args.data_dir)
    tickers = _select_tickers(
        explicit=args.tickers,
        sample_size=args.sample_size,
        snap_tickers=snap_tickers,
        scores=scores,
    )

    logger.info("estimates_coverage_start", tickers=len(tickers))

    features = build_latest_features(
        data_dir=args.data_dir,
        min_market_cap=min_cap,
        tickers=tickers,
    )

    store = EstimatesStore(data_dir=args.data_dir)
    rows = [
        audit_ticker(
            t,
            estimates_store=store,
            features=features,
            in_snapshot=t in snap_tickers,
        )
        for t in tickers
    ]
    df = pd.DataFrame(rows)

    out_csv = Path(args.output_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False)

    cause_counts = df["probable_root_cause"].value_counts().to_dict()
    winners_df = df[df["ticker"].isin(KNOWN_WINNERS)]
    summary = {
        "tickers_audited": len(tickers),
        "snapshot_universe": len(snap_tickers),
        "feature_rows_built": len(features),
        "root_cause_counts": cause_counts,
        "manual_revision_status_sample": (
            df["manual_revision_status"].value_counts().head(15).to_dict()
        ),
        "revision_populated_count": int(
            (df["probable_root_cause"] == "revision_columns_populated").sum()
        ),
        "revision_feature_assignment_bug_count": int(
            (df["probable_root_cause"] == "revision_feature_assignment_bug").sum()
        ),
        "revision_feature_quantiles": _revision_quantiles(features),
        "known_winners": winners_df.to_dict(orient="records"),
    }

    out_json = Path(args.output_json)
    out_json.write_text(json.dumps(summary, indent=2))

    print(f"\n{'=' * 60}")
    print("ESTIMATES COVERAGE AUDIT")
    print(f"{'=' * 60}")
    print(f"  tickers audited: {len(tickers)}")
    for cause, n in sorted(cause_counts.items(), key=lambda x: -x[1]):
        print(f"  {cause:<42} {n:>5}")
    print(f"\nWrote {out_csv}")
    print(f"Wrote {out_json}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Estimate revision coverage probe (read-only)"
    )
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--tickers", nargs="+", default=None)
    parser.add_argument("--sample-size", type=int, default=250)
    parser.add_argument(
        "--output-csv",
        default="data/ml/alpha_results/estimates_coverage_audit.csv",
    )
    parser.add_argument(
        "--output-json",
        default="data/ml/alpha_results/estimates_coverage_summary.json",
    )
    parser.add_argument(
        "--inspect-finnhub-api",
        action="store_true",
        help=(
            "Fetch raw Finnhub /stock/eps-estimate and /stock/revenue-estimate "
            "for probe tickers (default MU AVGO STX SNDK VRT); skips store audit"
        ),
    )
    parser.add_argument(
        "--finnhub-probe-output",
        default="data/ml/alpha_results/finnhub_estimates_api_probe.json",
        help="JSON path for --inspect-finnhub-api results",
    )
    args = parser.parse_args()
    settings = get_settings()

    if args.inspect_finnhub_api:
        probe_tickers = (
            [t.upper() for t in args.tickers]
            if args.tickers
            else FINNHUB_API_PROBE_TICKERS
        )
        asyncio.run(
            run_finnhub_api_inspect(
                tickers=probe_tickers,
                output_json=Path(args.finnhub_probe_output),
            )
        )
        return

    _run_store_coverage_audit(args, settings)


if __name__ == "__main__":
    main()
