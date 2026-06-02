"""Compare Finnhub fundamentals filing dates vs SEC EDGAR for STALE tickers.

For every ticker flagged STALE in ``demand_audit_report.csv`` (Finnhub latest
filing >120 days old), fetches the newest 10-Q/10-K from SEC submissions and
measures whether EDGAR has newer filed data than Finnhub — the PL pattern.

Run from ``backend/``:
    .venv/bin/python scripts/audit_finnhub_vs_edgar.py
    .venv/bin/python scripts/audit_finnhub_vs_edgar.py --limit 20

Outputs:
    data/ml/finnhub_edgar_lag_report.csv
    data/ml/finnhub_edgar_lag_summary.json

Requires ``TYCHE_EDGAR_USER_AGENT_EMAIL`` in ``.env``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import click
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tyche.config import get_settings  # noqa: E402
from tyche.market_data.edgar import EdgarClient  # noqa: E402

_QUARTERLY_FORMS = frozenset({"10-Q", "10-K", "10-Q/A", "10-K/A"})
# SEC filed this many days after Finnhub → Finnhub lag (not clock skew).
_LAG_THRESHOLD_DAYS = 7


def _latest_sec_quarterly(submissions: dict) -> dict | None:
    """Newest 10-Q/10-K from SEC submissions ``recent`` block."""
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    if not forms:
        return None

    best_filed: str | None = None
    best: dict | None = None
    for i, form in enumerate(forms):
        if (form or "").upper() not in _QUARTERLY_FORMS:
            continue
        filed = filing_dates[i] if i < len(filing_dates) else ""
        if not filed:
            continue
        if best_filed is None or filed > best_filed:
            best_filed = filed
            best = {
                "sec_latest_filing": filed,
                "sec_latest_period": report_dates[i] if i < len(report_dates) else "",
                "sec_form": form,
            }
    return best


def _classify(
    *,
    finnhub_filing: str | None,
    sec_filing: str | None,
    lag_days: int | None,
) -> str:
    if sec_filing is None:
        return "NO_SEC_FILINGS"
    if finnhub_filing is None:
        return "NO_FINNHUB_FILING"
    if lag_days is None:
        return "UNKNOWN"
    if lag_days > _LAG_THRESHOLD_DAYS:
        return "FINNHUB_LAG"
    if lag_days < -_LAG_THRESHOLD_DAYS:
        return "FINNHUB_AHEAD"
    return "ALIGNED"


@click.command()
@click.option(
    "--report",
    default="data/ml/demand_audit_report.csv",
    help="Input audit CSV with fund_status / fund_latest_filing.",
)
@click.option(
    "--output-dir",
    default="data/ml",
    help="Directory for lag report CSV + summary JSON.",
)
@click.option(
    "--max-filing-age-days",
    default=120,
    type=int,
    help="Only tickers with fund filing age above this (default: STALE threshold).",
)
@click.option("--limit", default=0, type=int, help="Process first N STALE tickers (0=all).")
@click.option(
    "--also-live-finnhub",
    is_flag=True,
    help="Re-fetch Finnhub financials-reported live (slow; confirms store/API).",
)
def main(
    report: str,
    output_dir: str,
    max_filing_age_days: int,
    limit: int,
    also_live_finnhub: bool,
) -> None:
    """Audit SEC vs Finnhub filing freshness for STALE fundamentals tickers."""
    settings = get_settings()
    if not settings.edgar_user_agent_email:
        raise click.ClickException(
            "TYCHE_EDGAR_USER_AGENT_EMAIL required — set in .env"
        )

    audit_path = Path(report)
    if not audit_path.exists():
        raise click.ClickException(f"Audit report not found: {audit_path}")

    audit = pd.read_csv(audit_path)
    stale = audit[
        (audit["fund_status"] == "STALE")
        | (audit["fund_filing_age_days"] > max_filing_age_days)
    ].copy()
    stale = stale.drop_duplicates(subset=["ticker"])
    if limit > 0:
        stale = stale.head(limit)

    click.echo(
        f"Comparing SEC EDGAR vs Finnhub for {len(stale)} STALE tickers "
        f"(filing age > {max_filing_age_days}d)..."
    )

    async def _run() -> pd.DataFrame:
        client = EdgarClient(user_agent_email=settings.edgar_user_agent_email)
        finnhub = None
        if also_live_finnhub and settings.finnhub_api_key:
            from tyche.market_data.finnhub import FinnhubClient

            finnhub = FinnhubClient(
                api_key=settings.finnhub_api_key,
                rate_limit_rpm=settings.finnhub_rate_limit_rpm,
            )

        cik_map = await client.resolve_ciks(stale["ticker"].tolist())
        rows: list[dict] = []
        done = 0

        for _, row in stale.iterrows():
            ticker = row["ticker"]
            fh_filing = row.get("fund_latest_filing")
            fh_period = row.get("fund_latest_period")
            fh_age = row.get("fund_filing_age_days")

            rec: dict = {
                "ticker": ticker,
                "fund_filing_age_days": fh_age,
                "finnhub_latest_filing": fh_filing,
                "finnhub_latest_period": fh_period,
                "sec_latest_filing": None,
                "sec_latest_period": None,
                "sec_form": None,
                "sec_filing_lag_days": None,
                "verdict": "NO_CIK",
                "note": "",
            }

            cik = cik_map.get(ticker)
            if not cik:
                rec["note"] = "ticker_not_in_sec_cik_map"
                rows.append(rec)
                done += 1
                continue

            try:
                subs = await client.get_submissions(cik)
            except Exception as exc:
                rec["verdict"] = "SEC_ERROR"
                rec["note"] = str(exc)[:120]
                rows.append(rec)
                done += 1
                continue

            sec = _latest_sec_quarterly(subs)
            if sec:
                rec.update(sec)
                if pd.notna(fh_filing) and fh_filing:
                    fh_d = date.fromisoformat(str(fh_filing)[:10])
                    sec_d = date.fromisoformat(rec["sec_latest_filing"][:10])
                    lag = (sec_d - fh_d).days
                    rec["sec_filing_lag_days"] = lag
                    rec["verdict"] = _classify(
                        finnhub_filing=str(fh_filing),
                        sec_filing=rec["sec_latest_filing"],
                        lag_days=lag,
                    )
                else:
                    rec["verdict"] = "NO_FINNHUB_FILING"
            else:
                rec["verdict"] = "NO_SEC_FILINGS"

            if finnhub is not None:
                live = await finnhub.get_financials_statements(
                    ticker, freq="quarterly", limit=1
                )
                if live:
                    rec["live_finnhub_filing"] = live[0].get("filing_date")
                    rec["live_finnhub_period"] = live[0].get("period_end")
                    if rec["sec_latest_filing"] and rec["live_finnhub_filing"]:
                        ld = date.fromisoformat(str(rec["live_finnhub_filing"])[:10])
                        sd = date.fromisoformat(rec["sec_latest_filing"][:10])
                        rec["live_finnhub_lag_days"] = (sd - ld).days
                else:
                    rec["live_finnhub_filing"] = None

            rows.append(rec)
            done += 1
            if done % 50 == 0:
                click.echo(f"  ... checked {done}/{len(stale)}")

        return pd.DataFrame(rows)

    df = asyncio.run(_run())
    df = df.sort_values(
        ["verdict", "sec_filing_lag_days"],
        ascending=[True, False],
        na_position="last",
    )

    verdict_counts = df["verdict"].value_counts().to_dict()
    lag = df[df["verdict"] == "FINNHUB_LAG"]["sec_filing_lag_days"]
    lag_30 = int((lag > 30).sum()) if len(lag) else 0
    lag_90 = int((lag > 90).sum()) if len(lag) else 0

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stale_universe_size": len(df),
        "max_filing_age_days_threshold": max_filing_age_days,
        "verdict_counts": verdict_counts,
        "finnhub_lag_count": int((df["verdict"] == "FINNHUB_LAG").sum()),
        "finnhub_lag_pct": round(
            100.0 * (df["verdict"] == "FINNHUB_LAG").sum() / max(len(df), 1), 1
        ),
        "finnhub_lag_gt_30d": lag_30,
        "finnhub_lag_gt_90d": lag_90,
        "aligned_count": int((df["verdict"] == "ALIGNED").sum()),
        "no_sec_filings": int((df["verdict"] == "NO_SEC_FILINGS").sum()),
        "no_cik": int((df["verdict"] == "NO_CIK").sum()),
        "median_lag_days_finnhub_lag": float(lag.median()) if len(lag) else None,
        "example_finnhub_lag": df[df["verdict"] == "FINNHUB_LAG"]
        .head(15)[
            [
                "ticker",
                "finnhub_latest_filing",
                "sec_latest_filing",
                "sec_filing_lag_days",
            ]
        ]
        .to_dict(orient="records"),
    }

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "finnhub_edgar_lag_report.csv"
    json_path = out_dir / "finnhub_edgar_lag_summary.json"
    df.to_csv(csv_path, index=False)
    json_path.write_text(json.dumps(summary, indent=2))

    click.echo("\n=== FINNHUB vs SEC EDGAR (STALE TICKERS) ===")
    click.echo(f"STALE tickers checked: {len(df)}")
    click.echo(f"Verdicts: {verdict_counts}")
    click.echo(
        f"FINNHUB_LAG (SEC newer by >{_LAG_THRESHOLD_DAYS}d): "
        f"{summary['finnhub_lag_count']} ({summary['finnhub_lag_pct']}%)"
    )
    click.echo(f"  lag >30d: {lag_30}  |  lag >90d: {lag_90}")
    click.echo(f"ALIGNED (Finnhub matches SEC): {summary['aligned_count']}")
    click.echo(f"Report:  {csv_path}")
    click.echo(f"Summary: {json_path}")


if __name__ == "__main__":
    main()
