"""Email notification channel — sends pullback alerts and daily digests via SMTP."""

from __future__ import annotations

import asyncio
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import structlog

from tyche.conviction.alerts import PullbackAlert
from tyche.models.conviction import ConvictionSnapshot, ConvictionTransition

logger = structlog.get_logger()


class EmailNotifier:
    """Sends pullback alert emails via SMTP (Gmail, etc.)."""

    def __init__(
        self,
        smtp_host: str = "smtp.gmail.com",
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        to_address: str = "",
    ) -> None:
        self._host = smtp_host
        self._port = smtp_port
        self._user = smtp_user
        self._password = smtp_password
        self._to = to_address

    @property
    def is_configured(self) -> bool:
        return bool(self._user and self._password and self._to)

    async def send_pullback_alerts(
        self,
        alerts: list[PullbackAlert],
        context: dict[str, Any] | None = None,
    ) -> bool:
        if not self.is_configured:
            logger.warning("email_not_configured")
            return False

        if not alerts:
            return True

        subject = self._build_subject(alerts)
        body = self._build_html(alerts, context)

        return await asyncio.to_thread(
            self._send_email, subject, body
        )

    def _send_email(self, subject: str, html_body: str) -> bool:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self._user
            msg["To"] = self._to
            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP(self._host, self._port) as server:
                server.starttls()
                server.login(self._user, self._password)
                server.sendmail(self._user, self._to, msg.as_string())

            logger.info("pullback_email_sent", to=self._to, subject=subject)
            return True
        except Exception:
            logger.error("pullback_email_failed", exc_info=True)
            return False

    async def send_daily_digest(
        self,
        pullbacks: list[ConvictionSnapshot],
        transitions: list[ConvictionTransition],
        context: dict[str, Any] | None = None,
    ) -> bool:
        if not self.is_configured:
            logger.warning("email_not_configured")
            return False

        if not pullbacks and not transitions:
            return True

        subject = self._build_digest_subject(pullbacks, transitions)
        body = self._build_digest_html(pullbacks, transitions, context)

        return await asyncio.to_thread(self._send_email, subject, body)

    def _build_digest_subject(
        self,
        pullbacks: list[ConvictionSnapshot],
        transitions: list[ConvictionTransition],
    ) -> str:
        new_pullback_count = sum(
            1 for t in transitions
            if t.to_state in ("pullback_to_8ema", "pullback_to_21ema")
        )
        parts = [f"{len(pullbacks)} active pullback{'s' if len(pullbacks) != 1 else ''}"]
        if new_pullback_count:
            parts.append(f"{new_pullback_count} new today")
        return f"Tyche Daily: {', '.join(parts)}"

    def _build_digest_html(
        self,
        pullbacks: list[ConvictionSnapshot],
        transitions: list[ConvictionTransition],
        context: dict[str, Any] | None = None,
    ) -> str:
        transition_rows = ""
        for t in transitions:
            arrow = "→"
            from_label = t.from_state.replace("_", " ").title()
            to_label = t.to_state.replace("_", " ").title()
            is_pullback = t.to_state in ("pullback_to_8ema", "pullback_to_21ema")
            row_color = "#fef2f2" if is_pullback else "#ffffff"
            transition_rows += f"""
            <tr style="background:{row_color};border-bottom:1px solid #e5e7eb;">
                <td style="padding:8px;font-weight:600;">{t.ticker}</td>
                <td style="padding:8px;">{from_label} {arrow} {to_label}</td>
                <td style="padding:8px;">${t.last_close:.2f}</td>
                <td style="padding:8px;">{t.conviction_level}</td>
            </tr>"""

        pullback_rows = ""
        ema_21_pullbacks = [p for p in pullbacks if p.trend_state == "pullback_to_21ema"]
        ema_8_pullbacks = [p for p in pullbacks if p.trend_state == "pullback_to_8ema"]

        for p in ema_21_pullbacks + ema_8_pullbacks:
            ema_type = "21-EMA" if p.trend_state == "pullback_to_21ema" else "8-EMA"
            severity_color = "#dc2626" if p.trend_state == "pullback_to_21ema" else "#6b7280"
            rsi_val = getattr(p, "rsi_14", 0)
            ema50_slope = getattr(p, "ema_50_slope", 0)
            ema50_arrow = "▲" if ema50_slope > 0 else "▼"
            pullback_rows += f"""
            <tr style="border-bottom:1px solid #e5e7eb;">
                <td style="padding:8px;font-weight:600;">{p.ticker}</td>
                <td style="padding:8px;"><span style="color:{severity_color}">{ema_type}</span></td>
                <td style="padding:8px;">${p.last_close:.2f}</td>
                <td style="padding:8px;">${p.ema_8:.2f} / ${p.ema_21:.2f}</td>
                <td style="padding:8px;">{rsi_val:.0f}</td>
                <td style="padding:8px;">{ema50_arrow}</td>
                <td style="padding:8px;">{p.conviction_level}</td>
                <td style="padding:8px;">{'Yes' if p.volume_declining else 'No'}</td>
            </tr>"""

        transitions_section = ""
        if transition_rows:
            transitions_section = f"""
            <h3 style="margin:20px 0 8px;color:#1e293b;">State Transitions Today</h3>
            <table style="width:100%;border-collapse:collapse;font-size:14px;">
                <thead>
                    <tr style="border-bottom:2px solid #1e293b;text-align:left;">
                        <th style="padding:8px;">Ticker</th>
                        <th style="padding:8px;">Transition</th>
                        <th style="padding:8px;">Price</th>
                        <th style="padding:8px;">Conviction</th>
                    </tr>
                </thead>
                <tbody>{transition_rows}</tbody>
            </table>"""

        pullbacks_section = ""
        if pullback_rows:
            pullbacks_section = f"""
            <h3 style="margin:20px 0 8px;color:#1e293b;">Active Pullbacks</h3>
            <table style="width:100%;border-collapse:collapse;font-size:14px;">
                <thead>
                    <tr style="border-bottom:2px solid #1e293b;text-align:left;">
                        <th style="padding:8px;">Ticker</th>
                        <th style="padding:8px;">Type</th>
                        <th style="padding:8px;">Price</th>
                        <th style="padding:8px;">8/21 EMA</th>
                        <th style="padding:8px;">RSI</th>
                        <th style="padding:8px;">50-EMA</th>
                        <th style="padding:8px;">Conviction</th>
                        <th style="padding:8px;">Vol Declining</th>
                    </tr>
                </thead>
                <tbody>{pullback_rows}</tbody>
            </table>"""

        return f"""
        <html>
        <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:900px;margin:0 auto;">
            <div style="background:#1e293b;color:white;padding:20px;border-radius:8px 8px 0 0;">
                <h2 style="margin:0;">Tyche — Daily Conviction Digest</h2>
                <p style="margin:4px 0 0;color:#94a3b8;">
                    {len(pullbacks)} active pullback{'s' if len(pullbacks) != 1 else ''},
                    {len(transitions)} transition{'s' if len(transitions) != 1 else ''} today
                </p>
            </div>
            <div style="padding:16px;border:1px solid #e5e7eb;border-top:none;">
                {transitions_section}
                {pullbacks_section}
            </div>
            <div style="background:#f8fafc;padding:12px 16px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;">
                <p style="margin:0;color:#64748b;font-size:12px;">
                    Recommendations only. Review each setup before placing orders.
                </p>
            </div>
        </body>
        </html>
        """

    def _build_subject(self, alerts: list[PullbackAlert]) -> str:
        high_count = sum(1 for a in alerts if a.severity == "high")
        tickers = ", ".join(a.ticker for a in alerts[:5])
        suffix = f" +{len(alerts) - 5} more" if len(alerts) > 5 else ""

        if high_count > 0:
            return f"🔔 {high_count} High-Conviction Pullback{'s' if high_count > 1 else ''}: {tickers}{suffix}"
        return f"📊 {len(alerts)} EMA Pullback Alert{'s' if len(alerts) > 1 else ''}: {tickers}{suffix}"

    def _build_html(
        self,
        alerts: list[PullbackAlert],
        context: dict[str, Any] | None = None,
    ) -> str:
        rows = ""
        for a in alerts:
            severity_color = "#dc2626" if a.severity == "high" else "#6b7280"
            severity_badge = (
                f'<span style="background:{severity_color};color:white;'
                f'padding:2px 8px;border-radius:4px;font-size:12px;">'
                f'{a.severity.upper()}</span>'
            )

            inst_display = f"{a.institutional_pct * 100:.1f}%" if a.institutional_pct else "N/A"

            rsi_val = getattr(a, "rsi_14", 0)
            ema50_slope = getattr(a, "ema_50_slope", 0)
            ema50_arrow = "▲" if ema50_slope > 0 else "▼"

            rows += f"""
            <tr style="border-bottom:1px solid #e5e7eb;">
                <td style="padding:12px 8px;font-weight:600;">{a.ticker}</td>
                <td style="padding:12px 8px;">{severity_badge}</td>
                <td style="padding:12px 8px;">{a.alert_type.replace('_', ' ').title()}</td>
                <td style="padding:12px 8px;">${a.last_close:.2f}</td>
                <td style="padding:12px 8px;">${a.ema_8:.2f} / ${a.ema_21:.2f}</td>
                <td style="padding:12px 8px;">{rsi_val:.0f}</td>
                <td style="padding:12px 8px;">{ema50_arrow}</td>
                <td style="padding:12px 8px;">${a.stop_loss_level:.2f}</td>
                <td style="padding:12px 8px;">{'✓' if a.volume_declining else '✗'}</td>
                <td style="padding:12px 8px;">{inst_display}</td>
                <td style="padding:12px 8px;">{a.position_size_hint}</td>
            </tr>
            <tr style="border-bottom:2px solid #e5e7eb;">
                <td colspan="11" style="padding:4px 8px 12px;color:#6b7280;font-size:13px;">
                    {a.suggested_action}
                </td>
            </tr>"""

        scan_info = ""
        if context and context.get("scan_id"):
            scan_info = f'<p style="color:#9ca3af;font-size:12px;">Scan ID: {context["scan_id"]}</p>'

        return f"""
        <html>
        <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:900px;margin:0 auto;">
            <div style="background:#1e293b;color:white;padding:20px;border-radius:8px 8px 0 0;">
                <h2 style="margin:0;">Tyche — EMA Pullback Alerts</h2>
                <p style="margin:4px 0 0;color:#94a3b8;">
                    {len(alerts)} pullback{'s' if len(alerts) > 1 else ''} detected
                </p>
            </div>
            <div style="padding:16px;border:1px solid #e5e7eb;border-top:none;">
                <table style="width:100%;border-collapse:collapse;font-size:14px;">
                    <thead>
                        <tr style="border-bottom:2px solid #1e293b;text-align:left;">
                            <th style="padding:8px;">Ticker</th>
                            <th style="padding:8px;">Severity</th>
                            <th style="padding:8px;">Type</th>
                            <th style="padding:8px;">Price</th>
                            <th style="padding:8px;">8/21 EMA</th>
                            <th style="padding:8px;">RSI</th>
                            <th style="padding:8px;">50-EMA</th>
                            <th style="padding:8px;">Stop</th>
                            <th style="padding:8px;">Vol↓</th>
                            <th style="padding:8px;">Inst%</th>
                            <th style="padding:8px;">Size</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
                {scan_info}
            </div>
            <div style="background:#f8fafc;padding:12px 16px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;">
                <p style="margin:0;color:#64748b;font-size:12px;">
                    This is a recommendation only. Review each setup before placing orders.
                    Stop-loss levels are suggestions based on EMA positioning.
                </p>
            </div>
        </body>
        </html>
        """
