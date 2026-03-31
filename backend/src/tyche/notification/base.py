"""Notification channel protocol — interface for all notification backends."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from tyche.conviction.alerts import PullbackAlert


@runtime_checkable
class NotificationChannel(Protocol):
    """Protocol for notification delivery channels (email, Slack, etc.)."""

    async def send_pullback_alerts(
        self,
        alerts: list[PullbackAlert],
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Send pullback alert notifications.

        Args:
            alerts: List of pullback alerts to notify about.
            context: Optional extra context (scan_id, timestamp, etc.).

        Returns:
            True if notification was sent successfully.
        """
        ...
