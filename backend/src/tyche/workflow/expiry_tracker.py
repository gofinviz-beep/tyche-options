"""CSP expiry tracker — monitors expired worthless CSPs for pullback re-entry.

When a CSP expires worthless, the user collected premium but missed the stock
entry. This module tracks those expirations and flags the ticker for priority
pullback alerts on the next scan.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from tyche.conviction.alerts import PullbackAlert

logger = structlog.get_logger()


@dataclass
class ExpiredCSP:
    """Record of a CSP that expired worthless."""

    ticker: str
    expired_strike: float
    expiry_date: str
    premium_collected: float
    recorded_at: str


@dataclass
class CSPFallbackAlert:
    """Alert for a ticker where a CSP expired and a new pullback is detected."""

    ticker: str
    expired_strike: float
    expiry_date: str
    premium_collected: float
    pullback_alert: PullbackAlert
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "expired_strike": self.expired_strike,
            "expiry_date": self.expiry_date,
            "premium_collected": round(self.premium_collected, 2),
            "pullback_alert": self.pullback_alert.to_dict(),
            "message": self.message,
        }


class ExpiryTracker:
    """Tracks CSP expirations and generates fallback buy alerts.

    Persists expiry records to a JSON file under db_dir so they survive
    restarts. Generates high-priority alerts when a ticker with a recently
    expired CSP shows a new pullback.
    """

    def __init__(self, db_dir: str = "db") -> None:
        self._db_path = Path(db_dir)
        self._db_path.mkdir(parents=True, exist_ok=True)
        self._file = self._db_path / "expired_csps.json"
        self._records: list[ExpiredCSP] = []
        self._load()

    def _load(self) -> None:
        if self._file.exists():
            try:
                data = json.loads(self._file.read_text())
                self._records = [ExpiredCSP(**r) for r in data]
            except Exception:
                logger.warning("expiry_tracker_load_failed", exc_info=True)
                self._records = []

    def _save(self) -> None:
        try:
            data = [
                {
                    "ticker": r.ticker,
                    "expired_strike": r.expired_strike,
                    "expiry_date": r.expiry_date,
                    "premium_collected": r.premium_collected,
                    "recorded_at": r.recorded_at,
                }
                for r in self._records
            ]
            self._file.write_text(json.dumps(data, indent=2))
        except Exception:
            logger.error("expiry_tracker_save_failed", exc_info=True)

    def record_expiry(
        self,
        ticker: str,
        strike: float,
        expiry_date: str | date,
        premium_collected: float,
    ) -> None:
        """Record a CSP that expired worthless."""
        if isinstance(expiry_date, date):
            expiry_date = expiry_date.isoformat()

        for existing in self._records:
            if (
                existing.ticker == ticker
                and existing.expired_strike == strike
                and existing.expiry_date == expiry_date
            ):
                return

        record = ExpiredCSP(
            ticker=ticker,
            expired_strike=strike,
            expiry_date=expiry_date,
            premium_collected=premium_collected,
            recorded_at=datetime.now(timezone.utc).isoformat(),
        )
        self._records.append(record)
        self._save()

        logger.info(
            "csp_expiry_recorded",
            ticker=ticker,
            strike=strike,
            expiry_date=expiry_date,
            premium=premium_collected,
        )

    def get_watched_tickers(self) -> list[str]:
        """Get list of tickers being watched for pullback re-entry."""
        return list({r.ticker for r in self._records})

    def get_all_records(self) -> list[ExpiredCSP]:
        return list(self._records)

    def remove_ticker(self, ticker: str) -> int:
        """Remove a ticker from the watch list (e.g., after buying stock).

        Returns the number of records removed.
        """
        before = len(self._records)
        self._records = [r for r in self._records if r.ticker != ticker]
        removed = before - len(self._records)
        if removed:
            self._save()
            logger.info("expiry_watch_removed", ticker=ticker, removed=removed)
        return removed

    def cleanup_old(self, max_age_days: int = 30) -> int:
        """Remove expired records older than max_age_days."""
        cutoff = datetime.now(timezone.utc)
        before = len(self._records)
        kept: list[ExpiredCSP] = []
        for r in self._records:
            try:
                exp_date = date.fromisoformat(r.expiry_date)
                age = (cutoff.date() - exp_date).days
                if age <= max_age_days:
                    kept.append(r)
            except ValueError:
                kept.append(r)
        self._records = kept
        removed = before - len(self._records)
        if removed:
            self._save()
            logger.info("expiry_cleanup", removed=removed, remaining=len(self._records))
        return removed

    def generate_fallback_alerts(
        self,
        pullback_alerts: list[PullbackAlert],
    ) -> list[CSPFallbackAlert]:
        """Cross-reference pullback alerts with expired CSP watch list.

        Returns high-priority fallback alerts for tickers where:
        1. A CSP previously expired worthless
        2. A new pullback to 8-EMA or 21-EMA is detected
        """
        watched = {r.ticker: r for r in self._records}
        fallbacks: list[CSPFallbackAlert] = []

        for alert in pullback_alerts:
            record = watched.get(alert.ticker)
            if record is None:
                continue

            ema_label = "21-EMA" if alert.alert_type == "pullback_21ema" else "8-EMA"
            message = (
                f"CSP on {alert.ticker} expired worthless at ${record.expired_strike:.2f} "
                f"(collected ${record.premium_collected:.2f}). "
                f"Stock still in uptrend — pullback to {ema_label} detected at "
                f"${alert.last_close:.2f}. Consider direct stock buy."
            )

            fallbacks.append(CSPFallbackAlert(
                ticker=alert.ticker,
                expired_strike=record.expired_strike,
                expiry_date=record.expiry_date,
                premium_collected=record.premium_collected,
                pullback_alert=alert,
                message=message,
            ))

        if fallbacks:
            logger.info(
                "csp_fallback_alerts_generated",
                count=len(fallbacks),
                tickers=[f.ticker for f in fallbacks],
            )
        return fallbacks
