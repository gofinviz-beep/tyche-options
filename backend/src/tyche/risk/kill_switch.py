"""Kill switch — toggle between preview-only and live trading mode."""

from __future__ import annotations

import structlog

logger = structlog.get_logger()


class KillSwitch:
    """Controls whether live trading is enabled.

    Default state is LOCKED (preview-only). Must be explicitly
    unlocked to enable live order submission.
    """

    def __init__(self, preview_only: bool = True) -> None:
        self._preview_only = preview_only

    @property
    def is_locked(self) -> bool:
        return self._preview_only

    @property
    def is_live(self) -> bool:
        return not self._preview_only

    def enable_live_trading(self) -> None:
        logger.warning("kill_switch_unlocked", action="live_trading_enabled")
        self._preview_only = False

    def enable_preview_only(self) -> None:
        logger.info("kill_switch_locked", action="preview_only_enabled")
        self._preview_only = True

    @property
    def status(self) -> str:
        return "preview_only" if self._preview_only else "live_enabled"
