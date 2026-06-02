"""Audit Finnhub-backed fundamentals and estimates coverage for the alpha universe.

Checks every ticker in the build-net universe ($250M+ common stock with OHLCV)
for on-disk Parquet hygiene. Optionally re-fetches from Finnhub (--repair) or
live-verifies API vs store (--live-verify).

Run from ``backend/``:
    .venv/bin/python scripts/audit_demand_coverage.py
    .venv/bin/python scripts/audit_demand_coverage.py --max-filing-age-days 120
    .venv/bin/python scripts/audit_demand_coverage.py --repair --repair-targets missing,stale
    .venv/bin/python scripts/audit_demand_coverage.py --live-verify --only-failures

Outputs:
    data/ml/demand_audit_report.csv      — one row per ticker
    data/ml/demand_audit_summary.json    — aggregate counts + thresholds
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import click
import pandas as pd
import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tyche.config import get_settings, TycheSettings  # noqa: E402
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore  # noqa: E402
from tyche.market_data.estimates_store import EstimatesStore  # noqa: E402
from tyche.market_data.finnhub import FinnhubClient  # noqa: E402
from tyche.market_data.fundamentals_store import FundamentalsStore  # noqa: E402
from tyche.ml.dataset import MIN_BARS, _filter_equity  # noqa: E402

logger = structlog.get_logger()

# Estimates need multiple daily snapshots to compute revisions; flag when flat.
MIN_EST_SNAPSHOT_DATES = 2
REQUIRED_EST_METRICS = ("eps_est_avg", "rec_strong_buy", "eps_surprise_pct")
REQUIRED_FUND_FIELDS = ("revenue", "filing_date", "period_end")

# CLI aliases → audit status codes (default --repair-targets uses these).
_REPAIR_TARGET_ALIASES: dict[str, str] = {
    "MISSING": "MISSING_FILE",
    "MISSING_FILE": "MISSING_FILE",
    "STALE": "STALE",
    "SPARSE": "SPARSE",
    "EMPTY": "EMPTY",
}


def _normalize_repair_targets(raw: str) -> set[str]:
    out: set[str] = set()
    for part in raw.split(","):
        key = part.strip().upper()
        if not key:
            continue
        out.add(_REPAIR_TARGET_ALIASES.get(key, key))
    return out


def _classify_fund(row: dict) -> str:
    """Tag fundamentals failure: source gap vs ingest bug vs audit threshold."""
    status = row.get("fund_status", "")
    if status == "OK":
        return "OK"

    live_empty = row.get("live_fund_api_empty")
    live_rows = row.get("live_fund_rows")
    mismatch = row.get("live_fund_mismatch")
    has_live = live_empty is not None or (live_rows is not None and not pd.isna(live_rows))

    if not has_live:
        return "UNVERIFIED"

    live_rows_n = 0 if live_rows is None or pd.isna(live_rows) else int(live_rows)

    if live_empty is True or live_rows_n == 0:
        if status in ("MISSING_FILE", "EMPTY"):
            return "SOURCE_EMPTY"
        if status == "STALE":
            return "SOURCE_STALE"
        if status == "SPARSE":
            return "SOURCE_SPARSE"
        return "SOURCE_UNKNOWN"

    if mismatch or status in ("MISSING_FILE", "EMPTY"):
        return "INGEST_GAP"

    null_pct = row.get("fund_revenue_null_pct")
    if status == "SPARSE" and null_pct is not None and float(null_pct) >= 90:
        return "SOURCE_SPARSE"

    if status == "STALE":
        return "SOURCE_STALE"

    return "THRESHOLD_ONLY"


def _classify_est(row: dict) -> str:
    """Tag estimates failure: source gap vs ingest bug vs partial coverage."""
    status = row.get("est_status", "")
    if status == "OK":
        return "OK"

    live_empty = row.get("live_est_api_empty")
    live_has_rec = row.get("live_est_has_rec")
    live_has_eps = row.get("live_est_has_eps")
    live_rows = row.get("live_est_rows")
    has_live = live_empty is not None or (live_rows is not None and not pd.isna(live_rows))

    if not has_live:
        return "UNVERIFIED"

    live_rows_n = 0 if live_rows is None or pd.isna(live_rows) else int(live_rows)
    has_api_data = bool(live_has_rec) or bool(live_has_eps) or live_rows_n > 0

    if live_empty is True or not has_api_data:
        if status in ("MISSING_FILE", "EMPTY"):
            return "SOURCE_EMPTY"
        if status == "WARN":
            return "SOURCE_PARTIAL"
        return "SOURCE_UNKNOWN"

    if status in ("MISSING_FILE", "EMPTY"):
        return "INGEST_GAP"

    if status == "WARN":
        issues = str(row.get("est_issues", ""))
        if "single_snapshot" in issues and not row.get("live_est_has_eps"):
            return "SOURCE_PARTIAL"
        if not row.get("est_has_eps_est_avg") and live_has_eps:
            return "INGEST_GAP"
        if not row.get("est_has_rec") and live_has_rec:
            return "INGEST_GAP"
        return "SOURCE_PARTIAL"

    return "THRESHOLD_ONLY"


def _universe(data_dir: str, min_market_cap: float) -> list[str]:
    ohlcv = OHLCVStore(data_dir=data_dir)
    meta = TickerMetaStore(data_dir=data_dir)
    caps = meta.get_market_caps()
    tickers = _filter_equity(
        ohlcv.get_all_tickers(),
        caps,
        min_market_cap,
        meta_store=meta,
        equity_only=True,
        require_cap=True,
    )
    return sorted(tickers)


def _audit_fundamentals(
    ticker: str,
    fund_store: FundamentalsStore,
    *,
    as_of: date,
    max_filing_age_days: int,
    min_quarters: int,
) -> dict:
    path = fund_store._ticker_path(ticker)
    row: dict = {
        "ticker": ticker,
        "fund_file": path.exists(),
        "fund_rows_q": 0,
        "fund_latest_filing": None,
        "fund_latest_period": None,
        "fund_filing_age_days": None,
        "fund_revenue_null_pct": None,
        "fund_status": "MISSING_FILE",
        "fund_issues": "",
    }
    if not path.exists():
        row["fund_issues"] = "no_parquet_file"
        return row

    df = fund_store.read_ticker(ticker, timeframe="quarterly")
    if df is None or df.empty:
        row["fund_status"] = "EMPTY"
        row["fund_issues"] = "file_exists_but_empty"
        return row

    row["fund_rows_q"] = len(df)
    latest = df.sort_values("period_end").iloc[-1]
    filing = pd.to_datetime(latest["filing_date"]).date()
    period = pd.to_datetime(latest["period_end"]).date()
    row["fund_latest_filing"] = filing.isoformat()
    row["fund_latest_period"] = period.isoformat()
    age = (as_of - filing).days
    row["fund_filing_age_days"] = age
    rev_null = df["revenue"].isna().mean() if "revenue" in df.columns else 1.0
    row["fund_revenue_null_pct"] = round(float(rev_null) * 100, 1)

    issues: list[str] = []
    if len(df) < min_quarters:
        issues.append(f"fewer_than_{min_quarters}_quarters")
    if age > max_filing_age_days:
        issues.append(f"filing_older_than_{max_filing_age_days}d")
    if rev_null > 0.5:
        issues.append("revenue_mostly_null")
    for field in REQUIRED_FUND_FIELDS:
        if field not in df.columns:
            issues.append(f"missing_column_{field}")

    if issues:
        if any("filing_older" in i for i in issues):
            row["fund_status"] = "STALE"
        else:
            row["fund_status"] = "SPARSE"
        row["fund_issues"] = ";".join(issues)
    else:
        row["fund_status"] = "OK"
        row["fund_issues"] = ""

    return row


def _audit_estimates(
    ticker: str,
    est_store: EstimatesStore,
    *,
    as_of: date,
    max_snapshot_age_days: int,
) -> dict:
    path = est_store._ticker_path(ticker)
    row: dict = {
        "ticker": ticker,
        "est_file": path.exists(),
        "est_rows": 0,
        "est_snapshot_dates": 0,
        "est_latest_snapshot": None,
        "est_snapshot_age_days": None,
        "est_has_eps_est_avg": False,
        "est_has_rec": False,
        "est_has_surprise": False,
        "est_status": "MISSING_FILE",
        "est_issues": "",
    }
    if not path.exists():
        row["est_issues"] = "no_parquet_file"
        return row

    df = est_store.read_ticker(ticker)
    if df is None or df.empty:
        row["est_status"] = "EMPTY"
        row["est_issues"] = "file_exists_but_empty"
        return row

    row["est_rows"] = len(df)
    metrics = set(df["metric"].unique())
    row["est_has_eps_est_avg"] = "eps_est_avg" in metrics or "eps_estimate" in metrics
    row["est_has_rec"] = any(m.startswith("rec_") for m in metrics)
    row["est_has_surprise"] = "eps_surprise_pct" in metrics

    snaps = pd.to_datetime(df["snapshot_date"]).dt.date
    uniq = sorted(set(snaps))
    row["est_snapshot_dates"] = len(uniq)
    latest_snap = max(uniq)
    row["est_latest_snapshot"] = latest_snap.isoformat()
    snap_age = (as_of - latest_snap).days
    row["est_snapshot_age_days"] = snap_age

    issues: list[str] = []
    if not row["est_has_eps_est_avg"]:
        issues.append("no_eps_consensus")
    if not row["est_has_rec"]:
        issues.append("no_recommendations")
    if snap_age > max_snapshot_age_days:
        issues.append(f"snapshot_older_than_{max_snapshot_age_days}d")
    if len(uniq) < MIN_EST_SNAPSHOT_DATES:
        issues.append("single_snapshot_no_revision_history")

    if issues:
        if "no_parquet" in str(issues):
            row["est_status"] = "MISSING_FILE"
        elif "no_eps" in str(issues) and "no_rec" in str(issues):
            row["est_status"] = "EMPTY"
        else:
            row["est_status"] = "WARN"
        row["est_issues"] = ";".join(issues)
    else:
        row["est_status"] = "OK"
        row["est_issues"] = ""

    return row


async def _live_verify_fundamentals(
    finnhub: FinnhubClient,
    ticker: str,
    local: dict,
) -> dict:
    """Compare Finnhub /stock/financials-reported vs local store."""
    out = {
        "live_fund_rows": 0,
        "live_fund_latest_filing": None,
        "live_fund_api_empty": False,
        "live_fund_mismatch": False,
        "live_fund_note": "",
    }
    rows = await finnhub.get_financials_statements(ticker, freq="quarterly", limit=20)
    if not rows:
        out["live_fund_api_empty"] = True
        out["live_fund_note"] = "finnhub_returned_empty"
        return out
    out["live_fund_rows"] = len(rows)
    filings = [r["filing_date"] for r in rows if r.get("filing_date")]
    if filings:
        latest = max(filings)
        out["live_fund_latest_filing"] = str(latest)
        local_f = local.get("fund_latest_filing")
        if local_f and str(latest) > str(local_f):
            out["live_fund_mismatch"] = True
            out["live_fund_note"] = f"api_newer_than_local({local_f})"
    return out


async def _live_verify_estimates(
    finnhub: FinnhubClient,
    ticker: str,
    local: dict,
) -> dict:
    """Compare Finnhub estimates endpoints vs local store."""
    out = {
        "live_est_rows": 0,
        "live_est_has_rec": False,
        "live_est_has_eps": False,
        "live_est_has_surprise": False,
        "live_est_api_empty": False,
        "live_est_mismatch": False,
        "live_est_note": "",
    }
    rec = await finnhub.get_recommendation_trends(ticker)
    eps_surprises = await finnhub.get_earnings_surprises(ticker)
    eps_est = await finnhub.get_estimates(ticker)
    out["live_est_rows"] = len(rec) + len(eps_surprises) + len(eps_est)
    out["live_est_has_rec"] = bool(rec)
    out["live_est_has_eps"] = any(
        r.get("metric") in ("eps_est_avg", "eps_estimate") for r in eps_est
    )
    out["live_est_has_surprise"] = bool(eps_surprises)

    if out["live_est_rows"] == 0:
        out["live_est_api_empty"] = True
        out["live_est_note"] = "finnhub_returned_empty"
        return out

    local_missing = local.get("est_status") in ("MISSING_FILE", "EMPTY") or not local.get(
        "est_file"
    )
    local_partial = local.get("est_status") == "WARN"
    if local_missing:
        out["live_est_mismatch"] = True
        out["live_est_note"] = "api_has_data_no_local_file"
    elif local_partial:
        if out["live_est_has_eps"] and not local.get("est_has_eps_est_avg"):
            out["live_est_mismatch"] = True
            out["live_est_note"] = "api_has_eps_local_missing"
        elif out["live_est_has_rec"] and not local.get("est_has_rec"):
            out["live_est_mismatch"] = True
            out["live_est_note"] = "api_has_rec_local_missing"
    return out


async def _repair_tickers(
    settings: TycheSettings,
    tickers: list[str],
    *,
    do_fundamentals: bool,
    do_estimates: bool,
    concurrency: int,
    limit_periods: int,
) -> dict:
    from tyche.workflow.demand_data import ingest_demand_data

    return await ingest_demand_data(
        settings,
        tickers=tickers,
        do_fundamentals=do_fundamentals,
        do_estimates=do_estimates,
        do_short_interest=False,
        do_guidance=False,
        concurrency=concurrency,
        limit_periods=limit_periods,
    )


@click.command()
@click.option("--data-dir", default="data", help="Data root (default: data).")
@click.option(
    "--min-market-cap",
    default=250_000_000,
    type=float,
    help="Universe floor in USD (matches alpha batch).",
)
@click.option(
    "--max-filing-age-days",
    default=120,
    type=int,
    help="Flag fundamentals stale when latest filing is older than this.",
)
@click.option(
    "--max-snapshot-age-days",
    default=14,
    type=int,
    help="Flag estimates stale when latest snapshot is older than this.",
)
@click.option("--min-quarters", default=4, type=int, help="Min quarterly rows required.")
@click.option(
    "--output-dir",
    default="data/ml",
    help="Directory for audit_report.csv and audit_summary.json.",
)
@click.option(
    "--repair",
    is_flag=True,
    help="Re-ingest failed tickers from Finnhub after audit.",
)
@click.option(
    "--repair-targets",
    default="missing,stale,sparse,empty",
    help="Comma-separated fund statuses to repair (default: missing,stale,sparse,empty).",
)
@click.option(
    "--repair-estimates",
    is_flag=True,
    help="Also re-ingest estimates for tickers with est_status != OK.",
)
@click.option("--concurrency", default=20, type=int, help="Parallel Finnhub workers for repair.")
@click.option(
    "--live-verify",
    is_flag=True,
    help="Hit Finnhub API for each ticker (slow; use --only-failures).",
)
@click.option(
    "--live-verify-estimates",
    is_flag=True,
    help="Also live-verify estimates (4 API calls/ticker; use --only-failures).",
)
@click.option(
    "--only-failures",
    is_flag=True,
    help="With --live-verify, only verify tickers that failed local audit.",
)
@click.option(
    "--from-report",
    default="",
    type=str,
    help="Skip local audit; load existing demand_audit_report.csv and continue.",
)
@click.option(
    "--repair-ingest-gaps-only",
    is_flag=True,
    help="Repair only INGEST_GAP tickers (requires live-verify classification).",
)
@click.option("--limit", default=0, type=int, help="Audit first N tickers only (0 = all).")
def main(
    data_dir: str,
    min_market_cap: float,
    max_filing_age_days: int,
    max_snapshot_age_days: int,
    min_quarters: int,
    output_dir: str,
    repair: bool,
    repair_targets: str,
    repair_estimates: bool,
    concurrency: int,
    live_verify: bool,
    live_verify_estimates: bool,
    only_failures: bool,
    from_report: str,
    repair_ingest_gaps_only: bool,
    limit: int,
) -> None:
    """Audit fundamentals + estimates Parquet coverage for the alpha universe."""
    as_of = date.today()
    settings = get_settings()

    if from_report:
        report_path = Path(from_report)
        if not report_path.exists():
            click.echo(f"Report not found: {report_path}", err=True)
            raise SystemExit(1)
        df = pd.read_csv(report_path)
        tickers = df["ticker"].tolist()
        click.echo(f"Loaded {len(tickers)} tickers from {report_path}")
    else:
        tickers = _universe(data_dir, min_market_cap)
        if limit > 0:
            tickers = tickers[:limit]

        fund_store = FundamentalsStore(data_dir=data_dir)
        est_store = EstimatesStore(data_dir=data_dir)

        click.echo(
            f"Auditing {len(tickers)} tickers (as_of={as_of}, mcap>={min_market_cap/1e6:.0f}M)"
        )

        records: list[dict] = []
        for i, t in enumerate(tickers, start=1):
            rec = {"ticker": t}
            rec.update(
                _audit_fundamentals(
                    t,
                    fund_store,
                    as_of=as_of,
                    max_filing_age_days=max_filing_age_days,
                    min_quarters=min_quarters,
                )
            )
            rec.update(
                _audit_estimates(
                    t,
                    est_store,
                    as_of=as_of,
                    max_snapshot_age_days=max_snapshot_age_days,
                )
            )
            try:
                ohlcv_len = len(OHLCVStore(data_dir=data_dir).read_ticker(t))
            except Exception:
                ohlcv_len = 0
            rec["ohlcv_bars"] = ohlcv_len
            rec["alpha_batch_eligible"] = ohlcv_len >= MIN_BARS
            records.append(rec)
            if i % 500 == 0:
                click.echo(f"  ... audited {i}/{len(tickers)}")

        df = pd.DataFrame(records)

    need_fund_live = live_verify and settings.finnhub_api_key and (
        "live_fund_rows" not in df.columns
    )
    need_est_live = (live_verify or live_verify_estimates) and settings.finnhub_api_key and (
        live_verify_estimates or "live_est_rows" not in df.columns
    )

    if need_fund_live or need_est_live:

        async def _verify_all() -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
            client = FinnhubClient(
                api_key=settings.finnhub_api_key,
                rate_limit_rpm=settings.finnhub_rate_limit_rpm,
            )
            targets = df
            if only_failures:
                if live_verify_estimates and not need_fund_live:
                    targets = df[df["est_status"] != "OK"]
                else:
                    targets = df[
                        (df["fund_status"] != "OK") | (df["est_status"] != "OK")
                    ]
            fund_live_rows: list[dict] = []
            est_live_rows: list[dict] = []
            sem = asyncio.Semaphore(concurrency)
            click.echo(
                f"Live Finnhub verify: {len(targets)} tickers "
                f"(fund={need_fund_live}, est={need_est_live}, workers={concurrency})"
            )

            async def _one(row: pd.Series) -> tuple[dict | None, dict | None]:
                async with sem:
                    t = row["ticker"]
                    local = row.to_dict()
                    fund_out = None
                    est_out = None
                    if need_fund_live and row.get("fund_status") != "OK":
                        fund_out = {"ticker": t, **await _live_verify_fundamentals(client, t, local)}
                    if need_est_live and row.get("est_status") != "OK":
                        est_out = {"ticker": t, **await _live_verify_estimates(client, t, local)}
                    return fund_out, est_out

            done = 0
            tasks = [asyncio.create_task(_one(row)) for _, row in targets.iterrows()]
            for coro in asyncio.as_completed(tasks):
                fund_out, est_out = await coro
                if fund_out:
                    fund_live_rows.append(fund_out)
                if est_out:
                    est_live_rows.append(est_out)
                done += 1
                if done % 50 == 0:
                    click.echo(f"  ... live verified {done}/{len(tasks)}")

            fund_df = pd.DataFrame(fund_live_rows) if fund_live_rows else None
            est_df = pd.DataFrame(est_live_rows) if est_live_rows else None
            return fund_df, est_df

        fund_live_df, est_live_df = asyncio.run(_verify_all())
        if fund_live_df is not None and not fund_live_df.empty:
            df = df.drop(
                columns=[c for c in fund_live_df.columns if c != "ticker" and c in df.columns],
                errors="ignore",
            )
            df = df.merge(fund_live_df, on="ticker", how="left")
        if est_live_df is not None and not est_live_df.empty:
            df = df.drop(
                columns=[c for c in est_live_df.columns if c != "ticker" and c in df.columns],
                errors="ignore",
            )
            df = df.merge(est_live_df, on="ticker", how="left")
    elif live_verify or live_verify_estimates:
        click.echo("Skipping live verify — TYCHE_FINNHUB_API_KEY not set", err=True)

    df["fund_class"] = df.apply(lambda r: _classify_fund(r.to_dict()), axis=1)
    df["est_class"] = df.apply(lambda r: _classify_est(r.to_dict()), axis=1)

    # Summary
    fund_counts = df["fund_status"].value_counts().to_dict()
    est_counts = df["est_status"].value_counts().to_dict()
    fund_class_counts = df["fund_class"].value_counts().to_dict()
    est_class_counts = df["est_class"].value_counts().to_dict()
    alpha_eligible = int(df["alpha_batch_eligible"].sum()) if "alpha_batch_eligible" in df.columns else 0
    universe_size = len(df)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of.isoformat(),
        "universe_size": universe_size,
        "alpha_batch_eligible": alpha_eligible,
        "alpha_batch_excluded_insufficient_bars": universe_size - alpha_eligible,
        "thresholds": {
            "min_market_cap_usd": min_market_cap,
            "max_filing_age_days": max_filing_age_days,
            "max_snapshot_age_days": max_snapshot_age_days,
            "min_quarters": min_quarters,
            "min_est_snapshot_dates": MIN_EST_SNAPSHOT_DATES,
        },
        "fundamentals": fund_counts,
        "estimates": est_counts,
        "fund_class": fund_class_counts,
        "est_class": est_class_counts,
        "fund_ingest_gaps": int((df["fund_class"] == "INGEST_GAP").sum()),
        "est_ingest_gaps": int((df["est_class"] == "INGEST_GAP").sum()),
        "both_ok": int(
            ((df["fund_status"] == "OK") & (df["est_status"].isin(["OK", "WARN"]))).sum()
        ),
        "fund_missing_or_stale": int(
            df["fund_status"].isin(["MISSING_FILE", "EMPTY", "STALE"]).sum()
        ),
        "est_missing": int(df["est_status"].isin(["MISSING_FILE", "EMPTY"]).sum()),
        "est_single_snapshot": int(
            df["est_issues"].str.contains("single_snapshot", na=False).sum()
        ),
    }

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "demand_audit_report.csv"
    summary_path = out_dir / "demand_audit_summary.json"
    df.sort_values(["fund_status", "est_status", "ticker"]).to_csv(report_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2))

    click.echo("\n=== DEMAND DATA AUDIT SUMMARY ===")
    click.echo(f"Universe: {universe_size} tickers (${min_market_cap/1e6:.0f}M+ equity)")
    click.echo(f"Alpha batch eligible (>={MIN_BARS} OHLCV bars): {alpha_eligible}")
    click.echo(f"Fundamentals: {fund_counts}")
    click.echo(f"Estimates:    {est_counts}")
    click.echo(f"Fund classification: {fund_class_counts}")
    click.echo(f"Est classification:  {est_class_counts}")
    click.echo(f"Ingest gaps (fund/est): {summary['fund_ingest_gaps']} / {summary['est_ingest_gaps']}")
    click.echo(f"Estimates w/ single snapshot (no revisions): {summary['est_single_snapshot']}")
    click.echo(f"Report:  {report_path}")
    click.echo(f"Summary: {summary_path}")

    if repair:
        if repair_ingest_gaps_only:
            fund_repair = df[df["fund_class"] == "INGEST_GAP"]["ticker"].tolist()
            est_repair = df[df["est_class"] == "INGEST_GAP"]["ticker"].tolist()
            if df["fund_class"].eq("UNVERIFIED").any() or df["est_class"].eq("UNVERIFIED").any():
                click.echo(
                    "Warning: some failures are UNVERIFIED — run with --live-verify "
                    "before --repair-ingest-gaps-only.",
                    err=True,
                )
        else:
            targets_set = _normalize_repair_targets(repair_targets)
            fund_repair = df[df["fund_status"].isin(targets_set)]["ticker"].tolist()
            est_repair = (
                df[df["est_status"].isin(["MISSING_FILE", "EMPTY", "WARN"])]["ticker"].tolist()
                if repair_estimates
                else []
            )
        repair_list = sorted(set(fund_repair) | set(est_repair))
        if not repair_list:
            click.echo("Repair: nothing to do.")
            return
        mode = "ingest-gaps-only" if repair_ingest_gaps_only else "status-targets"
        click.echo(
            f"\nRepairing {len(repair_list)} tickers via Finnhub ({mode}) "
            f"(fund={len(fund_repair)}, est={len(est_repair)})..."
        )
        counts = asyncio.run(
            _repair_tickers(
                settings,
                repair_list,
                do_fundamentals=bool(fund_repair),
                do_estimates=bool(est_repair),
                concurrency=concurrency,
                limit_periods=20,
            )
        )
        click.echo(f"Repair complete: {counts}")
        click.echo("Re-run audit without --repair to verify.")


if __name__ == "__main__":
    main()
