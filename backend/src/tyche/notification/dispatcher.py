"""Notification dispatcher — routes alerts to configured channels."""

from __future__ import annotations

from typing import Any

import structlog

from tyche.config import TycheSettings
from tyche.conviction.alerts import PullbackAlert
from tyche.models.conviction import ConvictionSnapshot, ConvictionTransition
from tyche.notification.base import NotificationChannel
from tyche.notification.email import EmailNotifier

logger = structlog.get_logger()


class NotificationDispatcher:
    """Routes pullback alerts to all configured notification channels."""

    def __init__(self, channels: list[NotificationChannel] | None = None) -> None:
        self._channels: list[NotificationChannel] = channels or []

    @classmethod
    def from_settings(cls, settings: TycheSettings) -> NotificationDispatcher:
        """Build a dispatcher from application settings."""
        channels: list[NotificationChannel] = []

        if settings.notification_email_enabled:
            notifier = EmailNotifier(
                smtp_host=settings.notification_smtp_host,
                smtp_port=settings.notification_smtp_port,
                smtp_user=settings.notification_smtp_user,
                smtp_password=settings.notification_smtp_password,
                to_address=settings.notification_email_to,
            )
            if notifier.is_configured:
                channels.append(notifier)
                logger.info("email_notification_channel_registered")
            else:
                logger.warning(
                    "email_notification_enabled_but_not_configured",
                    has_user=bool(settings.notification_smtp_user),
                    has_password=bool(settings.notification_smtp_password),
                    has_to=bool(settings.notification_email_to),
                )

        return cls(channels=channels)

    @property
    def channel_count(self) -> int:
        return len(self._channels)

    async def dispatch_pullback_alerts(
        self,
        alerts: list[PullbackAlert],
        context: dict[str, Any] | None = None,
    ) -> dict[str, bool]:
        """Send alerts to all registered channels.

        Returns:
            Dict of channel_name -> success boolean.
        """
        if not alerts:
            return {}

        if not self._channels:
            logger.debug("no_notification_channels_configured")
            return {}

        results: dict[str, bool] = {}
        for channel in self._channels:
            name = type(channel).__name__
            try:
                success = await channel.send_pullback_alerts(alerts, context)
                results[name] = success
            except Exception:
                logger.error(
                    "notification_channel_failed",
                    channel=name,
                    exc_info=True,
                )
                results[name] = False

        logger.info(
            "notifications_dispatched",
            channels=len(self._channels),
            results=results,
            alerts=len(alerts),
        )
        return results

    async def dispatch_daily_digest(
        self,
        pullbacks: list[ConvictionSnapshot],
        transitions: list[ConvictionTransition],
        context: dict[str, Any] | None = None,
    ) -> dict[str, bool]:
        """Send daily digest to all registered channels that support it."""
        if not pullbacks and not transitions:
            return {}

        if not self._channels:
            logger.debug("no_notification_channels_for_digest")
            return {}

        results: dict[str, bool] = {}
        for channel in self._channels:
            name = type(channel).__name__
            if not hasattr(channel, "send_daily_digest"):
                continue
            try:
                success = await channel.send_daily_digest(
                    pullbacks, transitions, context
                )
                results[name] = success
            except Exception:
                logger.error(
                    "digest_channel_failed", channel=name, exc_info=True
                )
                results[name] = False

        logger.info(
            "daily_digest_dispatched",
            channels=len(results),
            results=results,
            pullbacks=len(pullbacks),
            transitions=len(transitions),
        )
        return results
